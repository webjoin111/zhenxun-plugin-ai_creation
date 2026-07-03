import json
import re
from io import BytesIO
from typing import Any

import aiofiles
from nonebot.adapters.onebot.v11 import (
    Bot,
    GroupMessageEvent,
    Message,
    MessageEvent,
    MessageSegment,
)
from nonebot.exception import FinishedException
from nonebot_plugin_alconna import AlconnaMatcher, At, CommandResult, UniMessage
from nonebot_plugin_alconna.uniseg import Image as UniImage
from nonebot_plugin_alconna.uniseg import At
from playwright.async_api import Error as PlaywrightError
from pydantic import BaseModel, Field

from zhenxun.services import avatar_service
from zhenxun.services.ai.llm.api import generate_structured
from zhenxun.services.ai.core.messages import LLMMessage, ImagePart, TextPart
from zhenxun.services.ai.core.options import GenerationConfig
from zhenxun.services.ai.core.exceptions import get_user_friendly_error_message
from zhenxun.services.ai.message_builder import MessageBuilder
from zhenxun.services.ai.run.context import NoneBotDeps
from zhenxun.services.log import logger
from zhenxun.utils.http_utils import AsyncHttpx
from zhenxun.utils.platform import PlatformUtils
from zhenxun.utils.time_utils import TimeUtils

from ..config import SYSTEM_PROMPT_FUSION, SYSTEM_PROMPT_OPTIMIZE, base_config
from ..engines.doubao.exceptions import ImageGenerationError
from ..engines import DrawEngine, get_engine
from ..engines.llm_api import LlmApiEngine
from ..templates import template_manager


@MessageBuilder.register_segment_handler(At)
async def _handle_ai_creation_at(seg: At) -> ImagePart | None:
    deps = NoneBotDeps.get_current()
    if not deps or not deps.bot:
        return None

    platform = PlatformUtils.get_platform(deps.bot)
    avatar_path = await avatar_service.get_avatar_path(
        platform, seg.target, force_refresh=True
    )

    if avatar_path and avatar_path.exists():
        async with aiofiles.open(avatar_path, "rb") as f:
            return ImagePart(raw=await f.read())
    return None


