"""VolcanoVoiceAdapter：火山引擎豆包语音模型的 VoiceGateway 实现（STT + TTS）。

协议（来源：火山方舟个人语音模型文档，region cn-beijing）：
- STT  doubao-seed-asr-2.0  wss://openspeech.bytedance.com/api/v3/plan/sauc/bigmodel_async
       二进制帧协议（见 volcano_protocol.py）：gzip JSON 配置帧 → 音频帧（seq，最后一包负 seq）→
       SERVER_FULL_RESPONSE（utterances）解析为 partial/final 转写事件。
- TTS  doubao-seed-tts-2.0  wss://openspeech.bytedance.com/api/v3/plan/tts/bidirection
       JSON 事件流：StartConnection → StartSession → TaskRequest(文本) → FinishSession，
       二进制音频分片持续下行，SessionFinished 收尾。

架构定位（对核心论点的兑现）：
- 本类只是 VoiceGateway Port 的又一个适配器 —— core/ 零改动，
  ConsoleVoicePort（文本）与本类（音频）验证"语音只是可替换的外围"。
- 同步门面 + 后台 asyncio 线程：核心域调用是同步的，WS/重连/队列细节不外泄。
- 打断（barge-in）：interrupt() 置位 + 取消当前 TTS 任务 + 发出 interrupt 事件；
  与编排层的竞态防护见 design.html §09（深入研究项）。

凭据（三选一，优先级从高到低）：已有环境变量 > 当前目录/.env > 项目根/.env
（模板见 .env.example，必需项 VOLC_API_KEY）。
"""
from __future__ import annotations

import asyncio
import json
import os
import tempfile
import threading
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from voiceops.adapters import volcano_protocol as vp
from voiceops.ports import VoiceEvent

AudioPlayer = Callable[[bytes, str], None]

STT_URL = "wss://openspeech.bytedance.com/api/v3/plan/sauc/bigmodel_async"
TTS_URL = "wss://openspeech.bytedance.com/api/v3/plan/tts/bidirection"


@dataclass
class VolcanoConfig:
    api_key: str
    stt_resource_id: str = "volc.seedasr.sauc.duration"
    tts_resource_id: str = "seed-tts-2.0"
    speaker: str = "zh_female_gaolengyujie_uranus_bigtts"
    uid: str = "voiceops_lab"
    stt_url: str = STT_URL
    tts_url: str = TTS_URL
    stt_rate: int = 16000          # PCM s16le 单声道
    tts_format: str = "mp3"
    tts_sample_rate: int = 24000

    @classmethod
    def from_env(cls, dotenv_path: str | os.PathLike | None = None) -> "VolcanoConfig":
        """从环境变量构建；支持 .env 文件（优先级：已有环境变量 > .env）。

        查找顺序：显式 dotenv_path > 当前工作目录 > 项目根目录。
        """
        _load_dotenv(dotenv_path)
        api_key = os.environ.get("VOLC_API_KEY", "")
        return cls(
            api_key=api_key,
            stt_resource_id=os.environ.get("VOLC_STT_RESOURCE_ID",
                                           cls.stt_resource_id),
            tts_resource_id=os.environ.get("VOLC_TTS_RESOURCE_ID",
                                           cls.tts_resource_id),
            speaker=os.environ.get("VOLC_TTS_SPEAKER", cls.speaker),
            uid=os.environ.get("VOLC_UID", "voiceops_lab"),
        )


def _load_dotenv(dotenv_path: str | os.PathLike | None = None) -> None:
    """加载 .env（不覆盖已存在的环境变量）。找不到文件时静默跳过。"""
    try:
        from dotenv import load_dotenv
    except ImportError:                     # 可选依赖：未安装则只用真实环境变量
        return
    candidates: list[Path] = []
    if dotenv_path is not None:
        candidates.append(Path(dotenv_path))
    else:
        candidates.append(Path.cwd() / ".env")
        candidates.append(Path(__file__).resolve().parents[2] / ".env")
    for path in candidates:
        if path.is_file():
            load_dotenv(path, override=False)
            return


def _default_player(audio: bytes, fmt: str) -> None:
    """默认播放策略：落盘 mp3（可验证输出），由调用方/OS 播放。
    生产可注入 sounddevice/miniaudio 等实时播放器。"""
    path = Path(tempfile.gettempdir()) / f"voiceops_tts_{uuid.uuid4().hex[:8]}.{fmt}"
    path.write_bytes(audio)
    print(f"[VolcanoTTS] 音频已生成: {path} ({len(audio)} bytes)")


