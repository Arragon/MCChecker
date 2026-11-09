"""Independent Python 3.12 collector entry point.

--self-test emits one JSON line without importing browser/crypto libraries.
Normal operation uses the existing length-prefixed private IPC. Browser login,
connection sessions and encrypted credentials stay in this child process.
Stdout contains protocol frames only; no credential or exception text is echoed.
The stdlib host never imports collector code or its third-party dependencies.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import platform
import signal
import sys

#: 与 `app/services/collector_manager.py::PROTOCOL_VERSION` 必须逐字一致。
#: 跨进程握手只认这一个字符串；`tests/test_collector_manager.py` 会对比两处字面量。
PROTOCOL_VERSION = "m16.collector/1"

#: Runtime implementation version; production platform bundle validation is separate.
COLLECTOR_VERSION = "0.2.0"

#: 采集器环境的最低 Python 版本；主应用仍是 3.9。
MIN_PYTHON = (3, 12)

EXIT_OK = 0
EXIT_UNAVAILABLE = 2

#: 可选：指向已解包的 Chromium/Chrome 可执行文件。未设置时退回“Playwright 可解析”。
ENV_CHROMIUM = "MCHECKER_COLLECTOR_CHROMIUM"

REASON_BROWSER_MISSING = "collector_browser_missing"
REASON_VERSION_MISMATCH = "collector_version_mismatch"
REASON_INVALID_PAYLOAD = "invalid_payload"
REASON_UNSUPPORTED_OPERATION = "unsupported_operation"


def _browser_state():
    """返回 (available|missing, 来源, 说明)。不 import Playwright。"""
    configured = str(os.environ.get(ENV_CHROMIUM, "") or "").strip()
    if configured:
        path = os.path.expanduser(configured)
        if os.path.isfile(path) and os.access(path, os.X_OK):
            return "available", "configured_path", "配置的浏览器可执行文件存在"
        return "missing", "configured_path", "配置的浏览器可执行文件不存在或不可执行"

    try:
        # find_spec 只解析模块位置，不执行包体；骨架里绝不能 import playwright。
        found = importlib.util.find_spec("playwright") is not None
    except (ImportError, ValueError):
        found = False
    if found:
        # Playwright 可解析不等于 Chromium 已安装；真正的浏览器启动验证在目标环境。
        return "available", "playwright", "Playwright 可解析；Chromium 实机启动未探测"
    return "missing", "none", "未找到 Playwright（也没有配置浏览器可执行文件）"


def self_test():
    """生成自检结论；纯函数，方便主应用按 JSON 字段判定。"""
    python_version = platform.python_version()
    python_ok = tuple(sys.version_info[:2]) >= MIN_PYTHON
    browser, browser_source, browser_message = _browser_state()

    reason = None
    message = "collector 自检通过"
    if not python_ok:
        reason = REASON_VERSION_MISMATCH
        message = "采集器需要 Python %d.%d，当前 %s" % (MIN_PYTHON[0], MIN_PYTHON[1], python_version)
    elif browser != "available":
        reason = REASON_BROWSER_MISSING
        message = browser_message

    return {
        "ok": reason is None,
        "protocol_version": PROTOCOL_VERSION,
        "collector_version": COLLECTOR_VERSION,
        "python_version": python_version,
        "python_ok": python_ok,
        "browser": browser,
        "browser_source": browser_source,
        "reason_code": reason,
        "message": message,
    }


def _write_frame(frame):
    """stdout 只写协议行：一行一个 JSON 对象，立即 flush。"""
    sys.stdout.write(json.dumps(frame, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def _install_stop_handlers():
    def _stop(_signum, _frame):
        raise SystemExit(EXIT_OK)

    for name in ("SIGTERM", "SIGINT", "SIGBREAK"):
        sig = getattr(signal, name, None)
        if sig is None:
            continue
        try:
            signal.signal(sig, _stop)
        except (ValueError, OSError):  # 非主线程或平台不支持
            pass


def _serve_stdio():
    """Length-prefixed IPC; Playwright stays on this thread, input/cancel on a reader."""
    import queue
    import threading
    from datetime import datetime, timezone
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from collector import ipc
    from collector.runtime import Runtime

    _install_stop_handlers()
    pending = queue.Queue(maxsize=32)
    cancellations = {}
    lock = threading.Lock()
    stopping = threading.Event()

    def receive():
        try:
            while not stopping.is_set():
                frame = ipc.read_frame(sys.stdin.buffer)
                if frame is None or frame.get("type") == "shutdown":
                    stopping.set()
                    with lock:
                        for event in cancellations.values():
                            event.set()
                    pending.put(None)
                    return
                request_id = frame.get("request_id")
                with lock:
                    if frame.get("type") == "cancel":
                        if request_id in cancellations:
                            cancellations[request_id].set()
                        continue
                    event = threading.Event()
                    cancellations[request_id] = event
                pending.put((frame, event))
        except Exception:
            stopping.set()
            pending.put(None)

    threading.Thread(target=receive, daemon=True, name="collector-input").start()
    runtime = Runtime()
    instance = None
    try:
        while not stopping.is_set():
            entry = pending.get()
            if entry is None:
                break
            request, cancel = entry
            request_id = request.get("request_id")
            try:
                if request.get("type") != "request" or request.get("protocol_version") != ipc.PROTOCOL_VERSION:
                    raise ValueError("protocol_mismatch")
                received_instance = request.get("collector_instance_id")
                if not received_instance or instance is not None and received_instance != instance:
                    raise ValueError("protocol_mismatch")
                if not isinstance(request_id, str) or not 1 <= len(request_id) <= 100:
                    raise ValueError("protocol_mismatch")
                instance = received_instance
                deadline = datetime.strptime(request["deadline_at"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
                if datetime.now(timezone.utc) >= deadline:
                    raise TimeoutError()
                payload = runtime.handle(request, cancel)
                if cancel.is_set():
                    raise InterruptedError()
                if datetime.now(timezone.utc) >= deadline:
                    raise TimeoutError()
                raw = payload.pop("content", None)
                if isinstance(raw, bytes):
                    ipc.write_frame(sys.stdout.buffer, {"type": "meta", "request_id": request_id,
                        "collector_instance_id": instance, "result": payload})
                else:
                    raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()
                for frame in ipc.chunk_frames(request_id, raw):
                    ipc.write_frame(sys.stdout.buffer, frame)
            except Exception as exc:
                reason = getattr(exc, "reason_code", None)
                if not reason:
                    reason = "collector_timeout" if isinstance(exc, TimeoutError) else "canceled" if isinstance(exc, InterruptedError) else "invalid_payload"
                # Never echo exception text: it may contain credentials, cookies or a response.
                ipc.write_frame(sys.stdout.buffer, {"type": "error", "request_id": request_id, "reason_code": reason, "message": "collector request failed"})
            finally:
                with lock:
                    cancellations.pop(request_id, None)
    finally:
        stopping.set()
        runtime.close()
    return EXIT_OK


def main(argv=None):
    parser = argparse.ArgumentParser(description="MCChecker independent collector")
    parser.add_argument(
        "--self-test",
        action="store_true",
        help="输出一行 JSON 自检结论；不可用时退出码 2",
    )
    args = parser.parse_args(argv)

    if args.self_test:
        payload = self_test()
        _write_frame(payload)
        return EXIT_OK if payload["ok"] else EXIT_UNAVAILABLE
    return _serve_stdio()


if __name__ == "__main__":
    raise SystemExit(main())
