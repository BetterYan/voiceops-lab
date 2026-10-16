"""场景 3：记忆闭环 + 衰减（FakeClock 拨钟验证智能衰减策略）。"""
from __future__ import annotations

from voiceops.scenarios import demo3_memory


def test_episodic_and_procedural_memory(make_container):
    (llm1, text1), (llm1b, text1b), (llm2, text2) = demo3_memory()
    c = make_container(llm1)

    r1 = c.orchestrator.run(text1, thread_id="t3")
    assert "校准完成" in r1["final"]

    # 相同成功工具序列的第二次会话 → 触发程序记忆自动提炼
    c.orchestrator.deps.llm = llm1b
    r1b = c.orchestrator.run(text1b, thread_id="t3")
    assert "校准完成" in r1b["final"]
    assert c.events("memory.saved")

    # 同一容器换 LLM 脚本 = 同一记忆/硬件状态的第三个会话（口头追问）
    c.orchestrator.deps.llm = llm2
    r2 = c.orchestrator.run(text2, thread_id="t3")
    assert "5.0V" in r2["final"]

    # 程序记忆已自动沉淀
    procedures = [r for r in c.store.all_records() if r.kind == "procedural"]
    assert procedures, "相同成功工具序列应被自动提炼为程序记忆"


def test_decay_ages_out_low_importance_memories(make_container):
    """软遗忘：过期 + 低重要性的记忆不再命中；重要记忆半衰期加倍。"""
    from voiceops.ports import MemoryRecord

    (llm1, text1), _, _ = demo3_memory()
    c = make_container(llm1)
    now = c.clock.now()

    c.store.save(MemoryRecord(id="old", kind="episodic",
                              text="一次性闲聊 今天天气不错",
                              created_at=now - 90 * 86400, importance=0.3))
    c.store.save(MemoryRecord(id="old_important", kind="episodic",
                              text="关键设备变更记录 电压上限 6V",
                              created_at=now - 90 * 86400, importance=0.9))

    hits = c.memory.search("闲聊 天气")          # 90 天前的低重要性 → 软遗忘
    assert all(r.id != "old" for r, _ in hits)
    hits2 = c.memory.search("电压上限 设备变更")  # 同样 90 天，但 importance=0.9 → 保留
    assert any(r.id == "old_important" for r, _ in hits2)
