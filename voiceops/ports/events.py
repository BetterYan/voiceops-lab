"""事件总线与时间 Port。

设计理念：
- 事件总线是横切关注点（日志/审计/可观测/测试断言）的统一出口，
  核心域只负责 publish，不关心谁在听 —— 观察者与业务解耦。
- 测试通过订阅事件收集执行轨迹，替代侵入式日志断言。
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, Protocol, runtime_checkable

Event = dict  # {"name": str, "payload": dict, "ts": float, "trace_id": str}


@runtime_checkable
class EventPublisher(Protocol):
    """进程内/进程外事件发布的统一抽象。"""

    def publish(self, event: Event) -> None: ...

    def subscribe(self, name: str, handler: Callable[[Event], None]) -> None: ...


def make_event(
    name: str,
    payload: dict[str, Any] | None = None,
    ts: float = 0.0,
    trace_id: str = "",
) -> Event:
    return {"name": name, "payload": payload or {}, "ts": ts, "trace_id": trace_id}
