import json
import re
from datetime import datetime, timedelta

import aiofiles
from nonebot.adapters.onebot.v11 import (
    Bot,
    Event,
    GroupMessageEvent,
    Message,
    MessageEvent,
    MessageSegment,
)
from nonebot.exception import FinishedException
from nonebot.permission import SUPERUSER
from pydantic import BaseModel
from nonebot_plugin_alconna import (
    AlconnaMatcher,
    At,
    CommandResult,
    UniMessage,
    UniMsg,
)
from nonebot_plugin_waiter import waiter
from nonebot_plugin_alconna.uniseg import Image as UniImage

from zhenxun import ui
from zhenxun.services import avatar_service
from zhenxun.services.llm import (
    CommonOverrides,
    LLMMessage,
    generate,
    generate_structured,
    message_to_unimessage,
    unimsg_to_llm_parts,
)
from zhenxun.services.llm.config import LLMGenerationConfig
from zhenxun.services.llm.types import get_user_friendly_error_message
from zhenxun.services.log import logger
from zhenxun.ui.builders import TableBuilder
from zhenxun.utils.platform import PlatformUtils
from zhenxun.utils.time_utils import TimeUtils

from . import draw_cmd, draw_limiter, dtemplate_public_cmd, dtemplate_superuser_cmd
from .config import (
    SYSTEM_PROMPT_CREATE_FROM_IMAGE,
    SYSTEM_PROMPT_FUSION,
    SYSTEM_PROMPT_OPTIMIZE,
    SYSTEM_PROMPT_REFINE_TEMPLATE,
    base_config,
)
from .core.engine import LlmApiEngine, get_engine
from .templates import template_manager


async def send_images_as_forward(
    bot: Bot,
    event: MessageEvent,
    images_bytes: list[bytes],
    prompt: str,
    text_response: str | None = None,
):
    """发送图片作为合并转发消息"""
    try:
        images_count = len(images_bytes)
        forward_messages = []

        if text_response:
            forward_messages.append(
                {
                    "type": "node",
                    "data": {
                        "name": "AI绘图助手",
                        "uin": str(bot.self_id),
                        "content": [MessageSegment.text(f"📝 {text_response}")],
                    },
                }
            )

        for i, image_bytes in enumerate(images_bytes):
            content = [
                MessageSegment.text(f"🎨 图片 {i + 1}/{images_count}"),
                MessageSegment.image(file=image_bytes),
            ]

            forward_messages.append(
                {
                    "type": "node",
                    "data": {
                        "name": "AI绘图助手",
                        "uin": str(bot.self_id),
                        "content": content,
                    },
                }
            )

        if isinstance(event, GroupMessageEvent):
            await bot.call_api(
                "send_group_forward_msg",
                group_id=event.group_id,
                messages=forward_messages,
            )
            logger.info(f"✅ 成功发送 {images_count} 张图片的群聊合并转发消息")
        else:
            await bot.call_api(
                "send_private_forward_msg",
                user_id=event.user_id,
                messages=forward_messages,
            )
            logger.info(f"✅ 成功发送 {images_count} 张图片的私聊合并转发消息")

        return True

    except Exception as e:
        logger.error(f"发送合并转发消息失败: {e}")
        return False


async def send_images_as_single_message(
    bot: Bot,
    event: MessageEvent,
    images_bytes: list[bytes],
    prompt: str,
    text_response: str | None = None,
):
    """将所有内容放在一个消息里发送"""
    try:
        images_count = len(images_bytes)
        message_segments = [MessageSegment.text(f"📝 {prompt}")]

        if text_response:
            message_segments.append(MessageSegment.text(f"\n📝 {text_response}"))

        for i, image_bytes in enumerate(images_bytes):
            message_segments.append(
                MessageSegment.text(f"\n🎨 图片 {i + 1}/{images_count}")
            )
            message_segments.append(MessageSegment.image(file=image_bytes))

        await bot.send(event, Message(message_segments))
        logger.info(f"✅ 成功发送包含 {images_count} 张图片的单条消息")
        return True

    except Exception as e:
        logger.error(f"发送单条消息失败: {e}")
        return False


