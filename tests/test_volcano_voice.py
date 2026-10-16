"""火山引擎语音适配器测试：协议编解码 + 假 WebSocket 服务器集成（全离线）。

不需要真实 VOLC_API_KEY —— 用本地 websockets 服务冒充火山端点，
验证：握手头、STT 帧协议往返、转写事件映射、TTS 事件序列与音频下行、打断。
"""
from __future__ import annotations

import asyncio
import gzip
import json
import struct
import threading
import time

import pytest

from voiceops.adapters import volcano_protocol as vp
from voiceops.adapters.volcano_voice import (
    VolcanoConfig,
    VolcanoVoiceAdapter,
)
from voiceops.ports import VoiceEvent


# ---------------------------------------------------------------------------
# 协议编解码单元测试
# ---------------------------------------------------------------------------

def test_full_request_roundtrip():
    payload = {"user": {"uid": "u1"}, "audio": {"format": "pcm", "rate": 16000}}
    frame = vp.build_full_request(payload)
    assert frame[0] == 0x11 and frame[3] == 0x00
    parsed = vp.parse_server_frame(frame)
    assert parsed.msg_type == vp.CLIENT_FULL_REQUEST
    assert parsed.payload_json == payload


def test_audio_request_last_packet_negative_seq():
    data = b"\x01\x02" * 160
    frame = vp.build_audio_request(data, seq=7, last=True)
    assert frame[1] == ((vp.CLIENT_AUDIO_ONLY_REQUEST << 4) | vp.FLAG_NEG_WITH_SEQ)
    assert struct.unpack(">i", frame[4:8])[0] == -7
    body = gzip.decompress(frame[12:])
    assert body == data


def test_server_frame_roundtrip_and_error_frame():
    payload = {"result": {"text": "你好"}}
    frame = vp.build_server_frame_for_test(vp.SERVER_FULL_RESPONSE, payload,
                                           seq=3, is_last=True)
    parsed = vp.parse_server_frame(frame)
    assert parsed.msg_type == vp.SERVER_FULL_RESPONSE
    assert parsed.is_last and parsed.seq == 3
    assert parsed.payload_json == payload

    err = vp.build_server_frame_for_test(vp.SERVER_ERROR_RESPONSE, {"message": "bad"},
                                         code=45000001)
    parsed_err = vp.parse_server_frame(err)
    assert parsed_err.msg_type == vp.SERVER_ERROR_RESPONSE
    assert parsed_err.code == 45000001
    assert parsed_err.payload_json == {"message": "bad"}


# ---------------------------------------------------------------------------
# 假 WebSocket 服务器（本地端口冒充火山端点）
# ---------------------------------------------------------------------------

class FakeVolcanoServer:
    def __init__(self, handler):
        import websockets
        self._handler = handler
        self.port: int | None = None
        self._loop = None
        self._stop = None
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        for _ in range(200):
            if self.port:
                return
            time.sleep(0.01)
        raise RuntimeError("fake server failed to start")

    def _run(self) -> None:
        import websockets

        async def main():
            self._stop = asyncio.Event()
            async with websockets.serve(self._handler, "127.0.0.1", 0) as server:
                self.port = server.sockets[0].getsockname()[1]
                await self._stop.wait()
        self._loop = asyncio.new_event_loop()
        self._loop.run_until_complete(main())

    def stop(self) -> None:
        if self._loop and self._stop:
            self._loop.call_soon_threadsafe(self._stop.set)
            self._thread.join(timeout=5)


class EventCollector:
    def __init__(self) -> None:
        self.events: list[VoiceEvent] = []
        self._lock = threading.Lock()

    def __call__(self, event: VoiceEvent) -> None:
        with self._lock:
            self.events.append(event)

    def wait_for(self, event_type: str, timeout: float = 5.0) -> VoiceEvent | None:
        deadline = time.time() + timeout
        while time.time() < deadline:
            with self._lock:
                for e in self.events:
                    if e.type == event_type:
                        return e
            time.sleep(0.02)
        return None


# ---------------------------------------------------------------------------
# STT 集成
# ---------------------------------------------------------------------------

