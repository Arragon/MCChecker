"""M16-22 SDA 内容有界读取与 staging。

collector 只把有界字节写入自己的 staging sink；任何超限、deadline、取消、
EOF/类型错误都会 abort，不把半个文件交给主进程。主进程仍会重新计算
size/sha256 后才提交。本模块不解析 XML/JSON，不写业务 data。
"""

from __future__ import annotations

import hashlib
import os
import tempfile
import time
from typing import Any, Dict, Optional

from .ipc import MAX_CHUNK_BYTES, MAX_CONTENT_BYTES


class StreamError(Exception):
    def __init__(self, reason_code: str, message: str = "") -> None:
        super().__init__(message or reason_code)
        self.reason_code = reason_code
        self.message = message or reason_code


class StagingSink:
    """受控临时文件；成功才 commit，失败/退出自动删除。"""

    def __init__(self, directory: str, prefix: str = "stage-") -> None:
        os.makedirs(directory, exist_ok=True)
        fd, self.path = tempfile.mkstemp(prefix=prefix, dir=directory)
        self._handle = os.fdopen(fd, "wb")
        self.committed = False

    def write(self, data: bytes) -> None:
        self._handle.write(data)

    def commit(self) -> str:
        if self.committed:
            return self.path
        self._handle.flush()
        os.fsync(self._handle.fileno())
        self._handle.close()
        self.committed = True
        return self.path

    def abort(self) -> None:
        if self.committed:
            return
        try:
            self._handle.close()
        except OSError:
            pass
        try:
            os.unlink(self.path)
        except OSError:
            pass

    def __enter__(self) -> "StagingSink":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        if exc_type is not None or not self.committed:
            self.abort()


def _fail(sink, reason_code: str, message: str = "") -> None:
    try:
        sink.abort()
    except Exception:
        pass
    raise StreamError(reason_code, message)


def read_limited(
    stream,
    sink,
    limit_bytes: int = MAX_CONTENT_BYTES,
    deadline: Optional[float] = None,
    cancel_event=None,
    chunk_size: int = 64 * 1024,
    clock=time.monotonic,
) -> Dict[str, Any]:
    if limit_bytes < 1 or limit_bytes > MAX_CONTENT_BYTES:
        raise StreamError("too_large", "limit out of bounds")
    if chunk_size < 1 or chunk_size > MAX_CHUNK_BYTES:
        raise StreamError("protocol_mismatch", "chunk size out of bounds")
    hasher = hashlib.sha256()
    total = 0
    while True:
        if cancel_event is not None and cancel_event.is_set():
            _fail(sink, "canceled", "read canceled")
        if deadline is not None and clock() >= deadline:
            _fail(sink, "collector_timeout", "read deadline exceeded")
        try:
            chunk = stream.read(chunk_size)
        except Exception as exc:
            _fail(sink, "network_error", str(exc))
        if not chunk:
            break
        if not isinstance(chunk, (bytes, bytearray)):
            _fail(sink, "invalid_payload", "stream returned non-bytes")
        data = bytes(chunk)
        total += len(data)
        if total > limit_bytes:
            _fail(sink, "too_large", "content exceeds limit")
        try:
            sink.write(data)
        except Exception as exc:
            _fail(sink, "storage_failure", str(exc))
        hasher.update(data)
    return {"size": total, "sha256": hasher.hexdigest()}


def verify_received(data: bytes, expected_size: int, expected_sha256: str) -> None:
    if not isinstance(data, (bytes, bytearray)):
        raise StreamError("invalid_payload", "content must be bytes")
    body = bytes(data)
    if len(body) != expected_size:
        raise StreamError("invalid_payload", "content size mismatch")
    if hashlib.sha256(body).hexdigest() != expected_sha256:
        raise StreamError("invalid_payload", "content sha256 mismatch")
