"""Orchestrator：LangGraph 有状态多智能体编排（核心域）。

图结构（有状态、支持分支与自我修正）：

  START → plan ──→ execute ──┬─ 任务成功且还有任务 → execute（下一任务）
                             ├─ 任务成功且无任务   → respond → END
                             └─ 任务失败           → reflect ──┬─ retry/revise → execute
                                                              └─ abort → respond → END

工程要点（状态丢失 / 非确定性行为 / 死锁的对策）：
- 状态集中在 AgentState（TypedDict），配合 LangGraph checkpointer 持久化
  —— 长链路任务崩溃后可从检查点恢复（M2 有状态编排）。
- 循环防护是**结构性**的：
    a) steps 全局步数预算（max_steps）；
    b) loop guard：同一 (tool, args) 签名出现 ≥3 次强制判失败（无限循环检测）；
    c) retry_counts 限制每个任务的反思重试 ≤ 2 次。
- ReAct 在 execute 节点内部循环；Reflector 的产物（revise 的补救任务）
  会插回计划流 —— DAG/Cyclic 混合图的最小可验证样本。
- 唯一的框架依赖（langgraph）被隔离在本模块；
  Planner/Executor/Reflector 的提示与解析逻辑不依赖框架 ——
  "换编排框架 = 重写本文件的接线"。
"""

from __future__ import annotations

from typing import Any

from langgraph.graph import END, START, StateGraph

from voiceops.ports import (
    EventPublisher,
    LLMMessage,
    LLMProvider,
    UserInteraction,
    make_event,
)

from .memory import MemoryService
from .models import AgentState, Plan, Task, ToolResult
from .safety import SafetyGate
from .tools import ToolRegistry

FINAL_PREFIX = "FINAL:"
FAIL_PREFIX = "FAIL:"
MAX_TASK_RETRIES = 2
LOOP_GUARD_REPEATS = 3


class OrchestratorDeps:
    """核心域的依赖包（由 app.py 装配器注入）——显式依赖，构造期可见。"""

    def __init__(
        self,
        llm: LLMProvider,
        registry: ToolRegistry,
        gate: SafetyGate,
        memory: MemoryService,
        bus: EventPublisher,
        interaction: UserInteraction,
        max_steps: int = 24,
    ) -> None:
        self.llm = llm
        self.registry = registry
        self.gate = gate
        self.memory = memory
        self.bus = bus
        self.interaction = interaction
        self.max_steps = max_steps


def _emit(deps: OrchestratorDeps, state: AgentState, name: str, payload: dict) -> None:
    deps.bus.publish(
        make_event(name, payload, ts=0.0, trace_id=state.get("trace_id", ""))
    )


# ---------------------------------------------------------------------------
# 节点实现
# ---------------------------------------------------------------------------


def plan_node(deps: OrchestratorDeps, state: AgentState) -> dict[str, Any]:
    user_input = state["user_input"]
    # 记忆注入：检索相关记忆并带来源标注放入上下文（防漂移：可溯源）
    hits = deps.memory.search(user_input, limit=3)
    memory_ctx = [f"[{r.kind}@{int(r.created_at)}] {r.text}" for r, _ in hits]

    resp = deps.llm.complete(
        [
            LLMMessage(
                role="system",
                content="你是 Planner：把用户意图分解为最小步骤任务列表。",
            ),
            LLMMessage(role="user", content=user_input),
        ],
        structured="plan",
    )
    plan_data = resp.structured or {"tasks": []}
    plan = Plan(
        rationale=plan_data.get("rationale", ""),
        tasks=[Task(**t) for t in plan_data.get("tasks", [])],
    )
    _emit(
        deps,
        state,
        "plan.created",
        {"tasks": [t.description for t in plan.tasks], "memory_ctx": memory_ctx},
    )
    return {
        "plan": [t.__dict__ for t in plan.tasks],
        "task_idx": 0,
        "memory_context": memory_ctx,
        "retry_counts": {},
        "steps": 0,
        "tool_trace": state.get("tool_trace", []),
        "failure_reason": "",
    }


