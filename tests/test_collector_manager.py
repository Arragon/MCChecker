"""M16-14 受管 collector 子进程测试（INH-2179）。

用 `tmp_path` 下的 dummy 子进程脚本验证 start/status/stop、缺失入口/解释器、
自检失败、协议/Python 版本不匹配、立即崩溃、停止超时与进程组回收；
不伪造真实 Playwright/Chromium，也不启动真实浏览器。Windows 专属行为用
`skipif(os.name != "nt")` 隔离，本机（macOS）只验证命令形状。
"""

from __future__ import annotations

import importlib.util
import json
import os
import re
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, List, Optional

import pytest

import run
from app.services import collector_manager

ROOT = Path(collector_manager.project_root())
PROTOCOL = collector_manager.PROTOCOL_VERSION

#: dummy 采集器的固定运行体；参数由 `_write_dummy` 用字面量拼在头部。
_DUMMY_BODY = '''
def dump(path, text):
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)


if "--self-test" in sys.argv:
    if MODE == "selftest_secret":
        sys.stderr.write(SECRET)
        sys.stderr.flush()
        sys.exit(3)
    payload = {
        "ok": OK,
        "protocol_version": PROTOCOL,
        "collector_version": "dummy",
        "python_version": "3.12.0",
        "python_ok": PYTHON_OK,
        "browser": BROWSER,
        "reason_code": None if OK else "collector_browser_missing",
        "message": "dummy self-test",
    }
    print(json.dumps(payload))
    sys.exit(0 if OK else 2)

if MODE == "crash":
    sys.exit(3)

if MODE == "ignore_term":
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
else:
    def _stop(signum, frame):
        dump(MARKER, "term")
        sys.exit(0)

    signal.signal(signal.SIGTERM, _stop)

dump(PIDFILE, str(os.getpid()))
if GRANDCHILD:
    child = subprocess.Popen([sys.executable, GRANDCHILD])
    dump(PIDFILE + ".grandchild", str(child.pid))
while True:
    time.sleep(0.02)
'''

_GRANDCHILD_BODY = '''
import os, sys, time
with open(sys.argv[1], "w", encoding="utf-8") as fh:
    fh.write(str(os.getpid()))
while True:
    time.sleep(0.02)
'''


def _write_dummy(
    tmp_path: Path,
    *,
    name: str = "dummy_collector.py",
    mode: str = "ok",
    protocol: str = PROTOCOL,
    browser: str = "available",
    ok: bool = True,
    python_ok: bool = True,
    grandchild: Optional[str] = None,
    secret: str = "",
) -> Path:
    marker = tmp_path / "collector.term"
    pidfile = tmp_path / "collector.pid"
    header = (
        "import json, os, signal, subprocess, sys, time\n"
        "MODE = %r\nPROTOCOL = %r\nBROWSER = %r\nOK = %r\nPYTHON_OK = %r\n"
        "MARKER = %r\nPIDFILE = %r\nGRANDCHILD = %r\nSECRET = %r\n"
    ) % (
        mode,
        protocol,
        browser,
        ok,
        python_ok,
        str(marker),
        str(pidfile),
        grandchild,
        secret,
    )
    path = tmp_path / name
    path.write_text(header + _DUMMY_BODY, encoding="utf-8")
    return path


def _write_grandchild(tmp_path: Path) -> Optional[str]:
    if os.name == "nt":
        return None  # Windows 的真实进程树测试整条 skip（见下方用例）
    path = tmp_path / "grandchild.py"
    path.write_text(_GRANDCHILD_BODY, encoding="utf-8")
    return str(path)


def _manager(entry: Path, **kwargs: Any) -> collector_manager.CollectorManager:
    return collector_manager.CollectorManager(
        python=sys.executable, entry=str(entry), cwd=str(entry.parent), **kwargs
    )


def _wait_for(predicate, timeout: float = 5.0, message: str = "条件未在预算内成立") -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.02)
    raise AssertionError(message)


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


@pytest.fixture(autouse=True)
def _reset_collector_singleton():
    """单例是进程级的；用例失败时也要收掉 dummy 子进程。"""
    yield
    collector_manager.reset_manager(0.5)


# ---------------------------------------------------------------------- 状态


def test_status_before_start_is_unavailable_without_side_effects(tmp_path):
    entry = _write_dummy(tmp_path)
    status = _manager(entry).status()

    assert set(status) == collector_manager.STATUS_KEYS
    assert status["state"] == collector_manager.STATE_STOPPED
    assert status["available"] is False
    assert status["reason_code"] == collector_manager.REASON_OFFLINE
    assert status["pid"] is None
    assert status["protocol_version"] is None


