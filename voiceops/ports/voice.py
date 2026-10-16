"""语音网关 Port —— 本项目"只做接口设计"的模块。

设计理念（JD：硬件端 → 实时语音 → 系统自动化）：
- 核心域永远只面对文本事件与 speak()/interrupt() 两个动作，
  音频流、ASR/TTS 引擎、连接管理全部是适配器细节。
- 事件模型按流式语义设计：partial/final 转写、utterance_end、interrupt、
  tts_start/tts_end —— 任何全双工语音引擎（火山引擎流式 ASR/TTS、
  Azure Speech、端到端 Realtime 模型）都能映射到这套事件上。
- 因此"接入火山引擎"= 新增一个 VolcanoVoiceAdapter，核心域零改动。

火山引擎对接路线（详见 docs/design.html）：
  1. WebSocket 二进制帧（PCM/Opus）→ send_audio 分帧回调
  2. 引擎增量转写 JSON → VoiceEvent(partial_transcript / final_transcript)
  3. VAD 句尾检测 → VoiceEvent(utterance_end) 触发编排
  4. TTS 音频下行流 → speak() 的流式回调；用户开口 → interrupt() 打断合成
  5. 鉴权/断线重连/心跳在适配器内部闭环
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Protocol, runtime_checkable


@dataclass
class VoiceEvent:
    type: str        # partial_transcript / final_transcript / utterance_end /
                     # interrupt / tts_start / tts_end / error
    data: dict[str, Any] = field(default_factory=dict)


VoiceEventHandler = Callable[[VoiceEvent], None]


@runtime_checkable
class VoiceGateway(Protocol):
    def start_session(self, handler: VoiceEventHandler) -> None: ...

    def send_audio(self, frame: bytes) -> None:
        """流入一帧音频（PCM 16k/Opus），适配器负责转写并回调事件。"""
        ...

    def speak(self, text: str) -> None:
        """TTS 播报（流式）；用户开口时适配器应触发 interrupt 事件。"""
        ...

    def interrupt(self) -> None:
        """打断当前播报。"""
        ...

    def end_session(self) -> None: ...
