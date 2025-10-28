from io import BytesIO
from pathlib import Path
import tempfile
from typing import Any

import aiofiles
from PIL import Image

from zhenxun.services.log import logger

from ...utils.downloader import IMAGE_DIR
from .. import DrawEngine
from .queue_manager import RequestStatus, draw_queue_manager


class DoubaoEngine(DrawEngine):
    """使用 Playwright 模拟豆包网站的绘图引擎"""

    async def draw(
        self, prompt: str, image_bytes: list[bytes] | None = None
    ) -> list[dict[str, Any]]:
        prompt_str = ""
        if isinstance(prompt, list):
            logger.debug(
                f"Doubao引擎检测到列表型Prompt，将使用换行符连接 {len(prompt)} 个分镜。"
            )
            prompt_str = "\n\n".join(map(str, prompt))
        else:
            prompt_str = str(prompt)

        logger.debug("🎨 使用豆包 (Playwright) 引擎进行绘图...")
        image_file_paths: list[Path] = []
        temp_files_to_clean: list[Path] = []
        if image_bytes:
            upload_dir = IMAGE_DIR
            upload_dir.mkdir(parents=True, exist_ok=True)

            for i, img_bytes in enumerate(image_bytes):
                try:
                    with Image.open(BytesIO(img_bytes)) as img:
                        if getattr(img, "is_animated", False):
                            logger.debug(
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
                    logger.debug(
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
            "api_user", prompt_str, image_paths=image_paths_str
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

        if (
            completed_request.status != RequestStatus.COMPLETED
            or not completed_request.result
        ):
            error_msg = completed_request.error or "未知错误"
            raise RuntimeError(f"图片生成失败: {error_msg}")

        structured_result = completed_request.result.get("structured_result", [])
        if not structured_result:
            raise RuntimeError("图片生成失败：未获取到任何内容")

        for block in structured_result:
            if block["type"] == "image":
                image_bytes_list = []
                for img_info in block.get("content", []):
                    async with aiofiles.open(img_info["local_path"], "rb") as f:
                        image_bytes_list.append(await f.read())
                block["content"] = image_bytes_list

        return structured_result