def test_status_and_diagnose_expose_only_safe_fields(tmp_path):
    entry = _write_dummy(tmp_path)
    manager = _manager(entry)
    manager.start()

    status = manager.status()
    assert set(status) == collector_manager.STATUS_KEYS
    dumped = json.dumps(status, ensure_ascii=False).lower()
    for forbidden in ("password", "cookie", "csrf", "token", "argv", "environment"):
        assert forbidden not in dumped
    assert "selftest" not in dumped

    diagnosis = manager.diagnose()
    assert set(diagnosis) == {"status", "probe"}
    assert set(diagnosis["probe"]) == {
        "available",
        "reason_code",
        "message",
        "protocol_version",
        "python_version",
        "browser",
        "python",
        "entry",
    }


# ---------------------------------------------------------------------- 生命周期


def test_start_status_stop_lifecycle(tmp_path):
    entry = _write_dummy(tmp_path)
    pidfile = tmp_path / "collector.pid"
    marker = tmp_path / "collector.term"
    manager = _manager(entry)

    started = manager.start()
    assert started["state"] == collector_manager.STATE_ONLINE, started
    assert started["available"] is True
    assert started["protocol_version"] == PROTOCOL
    assert started["python_version"] == "3.12.0"
    assert started["browser"] == "available"
    pid = started["pid"]
    assert isinstance(pid, int) and _alive(pid)
    _wait_for(pidfile.exists, message="dummy 未写出 pid 文件")

    # 在线时重复 start 是幂等的，不会换进程。
    again = manager.start()
    assert again["pid"] == pid

    stopped = manager.stop()
    assert stopped["state"] == collector_manager.STATE_STOPPED, stopped
    assert stopped["available"] is False
    assert stopped["pid"] is None
    assert stopped["exit_code"] == 0
    _wait_for(lambda: not _alive(pid), message="dummy 子进程仍在运行")
    assert marker.read_text(encoding="utf-8") == "term"


def test_environment_overrides_select_python_and_entry(tmp_path, monkeypatch):
    entry = _write_dummy(tmp_path)
    monkeypatch.setenv(collector_manager.ENV_PYTHON, sys.executable)
    monkeypatch.setenv(collector_manager.ENV_MAIN, str(entry))

    manager = collector_manager.CollectorManager(cwd=str(tmp_path))
    assert manager.status()["entry"] == str(entry)
    started = manager.start()
    assert started["state"] == collector_manager.STATE_ONLINE, started
    manager.stop()


# ---------------------------------------------------------------------- 启动失败


def test_missing_entry_is_reported_without_spawning(tmp_path):
    manager = _manager(tmp_path / "missing_collector.py")
    status = manager.start()

    assert status["state"] == collector_manager.STATE_UNAVAILABLE
    assert status["available"] is False
    assert status["reason_code"] == collector_manager.REASON_ENTRY_MISSING
    assert status["pid"] is None


def test_missing_python_is_reported_without_spawning(tmp_path):
    entry = _write_dummy(tmp_path)
    manager = collector_manager.CollectorManager(
        python=str(tmp_path / "no-such-python"),
        entry=str(entry),
        cwd=str(tmp_path),
    )
    status = manager.start()

    assert status["state"] == collector_manager.STATE_UNAVAILABLE
    assert status["reason_code"] == collector_manager.REASON_PYTHON_MISSING


def test_browser_missing_is_an_observable_start_failure(tmp_path):
    entry = _write_dummy(tmp_path, ok=False, browser="missing")
    status = _manager(entry).start()

    assert status["state"] == collector_manager.STATE_UNAVAILABLE
    assert status["reason_code"] == collector_manager.REASON_BROWSER_MISSING
    assert status["browser"] == "missing"
    assert status["pid"] is None


def test_protocol_version_mismatch_is_rejected(tmp_path):
    entry = _write_dummy(tmp_path, protocol="m16.collector/999")
    status = _manager(entry).start()

    assert status["state"] == collector_manager.STATE_UNAVAILABLE
    assert status["reason_code"] == collector_manager.REASON_VERSION_MISMATCH
    assert status["protocol_version"] == "m16.collector/999"


def test_python_version_mismatch_is_rejected(tmp_path):
    entry = _write_dummy(tmp_path, python_ok=False)
    status = _manager(entry).start()

    assert status["state"] == collector_manager.STATE_UNAVAILABLE
    assert status["reason_code"] == collector_manager.REASON_VERSION_MISMATCH


def test_selftest_failure_never_leaks_stderr_secrets(tmp_path):
    secret = "s3cr3t-P@ssw0rd-from-target"
    entry = _write_dummy(tmp_path, mode="selftest_secret", secret=secret)
    manager = _manager(entry)

    status = manager.start()
    assert status["state"] == collector_manager.STATE_UNAVAILABLE
    assert status["reason_code"] == collector_manager.REASON_START_FAILED

    dumped = json.dumps(manager.diagnose(), ensure_ascii=False)
    assert secret not in dumped


