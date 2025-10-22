from abc import ABC, abstractmethod
from io import BytesIO
from pathlib import Path
import tempfile
from typing import Any

import aiofiles
from PIL import Image

from zhenxun.services.llm import create_image
from zhenxun.services.log import logger

from ..config import base_config
from .queue_manager import draw_queue_manager
from ..engine.downloader import IMAGE_DIR


class DrawEngine(ABC):
    """绘图引擎的抽象基类"""

    @abstractmethod
    async def draw(
        self, prompt: str, image_bytes: list[bytes] | None = None
    ) -> dict[str, Any]:
        """
        执行绘图操作并返回结果。

        Args:
            prompt (str): 绘图的提示词。
            image_bytes (list[bytes] | None): 用于图生图的原始图片字节数据列表。

        Returns:
            dict[str, Any]: A dictionary containing 'images' (list[bytes]) and optional 'text' (str).
        """
        pass


class LlmApiEngine(DrawEngine):
    """使用 zhenxun.services.llm API 的绘图引擎"""

    async def draw(
        self, prompt: str, image_bytes: list[bytes] | None = None
    ) -> dict[str, Any]:
        logger.info("🎨 使用 LLM API 引擎进行绘图...")
        draw_model_name = base_config.get("api_draw_model")
        if not draw_model_name:
            raise ValueError("未配置API绘图模型 (api_draw_model)")

        response = await create_image(
            prompt=prompt,
            images=image_bytes,  # type: ignore
            model=draw_model_name,
        )
        images = response.images or []
        return {"images": images, "text": response.text}


class DoubaoEngine(DrawEngine):
    """使用 Playwright 模拟豆包网站的绘图引擎"""

    async def draw(
        self, prompt: str, image_bytes: list[bytes] | None = None
    ) -> dict[str, Any]:
        logger.info("🎨 使用豆包 (Playwright) 引擎进行绘图...")
        image_file_paths: list[Path] = []
        temp_files_to_clean: list[Path] = []
        if image_bytes:
            upload_dir = IMAGE_DIR
            upload_dir.mkdir(parents=True, exist_ok=True)

            for i, img_bytes in enumerate(image_bytes):
                try:
                    with Image.open(BytesIO(img_bytes)) as img:
                        if getattr(img, "is_animated", False):
                            logger.info(
                                f"检测到第 {i + 1} 张图片为GIF，将提取第一帧并转换为PNG进行图生图。"
                            )
                            img.seek(0)

                        converted_img = img.convert("RGBA")
                        buffer = BytesIO()
                        converted_img.save(buffer, format="PNG")
                        png_image_bytes = buffer.getvalue()

                    with tempfile.NamedTemporaryFile(
                        delete=False,
                        suffix=".png",
                        dir=upload_dir,
                    ) as temp_file:
                        temp_file.write(png_image_bytes)
                        temp_file_path = Path(temp_file.name)
                        image_file_paths.append(temp_file_path)
                        temp_files_to_clean.append(temp_file_path)
                    logger.info(
                        f"图生图的第 {i + 1} 张输入图片已处理并保存为PNG: {temp_file_path}"
                    )
                except Exception as e:
                    logger.error(
                        f"为豆包引擎创建或转换第 {i + 1} 张临时图片文件失败: {e}"
                    )

        image_paths_str = (
            [str(p) for p in image_file_paths] if image_file_paths else None
        )

        request = await draw_queue_manager.add_request(
            "api_user", prompt, image_paths=image_paths_str
        )

        draw_queue_manager.start_queue_processor()

        completed_request = await draw_queue_manager.wait_for_request_completion(
            request.request_id, timeout=600.0
        )

        for temp_path in temp_files_to_clean:
            if temp_path.exists():
                temp_path.unlink()

        if not completed_request:
            raise RuntimeError("请求处理超时")

        result_data = completed_request.result or {}
        if not result_data.get("success"):
            error_msg = completed_request.error or (
                result_data.get("error", "未知错误")
            )
            raise RuntimeError(f"图片生成失败: {error_msg}")

        images_info = result_data.get("images", [])
        text_response = result_data.get("text", "")
        if not images_info and not text_response:
            raise RuntimeError("图片生成失败：未获取到图片或文本数据")

        results = []
        for img_info in images_info:
            async with aiofiles.open(img_info["local_path"], "rb") as f:
                results.append(await f.read())
        return {"images": results, "text": text_response}


def get_engine(engine_name: str) -> DrawEngine:
    """
    绘图引擎工厂函数。

    Args:
        engine_name (str): 引擎的名称 ('doubao' 或 'api').

    Returns:
        DrawEngine: 对应的引擎实例。
    """
    if engine_name.lower() == "doubao":
        return DoubaoEngine()
    elif engine_name.lower() == "api":
        return LlmApiEngine()
    else:
        raise ValueError(f"未知的绘图引擎: '{engine_name}'")
