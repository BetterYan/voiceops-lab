"""ScriptedLLM：脚本化 LLM 适配器（可测试性的基石）。

设计理念：
- 与 AzureOpenAIAdapter 实现同一 LLMProvider 契约，但响应来自脚本队列 ——
  编排/安全/记忆逻辑的全部测试与演示因此**离线、确定性、毫秒级**。
- 脚本项可以是 LLMResponse，也可以是 (messages) => LLMResponse 的闭包，
  便于按输入内容动态应答。
- 若脚本耗尽仍被调用 → 抛错（测试里的"意外 LLM 调用"立即暴露，而非静默漂移）。
"""
from __future__ import annotations

import itertools

from voiceops.ports.llm import LLMMessage, LLMResponse, ToolCall

ResponseFactory = "LLMResponse | (list[LLMMessage]) -> LLMResponse"

_id_counter = itertools.count(1)


def plan(tasks: list[dict], rationale: str = "") -> LLMResponse:
    """Planner 结构化输出。tasks: [{"id","description","tool_hint"}]"""
    return LLMResponse(structured={"rationale": rationale, "tasks": tasks})


def call_tool(name: str, **arguments) -> LLMResponse:
    """Executor 的 ReAct 一步：请求工具调用。"""
    return LLMResponse(tool_calls=[ToolCall(name=name, arguments=arguments,
                                            call_id=f"c{next(_id_counter)}")])


def call_tools(*calls: tuple[str, dict]) -> LLMResponse:
    return LLMResponse(tool_calls=[ToolCall(name=n, arguments=a,
                                            call_id=f"c{next(_id_counter)}")
                                   for n, a in calls])


def finish(text: str) -> LLMResponse:
    return LLMResponse(content=f"FINAL: {text}")


def fail(text: str) -> LLMResponse:
    return LLMResponse(content=f"FAIL: {text}")


def reflect(decision: str, reason: str = "", new_task: dict | None = None) -> LLMResponse:
    data: dict = {"decision": decision, "reason": reason}
    if new_task:
        data["new_task"] = new_task
    return LLMResponse(structured=data)


class ScriptedLLM:
    def __init__(self, script: list) -> None:
        self._script: list = list(script)
        self.calls: list[list[LLMMessage]] = []   # 记录收到的提示（可断言）

    def complete(self, messages: list[LLMMessage], *, tools=None,
                 structured: str | None = None) -> LLMResponse:
        self.calls.append(list(messages))
        if not self._script:
            raise AssertionError("ScriptedLLM: 脚本耗尽 —— 出现了未预期的 LLM 调用")
        item = self._script.pop(0)
        if callable(item):
            return item(messages)
        return item
