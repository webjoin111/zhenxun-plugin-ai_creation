import asyncio
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
import time
from typing import Any

from zhenxun.services.log import logger

from ...config import base_config
from .generator import DoubaoImageGenerator, ImageGenerationError


class RequestStatus(Enum):
    """请求状态枚举"""

    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass
class DrawRequest:
    """绘图请求数据类"""

    request_id: str
    user_id: str
    prompt: str
    status: RequestStatus = RequestStatus.PENDING
    created_at: datetime | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    result: dict[str, Any] | None = None
    error: str | None = None
    estimated_wait_time: float = 0.0
    queue_position: int = 0
    image_paths: list[str] | None = None
    cookie: str | None = None

    def __post_init__(self):
        if self.created_at is None:
            self.created_at = datetime.now(timezone.utc).astimezone()

    @property
    def wait_time(self) -> float:
        """实际等待时间（秒）"""
        if self.started_at and self.created_at:
            return (self.started_at - self.created_at).total_seconds()
        elif self.created_at:
            return (
                datetime.now(timezone.utc).astimezone() - self.created_at
            ).total_seconds()
        return 0.0

    @property
    def processing_time(self) -> float:
        """处理时间（秒）"""
        if self.started_at and self.completed_at:
            return (self.completed_at - self.started_at).total_seconds()
        elif self.started_at:
            return (
                datetime.now(timezone.utc).astimezone() - self.started_at
            ).total_seconds()
        return 0.0


