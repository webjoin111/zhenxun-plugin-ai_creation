import asyncio
import base64
from datetime import datetime
import hashlib
import json
import random
from typing import Any, cast

from playwright.async_api import (
    Browser,
    BrowserContext,
    Page,
    TimeoutError as PlaywrightTimeoutError,
    ViewportSize,
    async_playwright,
)
from playwright_stealth import Stealth

from zhenxun.services.log import logger

from ...config import DOUBAO_SELECTORS, base_config
from ...utils.downloader import IMAGE_DIR, ImageDownloader
from .exceptions import ImageGenerationError, CookieInvalidError

REALISTIC_USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36 Edg/123.0.0.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.2 Safari/605.1.15",
]

COMMON_VIEWPORTS = [
    {"width": 1920, "height": 1080},
    {"width": 1366, "height": 768},
    {"width": 1440, "height": 900},
    {"width": 1536, "height": 864},
    {"width": 1280, "height": 720},
]

HARDWARE_CONCURRENCY_OPTS = [4, 8, 12, 16]
DEVICE_MEMORY_OPTS = [4, 8, 16, 32]


class HumanActionUtils:
    """拟人化操作工具类"""

    @staticmethod
    async def random_sleep(min_s: float = 0.5, max_s: float = 1.5):
        """高斯分布随机等待"""
        mean = (min_s + max_s) / 2
        sigma = (max_s - min_s) / 4
        sleep_time = random.gauss(mean, sigma)
        sleep_time = max(min_s, min(max_s, sleep_time))
        await asyncio.sleep(sleep_time)

    @classmethod
    async def human_move_to(cls, page: Page, element, steps: int = 25):
        """模拟人类鼠标移动轨迹（分段逼近 + 变速）"""
        box = await element.bounding_box()
        if not box:
            return

        target_x = box["x"] + random.uniform(box["width"] * 0.2, box["width"] * 0.8)
        target_y = box["y"] + random.uniform(box["height"] * 0.2, box["height"] * 0.8)

        offset_x = random.uniform(-50, 50)
        offset_y = random.uniform(-50, 50)
        mid_x = target_x + offset_x
        mid_y = target_y + offset_y

        await page.mouse.move(mid_x, mid_y, steps=max(2, int(steps * 0.6)))

        await page.mouse.move(target_x, target_y, steps=steps)

    @classmethod
    async def random_mouse_wander(cls, page: Page, count: int = 2):
        """鼠标随机游走（模拟无意识晃动）"""
        for _ in range(count):
            x = random.randint(100, 1000)
            y = random.randint(100, 800)
            await page.mouse.move(x, y, steps=random.randint(10, 50))
            await asyncio.sleep(random.uniform(0.1, 0.5))

    @classmethod
    async def perform_keep_alive(cls, page: Page, stop_event: asyncio.Event):
        """后台保活任务：在等待生成时执行微小动作"""
        logger.debug("启动拟人化保活(噪音)任务...")
        while not stop_event.is_set():
            try:
                if random.random() > 0.3:
                    action = random.choice(["scroll", "move"])
                    if action == "scroll":
                        delta_y = random.randint(50, 200)
                        await page.mouse.wheel(0, delta_y)
                        await asyncio.sleep(random.uniform(0.5, 1.5))
                        await page.mouse.wheel(0, -delta_y)
                    elif action == "move":
                        await cls.random_mouse_wander(page, count=1)

                await asyncio.sleep(random.uniform(2.0, 5.0))
            except Exception:
                break


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
        logger.debug(
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

            selected_ua = random.choice(REALISTIC_USER_AGENTS)
            selected_viewport = random.choice(COMMON_VIEWPORTS)

            hw_concurrency = random.choice(HARDWARE_CONCURRENCY_OPTS)
            device_memory = random.choice(DEVICE_MEMORY_OPTS)

            logger.debug(
                f"指纹配置: Res={selected_viewport['width']}x{selected_viewport['height']}, CPU={hw_concurrency}, Mem={device_memory}"
            )

            self.context = await self.browser.new_context(
                viewport=cast(ViewportSize, selected_viewport),
                user_agent=selected_ua,
                locale="zh-CN",
                timezone_id="Asia/Shanghai",
                device_scale_factor=random.choice([1, 1.25, 1.5]),
            )

            if self.context is None:
                logger.error("浏览器上下文未初始化")
                return False

            await Stealth().apply_stealth_async(self.context)

            await self.context.add_init_script(f"""
                Object.defineProperty(navigator, 'hardwareConcurrency', {{ get: () => {hw_concurrency} }});
                Object.defineProperty(navigator, 'deviceMemory', {{ get: () => {device_memory} }});
                // 简单的 WebGL 干扰（微小的指纹噪声）
                const getParameter = WebGLRenderingContext.prototype.getParameter;
                WebGLRenderingContext.prototype.getParameter = function(parameter) {{
                    // 37446 是 RENDERER
                    if (parameter === 37446) {{
                        const result = getParameter.apply(this, [parameter]);
                        return result + ' (Custom Build)';
                    }}
                    return getParameter.apply(this, [parameter]);
                }};
            """)

            self.page = await self.context.new_page()

            logger.debug("豆包图片生成器浏览器初始化成功")
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
                logger.debug(f"浏览器会话已更新 {len(cookies)} 个Cookie。")

        except Exception as e:
            logger.error(f"设置cookies失败: {e}")

    async def navigate_to_create_image(self) -> bool:
        """导航到豆包图片创建页面"""
        if not self.page:
            logger.error("页面未初始化")
            return False

        try:
            logger.debug("正在导航到豆包图片创建页面...")
            await self.page.goto(
                self.create_image_url, wait_until="domcontentloaded", timeout=60000
            )

            await asyncio.sleep(5)

            title = await self.page.title()
            logger.debug(f"页面标题: {title}")

            await HumanActionUtils.random_sleep(0.5, 1.5)
            await HumanActionUtils.random_mouse_wander(self.page)

            return True

        except Exception as e:
            logger.error(f"导航到豆包图片创建页面失败: {e}")
            return False

    async def _upload_images(self, image_paths: list[str]) -> bool:
        """上传图片到豆包输入框"""
        if not self.page:
            return False

        try:
            logger.debug(f"开始上传 {len(image_paths)} 张图片...")

            success = await self._upload_file_input(image_paths)
            if success:
                logger.debug("文件输入框上传成功。")
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
                        logger.debug(
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
                        logger.debug(f"找到豆包输入框: {selector}")
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

            logger.debug(f"已输入豆包提示词: {prompt[:200]}...")
            return True

        except Exception as e:
            logger.error(f"输入提示词失败: {e}")
            return False

    async def _submit_generation(self) -> bool:
        """提交生成请求"""
        if not self.page:
            return False

        try:
            logger.debug("等待并点击豆包提交按钮...")
            try:
                submit_button = self.page.locator("button#flow-end-msg-send")

                await HumanActionUtils.human_move_to(self.page, submit_button)
                await HumanActionUtils.random_sleep(0.3, 0.7)

                box = await submit_button.bounding_box()
                if box:
                    await self.page.mouse.down()
                    await asyncio.sleep(random.uniform(0.08, 0.15))
                    await self.page.mouse.up()
                else:
                    await submit_button.click()

                await HumanActionUtils.random_sleep(1.0, 2.0)
                logger.debug("等待豆包图片生成 (点击按钮成功)...")
                return True
            except PlaywrightTimeoutError:
                logger.warning("点击提交按钮超时，尝试使用回车键作为备选方法...")
                input_element = None
                for selector in DOUBAO_SELECTORS["prompt_input"]:
                    if not self.page or self.page.is_closed():
                        logger.error("备选方法失败：页面已关闭。")
                        return False
                    element = await self.page.query_selector(selector)
                    if element and await element.is_visible():
                        input_element = element
                        logger.debug(f"找到用于回车的输入框: {selector}")
                        break

                if not input_element:
                    logger.error("备选方法失败：未能找到输入框来发送回车键。")
                    return False

                await input_element.press("Enter", delay=random.randint(50, 150))
                await HumanActionUtils.random_sleep(1.0, 2.0)
                logger.info("✅ 备选方法：成功通过回车键提交生成请求。")
                logger.debug("等待豆包图片生成 (回车键成功)...")
                return True
        except Exception as e:
            logger.error(f"提交生成请求时发生未知错误: {e}", e=e)
            return False

    async def check_login_status(self):
        """
        通过UI元素检测当前的登录状态。
        """
        if not self.page:
            return

        selector_login_btn = 'button[data-testid="to_login_button"]'
        selector_avatar = ".semi-avatar-no-focus-visible"

        try:
            logger.debug("正在检查登录状态 (UI检测)...")
            element = await self.page.wait_for_selector(
                f"{selector_login_btn}, {selector_avatar}",
                state="visible",
                timeout=8000,
            )

            if element:
                is_login_btn = await element.evaluate(
                    f"(el) => el.matches('{selector_login_btn}')"
                )

                if is_login_btn:
                    logger.warning("检测到页面存在登录按钮，判断为Cookie失效。")
                    raise CookieInvalidError("页面显示未登录状态。")
                else:
                    logger.debug("检测到用户头像，登录状态有效。")

        except PlaywrightTimeoutError:
            logger.warning("检查登录状态超时，未找到登录按钮或头像，将尝试继续执行。")
        except CookieInvalidError:
            raise
        except Exception as e:
            logger.warning(f"检查登录状态时发生意外错误: {e}")

    async def _handle_captcha_if_present(self) -> bool:
        """
        检查页面是否存在验证码，如果存在且配置开启，则尝试解决。
        """
        if not self.page:
            return False

        if not base_config.get("DOUBAO_AUTO_SOLVE_CAPTCHA", True):
            try:
                captcha_container = self.page.locator("#captcha_container")
                await captcha_container.wait_for(state="visible", timeout=5000)
                logger.warning("检测到豆包验证码，但自动破解功能已关闭，任务失败。")
                raise ImageGenerationError("遇到验证码，但自动破解功能已关闭。")
            except PlaywrightTimeoutError:
                logger.debug("未检测到验证码弹窗，且自动破解已关闭，流程继续。")
                return False

        from .captcha_solver import solve_drag_captcha_if_present

        return await solve_drag_captcha_if_present(self.page)

    async def generate_doubao_image(
        self,
        prompt: str,
        image_paths: list[str] | None = None,
        check_login: bool = False,
    ) -> list[dict[str, Any]]:
        """
        使用豆包生成图片，并返回文本和带索引的图片信息列表。
        这是一个无状态方法，所有状态都局限于本次调用。
        """
        generation_complete_event = asyncio.Event()
        sse_error_event = asyncio.Event()
        page_closed_event = asyncio.Event()
        sse_error_message: list[str | None] = [None]
        content_order: list[dict[str, Any]] = []
        image_data_map: dict[str, list[str]] = {}
        current_text_buffer: list[str] = []

        async def _local_sse_handler(response):
            try:
                if (
                    "completion" not in response.url
                    or response.status != 200
                    or "text/event-stream"
                    not in response.headers.get("content-type", "")
                ):
                    return

                try:
                    body_bytes = await response.body()  # type: ignore
                except Exception as exc:
                    if "No data found for resource with given identifier" in str(exc):
                        error_str = f"SSE流中断，可能因内容审核失败或网络问题: {exc}"
                        sse_error_message[0] = error_str
                        sse_error_event.set()
                    else:
                        logger.warning(f"获取SSE响应体时发生非关键错误: {exc}")
                    return
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
                            current_text_buffer.append(
                                repaired_text.replace("\\n", "\n")
                            )

                        creations = content_json.get("creations")
                        if creations and isinstance(creations, list):
                            if current_text_buffer:
                                content_order.append(
                                    {
                                        "type": "text",
                                        "content": "".join(current_text_buffer),
                                    }
                                )
                                current_text_buffer.clear()

                            message_id = message_data.get("id")
                            is_placeholder = any(
                                c.get("image", {}).get("status") == 1 for c in creations
                            )

                            if is_placeholder and not any(
                                block.get("id") == message_id for block in content_order
                            ):
                                content_order.append(
                                    {"type": "image", "id": message_id}
                                )
                            else:
                                urls = []
                                for creation in creations:
                                    image_info = creation.get("image", {})
                                    if raw_url_info := image_info.get("image_ori_raw"):
                                        logger.debug(
                                            f"  -> 发现 image_ori_raw 链接: {raw_url_info.get('url')}"
                                        )
                                    if ori_url_info := image_info.get("image_ori"):
                                        logger.debug(
                                            f"  -> 发现 image_ori 链接: {ori_url_info.get('url')}"
                                        )

                                    url = None
                                    if raw_url := image_info.get(
                                        "image_ori_raw", {}
                                    ).get("url"):
                                        if "_pre_" not in raw_url:
                                            url = raw_url
                                    if not url and (
                                        ori_url := image_info.get("image_ori", {}).get(
                                            "url"
                                        )
                                    ):
                                        if "_pre_" not in ori_url:
                                            url = ori_url
                                    if url:
                                        urls.append(url)
                                if message_id and urls:
                                    image_data_map[message_id] = urls

                    except (json.JSONDecodeError, KeyError) as e:
                        logger.debug(f"跳过无法解析的SSE片段: {e}")
                    except Exception as inner_exc:
                        logger.debug(f"SSE事件处理出现内部错误: {inner_exc}")
            except Exception as exc:
                logger.warning(f"SSE拦截器处理响应失败: {exc}")

        def _on_page_close(page=None):
            logger.warning("检测到豆包浏览器页面被关闭。")
            page_closed_event.set()

        if self.page:
            self.page.on("response", _local_sse_handler)
            self.page.on("close", _on_page_close)

        try:
            if not await self.navigate_to_create_image():
                raise ImageGenerationError("导航到豆包图片创建页面失败")

            if check_login:
                await self.check_login_status()

            if image_paths:
                logger.debug(f"检测到 {len(image_paths)} 张图片输入，开始上传...")
                if not await self._upload_images(image_paths):
                    logger.warning("图片上传失败，继续使用纯文本模式")
                else:
                    logger.debug("图片上传成功，等待图片处理...")
                    await asyncio.sleep(5)

            if not await self._input_prompt(prompt):
                raise ImageGenerationError("输入提示词失败")

            if not await self._submit_generation():
                raise ImageGenerationError("提交生成请求失败")

            captcha_was_handled = await self._handle_captcha_if_present()
            if captcha_was_handled:
                logger.debug("验证码已处理，重置SSE流结束信号，等待新的生成流。")
                generation_complete_event.clear()

            signal_timeout = int(base_config.get("doubao_wait_signal_timeout", 120))

            keep_alive_task = None
            if self.page:
                keep_alive_task = asyncio.create_task(
                    HumanActionUtils.perform_keep_alive(
                        self.page, generation_complete_event
                    )
                )

            try:
                done, pending = await asyncio.wait(
                    {
                        asyncio.create_task(generation_complete_event.wait()),
                        asyncio.create_task(sse_error_event.wait()),
                        asyncio.create_task(page_closed_event.wait()),
                    },
                    return_when=asyncio.FIRST_COMPLETED,
                    timeout=signal_timeout,
                )
                for task in pending:
                    task.cancel()

                if page_closed_event.is_set():
                    raise ImageGenerationError(
                        "绘图过程中浏览器页面意外关闭，任务终止。"
                    )

                if sse_error_event.is_set():
                    raise ImageGenerationError(sse_error_message[0])

                if not done:
                    raise asyncio.TimeoutError
                else:
                    logger.debug("✅ 收到豆包SSE流结束信号，额外等待以确保数据完整。")
                    await asyncio.sleep(3)
            except asyncio.TimeoutError:
                logger.warning(
                    f"等待生成完成信号超时 ({signal_timeout}s)。将尝试使用已收到的数据。"
                )
            finally:
                if keep_alive_task and not keep_alive_task.done():
                    keep_alive_task.cancel()
                    try:
                        await keep_alive_task
                    except asyncio.CancelledError:
                        pass

            if current_text_buffer:
                content_order.append(
                    {"type": "text", "content": "".join(current_text_buffer)}
                )
                current_text_buffer.clear()

            structured_result = []
            for block in content_order:
                if block["type"] == "text":
                    structured_result.append(block)
                elif block["type"] == "image":
                    image_urls = image_data_map.get(block["id"], [])
                    if image_urls:
                        structured_result.append(
                            {
                                "type": "image",
                                "content": [
                                    {"url": url, "index": i}
                                    for i, url in enumerate(image_urls)
                                ],
                            }
                        )

            return structured_result

        except CookieInvalidError:
            raise
        except Exception as e:
            logger.debug("底层豆包图片生成流程捕获到异常", e=e)
            raise ImageGenerationError(f"{e}") from e
        finally:
            if self.page:
                self.page.remove_listener("response", _local_sse_handler)
                try:
                    self.page.remove_listener("close", _on_page_close)
                except Exception:
                    pass

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
        for item in urls_with_index:
            logger.debug(f"  -> 准备下载图片: {item['url']}")

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

            logger.debug(
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
        check_login: bool = False,
    ) -> dict[str, Any]:
        """生成AI图片"""
        try:
            if image_paths:
                logger.debug(
                    f"🎨 开始生成AI图片 (基于 {len(image_paths)} 张图片): {prompt}"
                )
                logger.debug(f"📷 输入图片路径: {image_paths}")
            else:
                logger.debug(f"🎨 开始生成AI图片: {prompt}")

            structured_blocks = await self.generate_doubao_image(
                prompt, image_paths, check_login
            )

            if not structured_blocks:
                raise ImageGenerationError("未能生成任何内容")

            final_result_blocks = []
            for block in structured_blocks:
                if block["type"] == "text":
                    final_result_blocks.append(
                        {"type": "text", "content": block["content"].strip()}
                    )
                elif block["type"] == "image":
                    image_infos = block.get("content", [])
                    if image_infos:
                        downloaded_images = await self._download_images_with_browser(
                            image_infos, prompt
                        )
                        if downloaded_images:
                            downloaded_images.sort(key=lambda img: img.get("index", 99))
                            final_result_blocks.append(
                                {"type": "image", "content": downloaded_images}
                            )

            if not any(block.get("type") == "image" for block in final_result_blocks):
                if not any(
                    block.get("type") == "text" for block in final_result_blocks
                ):
                    raise ImageGenerationError("所有图片下载失败，且无文本内容")

            result = {
                "success": True,
                "prompt": prompt,
                "structured_result": final_result_blocks,
                "api_type": "doubao",
                "use_cookies": bool(base_config.get("DOUBAO_COOKIES")),
            }

            logger.debug(
                f"✅ AI内容生成成功，共 {len(final_result_blocks)} 个内容块 (使用: doubao)"
            )
            return result

        except CookieInvalidError:
            raise
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
            logger.debug(f"批量生成 {i + 1}/{len(prompts)}: {prompt}")

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
