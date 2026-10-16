"""LLM Port。

设计理念：
- 核心域（Planner/Executor/Reflector）只依赖本 Protocol。
- FakeLLM（脚本化回放）与 AzureOpenAIAdapter / 未来的火山方舟 Adapter 等价可换，
  因此整条编排逻辑可以离线、确定性、毫秒级地测试 —— 这是本项目可测试性论证的根基。
- structured 参数约定了结构化输出场景（plan / reflect），
  由适配器负责映射到各家 API（JSON mode / function calling）。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable


@dataclass
class ToolCall:
    """LLM 请求执行的一次工具调用。"""
    name: str
    arguments: dict[str, Any] = field(default_factory=dict)
    call_id: str = ""


@dataclass
class LLMResponse:
    content: str | None = None          # 自由文本；PoC 约定 "FINAL: ..." / "FAIL: ..."
    tool_calls: list[ToolCall] = field(default_factory=list)
    structured: dict[str, Any] | None = None  # structured 输出（plan / reflect）


@dataclass
class LLMMessage:
    role: str            # system / user / assistant / tool
    content: str
    name: str = ""       # tool 消息对应的工具名


@runtime_checkable
class LLMProvider(Protocol):
    def complete(
        self,
        messages: list[LLMMessage],
        *,
        tools: list[dict[str, Any]] | None = None,
        structured: str | None = None,
    ) -> LLMResponse: ...
