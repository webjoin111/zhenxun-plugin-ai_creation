from datetime import date, datetime
import json
from pathlib import Path
from typing import Any

import aiofiles

from zhenxun.configs.path_config import DATA_PATH
from zhenxun.services.log import logger

from ...config import base_config

COOKIE_DAILY_LIMIT = 100

PLUGIN_NAME = Path(__file__).resolve().parents[2].name
STATE_FILE = DATA_PATH / PLUGIN_NAME / "cookie_state.json"


class DoubaoCookieManager:
    def __init__(self):
        self._cookie_states: list[dict[str, Any]] = []
        self._last_reset_date: date | None = None
        STATE_FILE.parent.mkdir(parents=True, exist_ok=True)

    async def _save_states(self):
        """将当前Cookie状态持久化到文件。"""
        try:
            reset_date_to_save = self._last_reset_date or date.today()
            data_to_save = {
                "last_reset_date": reset_date_to_save.isoformat(),
                "cookies": self._cookie_states,
            }
            async with aiofiles.open(STATE_FILE, "w", encoding="utf-8") as f:
                await f.write(json.dumps(data_to_save, indent=4, ensure_ascii=False))
        except Exception as e:
            logger.error(f"保存Cookie状态文件失败: {e}")

    async def _check_and_reset_daily_usage(self):
        """检查是否为新的一天，如果是，则重置所有cookie的使用次数。"""
        today = date.today()
        if not self._last_reset_date or today > self._last_reset_date:
            logger.debug(f"新的一天 ({today.isoformat()})，重置所有Cookie使用额度。")
            for state in self._cookie_states:
                state["usage"] = 0
            self._last_reset_date = today
            await self._save_states()

    async def load_and_sync_cookies(self):
        """从配置和状态文件加载并同步Cookie，处理每日重置。"""
        logger.debug("正在加载并同步Cookie状态...")

        config_cookies_raw = base_config.get("DOUBAO_COOKIES", [])
        if isinstance(config_cookies_raw, str):
            config_cookies = {config_cookies_raw}
        elif isinstance(config_cookies_raw, list):
            config_cookies = {
                cookie for cookie in config_cookies_raw if isinstance(cookie, str)
            }
        else:
            config_cookies = set()

        old_states: dict[str, int] = {}
        last_reset_date_str = ""
        if STATE_FILE.exists():
            try:
                async with aiofiles.open(STATE_FILE, "r", encoding="utf-8") as f:
                    content = await f.read()
                    if content:
                        saved_data = json.loads(content)
                        last_reset_date_str = saved_data.get("last_reset_date", "")
                        for state in saved_data.get("cookies", []):
                            cookie_val = state.get("cookie")
                            usage_val = state.get("usage", 0)
                            if isinstance(cookie_val, str):
                                old_states[cookie_val] = int(usage_val)
            except Exception as e:
                logger.error(f"读取Cookie状态文件失败: {e}")

        if last_reset_date_str:
            try:
                self._last_reset_date = datetime.fromisoformat(
                    last_reset_date_str
                ).date()
            except ValueError:
                logger.warning(f"无法解析状态文件中的日期: {last_reset_date_str}")
                self._last_reset_date = None

        synced_states: list[dict[str, Any]] = []
        for cookie in config_cookies:
            usage = old_states.get(cookie, 0)
            synced_states.append({"cookie": cookie, "usage": usage})

        self._cookie_states = synced_states
        logger.debug(
            f"✅ Cookie状态同步完成，加载了 {len(self._cookie_states)} 个有效Cookie。"
        )
        await self._save_states()
        await self._check_and_reset_daily_usage()

    async def get_next_cookie(self) -> str | None:
        """获取下一个使用次数最少且可用的cookie。"""
        await self._check_and_reset_daily_usage()

        available_cookies = [
            state
            for state in self._cookie_states
            if state["usage"] < COOKIE_DAILY_LIMIT
        ]

        if not available_cookies:
            logger.warning("🍪 所有可用Cookie今日额度已用尽。")
            return None

        available_cookies.sort(key=lambda state: state["usage"])
        best_cookie_state = available_cookies[0]

        cookie_str = best_cookie_state["cookie"]
        usage = best_cookie_state["usage"]
        logger.debug(
            f"🍪 选中Cookie: ...{cookie_str[-20:]} (当前用量: {usage}/{COOKIE_DAILY_LIMIT})"
        )
        return cookie_str

    async def increment_usage(self, cookie: str):
        """为一个Cookie的使用次数+1并保存状态。"""
        for state in self._cookie_states:
            if state["cookie"] == cookie:
                state["usage"] += 1
                logger.debug(
                    f"✅ Cookie ...{cookie[-20:]} 使用次数+1，当前为: {state['usage']}"
                )
                await self._save_states()
                break

    def get_available_cookie_count(self) -> int:
        """获取当前可用cookie数量。"""
        return len(
            [
                state
                for state in self._cookie_states
                if state["usage"] < COOKIE_DAILY_LIMIT
            ]
        )

    def get_total_cookie_count(self) -> int:
        """获取配置的Cookie总数。"""
        return len(self._cookie_states)


cookie_manager = DoubaoCookieManager()
