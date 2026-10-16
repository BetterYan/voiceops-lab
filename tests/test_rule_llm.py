"""RuleLLM 指令解析测试（离线 Demo 大脑）。"""
from __future__ import annotations

from voiceops.adapters.rule_llm import RuleLLM
from voiceops.ports import LLMMessage


def _react_call(utterance: str):
    resp = RuleLLM().complete([
        LLMMessage(role="system", content="sys"),
        LLMMessage(role="user", content=f"当前任务: {utterance}"),
    ])
    assert resp.tool_calls, f"应产出工具调用: {utterance}"
    call = resp.tool_calls[0]
    return call.name, call.arguments


def test_direction_with_cn_number():
    name, args = _react_call("向左移动两格")
    assert name == "block.move"
    assert args == {"action": "move", "dx": -2, "dy": 0}


def test_direction_with_arabic_number():
    _, args = _react_call("向下移3格")
    assert args["dy"] == 3 and args["dx"] == 0


def test_direction_single_word_defaults_one_step():
    _, args = _react_call("右")
    assert args["dx"] == 1 and args["dy"] == 0


def test_corner_absolute_move():
    _, args = _react_call("移到右上角")
    assert args == {"action": "move_to", "x": 9, "y": 0}


def test_back_to_center():
    _, args = _react_call("回到中心")
    assert args == {"action": "move_to", "x": 5, "y": 5}


def test_unrecognized_gets_friendly_final():
    resp = RuleLLM().complete([
        LLMMessage(role="user", content="当前任务: 讲个笑话")])
    assert resp.content and resp.content.startswith("FINAL: 没听懂")


def test_plan_and_reflect_structured():
    llm = RuleLLM()
    plan = llm.complete([LLMMessage(role="user", content="向左移动两格")],
                        structured="plan")
    assert plan.structured["tasks"][0]["tool_hint"] == "block.move"
    reflect = llm.complete([], structured="reflect")
    assert reflect.structured["decision"] == "abort"
