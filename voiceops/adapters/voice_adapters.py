"""语音适配器：ConsoleVoicePort（文本模式）+ VolcanoVoiceAdapter（火山引擎，已实现）。

要点：两者实现同一 VoiceGateway 契约 ——
"语音只是可替换的外围适配器"不是口号，是 import 就能验证的事实。
火山引擎协议实现见 volcano_protocol.py（二进制帧编解码）与
volcano_voice.py（STT/TTS 会话）。
"""
from __future__ import annotations

from typing import Callable

from voiceops.adapters.volcano_voice import (  # noqa: F401 —— 对外统一出口
    VolcanoConfig,
    VolcanoVoiceAdapter,
)
from voiceops.ports import VoiceEvent


class ConsoleVoicePort:
    """文本模式：把"用户输入一行文本"模拟为 final_transcript 事件，
    speak() 退化为 print —— 核心域与语音链路解耦的最直观证明。"""

    def __init__(self, on_transcript: Callable[[VoiceEvent], None] | None = None) -> None:
        self._handler = on_transcript

    def start_session(self, handler: Callable[[VoiceEvent], None]) -> None:
        self._handler = handler

    def feed_text(self, text: str) -> None:
        """文本模式下由 CLI 调用：等价于真实适配器里 ASR 产出 final_transcript。"""
        if self._handler:
            self._handler(VoiceEvent(type="final_transcript", data={"text": text}))
            self._handler(VoiceEvent(type="utterance_end"))

    def send_audio(self, frame: bytes) -> None:
        raise NotImplementedError("ConsoleVoicePort 不处理音频；请接入真实语音适配器")

    def speak(self, text: str) -> None:
        print(f"[Agent] {text}")

    def interrupt(self) -> None:
        pass

    def end_session(self) -> None:
        pass
