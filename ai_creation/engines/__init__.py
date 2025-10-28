from abc import ABC, abstractmethod
from typing import Any


class DrawEngine(ABC):
    """绘图引擎的抽象基类"""

    @abstractmethod
    async def draw(
        self, prompt: str, image_bytes: list[bytes] | None = None
    ) -> dict[str, Any] | list[dict[str, Any]]:
        """
        执行绘图操作并返回结果。
        """
        raise NotImplementedError


def get_engine(engine_name: str) -> DrawEngine:
    """
    绘图引擎工厂函数。
    """
    normalized_name = engine_name.lower()
    if normalized_name == "doubao":
        from .doubao import DoubaoEngine

        return DoubaoEngine()
    if normalized_name == "api":
        from .llm_api import LlmApiEngine

        return LlmApiEngine()
    raise ValueError(f"未知的绘图引擎: '{engine_name}'")