async def _optimize_draw_prompt(
    user_message: UniMessage, user_id: str, template_prompt: str | None = None
) -> str:
    """
    使用支持视觉功能的LLM优化用户的绘图描述。
    支持“文生图”的创意扩展和“图生图”的指令理解与融合。
    """
    logger.info(f"🎨 启用绘图描述优化，为用户 '{user_id}' 的描述进行润色...")

    original_prompt = user_message.extract_plain_text().strip()

    try:
        logger.debug(
            f"绘图描述优化将使用模型: {base_config.get('auxiliary_llm_model')}"
        )

        gen_config = None
        if "gemini" in base_config.get("auxiliary_llm_model", "").lower():
            gen_config = CommonOverrides.gemini_json()
        else:
            gen_config = LLMGenerationConfig(response_format={"type": "json_object"})

        content_parts = await unimsg_to_llm_parts(user_message)
        if not content_parts and not template_prompt:
            logger.warning("无法从用户消息中提取有效内容进行优化，将使用原始描述。")
            return original_prompt

        if template_prompt:
            system_prompt = SYSTEM_PROMPT_FUSION
            fusion_user_text = (
                f"【基础模板】:\n{template_prompt}\n\n"
                f"【用户修改指令】:\n{original_prompt}"
            )
            fusion_message = UniMessage([fusion_user_text])
            for seg in user_message:
                if not isinstance(seg, str):
                    fusion_message.append(seg)
            final_content_parts = await unimsg_to_llm_parts(fusion_message)
        else:
            system_prompt = SYSTEM_PROMPT_OPTIMIZE
            final_content_parts = content_parts
        messages = [
            LLMMessage.system(system_prompt),
            LLMMessage.user(final_content_parts),
        ]

        llm_response = await generate(
            messages,
            model=base_config.get("auxiliary_llm_model"),
            **gen_config.to_dict(),
        )
        response_text = llm_response.text

        json_match = re.search(r"\{.*\}", response_text, re.DOTALL)
        if not json_match:
            logger.warning("描述优化LLM未返回有效的JSON结构，将使用原始描述。")
            return original_prompt

        parsed_json = json.loads(json_match.group())

        if parsed_json.get("success") and (
            optimized := parsed_json.get("optimized_prompt")
        ):
            logger.info(f"✅ 描述优化成功。优化后: '{optimized}'")
            return optimized
        else:
            logger.warning("描述优化LLM返回内容不符合预期，将使用原始描述。")
            return original_prompt

    except Exception as e:
        logger.error(f"❌ 绘图描述优化失败，将使用原始描述。错误: {e}")
        return original_prompt


class TemplateCreationResponse(BaseModel):
    success: bool
    template_name: str
    prompt: str


class TemplateRefinementResponse(BaseModel):
    success: bool
    new_prompt: str


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
        else:
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
        else:
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

        elif feedback_lower in ["no", "取消", "算了", "否"]:
            await cmd.finish("好的，操作已取消。")
            return

        else:
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


async def _resolve_template_name_by_input(user_input: str, cmd: AlconnaMatcher) -> str:
    """
    根据用户输入（名称或序号）解析出模板的真实名称。
    如果输入是无效序号，会自动发送错误消息并结束命令。
    """
    if not user_input:
        await cmd.finish("❌ 错误：模板名称或序号不能为空。")

    if user_input.isdigit():
        try:
            index = int(user_input) - 1
            all_templates = template_manager.list_templates()
            if 0 <= index < len(all_templates):
                return list(all_templates.keys())[index]
            else:
                await cmd.finish(
                    f"❌ 错误：序号 '{user_input}' 超出范围，请输入 1 到 {len(all_templates)} 之间的数字。"
                )
        except (ValueError, IndexError):
            await cmd.finish(f"❌ 错误：无效的模板序号 '{user_input}'。")
    return user_input


