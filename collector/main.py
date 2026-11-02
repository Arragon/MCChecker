"""M16-14 采集器进程骨架（Python 3.12；INH-2179）。

这个文件只属于**独立 collector 环境**，不属于主应用：主应用是 Python 3.9.11 +
标准库，`app/`、`run.py` 与核心 `requirements.txt` 都不得 import 本目录，也不得
把 Playwright/Chromium/cryptography 带进去。M16-14 只交付**可被受管启动/停止的
骨架**：真正的私有 stdio IPC 帧、Playwright/Chromium 会话在 M16-15 之后接入。

本骨架刻意只 import 标准库：

* `--self-test`：输出**一行** JSON 自检结论（协议版本、Python 版本、浏览器可用性），
  `ok=false` 时退出码 2，让主应用把失败原因变成可观察的 `collector_*` 原因码，
  而不是留下一个半死的子进程。
* 无参数：进入极简 stdio 生命周期，一次一行 JSON（`ping` / `shutdown`），
  stdout 只允许协议行，诊断文字一律去 stderr。
* SIGTERM/SIGINT/SIGBREAK：干净退出（Windows 上由主应用的 `taskkill /T /F`
  负责进程树，详见 `docs/M16_COLLECTOR.md`）。

**版本不在文档里猜**：Playwright/Chromium/cryptography 的精确版本必须由目标
Windows/Linux 环境锁定并写进 `requirements.lock`；本文件只检查它们**是否可解析**。
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

#: 骨架版本；真正的 collector 版本随 bundle 一起发布（M16-42）。
COLLECTOR_VERSION = "0.1.0-skeleton"

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
    """极简生命周期：一次一行 JSON；不带 request_id 时不回显。"""
    _install_stop_handlers()
    while True:
        line = sys.stdin.readline()
        if not line:
            return EXIT_OK
        try:
            frame = json.loads(line)
        except ValueError:
            _write_frame({"ok": False, "reason_code": REASON_INVALID_PAYLOAD})
            continue
        if not isinstance(frame, dict):
            _write_frame({"ok": False, "reason_code": REASON_INVALID_PAYLOAD})
            continue

        request_id = frame.get("request_id")
        operation = frame.get("op")
        if operation == "ping":
            response = {
                "ok": True,
                "op": "ping",
                "protocol_version": PROTOCOL_VERSION,
                "collector_version": COLLECTOR_VERSION,
                "python_version": platform.python_version(),
            }
        elif operation == "shutdown":
            response = {"ok": True, "op": "shutdown", "request_id": request_id}
            _write_frame(response)
            return EXIT_OK
        else:
            response = {
                "ok": False,
                "op": operation,
                "reason_code": REASON_UNSUPPORTED_OPERATION,
            }
        if request_id is not None:
            response["request_id"] = request_id
        _write_frame(response)


def main(argv=None):
    parser = argparse.ArgumentParser(description="MCChecker collector skeleton (M16-14)")
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
