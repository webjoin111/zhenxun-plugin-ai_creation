import asyncio
from datetime import datetime
import hashlib
from typing import Any

import aiofiles

from zhenxun.configs.path_config import TEMP_PATH
from zhenxun.services.log import logger
from zhenxun.utils.http_utils import AsyncHttpx

IMAGE_DIR = TEMP_PATH / "ai_creation"
IMAGE_DIR.mkdir(parents=True, exist_ok=True)


class ImageDownloader:
    """AI生成图片下载器"""

    def __init__(self):
        self.downloaded_images: list[dict[str, Any]] = []

    def _generate_filename(
        self, image_info: dict[str, Any], provider: str = "ai_generated"
    ) -> str:
        """生成图片文件名"""
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

        url_hash = hashlib.md5(image_info["url"].encode()).hexdigest()[:8]

        format_ext = {
            "jpeg": ".jpg",
            "jpg": ".jpg",
            "png": ".png",
            "webp": ".webp",
            "avif": ".avif",
        }.get(image_info.get("format", "png").lower(), ".png")

        filename_parts = [
            provider,
            timestamp,
            f"img{image_info.get('index', 1)}",
            url_hash,
        ]

        filename = "_".join(filename_parts) + format_ext
        return filename

    async def download_image(
        self,
        image_info: dict[str, Any],
        prompt: str = "",
        provider: str = "ai_generated",
    ) -> dict[str, Any] | None:
        """下载单张图片"""
        url = image_info["url"]
        logger.info(f"开始下载AI生成图片: {url[:100]}...")

        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/137.0.0.0 Safari/537.36"
            ),
            "Referer": "https://www.doubao.com/",
            "Accept": "image/webp,image/apng,image/*,*/*;q=0.8",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            "Cache-Control": "no-cache",
            "Pragma": "no-cache",
        }

        try:
            image_data = await AsyncHttpx.get_content(url, headers=headers, timeout=30)

            if not image_data or len(image_data) < 1024:
                logger.warning(
                    f"下载的图片数据过小 ({len(image_data)} bytes)，可能是错误响应"
                )
                logger.error(f"图片数据过小，下载失败: {url}")
                return None

            filename = self._generate_filename(image_info, provider)
            filepath = IMAGE_DIR / filename

            async with aiofiles.open(filepath, "wb") as f:
                await f.write(image_data)

            result = {
                "url": url,
                "local_path": str(filepath.resolve()),
                "filename": filename,
                "size_bytes": len(image_data),
                "format": image_info.get("format", "png"),
                "dimensions": image_info.get("dimensions", {}),
                "index": image_info.get("index", 99),
                "prompt": prompt,
                "provider": provider,
                "download_time": datetime.now().isoformat(),
            }

            self.downloaded_images.append(result)
            logger.info(f"✅ AI图片下载成功: {filename} ({len(image_data)} bytes)")
            return result

        except Exception as e:
            logger.error(f"图片下载失败 (URL: {url[:100]}...)，错误: {e}")
            return None

    async def download_images(
        self,
        image_infos: list[dict[str, Any]],
        prompt: str = "",
        provider: str = "ai_generated",
        min_success_count: int = 1,
    ) -> list[dict[str, Any]]:
        """批量下载图片（带最小成功数量保证）"""
        results = []
        failed_urls = []

        logger.info(f"开始批量下载 {len(image_infos)} 张图片...")

        tasks = []
        for i, info in enumerate(image_infos):
            if not info.get("url"):
                logger.warning(f"跳过一个没有URL的图片信息: {info}")
                continue
            if "index" not in info:
                info["index"] = i

            task = self.download_image(info, prompt, provider)
            tasks.append(task)

        download_results = await asyncio.gather(*tasks)

        for result, info in zip(download_results, image_infos):
            if result:
                results.append(result)
                logger.debug(
                    f"图片 {info.get('index', '?')}/{len(image_infos)} 下载成功"
                )
            else:
                failed_urls.append(info.get("url", "未知URL"))
                logger.warning(
                    f"图片 {info.get('index', '?')}/{len(image_infos)} 下载失败: "
                    f"{info.get('url', '未知URL')[:100]}..."
                )

        success_count = len(results)
        total_count = len(image_infos)

        logger.info(f"批量下载完成，成功下载 {success_count}/{total_count} 张图片")

        if success_count < min_success_count:
            logger.warning(
                f"下载成功数量 ({success_count}) 少于最小要求 ({min_success_count})"
            )
            if failed_urls:
                logger.warning(f"失败的URL: {failed_urls}")

        return results

    def _get_format_from_url(self, url: str) -> str:  # noqa: E501
        """从URL获取图片格式"""
        if ".jpeg" in url or ".jpg" in url:
            return "jpeg"
        elif ".png" in url:
            return "png"
        elif ".webp" in url:
            return "webp"
        elif ".avif" in url:
            return "avif"
        else:
            return "png"

    def _get_size_from_image_info(
        self, image_info: dict, url_type: str
    ) -> dict[str, int]:
        """从图片信息获取尺寸"""
        size_map = {
            "thumb": image_info.get("image_thumb", {}),
            "original": image_info.get("image_ori", {}),
            "raw": image_info.get("image_raw", {}),
            "preview": image_info.get("preview_img", {}),
        }

        size_info = size_map.get(url_type, {})
        return {
            "width": size_info.get("width", 0),
            "height": size_info.get("height", 0),
        }

    def get_downloaded_images(self) -> list[dict[str, Any]]:
        """获取已下载的图片列表"""
        return self.downloaded_images.copy()

    def clear_downloaded_images(self):
        """清空已下载图片列表"""
        self.downloaded_images.clear()
