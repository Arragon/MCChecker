"""M16-14 受管 collector 子进程（INH-2179）。

边界（设计说明书 §7.1/§7.4、ADR-012）：

* 主应用（Python 3.9.11 + 标准库）**不 import** `collector/`；本模块只启动、停止、
  诊断一个独立 Python 3.12 子进程。Playwright/Chromium 永远待在子进程里。
* `shell=False`，命令行固定为 `[python, entry]` / `[python, entry, "--self-test"]`，
  **不接受任意参数、不把密码/凭据放 argv**。凭据通道属于 M16-15 的私有 stdio。
* POSIX：子进程 `start_new_session=True` 自成进程组，停止时 `killpg(SIGTERM)`，
  超时再 `killpg(SIGKILL)`；Windows：`CREATE_NEW_PROCESS_GROUP` + `taskkill /T /F`
  回收整棵进程树（不能只杀 Python 留下 Chromium），详见 `docs/M16_COLLECTOR.md`。
* 停止在**进程真正退出之后**才返回，因此调用方（`run.ordered_stop`）可以把
  “停 collector”排在关 HTTP / 释放单实例锁之前，不会提前放第二个实例进来。
* 状态与诊断只返回安全字段：state/pid/原因码/版本/路径，绝不回显 env、argv、
  子进程原始 stdout/stderr 或任何凭据。
* collector 不存在/不可用时只影响采集能力：`status()` 返回 `unavailable` 与
  `collector_offline`/具体原因码，普通文件功能不依赖本模块，也不会被它阻塞。

M16-14 不默认启动 collector：哪条连接需要它由 M16-15 之后的调用方决定；本模块只
保证一旦启动，生命周期受管、可诊断、可在 SIGTERM 停止序列里收干净。
"""

from __future__ import annotations

import json
import logging
import os
import secrets
import shutil
import signal
import subprocess
import threading
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

#: 覆盖 collector 解释器（必须是可执行文件路径，不能是 “py -3.12” 这种带参数的命令串）。
ENV_PYTHON = "MCHECKER_COLLECTOR_PYTHON"
#: 覆盖 collector 入口脚本路径；相对路径按项目根解析。
ENV_MAIN = "MCHECKER_COLLECTOR_MAIN"

#: 未配置时的默认解释器；目标机应指向锁定后的 Python 3.12（或 bundle 内的 runtime）。
DEFAULT_PYTHON = "python3.12"
DEFAULT_ENTRY = os.path.join("collector", "main.py")

#: 私有握手版本；与 `collector/main.py::PROTOCOL_VERSION` 必须逐字一致。
PROTOCOL_VERSION = "m16.collector/1"

STATE_STOPPED = "stopped"
STATE_STARTING = "starting"
STATE_ONLINE = "online"
STATE_UNAVAILABLE = "unavailable"
STATE_FAILED = "failed"

#: `status()` 允许出现的全部字段（安全投影）。新增字段必须同时改这里与 `_snapshot_locked`。
STATUS_KEYS = frozenset(
    {
        "state",
        "available",
        "reason_code",
        "message",
        "pid",
        "started_at",
        "uptime_seconds",
        "exit_code",
        "protocol_version",
        "python_version",
        "browser",
        "python",
        "entry",
        "process_group",
        "tree_kill",
    }
)

REASON_OFFLINE = "collector_offline"
REASON_ENTRY_MISSING = "collector_entry_missing"
REASON_PYTHON_MISSING = "collector_python_missing"
REASON_START_FAILED = "collector_start_failed"
REASON_VERSION_MISMATCH = "collector_version_mismatch"
REASON_BROWSER_MISSING = "collector_browser_missing"
REASON_EXITED = "collector_exited"
REASON_STOP_TIMEOUT = "collector_stop_timeout"
REASON_SELFTEST_TIMEOUT = "collector_selftest_timeout"

