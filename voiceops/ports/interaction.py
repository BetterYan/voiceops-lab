"""人机交互 Port（Human-in-the-Loop 门禁的"人"侧）。

设计理念：
- 安全层遇到 R2（危险）操作时，只通过本 Port 请求确认 ——
  确认者可以是 CLI 提示、Web 确认卡片、移动端推送，甚至测试中的 AutoConfirm。
- 确定性的安全边界（哪些操作危险）写在工具注册表里，不由 LLM 判断；
  "是否放行"则确定性地交给人。
"""
from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class UserInteraction(Protocol):
    def confirm(self, action_summary: str, risk_reason: str) -> bool:
        """请求人工确认；True=放行。阻塞实现（CLI）或异步回调实现（Web）均可。"""
        ...

    def notify(self, text: str) -> None:
        """向用户推送非阻塞通知（进度/告警）。"""
        ...