def test_child_crash_after_start_is_reported(tmp_path):
    entry = _write_dummy(tmp_path, mode="crash")
    manager = _manager(entry)

    started = manager.start()
    assert started["state"] == collector_manager.STATE_FAILED, started
    assert started["reason_code"] == collector_manager.REASON_EXITED
    assert started["exit_code"] == 3
    assert started["pid"] is None

    # 崩溃后的状态是稳定的，再查一次不会变成 online。
    assert manager.status()["state"] == collector_manager.STATE_FAILED


def test_stop_timeout_escalates_and_is_observable(tmp_path):
    entry = _write_dummy(tmp_path, mode="ignore_term")
    pidfile = tmp_path / "collector.pid"
    manager = _manager(entry, stop_timeout=0.5)

    started = manager.start()
    pid = started["pid"]
    _wait_for(pidfile.exists)

    stopped = manager.stop(timeout=0.3)
    assert stopped["state"] == collector_manager.STATE_FAILED, stopped
    assert stopped["reason_code"] == collector_manager.REASON_STOP_TIMEOUT
    assert stopped["pid"] is None
    _wait_for(lambda: not _alive(pid), message="SIGKILL 之后 dummy 仍在运行")


# ---------------------------------------------------------------------- 进程树


@pytest.mark.skipif(os.name == "nt", reason="POSIX 进程组语义；Windows 见下方任务树用例")
def test_stop_reaps_the_whole_process_group(tmp_path):
    grandchild = _write_grandchild(tmp_path)
    assert grandchild is not None
    entry = _write_dummy(tmp_path, grandchild=grandchild)
    manager = _manager(entry)

    manager.start()
    pidfile = tmp_path / "collector.pid.grandchild"
    _wait_for(pidfile.exists, message="grandchild 未启动")
    grandchild_pid = int(pidfile.read_text(encoding="utf-8"))

    assert _alive(grandchild_pid)
    manager.stop()
    _wait_for(
        lambda: not _alive(grandchild_pid),
        message="停止 collector 后 grandchild（模拟 Chromium）仍存活：进程组回收没有生效",
    )


def test_windows_tree_kill_command_shape():
    assert collector_manager.windows_tree_kill_command(4321) == [
        "taskkill",
        "/PID",
        "4321",
        "/T",
        "/F",
    ]


@pytest.mark.skipif(os.name != "nt", reason="仅在 Windows 实机验证进程树回收")
def test_windows_stop_reaps_grandchild(tmp_path):
    child = tmp_path / "grandchild.py"
    child.write_text(_GRANDCHILD_BODY, encoding="utf-8")
    entry = _write_dummy(tmp_path, grandchild=str(child))
    manager = _manager(entry)

    started = manager.start()
    assert started["state"] == collector_manager.STATE_ONLINE, started
    pidfile = tmp_path / "collector.pid.grandchild"
    _wait_for(pidfile.exists)
    grandchild_pid = int(pidfile.read_text(encoding="utf-8"))

    stopped = manager.stop()
    assert stopped["pid"] is None
    _wait_for(lambda: not _alive(grandchild_pid), message="taskkill /T 之后 grandchild 仍存活")


# ---------------------------------------------------------------------- 停止顺序


class _RunnerStub:
    def __init__(self) -> None:
        self.calls: List[str] = []

    def shutdown(self, timeout: float = 30) -> None:
        self.calls.append("runner.shutdown")

    def wait_for_workers(self, timeout: float = 0) -> bool:
        self.calls.append("runner.wait_for_workers")
        return True


class _ServerStub:
    def __init__(self, on_shutdown=None) -> None:
        self.on_shutdown = on_shutdown
        self.calls: List[str] = []

    def shutdown(self) -> None:
        self.calls.append("server.shutdown")
        if self.on_shutdown is not None:
            self.on_shutdown()

    def server_close(self) -> None:
        self.calls.append("server.server_close")


class _LockStub:
    def __init__(self, on_release=None) -> None:
        self.on_release = on_release
        self.released = False

    def release(self) -> None:
        self.released = True
        if self.on_release is not None:
            self.on_release()


def test_stop_and_release_stops_collector_before_lock_release(tmp_path):
    entry = _write_dummy(tmp_path)
    manager = collector_manager.init_manager(
        python=sys.executable, entry=str(entry), cwd=str(tmp_path), stop_timeout=5.0
    )
    started = manager.start()
    pid = started["pid"]
    marker = tmp_path / "collector.term"

    def _assert_collector_gone() -> None:
        assert marker.exists(), "collector 还没有收到 SIGTERM"
        assert not _alive(pid), "collector 进程仍在，却已经走到关 HTTP/释放锁"

    server = _ServerStub(on_shutdown=_assert_collector_gone)
    lock = _LockStub(on_release=_assert_collector_gone)
    runner = _RunnerStub()

    assert run.stop_and_release(runner, server, lock, exit_timeout=2) is True
    assert lock.released is True
    assert runner.calls == ["runner.shutdown", "runner.wait_for_workers"]


