"""SafetyGate 单元测试：契约校验 / 分级分流 / 回滚。"""
from __future__ import annotations

import pytest

from voiceops.core.safety import SchemaError
from voiceops.ports import MemoryRecord
from voiceops.scenarios import demo1_branch_hitl, demo3_memory


def test_schema_rejection(make_container):
    """幻觉参数进不了门：缺参 / 未知参数被门禁拦截（返回 ToolResult，不抛异常）。"""
    llm, _ = demo1_branch_hitl(approved=True)
    c = make_container(llm)
    gate = c.gate

    spec = c.registry.get("hardware.read_state")
    for bad_args in ({}, {"device_id": "x", "hacker": True}):
        result = gate.execute(spec, bad_args)
        assert not result.ok
        assert "missing required" in result.error or "unexpected arg" in result.error


def test_rollback_restores_previous_state(make_container):
    """R1/R2 执行后 rollback_all 恢复执行前快照。"""
    from voiceops.adapters.fake_llm import call_tool, finish, plan
    from voiceops.scenarios import demo3_memory

    (llm1, text1), _, _ = demo3_memory()
    c = make_container(llm1)
    before = dict(c.hardware.read_state("instr_power").properties)

    c.orchestrator.run(text1, thread_id="t5")      # 电压 3.3 -> 5.0
    assert c.hardware.read_state("instr_power").properties["voltage"] == 5.0

    n = c.journal.rollback_all()
    assert n >= 1
    after = c.hardware.read_state("instr_power").properties
    assert after["voltage"] == before["voltage"] == 3.3
    assert any(e.status == "rolled_back" for e in c.journal.entries)


def test_memory_search_tool_returns_cited_sources(make_container):
    """防漂移：memory.search 工具结果必须携带 created_at 来源标注。"""
    from voiceops.ports import MemoryRecord

    (llm1, text1), _, _ = demo3_memory()
    c = make_container(llm1)
    c.orchestrator.run(text1, thread_id="t6")

    result = c.registry.execute(
        "memory.search", {"query": "校准 电压"}, c.gate)
    assert result.ok and result.data["hits"], "应能检索到校准情节记忆"
    for hit in result.data["hits"]:
        assert "created_at" in hit and "kind" in hit, "来源标注缺失"