async def send_images_as_forward(
    bot: Bot,
    event: MessageEvent,
    structured_result: list[dict[str, Any]],
) -> bool:
    """发送图片作为合并转发消息"""
    try:
        forward_messages = []

        for block in structured_result:
            if block["type"] == "text" and block.get("content"):
                text_content = block["content"]
                forward_messages.append(
                    {
                        "type": "node",
                        "data": {
                            "name": "AI绘图助手",
                            "uin": str(bot.self_id),
                            "content": [MessageSegment.text(text_content)],
                        },
                    }
                )
            elif block["type"] == "image" and block.get("content"):
                images_bytes = block["content"]
                for i, image_bytes in enumerate(images_bytes):
                    content = [
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
            logger.debug(
                f"✅ 成功发送包含 {len(forward_messages)} 个节点的群聊合并转发消息"
            )
        else:
            await bot.call_api(
                "send_private_forward_msg",
                user_id=event.user_id,
                messages=forward_messages,
            )
            logger.debug(
                f"✅ 成功发送包含 {len(forward_messages)} 个节点的私聊合并转发消息"
            )

        return True

    except Exception:
        return False


async def send_images_as_single_message(
    bot: Bot,
    event: MessageEvent,
    images_bytes: list[bytes],
    prompt: str,
    text_response: str | None = None,
) -> bool:
    """将所有内容放在一个消息里发送"""
    try:
        images_count = len(images_bytes)
        message_segments = [MessageSegment.text(f"📝 {prompt}")]

        if text_response:
            message_segments.append(MessageSegment.text(f"\n📝 {text_response}"))

        for i, image_bytes in enumerate(images_bytes):
            message_segments.append(MessageSegment.image(file=image_bytes))

        await bot.send(event, Message(message_segments))
        logger.info(f"✅ 成功发送包含 {images_count} 张图片的单条消息")
        return True

    except Exception as e:
        logger.error(f"发送单条消息失败: {e}")
        return False


async def resolve_template_name_by_input(
    user_input: str, matcher: AlconnaMatcher
) -> str:
    """
    根据用户输入（名称或序号）解析出模板的真实名称。
    如果输入是无效序号，会自动发送错误消息并结束命令。
    """
    if not user_input:
        await matcher.finish("❌ 错误：模板名称或序号不能为空。")

    if user_input.isdigit():
        try:
            index = int(user_input) - 1
            all_templates = template_manager.list_templates()
            if 0 <= index < len(all_templates):
                return list(all_templates.keys())[index]
            await matcher.finish(
                f"❌ 错误：序号 '{user_input}' 超出范围，请输入 1 到 {len(all_templates)} 之间的数字。"
            )
        except (ValueError, IndexError):
            await matcher.finish(f"❌ 错误：无效的模板序号 '{user_input}'。")
    return user_input


class PromptOptimizeResponse(BaseModel):
    """用于接收结构化优化结果的 Pydantic 模型"""
    success: bool
    original_prompt: str
    analysis: str
    optimized_prompt: str


async def _optimize_draw_prompt(
    user_message: UniMessage, user_id: str, template_prompt: str | None = None
) -> str:
    """
    使用支持视觉功能的LLM优化用户的绘图描述。
    支持“文生图”的创意扩展和“图生图”的指令理解与融合。
    """
    logger.debug(f"🎨 启用绘图描述优化，为用户 '{user_id}' 的描述进行润色...")

    original_prompt = user_message.extract_plain_text().strip()

    try:
        logger.debug(
            f"绘图描述优化将使用模型: {base_config.get('auxiliary_llm_model')}"
        )

        content_parts = await MessageBuilder.unimsg_to_llm_parts(user_message)
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
            final_content_parts = await MessageBuilder.unimsg_to_llm_parts(fusion_message)
        else:
            system_prompt = SYSTEM_PROMPT_OPTIMIZE
            final_content_parts = content_parts

        messages = [
            LLMMessage.system(system_prompt),
            LLMMessage.user(final_content_parts),
        ]

        response = await generate_structured(
            messages,
            response_model=PromptOptimizeResponse,
            model=base_config.get("auxiliary_llm_model")
        )

        if response.success and response.optimized_prompt:
            logger.info(f"✅ 描述优化成功。优化后: '{response.optimized_prompt}'")
            return response.optimized_prompt
        logger.warning("描述优化LLM返回内容不符合预期，将使用原始描述。")
        return original_prompt

    except Exception as e:
        logger.error(f"❌ 绘图描述优化失败，将使用原始描述。错误: {e}")
        return original_prompt


class DrawingContext(BaseModel):
    """绘图任务上下文，封装一次绘图请求的所有状态和数据"""

    bot: Bot = Field(..., exclude=True)
    event: MessageEvent
    matcher: AlconnaMatcher = Field(..., exclude=True)
    command_result: CommandResult = Field(..., exclude=True)
    user_id: str
    initial_options: dict[str, Any] = Field(default_factory=dict)
    initial_unimsg: UniMessage = Field(default_factory=UniMessage)

    is_superuser: bool = False
    user_intent_message: UniMessage = Field(default_factory=UniMessage)
    image_bytes_list: list[bytes] = Field(default_factory=list)
    initial_message_parts: list[str] = Field(default_factory=list)

    user_prompt: str = ""
    template_prompt: str | None = None
    final_prompt: str = ""
    image_size: str | None = None
    engine_name: str = ""
    engine: DrawEngine | None = Field(None, exclude=True)
    draw_result: dict[str, Any] | list[dict[str, Any]] | None = None

    class Config:
        arbitrary_types_allowed = True


class DrawingService:
    """绘图服务，负责处理完整的绘图流程"""

    def __init__(self, ctx: DrawingContext, limiter):
        self.ctx = ctx
        self.limiter = limiter

    async def run(self):
        """执行完整的绘图流程"""
        try:
            await self._prepare_input()
            await self._resolve_prompt_and_engine()
            await self._check_permissions_and_cd()
            await self._send_processing_message()
            await self._execute_drawing()
            await self._send_response()
        except FinishedException:
            raise
        except Exception as e:
            logger.error(f"处理绘图请求失败: {e}")
            friendly_message = get_user_friendly_error_message(e)
            await self.ctx.matcher.finish(f"❌ 绘图失败: {friendly_message}")

    async def _prepare_input(self):
        """准备并解析用户输入（文本、图片、@、引用消息）"""
        logger.debug("DrawingService: 准备和解析用户输入...")
        result = self.ctx.command_result
        raw_result = result.result

        main_args = raw_result.main_args if raw_result and raw_result.main_args else {}
        prompt_args = main_args.get("prompt", [])
        if not isinstance(prompt_args, list):
            prompt_args = [prompt_args]

        extra_args = main_args.get("$extra", [])
        initial_segments = prompt_args + list(extra_args)

        user_intent_message = UniMessage(initial_segments)

        if self.ctx.event.reply and self.ctx.event.reply.message:  # type: ignore
            reply_unimsg = MessageBuilder.message_to_unimessage(self.ctx.event.reply.message)
            if reply_unimsg[UniImage]:
                for seg in reply_unimsg:
                    if isinstance(seg, UniImage):
                        user_intent_message.append(seg)
                logger.debug("已合并引用消息中的图片内容。")
            else:
                user_intent_message = user_intent_message + reply_unimsg
                logger.debug("已合并引用消息中的文本内容。")

        self.ctx.initial_unimsg = UniMessage(initial_segments)
        self.ctx.user_intent_message = user_intent_message

        llm_parts = await MessageBuilder.unimsg_to_llm_parts(user_intent_message)

        image_bytes_list = []
        text_parts = []

        for part in llm_parts:
            if part.type == "image":
                if part.raw:
                    image_bytes_list.append(part.raw)
                elif part.path:
                    async with aiofiles.open(part.path, "rb") as f:
                        image_bytes_list.append(await f.read())
            elif part.type == "text":
                if part.text.strip():
                    text_parts.append(part.text)

        self.ctx.user_prompt = " ".join(text_parts).strip()
        self.ctx.image_bytes_list = image_bytes_list

    async def _resolve_prompt_and_engine(self):
        """解析模板配置，生成最终提示词并实例化绘图引擎"""
        options = self.ctx.initial_options
        matcher = self.ctx.matcher

        user_prompt = self.ctx.user_prompt
        template_prompt: str | None = None
        initial_message_parts: list[str] = []

        if template_option := options.get("template"):
            template_input = str(template_option.args.get("template_name", ""))
            resolved_template_name = await resolve_template_name_by_input(
                template_input, matcher
            )
            template_prompt = template_manager.get_prompt(resolved_template_name)
            if not template_prompt:
                await matcher.finish(
                    f"❌ 错误：未找到名为 '{resolved_template_name}' 的模板。"
                )
            else:
                initial_message_parts.append(
                    f"🎨 正在使用模板 '{resolved_template_name}' 进行绘图..."
                )

        if not user_prompt and not template_prompt and not self.ctx.image_bytes_list:
            await matcher.finish("请提供图片描述或附带图片，例如：draw 一只可爱的小猫")

        should_optimize = base_config.get("enable_draw_prompt_optimization")
        if optimize_option := options.get("optimize"):
            mode = optimize_option.args.get("mode", "").lower()
            if mode == "on":
                should_optimize = True
            elif mode == "off":
                should_optimize = False

        if should_optimize:
            final_prompt = await _optimize_draw_prompt(
                user_message=self.ctx.user_intent_message,
                user_id=self.ctx.user_id,
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
            await matcher.finish("❌ 错误：未配置默认绘图引擎，请联系管理员。")

        if size_option := options.get("size"):
            self.ctx.image_size = size_option.args.get("img_size")

        if (
            engine_name.lower() == "api"
            and not self.ctx.is_superuser
            and not base_config.get("enable_api_draw_engine")
        ):
            await matcher.finish(
                "❌ API绘图模式当前已禁用，请直接使用 draw [描述] 尝试默认绘图引擎。"
            )

        engine = get_engine(engine_name)

        self.ctx.initial_message_parts = initial_message_parts
        self.ctx.template_prompt = template_prompt
        self.ctx.final_prompt = final_prompt
        self.ctx.engine_name = engine_name
        self.ctx.engine = engine

        logger.info(f"用户 {self.ctx.user_id} 请求AI绘图, 使用引擎: {engine_name}")
        logger.info(f"最终提示词: {final_prompt[:100]}...")
        if self.ctx.image_bytes_list:
            logger.info(f"附带 {len(self.ctx.image_bytes_list)} 张图片。")

    async def _check_permissions_and_cd(self):
        """校验用户权限并处理功能冷却时间"""
        if self.ctx.is_superuser:
            return

        if not self.limiter.check(self.ctx.user_id):
            left_time = self.limiter.left_time(self.ctx.user_id)
            await self.ctx.matcher.finish(
                f"AI绘图功能冷却中，请等待{TimeUtils.format_duration(left_time)}后再试~"
            )
        self.limiter.start_cd(self.ctx.user_id)

    async def _send_processing_message(self):
        """依据引擎类型发送“处理中”提示"""
        engine = self.ctx.engine
        if engine is None:
            await self.ctx.matcher.finish("❌ 绘图引擎初始化失败。")

        if isinstance(engine, LlmApiEngine):
            message_to_send = "\n".join(
                [*self.ctx.initial_message_parts, "🎨 正在生成图片，请稍候..."]
            )
            await self.ctx.matcher.send(message_to_send)
            return

        if self.ctx.engine_name.lower() == "doubao":
            from ..engines.doubao.queue_manager import draw_queue_manager

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
                message_to_send = "\n".join(
                    [*self.ctx.initial_message_parts, queue_message]
                )
                await self.ctx.matcher.send(message_to_send)
            else:
                generating_message = "🎨 正在生成图片，请稍候..."
                message_to_send = "\n".join(
                    [*self.ctx.initial_message_parts, generating_message]
                )
                await self.ctx.matcher.send(message_to_send)

    async def _execute_drawing(self):
        """调用具体绘图引擎执行生成请求"""
        if self.ctx.engine is None:
            await self.ctx.matcher.finish("❌ 绘图引擎实例未创建。")

        try:
            gen_config = None
            if self.ctx.image_size:
                gen_config = GenerationConfig.builder().with_image_generation_params(resolution=self.ctx.image_size).build()

            draw_result = await self.ctx.engine.draw(
                self.ctx.final_prompt, self.ctx.image_bytes_list, config=gen_config
            )
            self.ctx.draw_result = draw_result

        except (ImageGenerationError, PlaywrightError) as e:
            logger.debug(f"捕获到预期的绘图引擎错误: {e}")
            friendly_message = get_user_friendly_error_message(e)
            if "no data found for resource" in str(e).lower():
                friendly_message = "图片生成失败，可能因为内容审核未通过或网络不稳定。"
            await self.ctx.matcher.finish(f"❌ 图片生成失败: {friendly_message}")

        except Exception as e:
            logger.error(
                f"绘图引擎 '{self.ctx.engine_name}' 执行失败: {e}",
                e=e,
            )
            friendly_message = get_user_friendly_error_message(e)
            await self.ctx.matcher.finish(f"❌ 图片生成失败: {friendly_message}")

    async def _send_response(self):
        """整理绘图结果并向用户发送回复"""
        result = self.ctx.draw_result or {}

        images_bytes: list[bytes] = []
        text_parts: list[str] = []
        structured_blocks: list[dict[str, Any]] = []

        if isinstance(result, list):
            structured_blocks = result
            for block in result:
                if block.get("type") == "image" and block.get("content"):
                    images_bytes.extend(block["content"])
                elif block.get("type") == "text" and block.get("content"):
                    text_parts.append(str(block["content"]))
        elif isinstance(result, dict):
            api_images = result.get("images", [])
            api_text = result.get("text", "").strip()
            if api_text:
                structured_blocks.append({"type": "text", "content": api_text})
                text_parts.append(api_text)
            if api_images:
                structured_blocks.append({"type": "image", "content": api_images})
                images_bytes.extend(api_images)

        text_content = "\n".join(text_parts).strip()

        if not images_bytes and not text_content:
            await self.ctx.matcher.finish("❌ 生成失败：模型未返回任何内容。")

        if not images_bytes and text_content:
            reply_message = Message(
                [
                    MessageSegment.reply(id_=self.ctx.event.message_id),
                    MessageSegment.text(f"🎨 AI回复：\n{text_content}"),
                ]
            )
            await self.ctx.matcher.finish(reply_message)
            return

        if len(images_bytes) == 1:
            message_to_send = [MessageSegment.reply(id_=self.ctx.event.message_id)]
            if text_content:
                message_to_send.append(MessageSegment.text(f"📝 {text_content}\n"))
            message_to_send.append(MessageSegment.image(file=images_bytes[0]))
            await self.ctx.matcher.finish(Message(message_to_send))
            return

        if len(images_bytes) > 1:
            success = await send_images_as_forward(
                self.ctx.bot, self.ctx.event, structured_blocks
            )
            if not success:
                logger.warning("合并转发失败")
            await self.ctx.matcher.finish()


__all__ = [
    "DrawingContext",
    "DrawingService",
    "resolve_template_name_by_input",
    "send_images_as_forward",
    "send_images_as_single_message",
]
