"""M16-15 collector 客户端：请求/分块/超时/取消/崩溃隔离。

通过一个后台 reader 线程读取 stdout 帧，请求线程只排队取帧；因此：
- 不依赖 `select`（Windows 管道也可用）；
- 超时不会卡住主线程；迟到的帧按 request_id 丢弃；
- collector 退出/EOF 转成 `collector_offline`，不把半文件交给提交层。

本模块不启动 collector（M16-14 的 manager 负责），也不写 data。
"""

from __future__ import annotations

import queue
import secrets
import threading
import time
from datetime import datetime, timedelta
from typing import Any, Dict, Optional

from . import collector_ipc as ipc


class CollectorClientError(Exception):
    def __init__(self, reason_code: str, message: str = "") -> None:
        super().__init__(message or reason_code)
        self.reason_code = reason_code
        self.message = message or reason_code


def _utc_after(seconds: float) -> str:
    moment = datetime.utcnow() + timedelta(seconds=max(1.0, float(seconds)))
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


class CollectorClient:
    def __init__(
        self,
        writer,
        reader,
        instance_id: str,
        protocol_version: int = ipc.PROTOCOL_VERSION,
    ) -> None:
        if not instance_id or not isinstance(instance_id, str):
            raise CollectorClientError("protocol_mismatch", "collector instance_id required")
        self._writer = writer
        self._reader = reader
        self.instance_id = instance_id
        self.protocol_version = protocol_version
        self.last_meta: Dict[str, Any] = {}
        self.last_request_id: Optional[str] = None
        self._write_lock = threading.Lock()
        self._request_lock = threading.Lock()
        self._queue: "queue.Queue" = queue.Queue()
        self._stop = threading.Event()
        self._reader_thread = threading.Thread(
            target=self._reader_loop,
            name="collector-client-reader",
            daemon=True,
        )
        self._reader_thread.start()

    def _reader_loop(self) -> None:
        while not self._stop.is_set():
            try:
                frame = ipc.read_frame(self._reader)
            except Exception as exc:
                self._queue.put(("error", exc))
                return
            if frame is None:
                self._queue.put(("eof", None))
                return
            self._queue.put(("frame", frame))

    def _write(self, payload: Dict[str, Any]) -> None:
        with self._write_lock:
            try:
                ipc.write_frame(self._writer, payload)
            except Exception as exc:
                raise CollectorClientError("collector_offline", str(exc))

    def _next_frame(self, request_id: str, deadline: float) -> Dict[str, Any]:
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise CollectorClientError("collector_timeout", "collector response timed out")
            try:
                kind, value = self._queue.get(timeout=remaining)
            except queue.Empty:
                raise CollectorClientError("collector_timeout", "collector response timed out")
            if kind == "eof":
                raise CollectorClientError("collector_offline", "collector closed stdout")
            if kind == "error":
                message = str(value) if value is not None else "collector protocol error"
                raise CollectorClientError("protocol_mismatch", message)
            if not isinstance(value, dict):
                raise CollectorClientError("protocol_mismatch", "collector frame is not an object")
            if value.get("request_id") != request_id:
                # 迟到/别的请求的帧：丢弃，不污染当前请求。
                continue
            return value

    def request(
        self,
        operation: str,
        workspace_id: str,
        connection_id: str,
        parameters: Optional[Dict[str, Any]] = None,
        timeout: float = 10.0,
        expected_policy_revision: Optional[int] = None,
        expected_auth_epoch: Optional[int] = None,
    ) -> bytes:
        if operation not in ipc.OPERATIONS or operation in ("cancel", "shutdown"):
            raise CollectorClientError("protocol_mismatch", "unsupported operation")
        if timeout <= 0:
            raise CollectorClientError("protocol_mismatch", "timeout must be positive")
        with self._request_lock:
            request_id = "req-" + secrets.token_hex(8)
            self.last_request_id = request_id
            envelope: Dict[str, Any] = {
                "type": "request",
                "protocol_version": self.protocol_version,
                "collector_instance_id": self.instance_id,
                "request_id": request_id,
                "workspace_id": workspace_id,
                "connection_id": connection_id,
                "operation": operation,
                "deadline_at": _utc_after(timeout),
                "parameters": parameters or {},
            }
            if expected_policy_revision is not None:
                envelope["expected_policy_revision"] = int(expected_policy_revision)
            if expected_auth_epoch is not None:
                envelope["expected_auth_epoch"] = int(expected_auth_epoch)
            self._write(envelope)
            deadline = time.monotonic() + timeout
            frames = []
            while True:
                frame = self._next_frame(request_id, deadline)
                frame_type = frame.get("type")
                if frame_type == "meta":
                    self.last_meta = frame
                    continue
                if frame_type == "error":
                    raise CollectorClientError(
                        str(frame.get("reason_code") or "business_error"),
                        str(frame.get("message") or "collector error"),
                    )
                if frame_type != "chunk":
                    raise CollectorClientError("protocol_mismatch", "unexpected collector frame")
                frames.append(frame)
                if frame.get("final") is True:
                    break
            try:
                return ipc.reassemble_chunks(frames, request_id)
            except ipc.CollectorProtocolError as exc:
                raise CollectorClientError(exc.reason_code, exc.message)

    def read_resource(
        self,
        workspace_id: str,
        connection_id: str,
        parameters: Dict[str, Any],
        timeout: float = 30.0,
        expected_policy_revision: Optional[int] = None,
        expected_auth_epoch: Optional[int] = None,
    ) -> bytes:
        return self.request(
            "read_resource",
            workspace_id,
            connection_id,
            parameters=parameters,
            timeout=timeout,
            expected_policy_revision=expected_policy_revision,
            expected_auth_epoch=expected_auth_epoch,
        )

    def cancel(self, request_id: str) -> None:
        self._write(
            {
                "type": "cancel",
                "protocol_version": self.protocol_version,
                "collector_instance_id": self.instance_id,
                "request_id": request_id,
            }
        )

    def shutdown(self) -> None:
        self._write(
            {
                "type": "shutdown",
                "protocol_version": self.protocol_version,
                "collector_instance_id": self.instance_id,
            }
        )

    def close(self) -> None:
        self._stop.set()
        try:
            self._reader_thread.join(0.2)
        except RuntimeError:
            pass

    def __enter__(self) -> "CollectorClient":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()
