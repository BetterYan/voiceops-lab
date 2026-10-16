"""DI 装配器：把 Port 与 Adapter 按配置装配成可运行的容器。

设计理念：
- 依赖在**构造期**显式装配（穷人版 DI 容器，零框架）——
  依赖图一眼可见，测试直接构造 Container 换适配器。
- 换实现 = 改一行装配代码（例如 voice: console→volcano，llm: fake→azure）。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from langgraph.checkpoint.memory import MemorySaver

from voiceops.adapters.event_bus import (
    AutoConfirm,
    InProcessEventBus,
    SystemClock,
)
from voiceops.adapters.memory_store import SqliteVectorMemory
from voiceops.adapters.simulated_hardware import SimulatedHardware
from voiceops.core.memory import MemoryService
from voiceops.core.orchestrator import Orchestrator, OrchestratorDeps
from voiceops.core.safety import Journal, SafetyGate
from voiceops.core.tools import (
    ToolRegistry,
    make_block_tools,
    make_hardware_tools,
    make_memory_tools,
)
from voiceops.ports import Clock, EventPublisher, LLMProvider, UserInteraction


@dataclass
class Container:
    bus: EventPublisher
    clock: Clock
    hardware: SimulatedHardware
    memory: MemoryService
    store: SqliteVectorMemory
    interaction: UserInteraction
    journal: Journal
    gate: SafetyGate
    registry: ToolRegistry
    llm: LLMProvider
    orchestrator: Orchestrator
    skills_dir: str = ""

    # 便捷断言/检查入口
    def events(self, name: str) -> list[dict]:
        return [e for e in self.bus.all_events if e["name"] == name]


def build_container(
    llm: LLMProvider | None = None,
    interaction: UserInteraction | None = None,
    clock: Clock | None = None,
    memory_path: str = ":memory:",
    checkpointer=None,
    skills_dir: str | None = None,
    thread_safe_confirm: bool = True,
) -> Container:
    bus = InProcessEventBus()
    clock = clock or SystemClock()
    hardware = SimulatedHardware(clock)
    hardware.subscribe_events(lambda e: bus.publish(
        {"name": "hardware.event", "payload": e, "ts": clock.now(), "trace_id": ""}))

    store = SqliteVectorMemory(memory_path)
    memory = MemoryService(store, clock, bus)
    journal = Journal()
    interaction = interaction or AutoConfirm(True)
    gate = SafetyGate(interaction, journal, bus, clock.now)

    registry = ToolRegistry()
    for spec in make_hardware_tools(hardware):
        registry.register(spec)
    for spec in make_memory_tools(memory):
        registry.register(spec)
    for spec in make_block_tools(hardware):
        registry.register(spec)

    if llm is None:
        raise ValueError("必须提供 LLMProvider（ScriptedLLM 或 AzureOpenAIAdapter）——"
                         "本项目不内置任何'哑响应'，LLM 是显式注入的依赖")

    deps = OrchestratorDeps(llm=llm, registry=registry, gate=gate, memory=memory,
                            bus=bus, interaction=interaction)
    orchestrator = Orchestrator(deps, checkpointer=checkpointer or MemorySaver())

    root = Path(__file__).parent
    return Container(bus=bus, clock=clock, hardware=hardware, memory=memory,
                     store=store, interaction=interaction, journal=journal,
                     gate=gate, registry=registry, llm=llm, orchestrator=orchestrator,
                     skills_dir=skills_dir or str(root / "skills_md"))