def _run_react(
    deps: OrchestratorDeps, state: AgentState, task: dict
) -> tuple[str, list[str]]:
    """Executor：节点内 ReAct 循环。返回 (status, observations)。"""
    observations: list[str] = []
    signatures: list[tuple[str, str]] = []
    tools_schema = deps.registry.llm_schemas()

    while True:
        state["steps"] += 1
        # 防护 a：全局步数预算
        if state["steps"] > deps.max_steps:
            return "failed", observations + ["step budget exceeded"]
        messages = [
            LLMMessage(
                role="system",
                content="你是 Executor。用工具完成任务；完成后回复 FINAL: <结果>；"
                "无法完成回复 FAIL: <原因>。可用记忆上下文："
                + " | ".join(state.get("memory_context", [])),
            ),
            LLMMessage(role="user", content=f"当前任务: {task['description']}"),
        ]
        for obs in observations:
            messages.append(LLMMessage(role="tool", content=obs))
        resp = deps.llm.complete(messages, tools=tools_schema)

        if not resp.tool_calls:
            content = resp.content or ""
            if content.startswith(FINAL_PREFIX):
                observations.append(content)
                return "ok", observations
            if content.startswith(FAIL_PREFIX):
                observations.append(content)
                return "failed", observations
            # 无工具且无终止符：视为完成
            return "ok", observations + [content]

        for call in resp.tool_calls:
            # 防护 b：无限循环检测（同签名 ≥3 次强制失败）
            sig = (call.name, repr(sorted(call.arguments.items())))
            signatures.append(sig)
            if signatures.count(sig) >= LOOP_GUARD_REPEATS:
                _emit(deps, state, "loop.guard", {"tool": call.name})
                observations.append(f"[loop guard] {call.name} 重复执行，强制终止")
                return "failed", observations

            result: ToolResult = deps.registry.execute(
                call.name, call.arguments, deps.gate, trace_id=state.get("trace_id", "")
            )
            if result.ok:
                state.setdefault("tool_trace", []).append(
                    {"tool": call.name, "args": call.arguments}
                )
                obs = f"{call.name} -> ok: {result.data}"
            else:
                obs = f"{call.name} -> error: {result.error} (denied={result.denied})"
            observations.append(obs)
            state["observations"] = observations
            if not result.ok:
                # 工具失败即观察反馈给 LLM；是否 FAIL 由下一轮 LLM 决定
                continue


def execute_node(deps: OrchestratorDeps, state: AgentState) -> dict[str, Any]:
    plan = state["plan"]
    idx = state["task_idx"]
    if idx >= len(plan):
        return {"task_status": "ok"}
    task = plan[idx]
    _emit(deps, state, "task.started", {"task": task["description"]})
    status, observations = _run_react(deps, state, task)
    _emit(deps, state, "task." + status, {"task": task["description"]})
    return {
        "task_status": status,
        "observations": observations,
        "steps": state["steps"],
        "tool_trace": state.get("tool_trace", []),
    }


def reflect_node(deps: OrchestratorDeps, state: AgentState) -> dict[str, Any]:
    """Reflector：根因分析 → 重试 / 修订计划（插入补救任务）/ 放弃。"""
    task = state["plan"][state["task_idx"]]
    resp = deps.llm.complete(
        [
            LLMMessage(
                role="system",
                content="你是 Reflector。基于失败观察决定：retry（原样重试）/"
                "revise（给出新的补救任务）/abort。",
            ),
            LLMMessage(
                role="user",
                content=f"任务: {task['description']} 失败。观察: "
                + " | ".join(state.get("observations", [])),
            ),
        ],
        structured="reflect",
    )
    decision = resp.structured or {"decision": "abort", "reason": "no reflection"}

    counts = state.get("retry_counts", {})
    task_id = task["id"]
    if decision.get("decision") == "revise" and decision.get("new_task"):
        counts[task_id] = counts.get(task_id, 0) + 1
        if counts[task_id] > MAX_TASK_RETRIES:
            return {"failure_reason": "retries exhausted", "retry_counts": counts}
        new_task = decision["new_task"]
        plan = list(state["plan"])
        plan.insert(state["task_idx"] + 1, new_task)  # 补救任务插队
        _emit(deps, state, "plan.revised", {"insert": new_task["description"]})
        return {
            "plan": plan,
            "task_idx": state["task_idx"] + 1,
            "task_status": "",
            "retry_counts": counts,
            "observations": [],
            "failure_reason": "",
        }
    if decision.get("decision") == "retry":
        counts[task_id] = counts.get(task_id, 0) + 1
        if counts[task_id] > MAX_TASK_RETRIES:
            return {"failure_reason": "retries exhausted", "retry_counts": counts}
        _emit(deps, state, "task.retry", {"task": task["description"]})
        return {
            "task_status": "",
            "retry_counts": counts,
            "observations": [],
            "failure_reason": "",
        }
    return {"failure_reason": decision.get("reason", "aborted by reflector")}


