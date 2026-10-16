"""核心域模型：安全分级、工具契约、Journal、计划与图状态。

设计理念：
- SafetyLevel 是**确定性**的枚举：哪些操作危险由注册表静态声明，
  绝不交给 LLM 现场判断 —— LLM 负责选工具，安全边界不由 LLM 定。
- ToolSpec 携带 JSON-Schema 子集契约（type/required/enum），入参先验证后执行，
  同一份契约同时服务 MCP 暴露与测试（契约测试）。
- JournalEntry 先记录意图、后补结果，undo 闭包在执行前构建 ——
  回滚能力是结构性的，而非事后补救。
"""
from __future__ import annotations

import enum
from dataclasses import dataclass, field
from typing import Any, Callable, TypedDict


class SafetyLevel(enum.IntEnum):
    """操作风险三级：确定性安全边界的基石。"""

    R0 = 0   # 只读：自动执行
    R1 = 1   # 可逆变更：自动执行 + 审计（journal 留痕 + 可撤销）
    R2 = 2   # 危险/不可逆：必须 Human-in-the-Loop 确认


@dataclass
class ToolSpec:
    """工具契约：名称、描述、安全级别、参数 schema、执行函数、回滚构建器。"""
    name: str
    description: str
    safety: SafetyLevel
    schema: dict[str, Any]                      # {"type":"object","required":[...],"properties":{...}}
    fn: Callable[..., dict[str, Any]]           # kwargs -> result dict
    undo_builder: Callable[..., Callable[[], None]] | None = None
    # undo_builder(args) 在 fn 执行**前**调用，返回"撤销本次操作"的闭包（或 None）

    def openai_style_schema(self) -> dict[str, Any]:
        """暴露给 LLM / MCP 的统一工具描述。"""
        return {
            "name": self.name,
            "description": self.description,
            "parameters": self.schema,
            "safety_level": int(self.safety),
        }


@dataclass
class ToolResult:
    ok: bool
    data: dict[str, Any] = field(default_factory=dict)
    error: str = ""
    tool_name: str = ""
    denied: bool = False       # 被 SafetyGate 拦截（未执行）
    rolled_back: bool = False


@dataclass
class JournalEntry:
    """执行轨迹：先记录意图，执行后补结果。既是审计日志，也是回滚依据。"""
    seq: int
    ts: float
    tool: str
    args: dict[str, Any]
    level: SafetyLevel
    status: str = "intent"     # intent -> approved/denied -> ok/failed -> rolled_back
    result: dict[str, Any] = field(default_factory=dict)
    undo: Callable[[], None] | None = None
    trace_id: str = ""


@dataclass
class Task:
    """计划中的一步。tool_hint 是给 Executor 的软提示（真实 LLM 场景）。"""
    id: str
    description: str
    tool_hint: str = ""


@dataclass
class Plan:
    tasks: list[Task] = field(default_factory=list)
    rationale: str = ""


class AgentState(TypedDict, total=False):
    """LangGraph 图的全局状态（有状态的载体，可被 checkpointer 持久化）。"""
    user_input: str
    trace_id: str
    memory_context: list[str]      # Planner 注入的相关记忆（带来源标注）
    plan: list[dict]               # Task.asdict()
    task_idx: int
    task_status: str               # "ok" | "failed" | ""
    observations: list[str]        # 当前任务的工具执行观察
    retry_counts: dict[str, int]
    tool_trace: list[dict]         # 本次会话全部成功工具调用（程序记忆原料）
    steps: int
    final: str
    failure_reason: str
