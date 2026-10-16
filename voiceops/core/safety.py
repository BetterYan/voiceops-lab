"""SafetyGate：确定性安全门禁 + Journal（审计/回滚）。

执行路径（每一次工具调用都必须过闸，无旁路）：
  1. 契约校验（schema 验证，失败即拒 —— 不给 LLM 的幻觉参数进门）
  2. 风险分级分流：
     R0 只读        → 直接执行
     R1 可逆        → 先构建 undo 闭包 → journal 留痕 → 执行
     R2 危险        → journal 记 intent → HITL 确认 → 拒绝则 status=denied 终止
  3. 执行异常 → journal status=failed，事件广播，异常不逃逸出闸门
  4. rollback_all()：按逆序调用 undo 闭包 —— 关键操作可撤销

设计要点：安全边界的"确定性"体现在分级表与执行路径都是代码，
LLM 的非确定性行为被限制在"选择哪个工具"这一层。
"""
from __future__ import annotations

from voiceops.ports import Event, EventPublisher, UserInteraction, make_event

from .models import JournalEntry, SafetyLevel, ToolResult, ToolSpec


class SchemaError(Exception):
    """入参不符合工具契约。"""


def validate_args(spec: ToolSpec, args: dict) -> None:
    """JSON-Schema 子集校验：required + type + enum。够用且零依赖。"""
    props = spec.schema.get("properties", {})
    for key in spec.schema.get("required", []):
        if key not in args:
            raise SchemaError(f"{spec.name}: missing required arg '{key}'")
    for key, value in args.items():
        if key not in props:
            raise SchemaError(f"{spec.name}: unexpected arg '{key}'")
        ptype = props[key].get("type")
        if ptype == "string" and not isinstance(value, str):
            raise SchemaError(f"{spec.name}: arg '{key}' must be string")
        if ptype == "number" and not isinstance(value, (int, float)):
            raise SchemaError(f"{spec.name}: arg '{key}' must be number")
        if ptype == "boolean" and not isinstance(value, bool):
            raise SchemaError(f"{spec.name}: arg '{key}' must be boolean")
        if "enum" in props[key] and value not in props[key]["enum"]:
            raise SchemaError(f"{spec.name}: arg '{key}' must be one of {props[key]['enum']}")


class Journal:
    """执行轨迹簿：审计 + 回滚双职责。"""

    def __init__(self) -> None:
        self.entries: list[JournalEntry] = []
        self._seq = 0

    def append(self, entry: JournalEntry) -> JournalEntry:
        self._seq += 1
        entry.seq = self._seq
        self.entries.append(entry)
        return entry

    def rollback_all(self) -> int:
        """逆序撤销所有已执行且可撤销的操作（测试/恢复用）。"""
        n = 0
        for entry in reversed(self.entries):
            if entry.status == "ok" and entry.undo is not None:
                entry.undo()
                entry.status = "rolled_back"
                n += 1
        return n


class SafetyGate:
    def __init__(self, interaction: UserInteraction, journal: Journal,
                 bus: EventPublisher, clock_now) -> None:
        self._interaction = interaction
        self.journal = journal
        self._bus = bus
        self._now = clock_now

    def _emit(self, name: str, payload: dict, trace_id: str = "") -> None:
        self._bus.publish(make_event(name, payload, self._now(), trace_id))

    def execute(self, spec: ToolSpec, args: dict, trace_id: str = "") -> ToolResult:
        # 1) 契约校验：先验证后执行
        try:
            validate_args(spec, args)
        except SchemaError as exc:
            self._emit("safety.schema_rejected", {"tool": spec.name, "error": str(exc)}, trace_id)
            return ToolResult(ok=False, error=str(exc), tool_name=spec.name)

        entry = self.journal.append(JournalEntry(
            seq=0, ts=self._now(), tool=spec.name, args=dict(args),
            level=spec.safety, trace_id=trace_id))

        # 2) R2：确定性门禁 —— 人说放行才放行
        if spec.safety == SafetyLevel.R2:
            self._emit("safety.confirm_required",
                       {"tool": spec.name, "args": args, "risk": spec.description}, trace_id)
            approved = self._interaction.confirm(
                action_summary=f"{spec.name} {args}", risk_reason=spec.description)
            if not approved:
                entry.status = "denied"
                self._emit("safety.denied", {"tool": spec.name, "args": args}, trace_id)
                return ToolResult(ok=False, denied=True,
                                  error="denied by human-in-the-loop",
                                  tool_name=spec.name)
            entry.status = "approved"
            self._emit("safety.approved", {"tool": spec.name}, trace_id)

        # 3) 执行前构建回滚闭包（R1/R2 且声明了 undo_builder）
        undo = None
        if spec.safety >= SafetyLevel.R1 and spec.undo_builder is not None:
            undo = spec.undo_builder(**args)
        entry.undo = undo

        # 4) 执行：异常不逃逸
        try:
            data = spec.fn(**args)
            entry.status = "ok"
            entry.result = data
            self._emit("tool.result", {"tool": spec.name, "ok": True}, trace_id)
            return ToolResult(ok=True, data=data, tool_name=spec.name)
        except Exception as exc:  # noqa: BLE001 —— 门禁统一兜底
            entry.status = "failed"
            self._emit("tool.result", {"tool": spec.name, "ok": False, "error": str(exc)}, trace_id)
            # R1/R2 失败时若已产生副作用，先回滚本次
            if undo is not None:
                undo()
                entry.status = "failed_rolled_back"
            return ToolResult(ok=False, error=str(exc), tool_name=spec.name,
                              rolled_back=undo is not None)
