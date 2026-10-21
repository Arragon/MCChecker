"""Python 源码热更新：只监视服务端 .py，变化稳定后换进程，崩溃不自动拉起。"""

from __future__ import annotations

import os
import signal

from app.server import reloader


class _Proc:
    def __init__(self):
        self.returncode = None
        self.signals = []

    def poll(self):
        return self.returncode

    def send_signal(self, signum):
        self.signals.append(signum)
        self.returncode = 0

    def wait(self, timeout=None):
        return self.returncode

    def kill(self):
        self.returncode = -9


def test_signature_ignores_pycache_and_sees_edits(tmp_path):
    app_dir = tmp_path / "app" / "server"
    app_dir.mkdir(parents=True)
    (tmp_path / "app" / "__pycache__").mkdir()
    (tmp_path / "app" / "__pycache__" / "x.pyc").write_bytes(b"pyc")
    source = app_dir / "http.py"
    source.write_text("x = 1\n", encoding="utf-8")
    (tmp_path / "run.py").write_text("", encoding="utf-8")
    (tmp_path / "main.py").write_text("", encoding="utf-8")

    first = reloader.source_signature(str(tmp_path))
    paths = [item[0] for item in first]
    assert str(source) in paths
    assert not any(path.endswith(".pyc") for path in paths)

    stat = source.stat()
    os.utime(source, (stat.st_atime, stat.st_mtime + 5))
    second = reloader.source_signature(str(tmp_path))
    assert second != first


def test_source_change_restarts_child_once_it_is_stable():
    launched = []
    state = {"sig": (("a.py", 1, 1),), "t": 0.0, "stop": False}

    def popen(command, env):
        assert env[reloader.CHILD_ENV] == "1"
        assert command[0]
        proc = _Proc()
        launched.append(proc)
        return proc

    def signature():
        return state["sig"]

    def clock():
        return state["t"]

    def pause(seconds):
        state["t"] += seconds
        if len(launched) >= 2:
            state["stop"] = True

    def flip():
        # 第一次取基线，之后源码变了就保持新签名。
        if state["t"] > 0:
            state["sig"] = (("a.py", 2, 1),)
        return state["sig"]

    code = reloader.supervise(
        ["--port", "50003"],
        popen=popen,
        signature=flip,
        sleep=pause,
        monotonic=clock,
        settle=0.4,
        interval=0.2,
        should_stop=lambda: state["stop"],
    )

    assert code == 0
    assert len(launched) == 2
    assert launched[0].signals == [signal.SIGTERM]
    assert launched[1].signals == [signal.SIGTERM]


def test_child_crash_is_not_restarted():
    launched = []

    def popen(command, env):
        proc = _Proc()
        proc.returncode = 4
        launched.append(proc)
        return proc

    code = reloader.supervise(
        [],
        popen=popen,
        signature=lambda: (("a.py", 1, 1),),
        sleep=lambda _seconds: None,
        monotonic=lambda: 0.0,
        should_stop=lambda: False,
    )
    assert code == 4
    assert len(launched) == 1


def test_no_reload_flag_and_env(monkeypatch):
    from app import config

    assert config.load_settings([]).reload is True
    assert config.load_settings(["--no-reload"]).reload is False
    assert config.load_settings(["--reload"]).reload is True
    monkeypatch.setenv("MCHECKER_RELOAD", "off")
    assert config.load_settings([]).reload is False
    assert config.load_settings(["--reload"]).reload is True
