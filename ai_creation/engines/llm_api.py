from typing import Any

from zhenxun.services.ai.llm.api import create_image
from zhenxun.services.log import logger

from ..config import base_config
from . import DrawEngine


class LlmApiEngine(DrawEngine):
    """使用 zhenxun.services.llm API 的绘图引擎"""

    async def draw(
        self,
        prompt: str,
        image_bytes: list[bytes] | None = None,
        config: Any | None = None,
    ) -> dict[str, Any]:
        logger.debug("🎨 使用 LLM API 引擎进行绘图...")
        draw_model_name = base_config.get("api_draw_model")
        if not draw_model_name:
            raise ValueError("未配置API绘图模型 (api_draw_model)")

        response = await create_image(
            prompt=prompt,
            images=image_bytes,  # type: ignore
            model=draw_model_name,
            config=config,
        )
        images = response.images or []
        return {"images": images, "text": response.text}
