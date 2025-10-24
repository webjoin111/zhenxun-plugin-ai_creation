from datetime import datetime, timedelta

from nonebot.adapters.onebot.v11 import Bot, Event, MessageEvent
from nonebot.exception import FinishedException
from nonebot.permission import SUPERUSER
from nonebot_plugin_alconna import AlconnaMatcher, CommandResult, UniMessage, UniMsg
from nonebot_plugin_alconna.uniseg import Image as UniImage
from nonebot_plugin_waiter import waiter
from pydantic import BaseModel

from zhenxun import ui
from zhenxun.services.llm import generate_structured, message_to_unimessage
from zhenxun.services.log import logger
from zhenxun.ui.builders import TableBuilder

from . import (
    draw_cmd,
    draw_limiter,
    dtemplate_public_cmd,
    dtemplate_superuser_cmd,
)
from .config import (
    SYSTEM_PROMPT_CREATE_FROM_IMAGE,
    SYSTEM_PROMPT_REFINE_TEMPLATE,
    base_config,
)
from .services.drawing_service import (
    DrawingContext,
    DrawingService,
    resolve_template_name_by_input,
)
from .templates import template_manager


@draw_cmd.handle()
async def draw_handler(
    bot: "Bot",
    event: MessageEvent,
    result: CommandResult,
    msg: UniMsg,
    cmd: AlconnaMatcher,
):
    """AI绘图命令处理器"""
    try:
        is_superuser = await SUPERUSER(bot, event)
        initial_options = result.result.options if result.result else {}

        ctx = DrawingContext(
            bot=bot,
            event=event,
            matcher=cmd,
            command_result=result,
            user_id=event.get_user_id(),
            initial_options=initial_options,
            is_superuser=is_superuser,
        )

        service = DrawingService(ctx, draw_limiter)
        await service.run()

    except FinishedException:
        raise


class TemplateCreationResponse(BaseModel):
    success: bool
    template_name: str
    prompt: str


class TemplateRefinementResponse(BaseModel):
    success: bool
    new_prompt: str


@dtemplate_public_cmd.handle()
async def dtemplate_handler(result: CommandResult, cmd: AlconnaMatcher):
    """绘图模板命令处理器 (list, info)"""
    if sub := result.result.subcommands.get("list"):
        templates = template_manager.list_templates()
        if not templates:
            await dtemplate_public_cmd.finish("当前没有任何绘图模板。")

        builder = TableBuilder(
            title="AI绘图模板列表", tip=f"共 {len(templates)} 个模板"
        )
        builder.set_headers(["序号", "模板名称", "提示词预览"])
        for i, (name, prompt) in enumerate(templates.items(), 1):
            preview = (prompt[:30] + "...") if len(prompt) > 30 else prompt
            builder.add_row([str(i), name, preview.replace("\n", " ")])

        img = await ui.render(builder.build(), use_cache=False)
        await dtemplate_public_cmd.finish(UniMessage.image(raw=img))

    elif sub := result.result.subcommands.get("info"):
        template_input = str(sub.args.get("name", ""))
        resolved_name = await resolve_template_name_by_input(template_input, cmd)
        prompt = template_manager.get_prompt(resolved_name)
        if prompt:
            await dtemplate_public_cmd.finish(
                f"🎨 模板 '{resolved_name}' 的内容如下：\n\n{prompt}"
            )
        else:
            await dtemplate_public_cmd.finish(
                f"❌ 未找到名为 '{resolved_name}' 的模板。"
            )