#: 自检子进程的默认上限（解释器启动 + import 探测）。
DEFAULT_SELFTEST_TIMEOUT = 15.0
#: 正常停止的默认上限；超时后强制结束进程树。
DEFAULT_STOP_TIMEOUT = 10.0
#: 强制结束后再等这么久，拿不到退出码就报 `collector_stop_timeout`。
_FORCE_WAIT_SECONDS = 3.0
#: `start()` 返回前短暂观察，尽早暴露“起来就崩”的子进程。
_START_GRACE_SECONDS = 0.2
#: Windows `taskkill` 自己的上限。
_WINDOWS_TASKKILL_TIMEOUT = 10.0

_WINDOWS_CREATION_FLAGS = int(getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)) | int(
    getattr(subprocess, "CREATE_NO_WINDOW", 0)
)


def project_root() -> str:
    """仓库根：本文件在 `<root>/app/services/collector_manager.py`。"""
    return os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _resolve_executable(value: str) -> str:
    """把 “python3.12” 解析成 PATH 上的绝对路径；已是路径则只展开 `~`。"""
    expanded = os.path.expanduser(str(value))
    if os.path.dirname(expanded):
        return os.path.abspath(expanded)
    return shutil.which(expanded) or expanded


def _is_executable(path: str) -> bool:
    return os.path.isfile(path) and os.access(path, os.X_OK)


def windows_tree_kill_command(pid: int) -> List[str]:
    """Windows 进程树回收命令；`/T` 连子进程（Chromium）一起，`/F` 强制。"""
    return ["taskkill", "/PID", str(int(pid)), "/T", "/F"]


def _last_json_object(raw: bytes) -> Optional[Dict[str, Any]]:
    """取 stdout 里最后一个 JSON 对象行；其它输出一概不解析、不外传。"""
    if not raw:
        return None
    text = raw.decode("utf-8", "replace")
    for line in reversed(text.splitlines()):
        line = line.strip()
        if not line:
            continue
        try:
            value = json.loads(line)
        except ValueError:
            continue
        if isinstance(value, dict):
            return value
    return None


def _signal_group(proc: "subprocess.Popen[Any]", sig: int) -> bool:
    """POSIX：对子进程组发信号；组不存在/无权限时退回单进程。"""
    try:
        os.killpg(os.getpgid(proc.pid), sig)
        return True
    except (ProcessLookupError, PermissionError, OSError):
        try:
            if sig == getattr(signal, "SIGKILL", 9):
                proc.kill()
            else:
                proc.terminate()
            return True
        except OSError:
            return False


