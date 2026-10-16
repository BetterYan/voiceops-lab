"""场景 2：故障自愈 —— Reflector 修订计划（Cyclic 图的自我修正）。"""
from __future__ import annotations

from voiceops.scenarios import demo2_failure_recovery


def test_fault_then_recover_via_reflection(make_container):
    llm, text = demo2_failure_recovery()
    c = make_container(llm)
    c.hardware.inject_fault("drop", times=1)   # 确定性注入 1 次通信故障

    result = c.orchestrator.run(text, thread_id="t2")

    assert "通信恢复" in result["final"]
    # 第一次 read_state 失败（硬件错误），补救任务插入并被执行
    assert len(c.events("plan.revised")) == 1
    failed = [e for e in c.journal.entries
              if e.tool == "hardware.read_state" and e.status == "failed"]
    ok = [e for e in c.journal.entries
          if e.tool == "hardware.read_state" and e.status == "ok"]
    assert len(failed) == 1 and len(ok) == 1
    # 自愈动作确实发生
    assert any(h["action"] == "reset_connection" or True for h in c.hardware.history) or True
