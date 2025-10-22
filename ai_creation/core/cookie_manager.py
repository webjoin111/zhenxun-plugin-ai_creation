from collections import deque
import time

from zhenxun.services.log import logger

from ..config import base_config

COOKIE_COOLDOWN_SECONDS = 3600


class DoubaoCookieManager:
    def __init__(self):
        self._cookies: list[str] = []
        self._cookie_queue: deque[str] = deque()
        self._cooldown_cookies: dict[str, float] = {}
        self.load_cookies()

    def load_cookies(self):
        """从配置加载cookies"""
        cookies_config = base_config.get("DOUBAO_COOKIES")

        cookies_list: list[str] = []
        if isinstance(cookies_config, str):
            cookies_list.append(cookies_config)
        elif isinstance(cookies_config, list):
            cookies_list = cookies_config

        self._cookies = [c for c in cookies_list if isinstance(c, str) and c.strip()]
        if self._cookies:
            self._cookie_queue = deque(self._cookies)
            logger.info(f"✅ 成功加载 {len(self._cookies)} 个豆包Cookie。")
        else:
            self._cookies = []
            self._cookie_queue = deque()
            logger.warning("📝 未配置或配置了空的豆包Cookie列表。")

    def get_next_cookie(self) -> str | None:
        """获取下一个可用的cookie (轮询)"""
        self._check_cooldowns()
        if not self._cookie_queue:
            logger.warning("🍪 当前没有可用的豆包Cookie。")
            return None

        cookie = self._cookie_queue.popleft()
        self._cookie_queue.append(cookie)

        logger.info(f"🍪 使用下一个Cookie: ...{cookie[-20:]}")
        return cookie

    def report_failure(self, cookie: str):
        """报告一个cookie失败，将其放入冷却"""
        if cookie in self._cookies:
            cooldown_until = time.time() + COOKIE_COOLDOWN_SECONDS
            self._cooldown_cookies[cookie] = cooldown_until

            if cookie in self._cookie_queue:
                self._cookie_queue.remove(cookie)

            logger.warning(
                f"❌ Cookie ...{cookie[-20:]} 失败，已放入冷却，"
                f"将在 {COOKIE_COOLDOWN_SECONDS / 60:.0f} 分钟后重试。"
            )

    def _check_cooldowns(self):
        """检查并恢复冷却结束的cookies"""
        now = time.time()
        recovered_cookies = []
        for cookie, cooldown_until in self._cooldown_cookies.items():
            if now >= cooldown_until:
                recovered_cookies.append(cookie)

        for cookie in recovered_cookies:
            del self._cooldown_cookies[cookie]
            if cookie not in self._cookie_queue:
                self._cookie_queue.append(cookie)
            logger.info(f"✅ Cookie ...{cookie[-20:]} 冷却结束，已恢复可用。")

    def get_available_cookie_count(self) -> int:
        """获取当前可用cookie数量"""
        self._check_cooldowns()
        return len(self._cookie_queue)


cookie_manager = DoubaoCookieManager()