class CollectorManager:
    """受管 collector 子进程的 start/stop/status/diagnose。

    `python` / `entry` 为空时按 `MCHECKER_COLLECTOR_PYTHON` / `MCHECKER_COLLECTOR_MAIN`
    解析，再退回 `python3.12` 与 `<项目根>/collector/main.py`。实例本身不启动任何
    线程；所有生命周期动作都在调用线程里完成。
    """

    def __init__(
        self,
        python: Optional[str] = None,
        entry: Optional[str] = None,
        cwd: Optional[str] = None,
        selftest_timeout: float = DEFAULT_SELFTEST_TIMEOUT,
        stop_timeout: float = DEFAULT_STOP_TIMEOUT,
    ) -> None:
        self._python = python
        self._entry = entry
        self._cwd = cwd or project_root()
        self._selftest_timeout = float(selftest_timeout)
        self._stop_timeout = float(stop_timeout)
        self._lock = threading.RLock()
        self._proc = None  # type: Optional[subprocess.Popen[Any]]
        self._client = None
        self._state = STATE_STOPPED
        self._reason = REASON_OFFLINE
        self._message = "collector 未启动"
        self._started_at = None  # type: Optional[str]
        self._started_mono = None  # type: Optional[float]
        self._exit_code = None  # type: Optional[int]
        self._protocol_version = None  # type: Optional[str]
        self._python_version = None  # type: Optional[str]
        self._browser = None  # type: Optional[str]
        #: 实际用于启动/预检的路径（环境变量在运行期变化时，状态仍报告真实使用值）。
        self._python_used = None  # type: Optional[str]
        self._entry_used = None  # type: Optional[str]

    # ------------------------------------------------------------------
    # 配置解析与预检
    # ------------------------------------------------------------------

    def _resolve(self) -> Tuple[str, str]:
        python = self._python or os.environ.get(ENV_PYTHON) or DEFAULT_PYTHON
        entry = self._entry or os.environ.get(ENV_MAIN) or DEFAULT_ENTRY
        entry = os.path.expanduser(str(entry))
        if not os.path.isabs(entry):
            entry = os.path.join(self._cwd, entry)
        return _resolve_executable(str(python)), os.path.abspath(entry)

    def _preflight(self, python: str, entry: str) -> Optional[Tuple[str, str]]:
        if not os.path.isfile(entry):
            return REASON_ENTRY_MISSING, "collector 入口不存在：%s" % entry
        if not _is_executable(python):
            return REASON_PYTHON_MISSING, "collector 解释器不存在或不可执行：%s" % python
        return None

    # ------------------------------------------------------------------
    # 诊断 / 启动 / 停止 / 状态
    # ------------------------------------------------------------------

    def probe(self, timeout: Optional[float] = None) -> Dict[str, Any]:
        """运行 `--self-test`，返回**安全**的可用性结论（不返回 stdout/stderr）。

        失败原因码：入口缺失、解释器缺失、协议/Python 版本不匹配、浏览器缺失、
        自检超时、自检失败。原始输出可能含路径或凭据，一律只在 `logger` 里留摘要，
        不进入返回值。
        """
        limit = self._selftest_timeout if timeout is None else float(timeout)
        python, entry = self._resolve()
        result = {
            "available": False,
            "reason_code": None,
            "message": "",
            "protocol_version": None,
            "python_version": None,
            "browser": None,
            "python": python,
            "entry": entry,
        }  # type: Dict[str, Any]

        missing = self._preflight(python, entry)
        if missing is not None:
            result["reason_code"], result["message"] = missing
            return result

        try:
            completed = subprocess.run(
                [python, entry, "--self-test"],
                cwd=self._cwd,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                shell=False,
                timeout=limit,
            )
        except subprocess.TimeoutExpired:
            result["reason_code"] = REASON_SELFTEST_TIMEOUT
            result["message"] = "collector 自检在 %.1f 秒内没有返回" % limit
            return result
        except OSError as exc:
            logger.warning("collector 自检无法启动（%s）", type(exc).__name__)
            result["reason_code"] = REASON_START_FAILED
            result["message"] = "collector 自检无法启动（%s）" % type(exc).__name__
            return result

        payload = _last_json_object(completed.stdout)
        if payload is None:
            logger.warning(
                "collector 自检没有输出协议 JSON（exit=%s，stderr=%d 字节）",
                completed.returncode,
                len(completed.stderr or b""),
            )
            result["reason_code"] = REASON_START_FAILED
            result["message"] = "collector 自检没有输出协议 JSON（exit=%s）" % completed.returncode
            return result

        protocol = payload.get("protocol_version")
        python_version = payload.get("python_version")
        browser = payload.get("browser")
        result["protocol_version"] = protocol if isinstance(protocol, str) else None
        result["python_version"] = python_version if isinstance(python_version, str) else None
        result["browser"] = browser if isinstance(browser, str) else None

        if protocol != PROTOCOL_VERSION:
            result["reason_code"] = REASON_VERSION_MISMATCH
            result["message"] = "collector 协议版本不匹配：期望 %s，实际 %s" % (
                PROTOCOL_VERSION,
                protocol,
            )
            return result
        if payload.get("python_ok") is not True:
            result["reason_code"] = REASON_VERSION_MISMATCH
            result["message"] = "collector 的 Python 版本不满足要求（当前 %s）" % (
                python_version or "未知",
            )
            return result
        if browser != "available":
            result["reason_code"] = REASON_BROWSER_MISSING
            result["message"] = "collector 浏览器/Playwright 不可用"
            return result
        if payload.get("ok") is not True or completed.returncode != 0:
            result["reason_code"] = REASON_START_FAILED
            result["message"] = "collector 自检失败（exit=%s）" % completed.returncode
            return result

        result["available"] = True
        result["message"] = "collector 自检通过"
        return result

    def start(self, timeout: Optional[float] = None) -> Dict[str, Any]:
        """先自检再启动；返回启动后的状态快照（失败时 state=unavailable/failed）。

        默认总是自检（缺口令行开关）：这样“浏览器缺失/版本不匹配”在启动前就是可观察的
        失败，而不是起一个半死的子进程。`timeout` 是自检上限。
        """
        with self._lock:
            self._reap_locked()
            if self._proc is not None:
                return self._snapshot_locked()

            python, entry = self._resolve()
            self._python_used, self._entry_used = python, entry
            probe = self.probe(timeout)
            if not probe["available"]:
                self._state = STATE_UNAVAILABLE
                self._reason = probe["reason_code"] or REASON_START_FAILED
                self._message = probe["message"]
                self._clear_runtime_locked()
                self._protocol_version = probe.get("protocol_version")
                self._python_version = probe.get("python_version")
                self._browser = probe.get("browser")
                return self._snapshot_locked()

            self._state = STATE_STARTING
            self._reason = None
            self._message = "collector 正在启动"
            try:
                proc = self._spawn(python, entry)
            except OSError as exc:
                logger.warning("collector 启动失败（%s）", type(exc).__name__)
                self._state = STATE_UNAVAILABLE
                self._reason = REASON_START_FAILED
                self._message = "collector 启动失败（%s）" % type(exc).__name__
                self._clear_runtime_locked()
                return self._snapshot_locked()

            self._proc = proc
            self._started_at = _utc_now()
            self._started_mono = time.monotonic()
            self._exit_code = None
            self._protocol_version = probe.get("protocol_version")
            self._python_version = probe.get("python_version")
            self._browser = probe.get("browser")
            self._state = STATE_ONLINE
            self._reason = None
            self._message = "collector 已启动"

            # 起来就崩的子进程不要留成假 online；短暂观察足够抓到导入期错误。
            try:
                code = proc.wait(timeout=_START_GRACE_SECONDS)
            except subprocess.TimeoutExpired:
                pass
            else:
                self._exit_code = code
                self._proc = None
                if code == 0:
                    self._state = STATE_STOPPED
                    self._reason = REASON_OFFLINE
                    self._message = "collector 启动后立即退出（exit=0）"
                else:
                    self._state = STATE_FAILED
                    self._reason = REASON_EXITED
                    self._message = "collector 启动后立即退出（exit=%s）" % code
                self._clear_runtime_locked()
            return self._snapshot_locked()

    def stop(self, timeout: Optional[float] = None) -> Dict[str, Any]:
        """停止子进程树；**进程真正退出之后**才返回（幂等）。

        POSIX 先 `killpg(SIGTERM)`，超时 `killpg(SIGKILL)`；Windows 用
        `taskkill /PID <pid> /T /F`。返回最终状态快照：正常停止
        `state=stopped`，强制结束 `state=failed` 且
        `reason_code=collector_stop_timeout`（可观察的失败，不静默）。
        """
        limit = self._stop_timeout if timeout is None else float(timeout)
        with self._lock:
            self._reap_locked()
            proc = self._proc
            if proc is None:
                return self._snapshot_locked()

            self._close_stdin_locked(proc)
            self._request_stop(proc)

            deadline = time.monotonic() + max(0.0, limit)
            try:
                proc.wait(timeout=max(0.0, deadline - time.monotonic()))
            except subprocess.TimeoutExpired:
                self._force_kill(proc)
                try:
                    proc.wait(timeout=_FORCE_WAIT_SECONDS)
                except subprocess.TimeoutExpired:
                    self._state = STATE_FAILED
                    self._reason = REASON_STOP_TIMEOUT
                    self._message = "collector 未在 %.1f 秒内退出，强制结束仍未回收" % limit
                    return self._snapshot_locked()
                self._exit_code = proc.returncode
                self._proc = None
                self._state = STATE_FAILED
                self._reason = REASON_STOP_TIMEOUT
                self._message = "collector 未在 %.1f 秒内退出，已强制结束整棵进程树" % limit
                self._clear_runtime_locked()
                return self._snapshot_locked()

            self._exit_code = proc.returncode
            self._proc = None
            self._state = STATE_STOPPED
            self._reason = REASON_OFFLINE
            self._message = "collector 已停止"
            self._clear_runtime_locked()
            return self._snapshot_locked()

    def status(self) -> Dict[str, Any]:
        """当前状态快照；永不抛异常，只含 `STATUS_KEYS`。"""
        with self._lock:
            self._reap_locked()
            return self._snapshot_locked()

    def diagnose(self, timeout: Optional[float] = None) -> Dict[str, Any]:
        """诊断包：当前状态 + 重新跑一次自检（含版本/浏览器原因码）。"""
        return {"status": self.status(), "probe": self.probe(timeout)}

    def client(self):
        """Lazy binary IPC client; manager remains the sole owner of process lifetime."""
        from .collector_client import CollectorClient, CollectorClientError
        with self._lock:
            state = self.start()
            if not state["available"]:
                raise CollectorClientError(state.get("reason_code") or REASON_OFFLINE)
            if self._client is None:
                self._client = CollectorClient(self._proc.stdin, self._proc.stdout, "inst-" + secrets.token_hex(12))
            return self._client

    # ------------------------------------------------------------------
    # 内部
    # ------------------------------------------------------------------

    def _spawn(self, python: str, entry: str) -> "subprocess.Popen[Any]":
        kwargs = {
            "cwd": self._cwd,
            "stdin": subprocess.PIPE,
            "stdout": subprocess.PIPE,
            "stderr": subprocess.DEVNULL,
            "shell": False,
            "close_fds": True,
        }  # type: Dict[str, Any]
        if os.name == "nt":
            kwargs["creationflags"] = _WINDOWS_CREATION_FLAGS
        else:
            # 自成会话/进程组：停止时按组回收，Chromium 不会掉队。
            kwargs["start_new_session"] = True
        # 命令行**只有**解释器与入口；凭据/密码一律不许走 argv（M16-15 走私有 stdio）。
        return subprocess.Popen([python, entry], **kwargs)

    def _close_stdin_locked(self, proc: "subprocess.Popen[Any]") -> None:
        stream = getattr(proc, "stdin", None)
        if stream is None:
            return
        try:
            stream.close()
        except OSError:
            pass

    def _request_stop(self, proc: "subprocess.Popen[Any]") -> None:
        if os.name == "nt":
            if not self._windows_tree_kill(proc.pid):
                try:
                    proc.terminate()
                except OSError:
                    pass
            return
        _signal_group(proc, signal.SIGTERM)

    def _force_kill(self, proc: "subprocess.Popen[Any]") -> None:
        if os.name == "nt":
            if not self._windows_tree_kill(proc.pid):
                try:
                    proc.kill()
                except OSError:
                    pass
            return
        _signal_group(proc, getattr(signal, "SIGKILL", 9))

    def _windows_tree_kill(self, pid: int) -> bool:
        try:
            completed = subprocess.run(
                windows_tree_kill_command(pid),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                shell=False,
                timeout=_WINDOWS_TASKKILL_TIMEOUT,
                creationflags=int(getattr(subprocess, "CREATE_NO_WINDOW", 0)),
            )
        except (OSError, subprocess.TimeoutExpired):
            return False
        return completed.returncode == 0

    def _reap_locked(self) -> None:
        proc = self._proc
        if proc is None:
            return
        code = proc.poll()
        if code is None:
            return
        self._exit_code = code
        self._proc = None
        if self._state == STATE_STARTING:
            self._state = STATE_FAILED
            self._reason = REASON_EXITED
            self._message = "collector 启动过程中退出（exit=%s）" % code
        elif code == 0:
            self._state = STATE_STOPPED
            self._reason = REASON_OFFLINE
            self._message = "collector 已退出"
        else:
            self._state = STATE_FAILED
            self._reason = REASON_EXITED
            self._message = "collector 意外退出（exit=%s）" % code
        self._clear_runtime_locked()

    def _clear_runtime_locked(self) -> None:
        if self._client is not None:
            self._client.close()
            self._client = None
        self._started_at = None
        self._started_mono = None

    def _snapshot_locked(self) -> Dict[str, Any]:
        python = self._python_used
        entry = self._entry_used
        if python is None or entry is None:
            python, entry = self._resolve()
        proc = self._proc
        alive = proc is not None and proc.poll() is None
        if not alive and self._state == STATE_ONLINE:
            # 进程刚走、还没经过 `_reap_locked` 的窗口：按安全口径报 failed/stopped。
            self._reap_locked()
            proc = self._proc
            alive = proc is not None and proc.poll() is None

        uptime = None
        if alive and self._started_mono is not None:
            uptime = round(max(0.0, time.monotonic() - self._started_mono), 1)

        snapshot = {
            "state": STATE_ONLINE if (alive and self._state == STATE_ONLINE) else self._state,
            "available": bool(alive) and self._state == STATE_ONLINE,
            "reason_code": self._reason,
            "message": self._message,
            "pid": proc.pid if alive else None,
            "started_at": self._started_at if alive else None,
            "uptime_seconds": uptime,
            "exit_code": self._exit_code,
            "protocol_version": self._protocol_version,
            "python_version": self._python_version,
            "browser": self._browser,
            "python": python,
            "entry": entry,
            "process_group": os.name != "nt",
            "tree_kill": "taskkill /T /F" if os.name == "nt" else "killpg SIGTERM→SIGKILL",
        }
        return {key: snapshot[key] for key in sorted(STATUS_KEYS)}


