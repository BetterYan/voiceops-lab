"""Web Demo 后端：浏览器麦克风 → FastAPI WebSocket → voiceops 编排 → 方块移动。

链路：
  浏览器(PCM 16k) --ws binary--> VolcanoVoiceAdapter(STT) --final_transcript-->
  Orchestrator(RuleLLM/AzureLLM) --block.move(R1+journal)--> SimulatedHardware
  --board 事件--> 浏览器渲染；Agent 回复 --> TTS --ws binary--> 浏览器播放

无 VOLC_API_KEY 时自动降级：文字输入指令仍然可用（跳过 STT/TTS），
全链路的编排/安全/记忆逻辑不变。
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles

from voiceops.adapters.volcano_voice import VolcanoConfig, VolcanoVoiceAdapter
from voiceops.app import Container, build_container

WEB_DIR = Path(__file__).resolve().parent / "web"


def build_web_container() -> tuple[Container, VolcanoConfig | None]:
    """Web Demo 装配：默认 RuleLLM（离线可跑）；有 Azure 凭据自动升级真 LLM。"""
    import os

    from voiceops.adapters.azure_llm import AzureOpenAIAdapter
    from voiceops.adapters.event_bus import AutoConfirm
    from voiceops.adapters.rule_llm import RuleLLM

    if os.environ.get("AZURE_OPENAI_ENDPOINT"):
        llm = AzureOpenAIAdapter()
    else:
        llm = RuleLLM()
    container = build_container(llm=llm, interaction=AutoConfirm(True))
    volc = VolcanoConfig.from_env()          # .env / 环境变量均可
    return container, volc


def create_app(container: Container,
               volc_config: VolcanoConfig | None = None) -> FastAPI:
    app = FastAPI(title="VoiceOps Block Demo")
    state: dict[str, Any] = {"volc": volc_config if (volc_config
                             and volc_config.api_key) else None}

    @app.get("/api/config")
    async def config() -> dict:
        """前端能力协商：语音是否可用（决定是否展示麦克风按钮）。"""
        return {"voice_enabled": state["volc"] is not None}

    @app.websocket("/ws")
    async def ws_endpoint(ws: WebSocket) -> None:
        await ws.accept()
        session = BoardSession(ws, container, state["volc"])
        try:
            await session.run()
        except WebSocketDisconnect:
            pass
        finally:
            await session.close()

    app.mount("/", StaticFiles(directory=str(WEB_DIR), html=True), name="web")
    return app


class BoardSession:
    """一个浏览器连接 = 一个会话：语音事件泵 + 指令处理 + TTS 回传。"""

    def __init__(self, ws: WebSocket, container: Container,
                 volc_config: VolcanoConfig | None) -> None:
        self.ws = ws
        self.c = container
        self.loop = asyncio.get_running_loop()
        self.voice: VolcanoVoiceAdapter | None = None
        self._events: asyncio.Queue = asyncio.Queue()
        if volc_config is not None:
            self.voice = VolcanoVoiceAdapter(volc_config)

    # ---- 语音回调（火山引擎线程 → 事件循环） ---------------------------------

    def _on_voice_event(self, event) -> None:
        self.loop.call_soon_threadsafe(self._events.put_nowait, event)

    # ---- 主循环 ---------------------------------------------------------------

    async def run(self) -> None:
        if self.voice is not None:
            self.voice.start_session(self._on_voice_event)
            await self.send({"type": "voice_ready"})
        pump = asyncio.create_task(self._pump_voice_events())
        try:
            while True:
                msg = await self.ws.receive()
                if msg["type"] == "websocket.disconnect":
                    return
                if msg.get("bytes") is not None:
                    if self.voice is not None:
                        self.voice.send_audio(msg["bytes"])   # 16k/16bit/mono PCM
                elif msg.get("text"):
                    data = json.loads(msg["text"])
                    if data.get("type") == "text":
                        # 文字输入直通编排（与语音同一命令路径）
                        await self.handle_command(data.get("text", ""))
                    elif data.get("type") == "stop":
                        break
        finally:
            pump.cancel()

    async def close(self) -> None:
        if self.voice is not None:
            try:
                await asyncio.to_thread(self.voice.end_session)
            except Exception:      # noqa: BLE001 —— 会话收尾不阻塞断开
                pass

    # ---- 语音事件泵：转写 → 编排 ----------------------------------------------

    async def _pump_voice_events(self) -> None:
        while True:
            event = await self._events.get()
            if event.type == "partial_transcript":
                await self.send({"type": "transcript",
                                 "text": event.data.get("text", ""),
                                 "final": False})
            elif event.type == "final_transcript":
                text = event.data.get("text", "")
                await self.send({"type": "transcript", "text": text, "final": True})
                await self.handle_command(text)
            elif event.type == "error":
                await self.send({"type": "error", "data": event.data})

    # ---- 指令 → 编排 → 状态/TTS 回传 ------------------------------------------

    async def handle_command(self, text: str) -> None:
        text = text.strip()
        if not text:
            return
        result = await asyncio.to_thread(
            self.c.orchestrator.run, text, "web", "web-session")
        block = self.c.hardware.read_state("block")
        await self.send({"type": "board",
                         "x": block.properties["x"], "y": block.properties["y"]})
        await self.send({"type": "agent_reply", "text": result["final"]})
        # TTS：音频字节直接回传浏览器播放（无凭据则跳过）
        if self.voice is not None and result["final"]:
            audio = await asyncio.to_thread(self._tts_bytes, result["final"])
            if audio:
                await self.ws.send_bytes(audio)

    def _tts_bytes(self, text: str) -> bytes | None:
        buf = bytearray()
        original = self.voice.audio_player
        self.voice.audio_player = lambda a, _fmt: buf.extend(a)
        try:
            self.voice.speak(text)
        finally:
            self.voice.audio_player = original
        return bytes(buf) or None

    async def send(self, payload: dict) -> None:
        await self.ws.send_text(json.dumps(payload, ensure_ascii=False))