class DrawQueueManager:
    """AI绘图队列管理器"""

    def __init__(self):
        self._queue: list[DrawRequest] = []
        self._processing_request: DrawRequest | None = None
        self._completed_requests: list[DrawRequest] = []
        self._lock = asyncio.Lock()
        self._guest_usage_count = 0
        self.image_generator = DoubaoImageGenerator()
        self._processing_lock = asyncio.Lock()

        self._total_requests = 0
        self._average_processing_time = 60.0
        self._last_browser_close_time: datetime | None = None
        self._last_activity_time: datetime | None = None
        self._browser_cooldown_seconds = 180

        self._queue_processor_task: asyncio.Task | None = None
        self._idle_monitor_task: asyncio.Task | None = None
        self._shutdown = False

        logger.debug("AI绘图队列管理器已初始化")

    async def initialize_browser(self):
        """初始化常驻浏览器实例"""
        logger.debug("正在初始化常驻浏览器...")
        await self.image_generator.initialize()

    async def shutdown_browser(self):
        """关闭常驻浏览器实例"""
        logger.debug("正在关闭常驻浏览器...")
        self._last_activity_time = None
        self._guest_usage_count = 0
        await self.image_generator.cleanup()

    def set_browser_cooldown(self, seconds: int):
        """设置浏览器冷却时间"""
        self._browser_cooldown_seconds = seconds
        logger.debug(f"浏览器冷却时间已设置为 {seconds} 秒")

    def set_browser_close_time(self):
        """记录任务完成时间，并启动浏览器冷却期"""
        self._last_browser_close_time = datetime.now(timezone.utc).astimezone()
        logger.info(
            f"任务处理完成，浏览器进入冷却期 ({self._browser_cooldown_seconds}秒)..."
        )

    def is_browser_in_cooldown(self) -> bool:
        """检查浏览器是否在冷却期"""
        if not self._last_browser_close_time:
            return False

        if self._last_browser_close_time:
            elapsed = (
                datetime.now(timezone.utc).astimezone() - self._last_browser_close_time
            ).total_seconds()
        else:
            elapsed = 0.0
        return elapsed < self._browser_cooldown_seconds

    def get_browser_cooldown_remaining(self) -> float:
        """获取浏览器冷却剩余时间（秒）"""
        if not self.is_browser_in_cooldown():
            return 0.0

        if self._last_browser_close_time:
            elapsed = (
                datetime.now(timezone.utc).astimezone() - self._last_browser_close_time
            ).total_seconds()
        else:
            elapsed = 0.0
        return max(0.0, self._browser_cooldown_seconds - elapsed)

    async def add_request(
        self, user_id: str, prompt: str, image_paths: list[str] | None = None
    ) -> DrawRequest:
        """添加绘图请求到队列"""
        async with self._lock:
            self._last_activity_time = datetime.now(timezone.utc).astimezone()
            request_id = f"{user_id}_{int(time.time() * 1000)}"

            queue_position = len(self._queue)
            estimated_wait = queue_position * self._average_processing_time

            if self._processing_request:
                estimated_wait += max(
                    0,
                    self._average_processing_time
                    - self._processing_request.processing_time,
                )

            if self.is_browser_in_cooldown():
                estimated_wait += self.get_browser_cooldown_remaining()

            request = DrawRequest(
                request_id=request_id,
                user_id=user_id,
                prompt=prompt,
                estimated_wait_time=estimated_wait,
                image_paths=image_paths,
            )

            self._queue.append(request)
            self._total_requests += 1

            actual_position = len(self._queue)

            logger.debug(
                f"用户 {user_id} 的绘图请求已加入队列，位置: {actual_position}, "
                f"预估等待: {estimated_wait:.1f}秒"
            )

            request.queue_position = actual_position
            return request

    async def get_next_request(self) -> DrawRequest | None:
        """获取下一个待处理的请求"""
        async with self._lock:
            if not self._queue:
                return None

            request = self._queue.pop(0)
            request.status = RequestStatus.PROCESSING
            request.started_at = datetime.now(timezone.utc).astimezone()
            self._processing_request = request

            logger.debug(f"开始处理请求 {request.request_id}")
            return request

    async def complete_request(self, request: DrawRequest, result: dict[str, Any]):
        """完成请求处理"""
        async with self._lock:
            request.status = RequestStatus.COMPLETED
            request.completed_at = datetime.now(timezone.utc).astimezone()
            request.result = result

            processing_time = request.processing_time
            if processing_time > 0:
                self._average_processing_time = (
                    self._average_processing_time * 0.8 + processing_time * 0.2
                )

            self._completed_requests.append(request)
            self._processing_request = None

            if request.cookie:
                from .cookie_manager import cookie_manager

                await cookie_manager.increment_usage(request.cookie)

            logger.debug(
                f"请求 {request.request_id} 处理完成，耗时: {processing_time:.1f}秒"
            )
            self.set_browser_close_time()
            self._last_activity_time = datetime.now(timezone.utc).astimezone()

    async def fail_request(self, request: DrawRequest, error: str):
        """标记请求失败"""
        async with self._lock:
            request.status = RequestStatus.FAILED
            request.completed_at = datetime.now(timezone.utc).astimezone()
            request.error = error

            self._completed_requests.append(request)
            self._processing_request = None

            logger.error(f"请求 {request.request_id} 处理失败: {error}")
            self.set_browser_close_time()
            self._last_activity_time = datetime.now(timezone.utc).astimezone()

    async def cancel_request(self, request_id: str) -> bool:
        """取消请求"""
        async with self._lock:
            for i, request in enumerate(self._queue):
                if request.request_id == request_id:
                    request.status = RequestStatus.CANCELLED
                    self._queue.pop(i)
                    self._completed_requests.append(request)
                    logger.debug(f"请求 {request_id} 已取消")
                    return True

            if (
                self._processing_request
                and self._processing_request.request_id == request_id
            ):
                logger.warning(f"请求 {request_id} 正在处理中，无法取消")
                return False

            return False

    def get_queue_status(self) -> dict[str, Any]:
        """获取队列状态"""
        return {
            "queue_length": len(self._queue),
            "processing_request": self._processing_request.request_id
            if self._processing_request
            else None,
            "total_requests": self._total_requests,
            "average_processing_time": self._average_processing_time,
            "browser_in_cooldown": self.is_browser_in_cooldown(),
            "browser_cooldown_remaining": self.get_browser_cooldown_remaining(),
        }

    def get_user_queue_position(self, user_id: str) -> int | None:
        """获取用户在队列中的位置（返回最新请求的位置）"""
        last_position = None
        for i, request in enumerate(self._queue):
            if request.user_id == user_id:
                last_position = i + 1
        return last_position

    def get_user_request_status(self, user_id: str) -> DrawRequest | None:
        """获取用户最新的请求状态"""
        if self._processing_request and self._processing_request.user_id == user_id:
            return self._processing_request

        for request in self._queue:
            if request.user_id == user_id:
                return request

        for request in reversed(self._completed_requests[-10:]):
            if request.user_id == user_id:
                return request

        return None

    async def wait_for_request_completion(
        self, request_id: str, timeout: float = 300.0
    ) -> DrawRequest | None:
        """等待特定请求完成"""
        start_time = asyncio.get_event_loop().time()

        while (asyncio.get_event_loop().time() - start_time) < timeout:
            for req in self._completed_requests:
                if req.request_id == request_id:
                    return req

            if (
                self._processing_request
                and self._processing_request.request_id == request_id
            ):
                await asyncio.sleep(1)
                continue

            for req in self._queue:
                if req.request_id == request_id:
                    await asyncio.sleep(1)
                    break
            else:
                return None

        return None

    async def process_queue_once(self):
        """处理队列中的一个请求（如果有的话）"""
        async with self._processing_lock:
            while self.is_browser_in_cooldown():
                cooldown_remaining = self.get_browser_cooldown_remaining()
                logger.debug(
                    f"队列处理器等待浏览器冷却结束，剩余 {cooldown_remaining:.1f}秒"
                )
                await asyncio.sleep(min(5, cooldown_remaining))

            if not self.image_generator.is_initialized:
                logger.warning("检测到浏览器未初始化，正在尝试启动...")
                await self.initialize_browser()
                if not self.image_generator.is_initialized:
                    logger.error("浏览器启动失败，无法处理任务。")
                    async with self._lock:
                        for req in self._queue:
                            req.status = RequestStatus.FAILED
                            req.error = "浏览器未能成功初始化，无法执行任务。"
                            self._completed_requests.append(req)
                        self._queue.clear()
                    return None

            current_request = await self.get_next_request()
            if not current_request:
                return

            try:
                from .cookie_manager import cookie_manager

                use_cookies = (
                    base_config.get("ENABLE_DOUBAO_COOKIES")
                    and cookie_manager.get_total_cookie_count() > 0
                )

                if use_cookies:
                    selected_cookie = await cookie_manager.get_next_cookie()
                    if not selected_cookie:
                        raise RuntimeError("今日AI绘图额度已用尽，请明日再试。")
                    current_request.cookie = selected_cookie
                    await self.image_generator.update_session_cookie(selected_cookie)
                else:
                    await self.image_generator.update_session_cookie(None)

                result = await self.image_generator.generate_image(
                    prompt=current_request.prompt,
                    count=1,
                    image_paths=current_request.image_paths,
                )

                if result.get("success"):
                    is_guest_draw = not current_request.cookie
                    if is_guest_draw:
                        self._guest_usage_count += 1
                        logger.info(
                            f"无Cookie模式使用次数: {self._guest_usage_count}/5"
                        )

                    await self.complete_request(current_request, result)

                    if is_guest_draw and self._guest_usage_count >= 5:
                        logger.info(
                            "无Cookie模式已达5次上限，将在本次任务完成后立即关闭浏览器。"
                        )
                        await self.shutdown_browser()
                else:
                    error_msg = result.get("error", "未知生成错误")
                    await self.fail_request(current_request, error_msg)

            except (ImageGenerationError, RuntimeError) as e:
                logger.error(f"图片生成发生可恢复错误: {e}")
                logger.error("发生RuntimeError，将关闭浏览器实例以待下次自愈...")
                await self.shutdown_browser()
                await self.fail_request(current_request, str(e))
            except Exception as e:
                await self.fail_request(current_request, str(e))
                logger.error("发生严重未知错误，将关闭浏览器实例以待下次重启...")
                await self.shutdown_browser()

            return current_request

    async def cleanup_old_requests(self, max_age_hours: int = 24):
        """清理旧的已完成请求"""
        async with self._lock:
            cutoff_time = datetime.now(timezone.utc).astimezone() - timedelta(
                hours=max_age_hours
            )
            original_count = len(self._completed_requests)

            self._completed_requests = [
                req
                for req in self._completed_requests
                if req.completed_at and req.completed_at > cutoff_time
            ]

            cleaned_count = original_count - len(self._completed_requests)
            if cleaned_count > 0:
                logger.debug(f"清理了 {cleaned_count} 个旧的请求记录")

    def start_queue_processor(self):
        """启动队列处理器"""
        if self._queue_processor_task is None or self._queue_processor_task.done():
            self._shutdown = False
            self._queue_processor_task = asyncio.create_task(
                self._queue_processor_loop()
            )
            logger.debug("队列处理器已启动")

    def start_idle_monitor(self):
        """启动浏览器闲置监控器"""
        if self._idle_monitor_task is None or self._idle_monitor_task.done():
            self._shutdown = False
            self._idle_monitor_task = asyncio.create_task(self._idle_check_loop())
            logger.debug("浏览器闲置监控器已启动")

    async def stop_idle_monitor(self):
        """停止浏览器闲置监控器"""
        if self._idle_monitor_task and not self._idle_monitor_task.done():
            self._idle_monitor_task.cancel()
            try:
                await self._idle_monitor_task
            except asyncio.CancelledError:
                pass
            logger.debug("浏览器闲置监控器已停止")

    async def _idle_check_loop(self):
        """监控浏览器闲置并自动关闭"""
        logger.debug("浏览器闲置监控循环已启动")
        while not self._shutdown:
            await asyncio.sleep(30)

            timeout_minutes = base_config.get("browser_idle_timeout_minutes", 10)
            if timeout_minutes <= 0:
                continue

            if (
                not self.image_generator.is_initialized
                or self._queue
                or self._processing_request
            ):
                continue

            if self._last_activity_time:
                idle_seconds = (
                    datetime.now(timezone.utc).astimezone() - self._last_activity_time
                ).total_seconds()
                if idle_seconds > timeout_minutes * 60:
                    logger.info(
                        f"浏览器闲置超过 {timeout_minutes} 分钟，将自动关闭以释放资源。"
                    )
                    await self.shutdown_browser()

    async def stop_queue_processor(self):
        """停止队列处理器"""
        self._shutdown = True
        if self._queue_processor_task and not self._queue_processor_task.done():
            self._queue_processor_task.cancel()
            try:
                await self._queue_processor_task
            except asyncio.CancelledError:
                pass
            logger.debug("队列处理器已停止")

    async def _queue_processor_loop(self):
        """队列处理器主循环"""
        logger.debug("队列处理器主循环已启动")
        while not self._shutdown:
            try:
                if self._queue:
                    await self.process_queue_once()
                else:
                    await asyncio.sleep(1)
            except Exception as e:
                logger.error(f"队列处理器发生错误: {e}")
                await asyncio.sleep(5)


draw_queue_manager = DrawQueueManager()