# ---------------------------------------------------------------------- 单例

_manager = None  # type: Optional[CollectorManager]
_manager_lock = threading.Lock()


def init_manager(
    python: Optional[str] = None,
    entry: Optional[str] = None,
    cwd: Optional[str] = None,
    stop_timeout: float = DEFAULT_STOP_TIMEOUT,
) -> CollectorManager:
    """替换进程内单例；旧实例先停干净，避免留下孤儿进程。"""
    global _manager
    with _manager_lock:
        previous = _manager
    if previous is not None:
        previous.stop()
    manager = CollectorManager(
        python=python, entry=entry, cwd=cwd, stop_timeout=stop_timeout
    )
    with _manager_lock:
        _manager = manager
    return manager


def get_manager() -> CollectorManager:
    """懒建单例；还没注入时按环境变量/默认路径解析（不启动进程）。"""
    global _manager
    with _manager_lock:
        if _manager is None:
            _manager = CollectorManager()
        return _manager


def reset_manager(timeout: Optional[float] = None) -> bool:
    """停掉并清空单例（测试与退出路径用）；返回进程是否已经不在。"""
    global _manager
    with _manager_lock:
        previous, _manager = _manager, None
    if previous is None:
        return True
    status = previous.stop(timeout)
    return status["pid"] is None


def stop_all(timeout: Optional[float] = None) -> bool:
    """停止当前受管 collector；没有实例时是无副作用的 no-op。

    `run.ordered_stop` 在排空任务、关 HTTP 之前调用它，因此 SIGTERM 停止序列里
    collector 一定先于单实例锁释放而收干净。
    """
    with _manager_lock:
        manager = _manager
    if manager is None:
        return True
    status = manager.stop(timeout)
    return status["pid"] is None