@draw_cmd.handle()
async def draw_handler(
    bot: "Bot",
    event: MessageEvent,
    result: CommandResult,
    msg: UniMsg,
    cmd: AlconnaMatcher,
):
    """AI绘图命令处理器"""
    user_id_str = event.get_user_id()

    is_superuser = await SUPERUSER(bot, event)

    try:
        main_args = result.result.main_args if result.result.main_args else {}
        initial_segments = list(main_args.get("prompt", [])) + list(
            main_args.get("$extra", [])
        )

        final_segments = []
        user_ids_to_fetch = set()
        image_bytes_list = []

        for seg in initial_segments:
            if isinstance(seg, At):
                user_ids_to_fetch.add(seg.target)
            elif isinstance(seg, str):
                matches = re.findall(r"@(\d{5,12})", seg)
                if matches:
                    user_ids_to_fetch.update(matches)
                    cleaned_text = re.sub(r"@\d{5,12}", "", seg).strip()
                    if cleaned_text:
                        final_segments.append(cleaned_text)
                else:
                    final_segments.append(seg)
            else:
                final_segments.append(seg)

        if user_ids_to_fetch:
            logger.info(f"检测到艾特 {len(user_ids_to_fetch)} 位用户，将获取头像...")
            platform = PlatformUtils.get_platform(bot)
            for uid in user_ids_to_fetch:
                avatar_path = await avatar_service.get_avatar_path(platform, uid)
                if avatar_path and avatar_path.exists():
                    async with aiofiles.open(avatar_path, "rb") as f:
                        image_bytes_list.append(await f.read())

        text_parts = [seg for seg in final_segments if isinstance(seg, str)]
        other_parts = [seg for seg in final_segments if not isinstance(seg, str)]

        reconstructed_text = " ".join(text_parts)

        new_message_parts = []
        if reconstructed_text:
            new_message_parts.append(reconstructed_text)
        new_message_parts.extend(other_parts)
        user_intent_message = UniMessage(new_message_parts)

        if event.reply and event.reply.message:  # type: ignore
            reply_unimsg = message_to_unimessage(event.reply.message)
            if reply_unimsg[UniImage]:
                for seg in reply_unimsg:
                    if isinstance(seg, UniImage):
                        user_intent_message.append(seg)
                logger.debug("已合并引用消息中的图片内容。")
            else:
                user_intent_message = user_intent_message + reply_unimsg
                logger.debug("已合并引用消息中的文本内容。")

        user_prompt = user_intent_message.extract_plain_text().strip()
        template_prompt = None
        options = result.result.options
        initial_message_parts = []

        if template_option := options.get("template"):
            template_input = str(template_option.args.get("template_name", ""))
            resolved_template_name = await _resolve_template_name_by_input(
                template_input, cmd
            )
            template_prompt = template_manager.get_prompt(resolved_template_name)
            if not template_prompt:
                await draw_cmd.finish(
                    f"❌ 错误：未找到名为 '{resolved_template_name}' 的模板。"
                )
            else:
                initial_message_parts.append(
                    f"🎨 正在使用模板 '{resolved_template_name}' 进行绘图..."
                )

        if image_segments := user_intent_message[UniImage]:
            logger.info(f"检测到 {len(image_segments)} 张图片输入，准备用于绘图...")
            for image_seg in image_segments:
                image_data = None
                if image_seg.raw:
                    image_data = image_seg.raw
                elif image_seg.path:
                    async with aiofiles.open(image_seg.path, "rb") as f:
                        image_data = await f.read()
                elif image_seg.url:
                    from zhenxun.utils.http_utils import AsyncHttpx

                    image_data = await AsyncHttpx.get_content(image_seg.url)
                if image_data:
                    image_bytes_list.append(image_data)

        if not user_prompt and not template_prompt and not image_bytes_list:
            await draw_cmd.finish("请提供图片描述或附带图片，例如：draw 一只可爱的小猫")
            return

        should_optimize = base_config.get("enable_draw_prompt_optimization")
        if optimize_option := options.get("optimize"):
            mode = optimize_option.args.get("mode", "").lower()
            if mode == "on":
                should_optimize = True
            elif mode == "off":
                should_optimize = False

        final_prompt = ""
        if should_optimize:
            final_prompt = await _optimize_draw_prompt(
                user_message=user_intent_message,
                user_id=user_id_str,
                template_prompt=template_prompt,
            )
        else:
            if user_prompt and template_prompt:
                final_prompt = (
                    f"{user_prompt}。\n请遵循以下风格和要求：{template_prompt}"
                )
            elif template_prompt:
                final_prompt = template_prompt
            else:
                final_prompt = user_prompt

        engine_option = options.get("engine")
        engine_name = (
            engine_option.args.get("engine_name") if engine_option else None
        ) or base_config.get("default_draw_engine")

        if not engine_name:
            await draw_cmd.finish("❌ 错误：未配置默认绘图引擎，请联系管理员。")
            return

        if (
            engine_name.lower() == "api"
            and not is_superuser
            and not base_config.get("enable_api_draw_engine")
        ):
            await draw_cmd.finish(
                "❌ API绘图模式当前已禁用，请直接使用 draw [描述] 尝试默认绘图引擎。"
            )

        logger.info(f"用户 {user_id_str} 请求AI绘图, 使用引擎: {engine_name}")
        logger.info(f"最终提示词: {final_prompt[:100]}...")
        if image_bytes_list:
            logger.info(f"附带 {len(image_bytes_list)} 张图片。")

        if not is_superuser:
            if not draw_limiter.check(user_id_str):
                left_time = draw_limiter.left_time(user_id_str)
                await draw_cmd.finish(
                    f"AI绘图功能冷却中，请等待{TimeUtils.format_duration(left_time)}后再试~"
                )
            draw_limiter.start_cd(user_id_str)

        engine = get_engine(engine_name)

        if isinstance(engine, LlmApiEngine):
            message_to_send = "\n".join(
                [*initial_message_parts, "🎨 正在生成图片，请稍候..."]
            )
            await draw_cmd.send(message_to_send)
        elif engine_name.lower() == "doubao":
            from .core.queue_manager import draw_queue_manager

            queue_len = len(draw_queue_manager._queue)
            is_processing = draw_queue_manager._processing_request is not None
            cooldown_remaining = draw_queue_manager.get_browser_cooldown_remaining()

            if cooldown_remaining > 0 or queue_len > 0 or is_processing:
                tasks_ahead = queue_len + (1 if is_processing else 0)
                wait_time = (
                    tasks_ahead * draw_queue_manager._average_processing_time
                ) + cooldown_remaining
                queue_message = (
                    f"⏳ 任务已加入队列，您前面还有 {tasks_ahead} 个任务，"
                    f"预计等待 {wait_time:.0f} 秒..."
                )
                message_to_send = "\n".join([*initial_message_parts, queue_message])
                await draw_cmd.send(message_to_send)
            else:
                generating_message = "🎨 正在生成图片，请稍候..."
                message_to_send = "\n".join(
                    [*initial_message_parts, generating_message]
                )
                await draw_cmd.send(message_to_send)

        try:
            draw_result = await engine.draw(final_prompt, image_bytes_list)

            if isinstance(draw_result, dict):
                result_images_bytes = draw_result.get("images", [])
                text_response = draw_result.get("text", "")
            else:
                result_images_bytes = draw_result
                text_response = None
        except Exception as e:
            logger.error(f"绘图引擎 '{engine_name}' 执行失败: {e}", e=e)
            friendly_message = get_user_friendly_error_message(e)
            await draw_cmd.finish(f"❌ 图片生成失败: {friendly_message}")
            return

        result_images_bytes = draw_result.get("images", [])
        text_response = draw_result.get("text", "")

        if not result_images_bytes:
            if text_response:
                await draw_cmd.finish(f"🎨 AI回复：\n{text_response}")
            else:
                await draw_cmd.finish("❌ 生成失败：模型未返回任何内容。")
            return

        if len(result_images_bytes) == 1 and len(text_response) < 200:
            message_to_send = []
            if text_response:
                message_to_send.append(MessageSegment.text(f"📝 {text_response}\n"))
            message_to_send.append(MessageSegment.image(file=result_images_bytes[0]))
            await draw_cmd.finish(Message(message_to_send))
        else:
            success = await send_images_as_forward(
                bot, event, result_images_bytes, final_prompt, text_response
            )
            if not success:
                logger.warning("合并转发失败")
            await cmd.finish()

    except Exception as e:
        if e.__class__.__name__ != "FinishedException":
            logger.error(f"处理绘图请求失败: {e}")
            friendly_message = get_user_friendly_error_message(e)
            await draw_cmd.finish(f"❌ 绘图失败: {friendly_message}")


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
        resolved_name = await _resolve_template_name_by_input(template_input, cmd)
        prompt = template_manager.get_prompt(resolved_name)
        if prompt:
            await dtemplate_public_cmd.finish(
                f"🎨 模板 '{resolved_name}' 的内容如下：\n\n{prompt}"
            )
        else:
            await dtemplate_public_cmd.finish(f"❌ 未找到名为 '{resolved_name}' 的模板。")


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
                user_intent_message.extend(reply_unimsg[UniImage]) # type: ignore

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
            resolved_name = await _resolve_template_name_by_input(name_input, cmd)
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
        resolved_name = await _resolve_template_name_by_input(template_input, cmd)
        prompt = str(sub.args["prompt"])
        if await template_manager.update_template(resolved_name, prompt):
            await dtemplate_superuser_cmd.finish(f"✅ 成功更新模板 '{resolved_name}'。")
        else:
            await dtemplate_superuser_cmd.finish(
                f"❌ 更新失败：未找到模板 '{resolved_name}'。"
            )