@pytest.mark.skipif(os.name == "nt", reason="Windows 的 os.kill 不是 SIGTERM 语义")
def test_sigterm_cleanup_finishes_before_the_driver_exits(tmp_path):
    """真实 SIGTERM：driver 在处理器里 stop collector，退出前子进程必须已经消失。"""
    entry = _write_dummy(tmp_path)
    ready = tmp_path / "driver.ready.json"
    report = tmp_path / "driver.stop.json"
    driver = tmp_path / "driver.py"
    driver.write_text(
        (
            "import json, os, signal, sys, time\n"
            "sys.path.insert(0, %r)\n"
            "from app.services import collector_manager\n"
            "manager = collector_manager.init_manager(python=%r, entry=%r, stop_timeout=5.0)\n"
            "started = manager.start()\n"
            "json.dump({'pid': started['pid'], 'state': started['state']}, open(%r, 'w'))\n"
            "def _term(signum, frame):\n"
            "    status = manager.stop()\n"
            "    json.dump({'state': status['state'], 'pid': status['pid']}, open(%r, 'w'))\n"
            "    sys.exit(0)\n"
            "signal.signal(signal.SIGTERM, _term)\n"
            "while True:\n"
            "    time.sleep(0.02)\n"
        )
        % (str(ROOT), sys.executable, str(entry), str(ready), str(report)),
        encoding="utf-8",
    )

    proc = subprocess.Popen(
        [sys.executable, str(driver)],
        cwd=str(ROOT),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        _wait_for(ready.exists, message="driver 未写出 ready 文件")
        collector_pid = int(json.loads(ready.read_text(encoding="utf-8"))["pid"])
        assert _alive(collector_pid)

        proc.send_signal(signal.SIGTERM)
        stdout, stderr = proc.communicate(timeout=10)
        assert proc.returncode == 0, (stdout, stderr)

        stopped = json.loads(report.read_text(encoding="utf-8"))
        assert stopped == {"state": "stopped", "pid": None}
        assert not _alive(collector_pid)
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.communicate(timeout=5)


# ---------------------------------------------------------------------- 边界守卫


def test_core_import_graph_never_imports_the_collector_package():
    paths = [ROOT / "run.py", ROOT / "main.py", ROOT / "app" / "__init__.py"]
    paths.extend(sorted((ROOT / "app").rglob("*.py")))
    offenders = []
    pattern = re.compile(r"(?m)^\s*(?:import|from)\s+collector(?:\.|\s|$)")
    for path in paths:
        text = path.read_text(encoding="utf-8", errors="replace")
        if pattern.search(text):
            offenders.append(str(path.relative_to(ROOT)))
    assert offenders == [], "主应用 import 图引用了 collector 包：%s" % offenders


def test_release_manifest_and_core_requirements_exclude_collector():
    spec = importlib.util.spec_from_file_location(
        "make_release_m16_14_guard", ROOT / "scripts" / "make_release.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    assert all(not entry.startswith("collector") for entry in module.RELEASE_ENTRIES), (
        "collector 不能进核心发布包（设计 D12：collector 是独立 bundle）"
    )

    lines = [
        line.strip()
        for line in (ROOT / "requirements.txt").read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]
    assert lines == [], "核心 requirements.txt 必须保持零第三方依赖"
    joined = "\n".join(lines).lower()
    for package in ("playwright", "chromium", "cryptography"):
        assert package not in joined


def test_collector_protocol_version_matches_the_manager():
    text = (ROOT / "collector" / "main.py").read_text(encoding="utf-8")
    match = re.search(r'(?m)^PROTOCOL_VERSION\s*=\s*"([^"]+)"', text)
    assert match is not None
    assert match.group(1) == collector_manager.PROTOCOL_VERSION


def test_real_skeleton_self_test_reports_its_environment_honestly():
    entry = ROOT / "collector" / "main.py"
    completed = subprocess.run(
        [sys.executable, str(entry), "--self-test"],
        cwd=str(ROOT),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=30,
    )
    payload = json.loads(completed.stdout.decode("utf-8").strip().splitlines()[-1])
    assert payload["protocol_version"] == PROTOCOL

    if tuple(sys.version_info[:2]) < (3, 12):
        assert payload["python_ok"] is False
        assert payload["reason_code"] == collector_manager.REASON_VERSION_MISMATCH
        assert payload["ok"] is False
        assert completed.returncode == 2
    else:
        # 本地没有 Playwright 时如实报 missing；装齐依赖的机器可以 ok=true。
        assert payload["ok"] is (payload["browser"] == "available")
        assert completed.returncode == (0 if payload["ok"] else 2)
