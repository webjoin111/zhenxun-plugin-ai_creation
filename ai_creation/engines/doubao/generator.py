import asyncio
import base64
from datetime import datetime
import hashlib
import json
from typing import Any

from playwright.async_api import Browser, BrowserContext, Page, async_playwright
from playwright_stealth import Stealth

from zhenxun.services.log import logger

from ...config import DOUBAO_SELECTORS, base_config
from ...utils.downloader import IMAGE_DIR, ImageDownloader


class ImageGenerationError(Exception):
    """图片生成错误"""

    pass


class DoubaoImageGenerator:
    """豆包AI图片生成器（基于Chromium浏览器自动化）"""

    def __init__(self):
        self.browser: Browser | None = None
        self.context: BrowserContext | None = None
        self.page: Page | None = None
        self.playwright = None
        self.downloader = ImageDownloader()

        self.base_url = "https://www.doubao.com"
        self.create_image_url = "https://www.doubao.com/chat/create-image"

        enable_cookies = base_config.get("ENABLE_DOUBAO_COOKIES", True)
        cookies_count = len(base_config.get("DOUBAO_COOKIES", []))
        logger.info(
            f"豆包图片生成器初始化。Cookie功能: {'启用' if enable_cookies else '禁用'}, "
            f"已配置Cookies数量: {cookies_count}"
        )

    async def initialize(self) -> bool:
        """初始化浏览器，并为本次会话使用指定的cookie"""
        try:
            self.playwright = await async_playwright().start()

            self.browser = await self.playwright.chromium.launch(
                headless=base_config.get("HEADLESS_BROWSER"),
                args=[
                    "--no-sandbox",
                    "--disable-blink-features=AutomationControlled",
                    "--disable-web-security",
                    "--disable-features=VizDisplayCompositor",
                    "--disable-dev-shm-usage",
                    "--no-first-run",
                    "--disable-default-apps",
                    "--disable-extensions",
                    "--disable-background-timer-throttling",
                    "--disable-backgrounding-occluded-windows",
                    "--disable-renderer-backgrounding",
                ],
            )

            if self.browser is None:
                logger.error("浏览器未初始化")
                return False
            self.context = await self.browser.new_context(
                viewport={"width": 1920, "height": 1080},
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/137.0.0.0 Safari/537.36"
                ),
            )

            if self.context is None:
                logger.error("浏览器上下文未初始化")
                return False
            await Stealth().apply_stealth_async(self.context)
            self.page = await self.context.new_page()

            logger.info("豆包图片生成器浏览器初始化成功")
            return True

        except Exception as e:
            logger.error(f"初始化浏览器失败: {e}")
            await self.cleanup()
            return False

    @staticmethod
    def _repair_mojibake_text(text: str) -> str:
        """将可能出现乱码的文本尝试还原为 UTF-8 正常文本。"""
        if not text:
            return ""

        byte_buffer = bytearray()
        parts: list[str] = []

        def flush_buffer() -> None:
            if byte_buffer:
                try:
                    parts.append(byte_buffer.decode("utf-8"))
                except UnicodeDecodeError:
                    parts.append(byte_buffer.decode("utf-8", errors="ignore"))
                byte_buffer.clear()

        for ch in text:
            encoded: bytes | None = None
            for enc in ("latin-1", "cp1252"):
                try:
                    encoded = ch.encode(enc)
                    break
                except UnicodeEncodeError:
                    continue

            if encoded is not None:
                byte_buffer.extend(encoded)
            else:
                flush_buffer()
                parts.append(ch)

        flush_buffer()
        return "".join(parts)

    @property
    def is_initialized(self) -> bool:
        """检查浏览器实例是否已成功初始化且页面可用"""
        return self.page is not None and not self.page.is_closed()

    async def cleanup(self):
        """清理资源"""
        try:
            if self.page:
                await self.page.close()
                self.page = None
            if self.context:
                await self.context.close()
                self.context = None
            if self.browser:
                await self.browser.close()
                self.browser = None
            if self.playwright:
                await self.playwright.stop()
                self.playwright = None

            logger.debug("豆包图片生成器资源清理完成")
        except Exception as e:
            error_msg = str(e).lower()
            if any(
                keyword in error_msg
                for keyword in [
                    "target page, context or browser has been closed",
                    "browser has been closed",
                    "context has been closed",
                    "page has been closed",
                ]
            ):
                logger.debug(f"浏览器资源已被关闭（正常情况）: {e}")
            else:
                logger.error(f"清理资源时发生错误: {e}")

    async def update_session_cookie(self, cookie_str: str | None):
        """动态更新当前浏览器会话的Cookie，实现轮询"""
        if not self.context:
            raise ConnectionError("浏览器上下文未初始化，无法更新Cookie。")

        try:
            await self.context.clear_cookies()
            if cookie_str:
                cookies = []
                for cookie_pair in cookie_str.split(";"):
                    if "=" in cookie_pair:
                        name, value = cookie_pair.strip().split("=", 1)
                        cookies.append(
                            {
                                "name": name.strip(),
                                "value": value.strip(),
                                "domain": ".doubao.com",
                                "path": "/",
                            }
                        )
                await self.context.add_cookies(cookies)
                logger.info(f"浏览器会话已更新 {len(cookies)} 个Cookie。")

        except Exception as e:
            logger.error(f"设置cookies失败: {e}")

    async def navigate_to_create_image(self) -> bool:
        """导航到豆包图片创建页面"""
        if not self.page:
            logger.error("页面未初始化")
            return False

        try:
            logger.info("正在导航到豆包图片创建页面...")
            await self.page.goto(
                self.create_image_url, wait_until="domcontentloaded", timeout=60000
            )

            await asyncio.sleep(5)

            title = await self.page.title()
            logger.info(f"页面标题: {title}")

            return True

        except Exception as e:
            logger.error(f"导航到豆包图片创建页面失败: {e}")
            return False

    async def _upload_images(self, image_paths: list[str]) -> bool:
        """上传图片到豆包输入框"""
        if not self.page:
            return False

        try:
            logger.info(f"开始上传 {len(image_paths)} 张图片...")

            success = await self._upload_file_input(image_paths)
            if success:
                logger.info("文件输入框上传成功。")
                return True

            logger.warning("图片上传失败")
            return False

        except Exception as e:
            logger.error(f"上传图片失败: {e}")
            return False

    async def _upload_file_input(self, image_paths: list[str]) -> bool:
        """使用文件输入框上传图片"""
        try:
            for selector in DOUBAO_SELECTORS["file_upload"]:
                try:
                    if self.page is None:
                        logger.error("页面未初始化")
                        continue
                    await self.page.evaluate(f"""
                        () => {{
                            const inputs = document.querySelectorAll('{selector}');
                            inputs.forEach(input => {{
                                input.style.display = 'block';
                                input.style.visibility = 'visible';
                                input.style.opacity = '1';
                                input.style.position = 'static';
                            }});
                        }}
                    """)

                    if self.page is None:
                        logger.error("页面未初始化")
                        continue
                    upload_element = await self.page.wait_for_selector(
                        selector, timeout=1000
                    )
                    if upload_element:
                        await upload_element.set_input_files(image_paths)
                        logger.info(
                            f"通过文件输入框成功上传 {len(image_paths)} 张图片: {selector}"
                        )
                        await asyncio.sleep(2)
                        return True

                except Exception:
                    continue

            return False

        except Exception as e:
            logger.error(f"文件输入框上传失败: {e}")
            return False

    async def _input_prompt(self, prompt: str) -> bool:
        """输入提示词"""
        if not self.page:
            return False

        try:
            input_element = None
            for selector in DOUBAO_SELECTORS["prompt_input"]:
                try:
                    input_element = await self.page.wait_for_selector(
                        selector, timeout=3000
                    )
                    if input_element:
                        logger.info(f"找到豆包输入框: {selector}")
                        break
                except Exception:
                    logger.warning(f"等待元素 {selector} 超时或失败")
                    continue

            if not input_element:
                logger.error("未找到输入框")
                return False

            await input_element.click()
            await input_element.fill(prompt)
            await asyncio.sleep(1)

            logger.info(f"已输入豆包提示词: {prompt[:200]}...")
            return True

        except Exception as e:
            logger.error(f"输入提示词失败: {e}")
            return False

    async def _submit_generation(self) -> bool:
        """提交生成请求"""
        if not self.page:
            return False

        try:
            logger.info("使用回车键提交豆包生成请求")
            await self.page.keyboard.press("Enter")

            await asyncio.sleep(2)
            logger.info("等待豆包图片生成...")
            return True

        except Exception as e:
            logger.error(f"提交生成请求失败: {e}")
            return False

    async def generate_doubao_image(
        self, prompt: str, image_paths: list[str] | None = None
    ) -> tuple[str, list[dict[str, Any]]]:
        """
        使用豆包生成图片，并返回文本和带索引的图片信息列表。
        这是一个无状态方法，所有状态都局限于本次调用。
        """
        generation_complete_event = asyncio.Event()
        generated_text_parts: list[str] = []
        generated_images: dict[str, Any] = {}
        generated_image_order: list[str] = []

        async def _local_sse_handler(response):
            try:
                if (
                    "completion" not in response.url
                    or response.status != 200
                    or "text/event-stream"
                    not in response.headers.get("content-type", "")
                ):
                    return

                body_bytes = await response.body()
                lines = body_bytes.decode("utf-8", errors="ignore").strip().split("\n")
                for line in lines:
                    if not line.startswith("data:"):
                        continue

                    json_str = line[6:].strip()
                    if not json_str:
                        continue

                    try:
                        data = json.loads(json_str)
                        event_type = data.get("event_type")

                        if event_type == 2003:
                            generation_complete_event.set()
                            continue
                        if event_type != 2001:
                            continue

                        event_data = json.loads(data.get("event_data", "{}"))
                        message_data = event_data.get("message", {})
                        content_json = json.loads(message_data.get("content", "{}"))

                        if raw_text := content_json.get("text"):
                            repaired_text = self._repair_mojibake_text(raw_text)
                            generated_text_parts.append(
                                repaired_text.replace("\\n", "\n")
                            )

                        creations = content_json.get("creations")
                        if not creations or not isinstance(creations, list):
                            continue

                        for creation in creations:
                            image_info = creation.get("image")
                            if not isinstance(image_info, dict):
                                continue

                            key = image_info.get("key")
                            if not key:
                                continue

                            if key not in generated_image_order:
                                generated_image_order.append(key)

                            priority_keys = [
                                "image_ori_raw",
                                "image_ori",
                                "image_preview",
                                "image_thumb",
                            ]
                            selected_url = next(
                                (
                                    image_info.get(url_key, {}).get("url")
                                    for url_key in priority_keys
                                    if isinstance(image_info.get(url_key), dict)
                                    and image_info.get(url_key, {}).get("url")
                                ),
                                None,
                            )
                            if selected_url:
                                generated_images[key] = {
                                    "url": selected_url,
                                    "key": key,
                                }
                    except (json.JSONDecodeError, KeyError):
                        logger.debug("跳过无法解析的SSE片段。")
                    except Exception as inner_exc:
                        logger.debug(f"SSE事件处理出现未知错误: {inner_exc}")
            except Exception as exc:
                logger.debug(f"SSE拦截器处理响应失败: {exc}")

        if self.page:
            self.page.on("response", _local_sse_handler)

        try:
            if not await self.navigate_to_create_image():
                raise ImageGenerationError("导航到豆包图片创建页面失败")

            if image_paths:
                logger.info(f"检测到 {len(image_paths)} 张图片输入，开始上传...")
                if not await self._upload_images(image_paths):
                    logger.warning("图片上传失败，继续使用纯文本模式")
                else:
                    logger.info("图片上传成功，等待图片处理...")
                    await asyncio.sleep(5)

            if not await self._input_prompt(prompt):
                raise ImageGenerationError("输入提示词失败")

            if not await self._submit_generation():
                raise ImageGenerationError("提交生成请求失败")

            signal_timeout = int(base_config.get("doubao_wait_signal_timeout", 120))
            try:
                await asyncio.wait_for(
                    generation_complete_event.wait(), timeout=signal_timeout
                )
                logger.info("✅ 收到豆包SSE流结束信号，额外等待以确保数据完整。")
                await asyncio.sleep(3)
            except asyncio.TimeoutError:
                logger.warning(
                    f"等待生成完成信号超时 ({signal_timeout}s)。将尝试使用已收到的数据。"
                )

            final_text = "".join(generated_text_parts)
            ordered_images: list[dict[str, Any]] = []
            if generated_image_order:
                for index, key in enumerate(generated_image_order):
                    image_data = generated_images.get(key)
                    if not image_data:
                        continue
                    image_data["index"] = index
                    ordered_images.append(image_data)
                logger.info(f"最终成功解析并排序了 {len(ordered_images)} 张图片。")
            else:
                logger.warning("未收集到任何有效的图片信息。")

            return final_text, ordered_images

        except Exception as e:
            logger.error(f"豆包图片生成失败: {e}")
            raise ImageGenerationError(f"豆包图片生成失败: {e}")
        finally:
            if self.page:
                self.page.remove_listener("response", _local_sse_handler)

    async def _download_images_with_browser(
        self, image_infos: list[dict[str, Any]], prompt: str
    ) -> list[dict[str, Any]]:
        """使用浏览器下载图片（避免403错误）"""
        if not self.page:
            logger.error("浏览器页面未初始化")
            return []

        if not image_infos:
            logger.warning("没有有效的图片信息需要下载")
            return []

        urls_with_index = [
            {"url": info["url"], "index": info["index"]} for info in image_infos
        ]

        logger.info(f"开始批量下载 {len(urls_with_index)} 张图片...")

        try:
            download_results = await self.page.evaluate(
                """
                async (urlsWithIndex) => {
                    // Helper function to convert ArrayBuffer to Base64
                    const toBase64 = (arrayBuffer) => {
                        let binary = '';
                        const bytes = new Uint8Array(arrayBuffer);
                        const len = bytes.byteLength;
                        for (let i = 0; i < len; i++) {
                            binary += String.fromCharCode(bytes[i]);
                        }
                        return window.btoa(binary);
                    };

                    const downloadPromises = urlsWithIndex.map(async (info) => {
                        try {
                            const response = await fetch(info.url);
                            if (!response.ok) {
                                throw new Error(
                                    `HTTP ${response.status}: ${response.statusText}`
                                );
                            }
                            const arrayBuffer = await response.arrayBuffer();
                            const base64Data = toBase64(arrayBuffer);
                            return {
                                url: info.url,
                                data: base64Data,
                                index: info.index,
                                success: true
                            };
                        } catch (error) {
                            return {
                                url: info.url,
                                data: null,
                                index: info.index,
                                success: false,
                                error: error.message || String(error)
                            };
                        }
                    });
                    return await Promise.all(downloadPromises);
                }
            """,
                urls_with_index,
            )

            downloaded_images = []
            import aiofiles

            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            prompt_hash = hashlib.md5(prompt.encode()).hexdigest()[:8]

            successful_downloads = 0
            for download_result in download_results:
                if download_result["success"] and download_result["data"]:
                    try:
                        filename = f"doubao_{timestamp}_{prompt_hash}_{download_result['index']}.png"
                        filepath = IMAGE_DIR / filename
                        filepath.parent.mkdir(parents=True, exist_ok=True)

                        image_data = base64.b64decode(download_result["data"])

                        async with aiofiles.open(filepath, "wb") as f:
                            await f.write(image_data)

                        image_result = {
                            "url": download_result["url"],
                            "local_path": str(filepath.resolve()),
                            "filename": filename,
                            "size_bytes": len(image_data),
                            "format": "png",
                            "dimensions": {},
                            "index": download_result["index"],
                            "prompt": prompt,
                            "provider": "doubao",
                            "download_time": datetime.now().isoformat(),
                            "download_method": "browser_batch",
                        }

                        downloaded_images.append(image_result)
                        successful_downloads += 1

                    except Exception as e:
                        logger.error(f"保存图片失败: {e}")
                        continue
                else:
                    logger.warning(
                        f"图片下载失败: {download_result.get('error', '未知错误')}"
                    )

            logger.info(
                f"✅ 批量下载完成，成功保存 "
                f"{successful_downloads}/{len(image_infos)} 张图片"
            )
            return downloaded_images

        except Exception as e:
            logger.error(f"批量下载图片失败: {e}")
            return []

    async def generate_image(
        self,
        prompt: str,
        count: int = 1,
        image_paths: list[str] | None = None,
    ) -> dict[str, Any]:
        """生成AI图片"""
        try:
            if image_paths:
                logger.info(
                    f"🎨 开始生成AI图片 (基于 {len(image_paths)} 张图片): {prompt}"
                )
                logger.info(f"📷 输入图片路径: {image_paths}")
            else:
                logger.info(f"🎨 开始生成AI图片: {prompt}")

            generated_text, image_infos = await self.generate_doubao_image(
                prompt, image_paths
            )
            final_text = generated_text.strip()
            api_type = "doubao"

            if not image_infos and not final_text:
                raise ImageGenerationError("未能生成任何图片或有效文本")

            downloaded_images: list[dict[str, Any]] = []
            if image_infos:
                logger.info("开始使用浏览器上下文批量下载图片...")
                downloaded_images = await self._download_images_with_browser(
                    image_infos, prompt
                )

                if not downloaded_images:
                    raise ImageGenerationError("所有图片下载失败，请稍后重试")

                downloaded_images.sort(key=lambda img: img.get("index", 99))
                logger.info("✅ 已根据原始生成顺序对下载的图片进行排序。")

            result = {
                "success": True,
                "prompt": prompt,
                "count": len(downloaded_images),
                "images": downloaded_images,
                "text": final_text,
                "api_type": api_type,
                "use_cookies": bool(base_config.get("DOUBAO_COOKIES")),
            }

            logger.info(
                f"✅ AI内容生成成功: {len(downloaded_images)} 张图片, "
                f"文本: '{final_text[:50]}...' (使用: {api_type})"
            )
            return result

        except Exception as e:
            logger.error(f"AI图片生成失败: {e}")
            return {
                "success": False,
                "error": str(e),
                "prompt": prompt,
                "count": 0,
                "images": [],
                "text": "",
                "api_type": "error",
            }

    async def batch_generate_images(
        self, prompts: list[str], delay: float = 3.0
    ) -> list[dict[str, Any]]:
        """批量生成图片"""
        results = []

        for i, prompt in enumerate(prompts):
            logger.info(f"批量生成 {i + 1}/{len(prompts)}: {prompt}")

            try:
                result = await self.generate_image(prompt)
                results.append(result)

                if i < len(prompts) - 1:
                    await asyncio.sleep(delay)

            except Exception as e:
                logger.error(f"批量生成第{i + 1}张图片失败: {e}")
                results.append(
                    {
                        "success": False,
                        "error": str(e),
                        "prompt": prompt,
                        "count": 0,
                        "images": [],
                    }
                )

        return results


image_generator = DoubaoImageGenerator()
