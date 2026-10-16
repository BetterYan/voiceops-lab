"""时间 Port。

设计理念：任何依赖"现在几点"的逻辑（记忆衰减、任务超时、限流）
都通过 Clock 注入 —— 测试用 FakeClock 手动拨钟，衰减策略变得可精确断言。
"""
from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class Clock(Protocol):
    def now(self) -> float:
        """Unix 秒。"""
        ...

    def advance(self, seconds: float) -> None:
        """仅测试实现需要支持；生产实现为 no-op 或 sleep。"""
        ...
