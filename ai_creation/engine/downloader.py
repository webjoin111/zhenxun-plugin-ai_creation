import asyncio
from datetime import datetime
import hashlib
from typing import Any

import aiofiles
import aiohttp

from zhenxun.configs.path_config import TEMP_PATH
from zhenxun.services.log import logger

IMAGE_DIR = TEMP_PATH / "ai_creation"
IMAGE_DIR.mkdir(parents=True, exist_ok=True)


class ImageDownloader:
    """AI生成图片下载器"""

    def __init__(self):
        self.session: aiohttp.ClientSession | None = None
        self.downloaded_images: list[dict[str, Any]] = []

    async def __aenter__(self):
        """异步上下文管理器入口"""
        self.session = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=30),
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/137.0.0.0 Safari/537.36"
                )
            },
        )
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """异步上下文管理器退出"""
        if self.session:
            await self.session.close()

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
        max_retries: int = 3,
        retry_delay: float = 2.0,
    ) -> dict[str, Any] | None:
        """下载单张图片（带重试机制）"""
        if not self.session:
            logger.error("HTTP会话未初始化")
            return None

        url = image_info["url"]

        for attempt in range(max_retries + 1):
            try:
                if attempt > 0:
                    logger.info(
                        f"重试下载图片 (第{attempt}/{max_retries}次): {url[:100]}..."
                    )
                    await asyncio.sleep(retry_delay * attempt)
                else:
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

                async with self.session.get(
                    url, headers=headers, timeout=aiohttp.ClientTimeout(total=30)
                ) as response:
                    if response.status == 200:
                        filename = self._generate_filename(image_info, provider)
                        filepath = IMAGE_DIR / filename

                        image_data = await response.read()

                        if len(image_data) < 1024:
                            logger.warning(
                                f"下载的图片数据过小 ({len(image_data)} bytes)，"
                                "可能是错误响应"
                            )
                            if attempt < max_retries:
                                continue
                            else:
                                logger.error(f"图片数据过小，下载失败: {url}")
                                return None

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
                            "download_attempts": attempt + 1,
                        }

                        self.downloaded_images.append(result)
                        logger.info(
                            f"✅ AI图片下载成功: {filename} ({len(image_data)} bytes)"
                            + (f" (重试{attempt}次后成功)" if attempt > 0 else "")
                        )
                        return result

                    elif response.status == 403:
                        logger.warning(
                            f"图片访问被拒绝 (403)，尝试重试: {url[:100]}..."
                        )
                        if attempt < max_retries:
                            continue
                        else:
                            logger.error(
                                f"图片下载失败，HTTP状态码: {response.status} "
                                f"(已重试{max_retries}次)"
                            )
                            return None
                    elif response.status in [429, 502, 503, 504]:
                        logger.warning(
                            f"服务器临时错误 ({response.status})，"
                            f"尝试重试: {url[:100]}..."
                        )
                        if attempt < max_retries:
                            continue
                        else:
                            logger.error(
                                f"图片下载失败，HTTP状态码: {response.status} "
                                f"(已重试{max_retries}次)"
                            )
                            return None
                    else:
                        logger.error(f"图片下载失败，HTTP状态码: {response.status}")
                        if attempt < max_retries:
                            continue
                        else:
                            return None

            except asyncio.TimeoutError:
                logger.warning(f"下载图片超时 (第{attempt + 1}次尝试): {url[:100]}...")
                if attempt < max_retries:
                    continue
                else:
                    logger.error(f"图片下载超时，已重试{max_retries}次: {url}")
                    return None
            except Exception as e:
                logger.warning(f"下载图片时发生错误 (第{attempt + 1}次尝试): {e}")
                if attempt < max_retries:
                    continue
                else:
                    logger.error(f"图片下载失败，已重试{max_retries}次: {e}")
                    return None

        return None

    async def download_images(
        self,
        image_infos: list[dict[str, Any]],
        prompt: str = "",
        provider: str = "ai_generated",
        max_retries: int = 3,
        retry_delay: float = 2.0,
        min_success_count: int = 1,
    ) -> list[dict[str, Any]]:
        """批量下载图片（带重试和最小成功数量保证）"""
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

            task = self.download_image(info, prompt, provider, max_retries, retry_delay)
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

    def parse_doubao_image_data(self, image_data_str: str) -> list[dict[str, Any]]:
        """解析豆包图片数据字符串，提取图片URL和其在creations数组中的原始索引。"""
        if not image_data_str or not image_data_str.strip():
            return []

        try:
            import json

            try:
                data = json.loads(image_data_str)
                if "event_data" in data and isinstance(data["event_data"], str):
                    data = json.loads(data["event_data"])
                if "message" in data and isinstance(data["message"], dict):
                    data = data["message"]
                if "content" in data and isinstance(data["content"], str):
                    # 增加对无效JSON的容错
                    try:
                        data = json.loads(data["content"])
                    except (json.JSONDecodeError, TypeError):
                        return []  # 如果content不是有效JSON，直接返回
            except (json.JSONDecodeError, TypeError):
                return []

            creations = data.get("creations", [])
            if not isinstance(creations, list):
                return []

            images = []
            for i, creation in enumerate(creations):
                if not isinstance(creation, dict) or creation.get("type") != 1:
                    continue

                image_info = creation.get("image", {})
                if not isinstance(image_info, dict):
                    continue

                image_key = image_info.get("key")
                if not image_key:
                    continue

                url = None
                # 优先选择更高质量的图片版本
                priority_keys = ["image_raw", "image_ori", "preview_img", "image_thumb"]
                for key in priority_keys:
                    if img_data := image_info.get(key, {}):
                        if found_url := img_data.get("url"):
                            url = found_url
                            logger.debug(
                                f"成功从字段 '{key}' 获取图片URL, "
                                "已选定最高优先级版本。"
                            )
                            break

                if url:
                    images.append({"url": url, "index": i, "key": image_key})

            return images

        except Exception as e:
            logger.error(f"处理豆包图片数据时发生未知错误: {e}", e=e)

        return []

    def _get_format_from_url(self, url: str) -> str:
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
