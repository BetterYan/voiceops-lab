"""可选扩展：AzureOpenAIAdapter —— LLMProvider 的云端实现（按需启用）。

设计理念：云适配器被 import-guard 隔离 —— 未安装 openai 包时核心域
与测试完全不受影响；这正是 Ports & Adapters 的价值：
FakeLLM 与本类可互换，编排逻辑一行不改。
"""
from __future__ import annotations

import json
import os

from voiceops.ports import LLMMessage, LLMResponse, ToolCall


class AzureOpenAIAdapter:
    def __init__(self, deployment: str | None = None) -> None:
        try:
            from openai import AzureOpenAI  # 延迟导入：可选依赖
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError("需要 pip install openai 并配置 Azure 环境变量") from exc
        self._client = AzureOpenAI(
            api_key=os.environ["AZURE_OPENAI_API_KEY"],
            azure_endpoint=os.environ["AZURE_OPENAI_ENDPOINT"],
            api_version=os.environ.get("AZURE_OPENAI_API_VERSION", "2024-10-21"),
        )
        self._deployment = deployment or os.environ.get("AZURE_OPENAI_DEPLOYMENT", "gpt-4o")

    def complete(self, messages: list[LLMMessage], *, tools=None,
                 structured: str | None = None) -> LLMResponse:
        kwargs: dict = {
            "model": self._deployment,
            "messages": [{"role": m.role, "content": m.content} for m in messages],
        }
        if structured == "plan":
            kwargs["response_format"] = {"type": "json_object"}
        if tools:
            kwargs["tools"] = [{"type": "function",
                                "function": {"name": t["name"],
                                             "description": t["description"],
                                             "parameters": t["parameters"]}}
                               for t in tools]
        resp = self._client.chat.completions.create(**kwargs)
        msg = resp.choices[0].message
        out = LLMResponse(content=msg.content)
        if msg.tool_calls:
            out.tool_calls = [ToolCall(name=tc.function.name,
                                       arguments=json.loads(tc.function.arguments or "{}"),
                                       call_id=tc.id)
                              for tc in msg.tool_calls]
        if structured and msg.content:
            try:
                out.structured = json.loads(msg.content)
            except json.JSONDecodeError:
                out.structured = None
        return out
