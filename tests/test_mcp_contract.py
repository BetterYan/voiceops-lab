"""MCP 契约测试 + Skills 加载器测试。"""
from __future__ import annotations

import asyncio

from voiceops.mcp_server.server import build_mcp_server


def _container_for_mcp():
    from voiceops.adapters.event_bus import FakeClock, InProcessEventBus
    from voiceops.adapters.memory_store import SqliteVectorMemory
    from voiceops.adapters.simulated_hardware import SimulatedHardware
    from voiceops.core.memory import MemoryService

    clock = FakeClock()
    bus = InProcessEventBus()
    hardware = SimulatedHardware(clock)
    memory = MemoryService(SqliteVectorMemory(), clock, bus)
    return hardware, memory


def test_mcp_tool_contracts():
    """MCP 暴露的工具必须齐备且 schema 完整（契约即文档）。"""
    hardware, memory = _container_for_mcp()
    server = build_mcp_server(hardware, memory)

    tools = asyncio.run(server.list_tools())
    names = {t.name for t in tools}
    assert {"hardware_read_state", "hardware_actuate", "memory_search"} <= names
    for t in tools:
        assert t.inputSchema.get("type") == "object"
        assert "properties" in t.inputSchema


def test_mcp_r2_requires_explicit_confirmation():
    """MCP 直调 R2 动作：无人工确认标志必须被拒（双保险，不给协议旁路）。"""
    hardware, memory = _container_for_mcp()
    server = build_mcp_server(hardware, memory)

    async def call_unconfirmed():
        return await server.call_tool("hardware_actuate",
                                      {"device_id": "instr_power",
                                       "action": "power_off"})

    result = asyncio.run(call_unconfirmed())
    # 未确认 → 拒绝信息；硬件无任何副作用
    assert any("confirmed" in str(c) or "人工确认" in str(c) for c in result)
    assert all(h["action"] != "power_off" for h in hardware.history)


def test_skills_loader():
    """技能包加载：frontmatter 解析 + 步骤提取。"""
    from pathlib import Path

    from voiceops.skills_loader import load_skills

    root = Path(__file__).resolve().parents[1] / "voiceops" / "skills_md"
    skills = load_skills(root)
    assert "calibration" in skills
    skill = skills["calibration"]
    assert "hardware.read_state" in skill.tools
    assert len(skill.steps) >= 3
