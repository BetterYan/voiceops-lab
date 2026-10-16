"""场景 1：条件分支 + Human-in-the-Loop。"""
from __future__ import annotations

from voiceops.scenarios import demo1_branch_hitl


def test_r2_approved_executes(make_container):
    llm, text = demo1_branch_hitl(approved=True)
    c = make_container(llm, approved=True)

    result = c.orchestrator.run(text, thread_id="t1")

    assert "已执行关机" in result["final"]
    # HITL：确认请求恰好一次，且被放行
    assert len(c.events("safety.confirm_required")) == 1
    assert len(c.events("safety.approved")) == 1
    # 真实副作用发生在仿真硬件上
    assert any(h["action"] == "power_off" and h["device"] == "instr_power"
               for h in c.hardware.history)
    # Journal 完整生命周期：同一 entry 走 intent → approved → ok（最终态 ok）
    statuses = [e.status for e in c.journal.entries if e.tool == "hardware.actuate"]
    assert statuses == ["ok"]
    # 情节记忆沉淀
    assert len(c.events("memory.saved")) >= 1


def test_r2_denied_blocks_execution(make_container):
    """用户拒绝 → actuate 不执行，任务仍优雅收尾（安全优先于任务完成）。"""
    llm, text = demo1_branch_hitl(approved=False)
    c = make_container(llm, approved=False)

    result = c.orchestrator.run(text, thread_id="t1b")

    assert "拒绝" in result["final"]
    assert len(c.events("safety.denied")) == 1
    assert all(h["action"] != "power_off" for h in c.hardware.history)  # 零副作用
    assert [e.status for e in c.journal.entries if e.tool == "hardware.actuate"] == ["denied"]
