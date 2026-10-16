"""事件总线 / 时钟 / 交互适配器（进程内实现）。

- InProcessEventBus：同步 pub/sub。同步是 PoC 的刻意选择 —— 确定性、易断言；
  生产可替换为 asyncio/Redis 实现，核心域无感。
- FakeClock：手动拨钟，让记忆衰减、超时策略可被精确断言。
- AutoConfirm / ConsoleInteraction：HITL 的两个"人"实现。
"""
from __future__ import annotations

import threading
import time

from voiceops.ports import Clock, Event, EventPublisher, UserInteraction, make_event


class InProcessEventBus(EventPublisher):
    def __init__(self) -> None:
        self._handlers: dict[str, list] = {}
        self._lock = threading.Lock()
        self.all_events: list[Event] = []   # 测试断言用：全量轨迹

    def publish(self, event: Event) -> None:
        with self._lock:
            self.all_events.append(event)
            handlers = list(self._handlers.get(event["name"], []))
        for handler in handlers:
            handler(event)

    def subscribe(self, name: str, handler) -> None:
        with self._lock:
            self._handlers.setdefault(name, []).append(handler)


class SystemClock(Clock):
    def now(self) -> float:
        return time.time()

    def advance(self, seconds: float) -> None:
        raise NotImplementedError("SystemClock 不可拨钟；测试请用 FakeClock")


class FakeClock(Clock):
    def __init__(self, start: float = 1_700_000_000.0) -> None:
        self._now = start

    def now(self) -> float:
        return self._now

    def advance(self, seconds: float) -> None:
        self._now += seconds


class AutoConfirm(UserInteraction):
    """测试/演示用：固定放行或固定拒绝。"""

    def __init__(self, decision: bool = True, log: list | None = None) -> None:
        self.decision = decision
        self.log = log if log is not None else []

    def confirm(self, action_summary: str, risk_reason: str) -> bool:
        self.log.append({"action": action_summary, "risk": risk_reason,
                         "approved": self.decision})
        return self.decision

    def notify(self, text: str) -> None:
        self.log.append({"notify": text})


class ConsoleInteraction(UserInteraction):
    """CLI 交互实现：终端里问人。"""

    def confirm(self, action_summary: str, risk_reason: str) -> bool:
        print(f"\n[HITL 确认] 风险操作: {action_summary}\n            原因: {risk_reason}")
        return input("放行? [y/N] ").strip().lower() in ("y", "yes")

    def notify(self, text: str) -> None:
        print(f"[通知] {text}")
