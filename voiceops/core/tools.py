"""ToolRegistry：工具集的单一事实源。

设计理念（JD：Agent Skills 与任务编排 / 高质量工具集）：
- 工具 = ToolSpec（契约 + 实现 + 安全级别 + 回滚构建器），
  同一份注册表服务三个消费方：Executor 的 ReAct 循环、MCP Server 暴露、契约测试 ——
  一处定义，处处一致。
- 工具按能力域分组注册（make_hardware_tools / make_memory_tools），
  新增能力域 = 新增一个工厂函数，Executor/MCP 无需改动（可扩展性）。
"""
from __future__ import annotations

from voiceops.ports import HardwareTransport

from .models import SafetyLevel, ToolResult, ToolSpec
from .safety import SafetyGate


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, ToolSpec] = {}

    def register(self, spec: ToolSpec) -> None:
        if spec.name in self._tools:
            raise ValueError(f"duplicate tool: {spec.name}")
        self._tools[spec.name] = spec

    def get(self, name: str) -> ToolSpec:
        return self._tools[name]

    def specs(self) -> list[ToolSpec]:
        return list(self._tools.values())

    def llm_schemas(self) -> list[dict]:
        return [t.openai_style_schema() for t in self._tools.values()]

    def execute(self, name: str, args: dict, gate: SafetyGate,
                trace_id: str = "") -> ToolResult:
        return gate.execute(self.get(name), args, trace_id=trace_id)


# ---------------------------------------------------------------------------
# 能力域工具工厂：绑定 Port 适配器，声明安全级别与回滚构建器
# ---------------------------------------------------------------------------

def make_hardware_tools(hardware: HardwareTransport) -> list[ToolSpec]:
    """硬件域工具：R0 读 / R1 可逆变更 / R2 危险操作（关停类）。"""

    def read_state(device_id: str) -> dict:
        st = hardware.read_state(device_id)
        return {"device_id": st.device_id, "online": st.online,
                "properties": st.properties}

    def actuate(device_id: str, action: str, params: dict | None = None) -> dict:
        res = hardware.actuate(device_id, action, params or {})
        return {"ok": res.ok, "message": res.message,
                "previous_state": res.previous_state}

    def undo_actuate(device_id: str, action: str, params: dict | None = None):
        """执行前快照当前属性，返回"恢复快照"的闭包。"""
        snapshot = dict(hardware.read_state(device_id).properties)

        def _undo() -> None:
            for key, value in snapshot.items():
                hardware.actuate(device_id, "set_property", {"key": key, "value": value})
        return _undo

    def reset_connection(device_id: str) -> dict:
        hardware.reset_connection(device_id)
        return {"ok": True, "message": f"connection to {device_id} reset"}

    def _no_undo(**_kw):
        return None

    return [
        ToolSpec(
            name="hardware.read_state",
            description="读取硬件设备当前状态（只读）",
            safety=SafetyLevel.R0,
            schema={"type": "object", "required": ["device_id"],
                    "properties": {"device_id": {"type": "string"}}},
            fn=read_state,
            undo_builder=_no_undo,
        ),
        ToolSpec(
            name="hardware.actuate",
            description="对硬件设备执行动作（set_voltage/power_off/set_property 等）；"
                        "power_off 属于不可逆危险操作",
            safety=SafetyLevel.R2,
            schema={"type": "object", "required": ["device_id", "action"],
                    "properties": {
                        "device_id": {"type": "string"},
                        "action": {"type": "string", "enum": [
                            "set_voltage", "power_off", "set_property", "start", "stop"]},
                        "params": {"type": "object"},
                    }},
            fn=actuate,
            undo_builder=undo_actuate,
        ),
        ToolSpec(
            name="hardware.reset_connection",
            description="重置与设备的通信链路（故障自愈动作，可逆）",
            safety=SafetyLevel.R1,
            schema={"type": "object", "required": ["device_id"],
                    "properties": {"device_id": {"type": "string"}}},
            fn=reset_connection,
            undo_builder=_no_undo,
        ),
    ]


def make_memory_tools(memory_service) -> list[ToolSpec]:
    """记忆域工具：检索（R0）/ 保存程序性记忆（R1）。"""

    def memory_search(query: str, kind: str = "") -> dict:
        hits = memory_service.search(query, kind=kind or None, limit=3)
        # 带来源标注返回 —— 上下文注入可溯源，是防"记忆漂移"的第一道防线
        return {"hits": [{"text": r.text, "kind": r.kind, "score": round(s, 3),
                          "created_at": r.created_at, "id": r.id}
                         for r, s in hits]}

    def memory_save_procedure(name: str, steps: list[str]) -> dict:
        memory_service.record_procedure(name, steps)
        return {"ok": True, "name": name}

    return [
        ToolSpec(
            name="memory.search",
            description="检索长期记忆（情节/程序），返回带来源标注的相关记忆",
            safety=SafetyLevel.R0,
            schema={"type": "object", "required": ["query"],
                    "properties": {"query": {"type": "string"},
                                   "kind": {"type": "string", "enum": ["", "episodic", "procedural"]}}},
            fn=memory_search,
        ),
        ToolSpec(
            name="memory.save_procedure",
            description="把一段可复用的工作流程沉淀为程序记忆（playbook）",
            safety=SafetyLevel.R1,
            schema={"type": "object", "required": ["name", "steps"],
                    "properties": {"name": {"type": "string"},
                                   "steps": {"type": "array", "items": {"type": "string"}}}},
            fn=memory_save_procedure,
        ),
    ]


def make_block_tools(hardware: HardwareTransport) -> list[ToolSpec]:
    """UI 方块域工具（网页 Demo）：方块是一台"虚拟执行器"——
    与仪器共用 HardwareTransport/SafetyGate/Journal 全链路（R1 可逆：执行前快照可回滚）。"""

    def block_actuate(action: str, dx: float = 0, dy: float = 0,
                      x: float = 0, y: float = 0) -> dict:
        params = {"dx": dx, "dy": dy} if action == "move" else {"x": x, "y": y}
        res = hardware.actuate("block", action, params)
        st = hardware.read_state("block")
        return {"ok": res.ok, "message": res.message,
                "x": st.properties["x"], "y": st.properties["y"]}

    def undo_block(action: str, **_kw):
        snapshot = dict(hardware.read_state("block").properties)

        def _undo() -> None:
            hardware.actuate("block", "move_to",
                             {"x": snapshot["x"], "y": snapshot["y"]})
        return _undo

    return [
        ToolSpec(
            name="block.move",
            description="移动网页上的方块：move 为相对移动(dx,dy)，move_to 为绝对定位(x,y)；"
                        "网格 0..9，左上角为 (0,0)",
            safety=SafetyLevel.R1,
            schema={"type": "object", "required": ["action"],
                    "properties": {
                        "action": {"type": "string", "enum": ["move", "move_to"]},
                        "dx": {"type": "number"}, "dy": {"type": "number"},
                        "x": {"type": "number"}, "y": {"type": "number"},
                    }},
            fn=block_actuate,
            undo_builder=undo_block,
        ),
    ]
