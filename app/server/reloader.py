"""Python 源码热更新：监视 `.py`，变化后把服务进程停掉再拉起。

不做进程内 `importlib.reload`。调度器、任务线程和单实例锁都绑在进程上，
半截重载会留下旧代码和新代码同时在跑。父进程不占锁、不听端口；子进程才是
原来的 `run.main`。源码稳定一小段时间后给子进程 SIGTERM，走现有的有序停止
（先停调度、排空任务、再删锁），然后启动新进程。

只监视仓库里的 `app/**/*.py`、`run.py`、`main.py`。`data/`、`__pycache__`、
页面静态文件不触发重启——那些本来就按请求读磁盘。
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from typing import Callable, List, Optional, Sequence, Tuple

from app.config import REPO_ROOT

#: 子进程带上这个变量，避免再套一层监视进程。
CHILD_ENV = "MCCHECKER_RUN_CHILD"

#: 源码连续这么久不再变化，才重启。避免一次保存写了两次文件就重启两回。
SETTLE_SECONDS = 0.4
POLL_SECONDS = 0.2

Signature = Tuple[Tuple[str, Optional[int], Optional[int]], ...]


def iter_source_files(root: str = REPO_ROOT) -> List[str]:
    """要监视的 Python 源文件。目录不存在时跳过，不因此启动失败。"""
    paths = [os.path.join(root, "run.py"), os.path.join(root, "main.py")]
    app_dir = os.path.join(root, "app")
    if os.path.isdir(app_dir):
        for dirpath, dirnames, filenames in os.walk(app_dir):
            dirnames[:] = [name for name in dirnames if name != "__pycache__"]
            for name in filenames:
                if name.endswith(".py"):
                    paths.append(os.path.join(dirpath, name))
    return paths


def source_signature(root: str = REPO_ROOT) -> Signature:
    """`(路径, mtime_ns, size)`。文件消失记成 `None`，删除源码也会重启。"""
    items = []
    for path in iter_source_files(root):
        try:
            stat = os.stat(path)
        except OSError:
            items.append((path, None, None))
            continue
        items.append((path, int(stat.st_mtime_ns), int(stat.st_size)))
    items.sort()
    return tuple(items)


def child_command(argv: Sequence[str]) -> List[str]:
    """用当前解释器重跑启动脚本，原样带上命令行参数。"""
    script = os.path.abspath(sys.argv[0])
    return [sys.executable, script] + list(argv)


def child_env() -> dict:
    env = os.environ.copy()
    env[CHILD_ENV] = "1"
    return env


def supervise(
    argv: Optional[Sequence[str]] = None,
    *,
    popen: Optional[Callable[..., subprocess.Popen]] = None,
    signature: Optional[Callable[[], Signature]] = None,
    sleep: Optional[Callable[[float], None]] = None,
    monotonic: Optional[Callable[[], float]] = None,
    settle: float = SETTLE_SECONDS,
    interval: float = POLL_SECONDS,
    should_stop: Optional[Callable[[], bool]] = None,
) -> int:
    """盯着源码签名。变了就停掉子进程再拉起。父进程自己收到停止信号时把信号转交子进程。

    `popen` / `signature` / `sleep` 是测试注入点。正常启动不传。
    """
    args = list(sys.argv[1:] if argv is None else argv)
    launch = popen or _popen
    current_signature = signature or source_signature
    pause = sleep or time.sleep
    clock = monotonic or time.monotonic
    stop = should_stop or _install_stop_flag()

    child = launch(child_command(args), child_env())
    baseline = current_signature()
    pending: Optional[Tuple[Signature, float]] = None
    print("Python 热更新已开启：修改 app/、run.py、main.py 后会自动重启进程", flush=True)

    while True:
        if stop():
            _stop_child(child)
            code = child.poll()
            return 0 if code is None else int(code)
        code = child.poll()
        if code is not None:
            return int(code)

        seen = current_signature()
        now = clock()
        if seen != baseline:
            if pending is None or pending[0] != seen:
                pending = (seen, now)
            elif now - pending[1] >= settle:
                print("检测到 Python 源码变化，正在重启服务…", flush=True)
                _stop_child(child)
                _wait_child(child)
                baseline = current_signature()
                pending = None
                child = launch(child_command(args), child_env())
        else:
            pending = None
        pause(interval)


def _popen(command: List[str], env: dict) -> subprocess.Popen:
    # 新会话：Ctrl+C 只打到父进程，由父进程把 SIGTERM 转给子进程，避免父子一起抢退出。
    return subprocess.Popen(command, env=env, start_new_session=True)


def _install_stop_flag() -> Callable[[], bool]:
    flag = {"stop": False}

    def _mark(signum, _frame):
        flag["stop"] = True
        print("收到信号 %s，正在停止…" % signum, flush=True)

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            signal.signal(sig, _mark)
        except (ValueError, OSError):
            pass
    return lambda: flag["stop"]


def _stop_child(child: subprocess.Popen) -> None:
    if child.poll() is not None:
        return
    try:
        child.send_signal(signal.SIGTERM)
    except OSError:
        return


def _wait_child(child: subprocess.Popen) -> None:
    try:
        child.wait(timeout=30)
    except subprocess.TimeoutExpired:
        child.kill()
        child.wait(timeout=5)
