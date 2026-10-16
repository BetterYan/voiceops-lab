"""RuleLLM：离线规则 LLM 适配器（网页 Demo 的默认"大脑"）。

设计理念：
- 实现同一 LLMProvider 契约 —— 与 ScriptedLLM/AzureOpenAIAdapter 可互换，
  Web Demo 因此**不需要任何 API Key 就能跑通"语音 → 编排 → 方块移动"全链路**。
- 只覆盖演示语法（方向/步数/角点/中心）；生产接 Azure OpenAI 等真 LLM
  （webapp 装配器在检测到 AZURE_OPENAI_ENDPOINT 时自动切换），核心域无感。
"""
from __future__ import annotations

import ast
import re

from voiceops.ports import LLMMessage, LLMResponse, ToolCall

_CN_NUM = {"一": 1, "两": 2, "二": 2, "三": 3, "四": 4, "五": 5,
           "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}
_DIRS = {"左": (-1, 0), "右": (1, 0), "上": (0, -1), "下": (0, 1)}
_CORNERS = {"左上": (0, 0), "右上": (9, 0), "左下": (0, 9), "右下": (9, 9)}

_DIDNT_GET = ("FINAL: 没听懂这个指令。可以试试：向左移动两格 / 向下移三格 / "
              "移到右上角 / 回到中心。")


def _steps(text: str, default: int = 1) -> int:
    m = re.search(r"[一二两三四五六七八九十\d]+", text)
    if not m:
        return default
    token = m.group()
    return _CN_NUM.get(token) or (int(token) if token.isdigit() else default)


class RuleLLM:
    def complete(self, messages: list[LLMMessage], *, tools=None,
                 structured: str | None = None) -> LLMResponse:
        user_texts = [m.content for m in messages if m.role == "user"]
        observations = [m.content for m in messages if m.role == "tool"]

        if structured == "plan":
            return LLMResponse(structured={"rationale": "rule-based",
                "tasks": [{"id": "t1", "description": user_texts[-1],
                           "tool_hint": "block.move"}]})

        if structured == "reflect":
            return LLMResponse(structured={"decision": "abort",
                                           "reason": "规则模式不重试"})

        # ReAct：工具已有观察 → 从结果取新位置收尾
        if observations:
            m = re.search(r"ok: (\{.*\})", observations[-1])
            # 观察串中的 dict 是 Python repr（单引号），用 literal_eval 解析
            data = ast.literal_eval(m.group(1)) if m else {}
            if data.get("ok") is False:
                return LLMResponse(content="FAIL: 移动失败")
            return LLMResponse(
                content=f"FINAL: 好的，方块已移动到 "
                        f"({data.get('x', '?')}, {data.get('y', '?')})。")

        # ReAct：把自然语言指令编译为工具调用
        task = next((t.split("当前任务:", 1)[1].strip()
                     for t in reversed(user_texts) if "当前任务:" in t), "")
        parsed = self._parse(task)
        if parsed is None:
            return LLMResponse(content=_DIDNT_GET)
        name, args = parsed
        return LLMResponse(tool_calls=[ToolCall(name=name, arguments=args,
                                                call_id="rule-1")])

    @staticmethod
    def _parse(text: str) -> tuple[str, dict] | None:
        text = text.replace(" ", "")
        if re.search(r"回到?(中心|中间|原点|初始位)", text):
            return "block.move", {"action": "move_to", "x": 5, "y": 5}
        for corner, (x, y) in _CORNERS.items():
            if corner in text and "角" in text:
                return "block.move", {"action": "move_to", "x": x, "y": y}
        for word, (dx, dy) in _DIRS.items():
            if word in text:
                steps = _steps(text.split(word, 1)[1]) if len(text) > 1 else 1
                return "block.move", {"action": "move",
                                      "dx": dx * steps, "dy": dy * steps}
        return None
