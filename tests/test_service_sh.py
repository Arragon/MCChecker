"""Linux 后台启停脚本（service.sh）。

用一个只写锁、不起 HTTP 的 run.py。不断言真实服务的端口监听。
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]

pytestmark = pytest.mark.skipif(
    os.name == "nt", reason="service.sh 依赖 POSIX sh/signal 语义，Windows 无对应服务脚本"
)

RUN_PY = r"""
import json, os, signal, sys, time
args = sys.argv[1:]
data = None
port = 1
i = 0
while i < len(args):
    if args[i] == "--data-dir" and i + 1 < len(args):
        data = args[i + 1]
        i += 2
        continue
    if args[i].startswith("--data-dir="):
        data = args[i].split("=", 1)[1]
    elif args[i] == "--port" and i + 1 < len(args):
        port = int(args[i + 1])
        i += 2
        continue
    elif args[i].startswith("--port="):
        port = int(args[i].split("=", 1)[1])
    i += 1
if not data:
    data = "data"
data = os.path.abspath(data)
os.makedirs(data, exist_ok=True)
with open(os.path.join(data, "argv.txt"), "a", encoding="utf-8") as fh:
    fh.write(repr(args) + "\n")
lock = os.path.join(data, ".mcchecker.lock")
def stop(_signum, _frame):
    try:
        os.remove(lock)
    except OSError:
        pass
    raise SystemExit(0)
signal.signal(signal.SIGTERM, stop)
signal.signal(signal.SIGINT, stop)
with open(lock, "w", encoding="utf-8") as fh:
    json.dump({"pid": os.getpid(), "port": port, "started_at": "2026-01-01 00:00:00"}, fh)
while True:
    time.sleep(0.2)
"""

IGNORE_PY = r"""
import json, os, signal, sys, time
args = sys.argv[1:]
data = None
i = 0
while i < len(args):
    if args[i] == "--data-dir" and i + 1 < len(args):
        data = args[i + 1]
        i += 2
        continue
    if args[i].startswith("--data-dir="):
        data = args[i].split("=", 1)[1]
    i += 1
if not data:
    data = "data"
data = os.path.abspath(data)
os.makedirs(data, exist_ok=True)
lock = os.path.join(data, ".mcchecker.lock")
signal.signal(signal.SIGTERM, signal.SIG_IGN)
with open(lock, "w", encoding="utf-8") as fh:
    json.dump({"pid": os.getpid(), "port": 1, "started_at": "2026-01-01 00:00:00"}, fh)
while True:
    time.sleep(0.2)