def test_stt_roundtrip_with_fake_server():
    import websockets

    async def handler(ws):
        # 1) 校验握手头（火山协议要求）
        assert ws.request.headers["x-api-key"] == "test-key-123"
        assert ws.request.headers["x-api-resource-id"] == "volc.seedasr.sauc.duration"
        # 2) 收到初始配置帧
        cfg_frame = vp.parse_server_frame(await ws.recv())
        assert cfg_frame.msg_type == vp.CLIENT_FULL_REQUEST
        assert cfg_frame.payload_json["audio"]["rate"] == 16000
        # 3) 收到音频帧 → 回一个 definite utterance
        audio_frame = vp.parse_server_frame(await ws.recv())
        assert audio_frame.msg_type == vp.CLIENT_AUDIO_ONLY_REQUEST
        assert gzip.decompress(audio_frame.raw_payload) == b"\x00" * 320
        await ws.send(vp.build_server_frame_for_test(
            vp.SERVER_FULL_RESPONSE,
            {"result": {"text": "把电源关掉",
                        "utterances": [{"text": "把电源关掉", "definite": True}]}}))
        # 4) 收尾帧（end_session 触发）
        last = vp.parse_server_frame(await ws.recv())
        assert last.msg_type == vp.CLIENT_AUDIO_ONLY_REQUEST
        assert last.is_last

    server = FakeVolcanoServer(handler)
    try:
        cfg = VolcanoConfig(api_key="test-key-123",
                            stt_url=f"ws://127.0.0.1:{server.port}",
                            tts_url="ws://127.0.0.1:1")
        adapter = VolcanoVoiceAdapter(cfg)
        collector = EventCollector()
        adapter.start_session(collector)
        adapter.send_audio(b"\x00" * 320)
        final = collector.wait_for("final_transcript")
        assert final is not None and final.data["text"] == "把电源关掉"
        assert collector.wait_for("utterance_end") is not None
        adapter.end_session()
    finally:
        server.stop()


def test_missing_api_key_fails_fast():
    adapter = VolcanoVoiceAdapter(VolcanoConfig(api_key=""))
    with pytest.raises(RuntimeError, match="VOLC_API_KEY"):
        adapter.start_session(lambda e: None)


# ---------------------------------------------------------------------------
# TTS 集成
# ---------------------------------------------------------------------------

def test_tts_event_sequence_and_audio_delivery():
    played: list[tuple[bytes, str]] = []

    async def handler(ws):
        # 客户端事件序列：StartConnection → StartSession → TaskRequest → FinishSession
        events = [json.loads(await ws.recv()) for _ in range(4)]
        assert [e["event"] for e in events] == [
            "StartConnection", "StartSession", "TaskRequest", "FinishSession"]
        assert events[1]["req_params"]["speaker"]
        assert events[2]["req_params"]["text"] == "已为你关闭电源"
        # 音频下行：一段裸字节（兼容路径）+ 一段 SERVER_AUDIO_ONLY 帧（协议路径）
        await ws.send(b"ID3-raw-mp3-chunk")
        framed = (bytes([(0x10), ((vp.SERVER_AUDIO_ONLY << 4) | 0b0000), 0x00, 0x00])
                  + struct.pack(">I", 11)
                  + b"AUDIO-chunk")
        await ws.send(framed)
        await ws.send(json.dumps({"event": "SessionFinished"}))

    server = FakeVolcanoServer(handler)
    try:
        cfg = VolcanoConfig(api_key="test-key-123",
                            stt_url="ws://127.0.0.1:1",
                            tts_url=f"ws://127.0.0.1:{server.port}")
        adapter = VolcanoVoiceAdapter(cfg, audio_player=lambda a, f: played.append((a, f)))
        collector = EventCollector()
        adapter.start_session(collector)
        adapter.speak("已为你关闭电源")
        assert collector.wait_for("tts_start") is not None
        end = collector.wait_for("tts_end")
        assert end is not None and end.data["interrupted"] is False
        # 两种音频形态都到达播放器
        assert b"ID3-raw-mp3-chunk" in played[0][0]
        assert b"AUDIO-chunk" in played[0][0]
        assert played[0][1] == cfg.tts_format
        adapter.end_session()
    finally:
        server.stop()


def test_interrupt_cancels_tts():
    async def handler(ws):
        # 慢速服务端：拖住会话让主线程有机会打断
        for _ in range(4):
            await ws.recv()
        await asyncio.sleep(10)
        await ws.send(json.dumps({"event": "SessionFinished"}))

    server = FakeVolcanoServer(handler)
    try:
        cfg = VolcanoConfig(api_key="k", stt_url="ws://127.0.0.1:1",
                            tts_url=f"ws://127.0.0.1:{server.port}")
        adapter = VolcanoVoiceAdapter(cfg, audio_player=lambda a, f: None)
        collector = EventCollector()
        adapter.start_session(collector)

        def interrupter():
            time.sleep(0.3)
            adapter.interrupt()
        threading.Thread(target=interrupter, daemon=True).start()

        adapter.speak("很长的一段话")
        end = collector.wait_for("tts_end")
        assert end is not None and end.data["interrupted"] is True
        adapter.end_session()
    finally:
        server.stop()