@dtemplate_superuser_cmd.handle()
async def dtemplate_superuser_handler(
    result: CommandResult, cmd: AlconnaMatcher, event: MessageEvent, msg: UniMsg
):
    """绘图模板管理命令处理器 (超级用户)"""
    if sub := result.result.subcommands.get("create"):
        main_args = sub.args.get("prompt", [])
        user_intent_message = UniMessage(main_args)

        if event.reply and event.reply.message:
            reply_unimsg = message_to_unimessage(event.reply.message)
            if reply_unimsg[UniImage]:
                user_intent_message.extend(reply_unimsg[UniImage])  # type: ignore

        if not user_intent_message[UniImage]:
            await cmd.finish(
                "❌ 创建模板需要一张图片。请在命令中附带图片，或回复一张包含图片的聊天记录。"
            )

        try:
            await cmd.send("⏳ 正在分析图片并生成模板，请稍候...")
            initial_name, initial_prompt = await _llm_create_template_from_image(
                user_intent_message
            )
            await _template_refinement_session(
                cmd=cmd,
                event=event,
                initial_prompt=initial_prompt,
                template_name=initial_name,
                is_new=True,
            )
        except ValueError as e:
            await cmd.finish(f"❌ 创建失败: {e}")
        except FinishedException:
            raise
        except Exception as e:
            logger.error("处理 preset create 命令时发生未知错误", e=e)
            await cmd.finish("❌ 创建模板时发生意外错误，请检查后台日志。")

    elif sub := result.result.subcommands.get("optimize"):
        template_name = sub.args["name"]
        instruction = sub.args.get("instruction", "")

        base_prompt = template_manager.get_prompt(template_name)
        if not base_prompt:
            await cmd.finish(f"❌ 未找到名为 “{template_name}” 的模板。")

        current_prompt = base_prompt
        try:
            if instruction:
                await cmd.send("⏳ 正在根据您的指令进行优化，请稍候...")
                current_prompt = await _llm_refine_template(base_prompt, instruction)

            await _template_refinement_session(
                cmd=cmd,
                event=event,
                initial_prompt=current_prompt,
                template_name=template_name,
                is_new=False,
            )
        except ValueError as e:
            await cmd.finish(f"❌ 优化失败: {e}")
        except FinishedException:
            raise
        except Exception as e:
            logger.error("处理 preset optimize 命令时发生未知错误", e=e)
            await cmd.finish("❌ 优化模板时发生意外错误，请检查后台日志。")

    if sub := result.result.subcommands.get("add"):
        name = sub.args["name"]
        prompt = str(sub.args["prompt"])
        if await template_manager.add_template(name, prompt):
            await dtemplate_superuser_cmd.finish(f"✅ 成功添加模板 '{name}'。")
        else:
            await dtemplate_superuser_cmd.finish(f"❌ 添加失败：模板 '{name}' 已存在。")

    elif sub := result.result.subcommands.get("del"):
        names_to_delete = sub.args.get("names", [])
        if not names_to_delete:
            await dtemplate_superuser_cmd.finish("❌ 请提供至少一个要删除的模板名称。")

        deleted_templates = []
        failed_templates = []

        for name_input in names_to_delete:
            resolved_name = await resolve_template_name_by_input(name_input, cmd)
            if await template_manager.delete_template(resolved_name):
                deleted_templates.append(resolved_name)
            else:
                failed_templates.append(resolved_name)

        message_parts = []
        if deleted_templates:
            message_parts.append(f"🗑️ 成功删除模板：{'、'.join(deleted_templates)}")
        if failed_templates:
            message_parts.append(
                f"❌ 删除失败（未找到）：{'、'.join(failed_templates)}"
            )

        await dtemplate_superuser_cmd.finish("\n".join(message_parts))

    elif sub := result.result.subcommands.get("clear"):
        template_count = len(template_manager.list_templates())
        if template_count == 0:
            await dtemplate_superuser_cmd.finish("当前没有任何绘图模板，无需清空。")

        @waiter(waits=["message"], keep_session=True)
        async def confirm_waiter(event: Event):
            if event.get_plaintext().strip().lower() == "yes":
                return True
            return False

        await dtemplate_superuser_cmd.send(
            f"⚠️ 您确定要删除全部 {template_count} 个模板吗？此操作不可逆！\n请在30秒内回复【yes】确认。"
        )
        confirmed = await confirm_waiter.wait(timeout=30)

        if confirmed:
            deleted_count = await template_manager.clear_all_templates()
            await dtemplate_superuser_cmd.finish(
                f"✅ 已成功清空 {deleted_count} 个绘图模板。"
            )
        else:
            await dtemplate_superuser_cmd.finish("操作已取消。")

    elif sub := result.result.subcommands.get("reload"):
        try:
            count = await template_manager.reload_templates()
            await dtemplate_superuser_cmd.finish(
                f"✅ 成功从 templates.toml 重新加载了 {count} 个模板。"
            )
        except FinishedException:
            raise
        except Exception as e:
            logger.error("重载绘图模板失败", e=e)
            await dtemplate_superuser_cmd.finish(
                f"❌ 重载模板失败，请检查后台日志。错误: {e}"
            )

    elif sub := result.result.subcommands.get("edit"):
        template_input = str(sub.args.get("name", ""))
        resolved_name = await resolve_template_name_by_input(template_input, cmd)
        prompt = str(sub.args["prompt"])
        if await template_manager.update_template(resolved_name, prompt):
            await dtemplate_superuser_cmd.finish(f"✅ 成功更新模板 '{resolved_name}'。")
        else:
            await dtemplate_superuser_cmd.finish(
                f"❌ 更新失败：未找到模板 '{resolved_name}'。"
            )


