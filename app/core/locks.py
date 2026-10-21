"""并发原语：机型锁、注册表锁、维护态写闸门（规范 §3.5、T-02）。

本模块只提供原语，不把它们插入业务函数（那是 M0-06 的事），也不做任何
网络 I/O。三条规则：

* 任何"读-改-写"权威 JSON 或配置文件必须在 `profile_lock` 内完成整个过程；
* `device_models.json` 的读改写用 `registry_lock`；
* 备份/恢复等场景下进入维护态：新的写入被拒绝（HTTP 层映射 503
  `MAINTENANCE`），读取不受影响。
"""

from __future__ import annotations

import threading
import time
from contextlib import contextmanager
from typing import Dict, Iterator, Optional

from .errors import MAINTENANCE, AppError

# ---------------------------------------------------------------------------
# 机型锁
# ---------------------------------------------------------------------------

#: pid -> RLock。创建过程由 _locks_guard 保护，避免同一 pid 拿到两把锁。
_profile_locks: Dict[str, threading.RLock] = {}
_locks_guard = threading.Lock()


def profile_lock(pid: str) -> threading.RLock:
    """返回 `pid` 对应的可重入锁；同一 pid 永远返回同一把对象。"""
    key = str(pid)
    lock = _profile_locks.get(key)
    if lock is not None:
        return lock
    with _locks_guard:
        lock = _profile_locks.get(key)
        if lock is None:
            lock = threading.RLock()
            _profile_locks[key] = lock
        return lock


def profile_lock_count() -> int:
    """已创建的机型锁数量。测试与诊断用。"""
    return len(_profile_locks)


# ---------------------------------------------------------------------------
# 注册表锁
# ---------------------------------------------------------------------------

#: 保护 `device_models.json` 的读改写。
registry_lock = threading.RLock()


# ---------------------------------------------------------------------------
# 维护态写闸门
# ---------------------------------------------------------------------------


class WriteGate:
    """写入计数 + 维护态闸门。

    语义：

    * `enter_write()` / `exit_write()` 成对使用，统计进行中的写入数；
    * `enter_maintenance()` 会**等待**进行中的写入结束，之后新的
      `enter_write()` 一律抛 `AppError(MAINTENANCE)`；
    * 读取路径完全不经过闸门，因此维护期间读不受影响。

    注意：调用 `maintenance()` 的线程自身不能持有写入，否则会等到超时。
    """

    def __init__(self, maintenance_timeout: float = 30.0) -> None:
        self._maintenance_timeout = maintenance_timeout
        self._cond = threading.Condition(threading.Lock())
        self._writers = 0
        self._in_maintenance = False

    # -- 写入 ---------------------------------------------------------------

    def enter_write(self) -> None:
        with self._cond:
            if self._in_maintenance:
                raise AppError(MAINTENANCE, "系统处于维护状态，暂不接受写入")
            self._writers += 1

    def exit_write(self) -> None:
        with self._cond:
            if self._writers > 0:
                self._writers -= 1
            if self._writers <= 0:
                self._writers = 0
                self._cond.notify_all()

    @contextmanager
    def write(self) -> Iterator[None]:
        self.enter_write()
        try:
            yield
        finally:
            self.exit_write()

    # -- 维护 ---------------------------------------------------------------

    def enter_maintenance(self, timeout: Optional[float] = None) -> None:
        """进入维护态，等待进行中的写入结束。

        超时抛 `AppError(MAINTENANCE)`——宁可失败，也不要在写入进行中进入维护态。
        """
        limit = self._maintenance_timeout if timeout is None else timeout
        with self._cond:
            deadline = time.monotonic() + limit
            while self._writers > 0:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise AppError(MAINTENANCE, "等待进行中的写入结束超时")
                self._cond.wait(remaining)
            self._in_maintenance = True

    def exit_maintenance(self) -> None:
        with self._cond:
            self._in_maintenance = False
            self._cond.notify_all()

    @contextmanager
    def maintenance(self, timeout: Optional[float] = None) -> Iterator[None]:
        self.enter_maintenance(timeout)
        try:
            yield
        finally:
            self.exit_maintenance()

    # -- 观察 ---------------------------------------------------------------

    @property
    def writers(self) -> int:
        with self._cond:
            return self._writers

    @property
    def in_maintenance(self) -> bool:
        with self._cond:
            return self._in_maintenance


#: 进程级共享的写闸门。业务代码用 `with write_gate.write():`，备份/恢复工具用
#: `with write_gate.maintenance():`。
write_gate = WriteGate()