"""


def service_env(tmp_path: Path, **extra: str) -> dict:
    path = os.pathsep.join([str(Path(sys.executable).parent), "/usr/bin", "/bin"])
    env = {
        "PATH": path,
        "HOME": str(tmp_path / "home"),
        "MCHECKER_PYTHON": sys.executable,
        "MCHECKER_SERVICE_START_TIMEOUT": "5",
        "MCHECKER_SERVICE_STOP_TIMEOUT": "5",
    }
    env.update(extra)
    return env


def install_release(root: Path, run_py: str) -> None:
    root.mkdir(parents=True, exist_ok=True)
    for name in ("service.sh", "start.sh"):
        target = root / name
        target.write_text((REPO_ROOT / name).read_text(encoding="utf-8"), encoding="utf-8")
        target.chmod(0o755)
    app = root / "app"
    app.mkdir(exist_ok=True)
    (app / "config.py").write_text(
        (REPO_ROOT / "app" / "config.py").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    (root / "run.py").write_text(run_py, encoding="utf-8")


def run_service(root: Path, env: dict, args: list) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["/bin/sh", str(root / "service.sh")] + args,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        universal_newlines=True,
        env=env,
        cwd=str(root),
        timeout=30,
    )


def read_pid(data: Path) -> int:
    return int((data / ".mcchecker.service.pid").read_text(encoding="utf-8").strip())


def cleanup(data: Path) -> None:
    pid_file = data / ".mcchecker.service.pid"
    if not pid_file.is_file():
        return
    try:
        pid = int(pid_file.read_text(encoding="utf-8").strip())
    except ValueError:
        return
    os.kill(pid, signal.SIGKILL)
    try:
        os.waitpid(pid, 0)
    except ChildProcessError:
        pass


def test_library_mode_does_not_start(tmp_path: Path) -> None:
    env = service_env(tmp_path)
    env["MCHECKER_SERVICE_LIB"] = "0"
    proc = subprocess.run(
        ["/bin/sh", "-c", ". '%s'; printf 'sourced\\n'" % (REPO_ROOT / "service.sh")],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        universal_newlines=True,
        env=env,
        cwd=str(REPO_ROOT),
        timeout=30,
    )
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == "sourced"


def test_start_status_stop_and_second_start_refused(tmp_path: Path) -> None:
    root = tmp_path / "release"
    data = tmp_path / "svc-data"
    install_release(root, RUN_PY)
    env = service_env(tmp_path)
    try:
        started = run_service(root, env, ["start", "--data-dir", str(data), "--port", "9"])
        assert started.returncode == 0, started.stderr + started.stdout
        assert "已在后台启动" in started.stdout
        assert "端口=9" in started.stdout
        assert (data / ".mcchecker.lock").is_file()

        status = run_service(root, env, ["status"])
        assert status.returncode == 0, status.stdout
        assert "状态：运行中" in status.stdout
        assert str(data) in status.stdout

        again = run_service(root, env, ["start", "--data-dir", str(data)])
        assert again.returncode == 1
        assert "已经在运行" in again.stderr

        logs = run_service(root, env, ["logs"])
        assert logs.returncode == 0
        assert "service.out" in logs.stdout

        stopped = run_service(root, env, ["stop"])
        assert stopped.returncode == 0, stopped.stderr + stopped.stdout
        assert "已停止" in stopped.stdout
        assert not (data / ".mcchecker.service.pid").exists()

        idle = run_service(root, env, ["status"])
        assert idle.returncode == 1
        assert "状态：未运行" in idle.stdout
    finally:
        cleanup(data)


def test_restart_replays_saved_args(tmp_path: Path) -> None:
    root = tmp_path / "release"
    data = tmp_path / "replay data"
    install_release(root, RUN_PY)
    env = service_env(tmp_path)
    try:
        started = run_service(root, env, ["start", "--data-dir", str(data), "--port", "9"])
        assert started.returncode == 0, started.stderr + started.stdout
        restarted = run_service(root, env, ["restart"])
        assert restarted.returncode == 0, restarted.stderr + restarted.stdout
        argv_log = (data / "argv.txt").read_text(encoding="utf-8")
        assert argv_log.count("--port") >= 2
        assert str(data) in argv_log
        stopped = run_service(root, env, ["stop"])
        assert stopped.returncode == 0, stopped.stderr
    finally:
        cleanup(data)


def test_relative_data_dir_is_remembered(tmp_path: Path) -> None:
    root = tmp_path / "release"
    install_release(root, RUN_PY)
    env = service_env(tmp_path)
    data = root / "rel-data"
    try:
        started = run_service(root, env, ["start", "--data-dir", "rel-data", "--port", "9"])
        assert started.returncode == 0, started.stderr + started.stdout
        away = subprocess.run(
            ["/bin/sh", str(root / "service.sh"), "status"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            universal_newlines=True,
            env=env,
            cwd="/",
            timeout=30,
        )
        assert away.returncode == 0, away.stdout + away.stderr
        assert str(data) in away.stdout
        stopped = run_service(root, env, ["stop"])
        assert stopped.returncode == 0, stopped.stderr
    finally:
        cleanup(data)


def test_stop_does_not_sigkill_when_process_ignores_term(tmp_path: Path) -> None:
    root = tmp_path / "release"
    data = tmp_path / "stuck-data"
    install_release(root, IGNORE_PY)
    env = service_env(tmp_path, MCHECKER_SERVICE_STOP_TIMEOUT="1")
    pid = None
    try:
        started = run_service(root, env, ["start", "--data-dir", str(data), "--port", "9"])
        assert started.returncode == 0, started.stderr + started.stdout
        pid = read_pid(data)
        stopped = run_service(root, env, ["stop"])
        assert stopped.returncode == 1
        assert "锁文件保留" in stopped.stderr
        assert "SIGKILL" not in stopped.stderr
        os.kill(pid, 0)  # 仍在
        assert (data / ".mcchecker.lock").is_file()
    finally:
        if pid:
            os.kill(pid, signal.SIGKILL)
            try:
                os.waitpid(pid, 0)
            except ChildProcessError:
                pass


def test_unit_uses_foreground_start_without_reload(tmp_path: Path) -> None:
    root = tmp_path / "release"
    install_release(root, "raise SystemExit(0)\n")
    env = service_env(tmp_path)
    data = tmp_path / "unit data"
    proc = run_service(root, env, ["unit", "--data-dir", str(data), "--reload"])
    assert proc.returncode == 0, proc.stderr
    text = proc.stdout
    assert "Type=simple" in text
    assert "KillSignal=SIGTERM" in text
    assert "TimeoutStopSec=5" in text
    assert "--no-reload" in text
    assert "--reload" not in text.split("--no-reload", 1)[-1]
    assert "'%s'" % (root / "start.sh") in text
    assert "'%s'" % data in text
    assert "service.sh start" in text


def test_script_never_sends_sigkill() -> None:
    text = (REPO_ROOT / "service.sh").read_text(encoding="utf-8")
    for line in text.splitlines():
        if line.lstrip().startswith("#"):
            continue
        assert "SIGKILL" not in line
        assert "kill -9" not in line
        assert "kill -KILL" not in line
