"""火山引擎豆包语音模型 二进制帧编解码（STT sauc 协议）。

协议来源：火山方舟 doubao-seed-asr-2.0（volc.seedasr.sauc.duration）
  4 字节 Header：byte0=(protocol_version<<4)|header_size
                byte1=(message_type<<4)|specific_flags
                byte2=(serialization<<4)|compression
                byte3=保留
  Payload：gzip 压缩；帧尾结构 header + [seq:int32] + [event:int32] + payload_size:uint32 + gzip(data)

编解码独立成纯函数模块 —— 零 IO 依赖，协议逻辑可被单元测试穷尽覆盖。
"""
from __future__ import annotations

import gzip
import json
import struct
from dataclasses import dataclass

# MessageType
CLIENT_FULL_REQUEST = 0b0001
CLIENT_AUDIO_ONLY_REQUEST = 0b0010
SERVER_FULL_RESPONSE = 0b1001
SERVER_ERROR_RESPONSE = 0b1111
SERVER_ACK = 0b0101
SERVER_AUDIO_ONLY = 0b1011

# SpecificFlags
FLAG_NO_SEQ = 0b0000
FLAG_POS_SEQ = 0b0001      # bit0: 带 seq
FLAG_NEG_SEQ = 0b0010      # bit1: 最后一包
FLAG_NEG_WITH_SEQ = 0b0011

SERIAL_JSON = 0b0001
COMP_GZIP = 0b0001
PROTOCOL_VERSION = 0b0001
HEADER_SIZE_UNITS = 0b0001  # 1 个 4 字节单元


def _header(msg_type: int, flags: int, serial: int = SERIAL_JSON,
            comp: int = COMP_GZIP) -> bytes:
    return bytes([
        (PROTOCOL_VERSION << 4) | HEADER_SIZE_UNITS,
        (msg_type << 4) | flags,
        (serial << 4) | comp,
        0x00,
    ])


def build_full_request(payload: dict, seq: int = 1) -> bytes:
    """客户端完整请求（连接后的初始 JSON 配置）。"""
    body = gzip.compress(json.dumps(payload).encode("utf-8"))
    return (_header(CLIENT_FULL_REQUEST, FLAG_POS_SEQ)
            + struct.pack(">i", seq)
            + struct.pack(">I", len(body))
            + body)


def build_audio_request(data: bytes, seq: int, last: bool = False) -> bytes:
    """客户端音频帧；最后一包 seq 取负、flags=NEG_WITH_SEQUENCE。"""
    body = gzip.compress(data)
    flags = FLAG_NEG_WITH_SEQ if last else FLAG_POS_SEQ
    wire_seq = -seq if last else seq
    return (_header(CLIENT_AUDIO_ONLY_REQUEST, flags, serial=0)
            + struct.pack(">i", wire_seq)
            + struct.pack(">I", len(body))
            + body)


@dataclass
class ServerFrame:
    msg_type: int
    is_last: bool
    seq: int | None
    event: int | None
    code: int                      # SERVER_ERROR_RESPONSE 时的错误码
    payload_json: dict | None      # 解压后的 JSON（若可解析）
    raw_payload: bytes


def parse_server_frame(msg: bytes) -> ServerFrame:
    """解析服务端响应帧（SERVER_FULL_RESPONSE / SERVER_ERROR_RESPONSE）。"""
    header_size = msg[0] & 0x0F
    msg_type = (msg[1] >> 4) & 0x0F
    flags = msg[1] & 0x0F
    compression = msg[2] & 0x0F
    pos = header_size * 4

    seq = event = None
    if flags & 0b0001:
        seq = struct.unpack(">i", msg[pos:pos + 4])[0]
        pos += 4
    is_last = bool(flags & 0b0010)
    if flags & 0b0100:
        event = struct.unpack(">i", msg[pos:pos + 4])[0]
        pos += 4

    code = 0
    if msg_type == SERVER_ERROR_RESPONSE:
        code = struct.unpack(">i", msg[pos:pos + 4])[0]
        pos += 4

    raw = b""
    payload_json = None
    if pos + 4 <= len(msg):
        size = struct.unpack(">I", msg[pos:pos + 4])[0]
        raw = msg[pos + 4:pos + 4 + size]
        if raw:
            data = gzip.decompress(raw) if compression == COMP_GZIP else raw
            try:
                payload_json = json.loads(data.decode("utf-8"))
            except (json.JSONDecodeError, UnicodeDecodeError):
                payload_json = None
    return ServerFrame(msg_type=msg_type, is_last=is_last, seq=seq, event=event,
                       code=code, payload_json=payload_json, raw_payload=raw)


def build_server_frame_for_test(msg_type: int, payload: dict, *, seq: int = 1,
                                is_last: bool = False, code: int = 0) -> bytes:
    """测试辅助：构造一个合法的服务端响应帧（往返验证编解码对称性）。"""
    flags = (FLAG_NEG_WITH_SEQ if is_last else FLAG_POS_SEQ) | 0b0000
    body = gzip.compress(json.dumps(payload).encode("utf-8"))
    wire = [(_header(msg_type, flags)
             + struct.pack(">i", seq)
             + struct.pack(">I", len(body))
             + body)]
    frame = wire[0]
    if msg_type == SERVER_ERROR_RESPONSE and code:
        frame = (_header(msg_type, flags)
                 + struct.pack(">i", seq) + struct.pack(">i", code)
                 + struct.pack(">I", len(body)) + body)
    return frame


# ---------------------------------------------------------------------------
# TTS 双流协议（JSON 事件模型，无二进制头）
# ---------------------------------------------------------------------------

def tts_connect_headers(api_key: str, resource_id: str, connect_id: str) -> dict:
    return {
        "X-Api-Key": api_key,
        "X-Api-Resource-Id": resource_id,
        "X-Api-Connect-Id": connect_id,
    }


def tts_stt_common_headers(api_key: str, resource_id: str,
                           request_id: str, connect_id: str) -> dict:
    return {
        "X-Api-Key": api_key,
        "X-Api-Resource-Id": resource_id,
        "X-Api-Request-Id": request_id,
        "X-Api-Connect-Id": connect_id,
        "X-Api-Sequence": "-1",
    }
