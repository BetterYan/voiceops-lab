"""Ports：核心域唯一依赖的接口层。

六边形架构约束：core/* 不得 import adapters/* 或任何第三方 SDK，
只能 import 本包 —— 这是"换火山引擎/换 LLM/换硬件总线，核心域零改动"的结构性保证。
"""
from voiceops.ports.clock import Clock
from voiceops.ports.events import Event, EventPublisher, make_event
from voiceops.ports.hardware import (
    ActuationResult,
    DeviceState,
    HardwareError,
    HardwareTransport,
)
from voiceops.ports.interaction import UserInteraction
from voiceops.ports.llm import LLMMessage, LLMProvider, LLMResponse, ToolCall
from voiceops.ports.memory import MemoryRecord, MemoryStore
from voiceops.ports.voice import VoiceEvent, VoiceGateway, VoiceEventHandler

__all__ = [
    "Clock", "Event", "EventPublisher", "make_event",
    "ActuationResult", "DeviceState", "HardwareError", "HardwareTransport",
    "UserInteraction",
    "LLMMessage", "LLMProvider", "LLMResponse", "ToolCall",
    "MemoryRecord", "MemoryStore",
    "VoiceEvent", "VoiceGateway", "VoiceEventHandler",
]