async def _llm_create_template_from_image(user_intent: UniMessage) -> tuple[str, str]:
    """使用LLM从图片和文本生成模板名称和提示词"""
    try:
        response = await generate_structured(
            user_intent,
            response_model=TemplateCreationResponse,
            model=base_config.get("auxiliary_llm_model"),
            instruction=SYSTEM_PROMPT_CREATE_FROM_IMAGE,
        )
        if response.success and response.template_name and response.prompt:
            return response.template_name, response.prompt
        raise ValueError("LLM返回的数据不完整")
    except Exception as e:
        logger.error("LLM创建模板失败", e=e)
        raise ValueError(f"AI未能成功生成模板，请稍后重试。({e})")


async def _llm_refine_template(base_prompt: str, instruction: str) -> str:
    """使用LLM根据指令优化现有模板"""
    try:
        response = await generate_structured(
            f"【基础模板】:\n{base_prompt}\n\n【用户修改指令】:\n{instruction}",
            response_model=TemplateRefinementResponse,
            model=base_config.get("auxiliary_llm_model"),
            instruction=SYSTEM_PROMPT_REFINE_TEMPLATE,
        )
        if response.success and response.new_prompt:
            return response.new_prompt
        raise ValueError("LLM返回的数据不完整")
    except Exception as e:
        logger.error("LLM优化模板失败", e=e)
        raise ValueError(f"AI未能成功优化模板，请稍后重试。({e})")


async def _template_refinement_session(
    cmd: AlconnaMatcher,
    event: MessageEvent,
    initial_prompt: str,
    template_name: str,
    is_new: bool,
):
    """管理模板创建/优化的连续对话会话"""
    current_prompt = initial_prompt
    session_end_time = datetime.now() + timedelta(minutes=5)

    action_text = "创建" if is_new else "优化"

    while datetime.now() < session_end_time:
        remaining_seconds = (session_end_time - datetime.now()).total_seconds()

        await cmd.send(
            f"🎨 **模板{action_text}预览**\n\n"
            f"**名称**: `{template_name}`\n"
            f"**提示词**: \n{current_prompt}\n\n"
            " > 请在 **{:.0f}秒** 内回复：\n"
            " > - **【确认】** 保存此模板\n"
            " > - **【取消】** 放弃操作\n"
            " > - 或直接发送 **新的修改指令**".format(remaining_seconds)
        )

        @waiter(waits=["message"], keep_session=True)
        async def get_user_feedback(event: Event):
            return event.get_plaintext().strip()

        feedback = await get_user_feedback.wait(timeout=remaining_seconds)

        if feedback is None:
            await cmd.finish("⏳ 操作超时，已自动取消。")
            return

        feedback_lower = feedback.lower()

        if feedback_lower in ["yes", "确认", "ok", "保存", "是"]:
            try:
                if is_new:
                    success = await template_manager.add_template(
                        template_name, current_prompt
                    )
                    if not success:
                        await cmd.finish(
                            f"❌ 添加失败：模板 “{template_name}” 已存在。"
                        )
                else:
                    success = await template_manager.update_template(
                        template_name, current_prompt
                    )
                    if not success:
                        await cmd.finish(f"❌ 更新失败：未找到模板 “{template_name}”。")

                await cmd.finish(f"✅ 模板 “{template_name}” 已成功保存！")
            except FinishedException:
                raise
            except Exception as e:
                await cmd.finish(f"❌ 保存模板时出错: {e}")
            return

        if feedback_lower in ["no", "取消", "算了", "否"]:
            await cmd.finish("好的，操作已取消。")
            return

        try:
            await cmd.send("⏳ 正在根据您的新指令进行优化，请稍候...")
            new_prompt = await _llm_refine_template(current_prompt, feedback)
            current_prompt = new_prompt
            logger.info(f"模板 “{template_name}” 已被用户指令优化。")
        except FinishedException:
            raise
        except ValueError as e:
            await cmd.send(str(e))
        except Exception as e:
            logger.error("在模板优化会话中调用LLM失败", e=e)
            await cmd.send("抱歉，在处理您的指令时遇到了问题，请稍后再试。")

    await cmd.finish("⏳ 会话已达5分钟上限，操作已结束。")


__all__ = [
    "draw_handler",
    "dtemplate_handler",
    "dtemplate_superuser_handler",
]
