"""方块工具（R1 可逆）+ Web Demo WebSocket 端到端测试。"""
from __future__ import annotations

from voiceops.adapters.rule_llm import RuleLLM


def test_block_move_via_gate_is_r1_and_reversible(make_container):
    from voiceops.adapters.event_bus import FakeClock, AutoConfirm
    from voiceops.app import build_container
    from voiceops.scenarios import demo1_branch_hitl  # noqa: F401（仅确保模块可用）

    llm, _ = demo1_branch_hitl(approved=True)
    c = build_container(llm=llm, interaction=AutoConfirm(True), clock=FakeClock())
    before = dict(c.hardware.read_state("block").properties)
    assert before == {"x": 5, "y": 5}

    spec = c.registry.get("block.move")
    assert int(spec.safety) == 1                       # R1：可逆，不需要 HITL 弹窗
    result = c.registry.execute("block.move",
                                {"action": "move", "dx": -2, "dy": 0}, c.gate)
    assert result.ok and result.data["x"] == 3
    st = c.hardware.read_state("block").properties
    assert (st["x"], st["y"]) == (3, 5)

    n = c.journal.rollback_all()
    assert n == 1
    st = c.hardware.read_state("block").properties
    assert (st["x"], st["y"]) == (5, 5)                # 回滚恢复快照


def test_block_boundary_clamped(make_container):
    """网格边界钳位：越界指令被硬件层收敛（确定性安全）。"""
    from voiceops.adapters.event_bus import FakeClock, AutoConfirm
    from voiceops.app import build_container

    c = build_container(llm=RuleLLM(), interaction=AutoConfirm(True),
                        clock=FakeClock())
    c.registry.execute("block.move",
                       {"action": "move", "dx": -50, "dy": 0}, c.gate)
    st = c.hardware.read_state("block").properties
    assert st["x"] == 0                                # 钳到边界，不会越界
    c.registry.execute("block.move",
                       {"action": "move_to", "x": 99, "y": 99}, c.gate)
    st = c.hardware.read_state("block").properties
    assert (st["x"], st["y"]) == (9, 9)


def test_websocket_text_command_moves_block():
    """端到端：WS 文本指令 → 编排 → board 事件（与语音同一命令路径）。"""
    from fastapi.testclient import TestClient

    from voiceops.webapp import build_web_container, create_app

    container, volc = build_web_container()
    assert volc is not None or True
    app = create_app(container, None)                  # None = 无火山凭据（文字模式）

    with TestClient(app) as client:
        with client.websocket_connect("/ws") as ws:
            ws.send_json({"type": "text", "text": "向左移动两格"})
            board = ws.receive_json()
            assert board["type"] == "board" and (board["x"], board["y"]) == (3, 5)
            reply = ws.receive_json()
            assert reply["type"] == "agent_reply"
            assert "(3, 5)" in reply["text"]

            ws.send_json({"type": "text", "text": "移到右上角"})
            board = ws.receive_json()
            assert (board["x"], board["y"]) == (9, 0)
            # 情节记忆沉淀（编排闭环照常工作）
            assert container.events("memory.saved")
