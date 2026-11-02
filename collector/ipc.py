"""M16-15 collector 侧 IPC 协议（独立进程内使用）。

与 `app/services/collector_ipc.py` 保持同一帧格式；collector 不 import 主应用，
只复制这份纯标准库协议。测试会核对两边的 `PROTOCOL_VERSION`/`MAX_FRAME_BYTES`
等常量一致，避免两边悄悄漂移。
"""

from __future__ import annotations

import base64
import hashlib
import json
import struct
from typing import Any, Dict, Iterable, List, Optional

PROTOCOL_VERSION = 1
MAX_FRAME_BYTES = 2 * 1024 * 1024
MAX_CHUNK_BYTES = 256 * 1024
MAX_CONTENT_BYTES = 64 * 1024 * 1024
FRAME_HEADER = struct.Struct(">I")


class CollectorProtocolError(Exception):
    def __init__(self, reason_code: str, message: str = "") -> None:
        super().__init__(message or reason_code)
        self.reason_code = reason_code
        self.message = message or reason_code


def encode_frame(payload: Dict[str, Any]) -> bytes:
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    if not raw or len(raw) > MAX_FRAME_BYTES:
        raise CollectorProtocolError("protocol_mismatch", "frame size out of bounds")
    return FRAME_HEADER.pack(len(raw)) + raw


def decode_frame(data: bytes) -> Dict[str, Any]:
    if len(data) < FRAME_HEADER.size:
        raise CollectorProtocolError("protocol_mismatch", "truncated frame header")
    length = FRAME_HEADER.unpack(data[: FRAME_HEADER.size])[0]
    if length < 1 or length > MAX_FRAME_BYTES:
        raise CollectorProtocolError("protocol_mismatch", "frame length out of bounds")
    raw = data[FRAME_HEADER.size :]
    if len(raw) != length:
        raise CollectorProtocolError("protocol_mismatch", "frame body length mismatch")
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        raise CollectorProtocolError("protocol_mismatch", "frame body is not JSON")
    if not isinstance(payload, dict):
        raise CollectorProtocolError("protocol_mismatch", "frame body must be an object")
    return payload


def read_frame(reader) -> Optional[Dict[str, Any]]:
    header = reader.read(FRAME_HEADER.size)
    if not header:
        return None
    if len(header) != FRAME_HEADER.size:
        raise CollectorProtocolError("protocol_mismatch", "truncated frame header")
    length = FRAME_HEADER.unpack(header)[0]
    if length < 1 or length > MAX_FRAME_BYTES:
        raise CollectorProtocolError("protocol_mismatch", "frame length out of bounds")
    body = reader.read(length)
    if len(body) != length:
        raise CollectorProtocolError("protocol_mismatch", "truncated frame body")
    return decode_frame(header + body)


def write_frame(writer, payload: Dict[str, Any]) -> None:
    writer.write(encode_frame(payload))
    writer.flush()


def chunk_frames(request_id: str, data: bytes, chunk_size: int = MAX_CHUNK_BYTES) -> List[Dict[str, Any]]:
    if len(data) > MAX_CONTENT_BYTES:
        raise CollectorProtocolError("too_large", "content exceeds limit")
    body = bytes(data)
    digest = hashlib.sha256(body).hexdigest()
    if not body:
        return [
            {
                "type": "chunk",
                "request_id": request_id,
                "seq": 1,
                "final": True,
                "data": "",
                "size": 0,
                "sha256": digest,
            }
        ]
    frames: List[Dict[str, Any]] = []
    for index, offset in enumerate(range(0, len(body), chunk_size), start=1):
        piece = body[offset : offset + chunk_size]
        frame = {
            "type": "chunk",
            "request_id": request_id,
            "seq": index,
            "final": offset + len(piece) >= len(body),
            "data": base64.b64encode(piece).decode("ascii"),
        }
        if frame["final"]:
            frame["size"] = len(body)
            frame["sha256"] = digest
        frames.append(frame)
    return frames


def reassemble_chunks(frames: Iterable[Dict[str, Any]], request_id: str) -> bytes:
    collected: List[bytes] = []
    expected_seq = 1
    total = 0
    final_seen = False
    for frame in frames:
        if frame.get("type") != "chunk" or frame.get("request_id") != request_id:
            raise CollectorProtocolError("protocol_mismatch", "chunk identity mismatch")
        if frame.get("seq") != expected_seq:
            raise CollectorProtocolError("protocol_mismatch", "chunk sequence mismatch")
        if final_seen:
            raise CollectorProtocolError("protocol_mismatch", "chunk after final")
        try:
            piece = base64.b64decode(str(frame.get("data", "")).encode("ascii"), validate=True)
        except Exception:
            raise CollectorProtocolError("protocol_mismatch", "chunk base64 invalid")
        total += len(piece)
        if total > MAX_CONTENT_BYTES:
            raise CollectorProtocolError("too_large", "content exceeds limit")
        collected.append(piece)
        if frame.get("final") is True:
            final_seen = True
            body = b"".join(collected)
            if frame.get("size") != len(body):
                raise CollectorProtocolError("protocol_mismatch", "content size mismatch")
            if frame.get("sha256") != hashlib.sha256(body).hexdigest():
                raise CollectorProtocolError("protocol_mismatch", "content sha256 mismatch")
            return body
        expected_seq += 1
    raise CollectorProtocolError("protocol_mismatch", "missing final chunk")
