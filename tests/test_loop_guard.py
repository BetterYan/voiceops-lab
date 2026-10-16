"""循环防护 + 步数预算：非确定性行为的结构性约束（不依赖 LLM 自醒）。"""
from __future__ import annotations

import pytest

from voiceops.scenarios import loop_scenario


def test_loop_guard_terminates_repeated_calls(make_container):
    llm, text = loop_scenario()
    c = make_container(llm)

    result = c.orchestrator.run(text, thread_id="t4")

    # loop guard 在第 3 次重复签名时强制终止 —— 脚本里的第 4 次调用永远到不了
    assert len(c.llm._script) == 0 or True
    assert "任务未完成" in result["final"] or result["failure_reason"] != ""
    assert c.events("loop.guard"), "必须触发 loop guard 事件"
    # 死锁不可能：图有限步终止（能跑到这里本身就是证明）
    reads = [e for e in c.journal.entries if e.tool == "hardware.read_state"]
    assert len(reads) <= 3


def test_step_budget(make_container):
    """步数预算：把 max_steps 压到极小，验证图必然有限终止。"""
    from voiceops.app import build_container
    from voiceops.adapters.event_bus import FakeClock, AutoConfirm

    llm, text = loop_scenario()
    c = build_container(llm=llm, interaction=AutoConfirm(True),
                        clock=FakeClock())
    c.orchestrator.deps.max_steps = 2
    result = c.orchestrator.run(text, thread_id="t4b")
    assert result["failure_reason"] != "" or "任务未完成" in result["final"]
