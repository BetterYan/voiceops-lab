"""MCP Server：把工具集以 Model Context Protocol 标准暴露（统一软硬件上下文协议层）。

设计理念：
- 工具契约（ToolSpec）在 core.tools 一处定义；本模块只做"协议桥"——
  把注册表映射为 MCP tool。协议演进（MCP 版本升级）不影响工具实现与安全语义。
- 安全语义在 MCP 侧同样成立：R2 动作在 MCP 直调场景下要求显式 confirmed 标志，
  且真实放行链路仍应回到编排层的 HITL 门禁 —— 双保险而非旁路。
"""
from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from voiceops.adapters.memory_store import SqliteVectorMemory
from voiceops.adapters.simulated_hardware import SimulatedHardware
from voiceops.core.memory import MemoryService
from voiceops.ports import Clock


def build_mcp_server(hardware: SimulatedHardware, memory: MemoryService) -> FastMCP:
    mcp = FastMCP("voiceops-lab",
                  instructions="voiceops-lab 硬件工作台工具集：硬件状态/动作 + 长期记忆")

    @mcp.tool()
    def hardware_read_state(device_id: str) -> dict:
        """读取硬件设备当前状态（只读）。"""
        st = hardware.read_state(device_id)
        return {"device_id": st.device_id, "online": st.online,
                "properties": st.properties}

    @mcp.tool()
    def hardware_actuate(device_id: str, action: str, confirmed: bool = False) -> dict:
        """对硬件执行动作（R2 危险操作）。MCP 直调场景必须显式 confirmed=True，
        且该确认应来自真实的人工链路（非 LLM 自行伪造）。"""
        if not confirmed:
            return {"ok": False, "error": "R2 操作需要人工确认标志 confirmed=true（HITL）"}
        res = hardware.actuate(device_id, action, {})
        return {"ok": res.ok, "message": res.message}

    @mcp.tool()
    def memory_search(query: str, kind: str = "") -> dict:
        """检索长期记忆（情节/程序），结果带来源标注。"""
        hits = memory.search(query, kind=kind or None, limit=3)
        return {"hits": [{"text": r.text, "kind": r.kind, "score": round(s, 3)}
                         for r, s in hits]}

    return mcp


def main() -> None:
    """stdio 传输启动：`python -m voiceops.mcp_server`"""
    clock = _SystemClockForMcp()
    hardware = SimulatedHardware(clock)
    from voiceops.adapters.event_bus import InProcessEventBus
    memory = MemoryService(SqliteVectorMemory(), clock, InProcessEventBus())
    build_mcp_server(hardware, memory).run(transport="stdio")


class _SystemClockForMcp:
    def now(self) -> float:
        import time
        return time.time()

    def advance(self, seconds: float) -> None:
        raise NotImplementedError


if __name__ == "__main__":
    main()