def respond_node(deps: OrchestratorDeps, state: AgentState) -> dict[str, Any]:
    """收尾：生成最终答复 + 记忆沉淀（情节写入 + 程序记忆自动提炼）。"""
    if state.get("failure_reason"):
        final = f"任务未完成: {state['failure_reason']}"
    else:
        final = "全部任务完成。"
        for obs in state.get("observations", []):
            if obs.startswith(FINAL_PREFIX):
                final = obs[len(FINAL_PREFIX) :].strip()
    # 情节记忆：会话摘要沉淀
    deps.memory.record_episode(
        text=f"{state['user_input']} -> {final}",
        tags=[t["tool"] for t in state.get("tool_trace", [])],
        importance=0.5 if not state.get("failure_reason") else 0.7,
    )
    # 程序记忆：成功工具序列自动提炼
    if not state.get("failure_reason"):
        seq = [t["tool"] for t in state.get("tool_trace", [])]
        deps.memory.observe_tool_sequence(seq)
    return {"final": final}


# ---------------------------------------------------------------------------
# 图装配
# ---------------------------------------------------------------------------


def _route_after_execute(state: AgentState) -> str:
    if state.get("task_status") == "failed":
        return "reflect"
    if state["task_idx"] + 1 < len(state["plan"]):
        return "next_task"
    return "respond"


def _route_after_reflect(state: AgentState) -> str:
    if state.get("failure_reason"):
        return "respond"
    return "continue"


def build_graph(deps: OrchestratorDeps, checkpointer=None):
    """装配并编译图。checkpointer 由外部注入（MemorySaver/SqliteSaver）。"""
    g = StateGraph(AgentState)
    g.add_node("plan", lambda s: plan_node(deps, s))
    g.add_node("execute", lambda s: execute_node(deps, s))
    g.add_node("reflect", lambda s: reflect_node(deps, s))
    g.add_node("respond", lambda s: respond_node(deps, s))

    g.add_edge(START, "plan")
    g.add_edge("plan", "execute")

    # 任务推进走 advance 节点（task_idx+1 并清空观察），保持图为显式状态机
    def advance_node(state: AgentState) -> dict[str, Any]:
        return {
            "task_idx": state["task_idx"] + 1,
            "task_status": "",
            "observations": [],
        }

    g.add_node("advance", advance_node)
    g.add_conditional_edges(
        "execute",
        _route_after_execute,
        {"next_task": "advance", "reflect": "reflect", "respond": "respond"},
    )
    g.add_edge("advance", "execute")
    g.add_conditional_edges(
        "reflect", _route_after_reflect, {"continue": "execute", "respond": "respond"}
    )
    g.add_edge("respond", END)
    return g.compile(checkpointer=checkpointer)


class Orchestrator:
    """核心域对外的会话入口：一次 run = 一条用户指令的完整编排。"""

    def __init__(self, deps: OrchestratorDeps, checkpointer=None) -> None:
        self.deps = deps
        self._app = build_graph(deps, checkpointer)

    def run(
        self, user_input: str, trace_id: str = "t0", thread_id: str = "main"
    ) -> dict:
        # 运行期字段显式重置：checkpointer 的线程状态跨 run 持久化，
        # 不重置会污染下一次执行的轨迹与计数（状态所有权的清晰边界）
        state: AgentState = {
            "user_input": user_input,
            "trace_id": trace_id,
            "tool_trace": [],
            "steps": 0,
            "retry_counts": {},
            "failure_reason": "",
            "observations": [],
            "task_status": "",
            "task_idx": 0,
        }
        result = self._app.invoke(
            state, config={"configurable": {"thread_id": thread_id}}
        )
        return {
            "final": result.get("final", ""),
            "tool_trace": result.get("tool_trace", []),
            "failure_reason": result.get("failure_reason", ""),
        }