class VolcanoVoiceAdapter:
    """VoiceGateway 的火山引擎实现。用法：

        adapter = VolcanoVoiceAdapter(VolcanoConfig.from_env())
        adapter.start_session(handler)      # handler 处理转写/播放事件
        adapter.send_audio(pcm_frame)       # 16k/16bit/mono PCM 流
        adapter.speak("已为你关闭电源")      # TTS 播报
    """

    def __init__(self, config: VolcanoConfig | None = None,
                 audio_player: AudioPlayer | None = None,
                 connect_timeout: float = 10.0) -> None:
        self.cfg = config or VolcanoConfig.from_env()
        self.audio_player = audio_player or _default_player
        self.connect_timeout = connect_timeout
        self._handler: Callable[[VoiceEvent], None] | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._audio_queue: asyncio.Queue[bytes | None] = asyncio.Queue()
        self._interrupted = threading.Event()
        self._session_ended = threading.Event()
        self._tts_task: asyncio.Task | None = None
        self._stt_task: asyncio.Task | None = None

    # ---- VoiceGateway：同步门面 -------------------------------------------

    def start_session(self, handler: Callable[[VoiceEvent], None]) -> None:
        if not self.cfg.api_key:
            raise RuntimeError(
                "缺少火山引擎凭据：请设置 VOLC_API_KEY（语音网关按需求只对协议负责，"
                "凭据由部署环境提供；离线演示请继续使用 ConsoleVoicePort）")
        self._handler = handler
        self._interrupted.clear()
        self._session_ended.clear()
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._loop.run_forever,
                                        name="volcano-voice", daemon=True)
        self._thread.start()
        self._stt_task = asyncio.run_coroutine_threadsafe(
            self._stt_session(), self._loop)

    def send_audio(self, frame: bytes) -> None:
        if self._loop is None:
            raise RuntimeError("start_session 未调用")
        asyncio.run_coroutine_threadsafe(self._audio_queue.put(frame), self._loop)

    def speak(self, text: str) -> None:
        if self._loop is None:
            raise RuntimeError("start_session 未调用")
        fut = asyncio.run_coroutine_threadsafe(self._tts_speak(text), self._loop)
        fut.result(timeout=60)     # 同步等待播报完成（可被打断提前返回）

    def interrupt(self) -> None:
        self._interrupted.set()
        # 打断必须能唤醒阻塞在网络读上的协程：跨线程直接 cancel 当前 TTS 任务
        if self._loop is not None and self._tts_task is not None:
            task = self._tts_task
            self._loop.call_soon_threadsafe(task.cancel)
        self._emit(VoiceEvent(type="interrupt"))

    def end_session(self) -> None:
        self._session_ended.set()
        if self._loop is not None:
            asyncio.run_coroutine_threadsafe(
                self._audio_queue.put(None), self._loop)
            asyncio.run_coroutine_threadsafe(self._shutdown(), self._loop).result(timeout=10)
            self._loop.call_soon_threadsafe(self._loop.stop)

    # ---- 内部 ---------------------------------------------------------------

    def _emit(self, event: VoiceEvent) -> None:
        if self._handler is not None:
            self._handler(event)

    async def _shutdown(self) -> None:
        for task in (self._stt_task, self._tts_task):
            if task and not task.done():
                task.cancel()
        self._stt_task = self._tts_task = None

    # ---- STT：二进制帧流式识别 ----------------------------------------------

    async def _stt_session(self) -> None:
        import websockets

        headers = vp.tts_stt_common_headers(
            self.cfg.api_key, self.cfg.stt_resource_id,
            request_id=str(uuid.uuid4()), connect_id=str(uuid.uuid4()))
        try:
            async with websockets.connect(
                    self.cfg.stt_url, additional_headers=headers,
                    open_timeout=self.connect_timeout, max_size=16 * 1024 * 1024) as ws:
                await ws.send(vp.build_full_request({
                    "user": {"uid": self.cfg.uid},
                    "audio": {"format": "pcm", "codec": "raw",
                              "rate": self.cfg.stt_rate, "bits": 16, "channel": 1},
                    "request": {"model_name": "bigmodel", "enable_itn": True,
                                "enable_punc": True, "enable_ddc": False,
                                "show_utterances": True,
                                "enable_nonstream": False},
                }))
                seq = 0
                sender = asyncio.create_task(self._stt_send_loop(ws))
                try:
                    async for msg in ws:
                        if isinstance(msg, str):
                            continue                      # 控制文本帧：忽略
                        frame = vp.parse_server_frame(msg)
                        if frame.msg_type == vp.SERVER_ERROR_RESPONSE:
                            err = (frame.payload_json or {}).get("message", "")
                            self._emit(VoiceEvent(type="error", data={
                                "source": "stt", "code": frame.code, "message": err}))
                            break
                        if frame.msg_type != vp.SERVER_FULL_RESPONSE:
                            continue
                        result = (frame.payload_json or {}).get("result", {})
                        text = result.get("text", "")
                        utterances = result.get("utterances") or []
                        definite = any(u.get("definite") for u in utterances)
                        if definite:
                            self._emit(VoiceEvent(type="final_transcript",
                                                  data={"text": text}))
                            self._emit(VoiceEvent(type="utterance_end"))
                        elif text:
                            self._emit(VoiceEvent(type="partial_transcript",
                                                  data={"text": text}))
                        if frame.is_last:
                            break
                finally:
                    sender.cancel()
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 —— 网络异常转事件，不逃逸线程
            self._emit(VoiceEvent(type="error", data={"source": "stt",
                                                      "message": str(exc)}))

    async def _stt_send_loop(self, ws) -> None:
        seq = 0
        while True:
            data = await self._audio_queue.get()
            if data is None or self._session_ended.is_set():
                seq += 1
                await ws.send(vp.build_audio_request(b"\x00" * 320, seq, last=True))
                return
            seq += 1
            await ws.send(vp.build_audio_request(data, seq))

    # ---- TTS：JSON 事件流 + 二进制音频下行 ----------------------------------

    async def _tts_speak(self, text: str) -> None:
        import websockets

        if self._interrupted.is_set():
            self._interrupted.clear()
        self._emit(VoiceEvent(type="tts_start", data={"text": text}))
        audio = bytearray()
        fmt = self.cfg.tts_format
        try:
            task = asyncio.create_task(self._tts_collect(text, audio))
            self._tts_task = task
            await task
        except asyncio.CancelledError:
            self._interrupted.set()
            self._emit(VoiceEvent(type="tts_end", data={"interrupted": True}))
            return
        finally:
            self._tts_task = None
        if audio and not self._interrupted.is_set():
            self.audio_player(bytes(audio), fmt)
        self._emit(VoiceEvent(type="tts_end",
                              data={"bytes": len(audio), "interrupted": False}))

    async def _tts_collect(self, text: str, audio: bytearray) -> None:
        import websockets

        headers = vp.tts_connect_headers(self.cfg.api_key, self.cfg.tts_resource_id,
                                         str(uuid.uuid4()))
        async with websockets.connect(
                self.cfg.tts_url, additional_headers=headers,
                open_timeout=self.connect_timeout, max_size=16 * 1024 * 1024) as ws:
            await ws.send(json.dumps({"event": "StartConnection"}))
            await ws.send(json.dumps({"event": "StartSession", "req_params": {
                "speaker": self.cfg.speaker,
                "audio_params": {"format": self.cfg.tts_format,
                                 "sample_rate": self.cfg.tts_sample_rate},
            }}))
            # 文本一次性下发（短句播报场景）；长文本可按句切分为多个 TaskRequest
            await ws.send(json.dumps({"event": "TaskRequest",
                                      "req_params": {"text": text}}))
            await ws.send(json.dumps({"event": "FinishSession"}))

            async for msg in ws:
                if self._interrupted.is_set():
                    return
                if isinstance(msg, bytes):
                    audio += self._extract_tts_audio(msg)
                else:
                    event = (json.loads(msg) or {}).get("event", "")
                    if event in ("SessionFinished", "SessionFailed"):
                        if event == "SessionFailed":
                            self._emit(VoiceEvent(type="error", data={
                                "source": "tts", "message": str(msg)}))
                        return

    @staticmethod
    def _extract_tts_audio(msg: bytes) -> bytes:
        """TTS 音频下行兼容两种形态：带协议头的 SERVER_AUDIO_ONLY 帧 / 纯音频字节。"""
        if len(msg) >= 4 and (msg[0] >> 4) == vp.PROTOCOL_VERSION and msg[3] == 0:
            try:
                frame = vp.parse_server_frame(msg)
                if frame.msg_type == vp.SERVER_AUDIO_ONLY and frame.raw_payload:
                    return frame.raw_payload
                if frame.msg_type == vp.SERVER_FULL_RESPONSE and frame.payload_json:
                    return b""   # 控制响应伪装成二进制：忽略
            except Exception:    # noqa: BLE001 —— 解析失败按裸音频处理
                pass
        return msg
