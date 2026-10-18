"""MCChecker 启动入口（规范 §3.2、§14.2；T-04、C25、F28）。

`main.py` 仍是 `from run import main; main()`，保持旧命令行兼容。

本模块只做：解析配置 → 初始化（数据根、日志、布局、单实例锁、后台任务队列）→
启动 HTTP → 有序停止（`ordered_stop` 关 HTTP，`stop_and_release` 再等后台工作线程散尽
才删单实例锁，C1）。业务接口挂在 `app/server/router.py`（M1 起逐步补齐）。
"""

from __future__ import annotations

import logging
import signal
import sys
import threading
from typing import Any, List, Optional

from app import __version__
from app.config import SettingsError, load_settings
from app.core import storage
from app.services import tasks

logger = logging.getLogger("mcchecker")

LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"


def configure_logging(level: str) -> None:
    logging.basicConfig(level=getattr(logging, level, logging.INFO), format=LOG_FORMAT)


def ordered_stop(
    runner: tasks.TaskRunner, server: Any, timeout: Optional[float] = None
) -> None:
    """有序停止（R9）：**先**停止接收新任务并等在途任务收尾，**再**关 HTTP。

    顺序反了会让 `serve_forever` 先退出、主线程走完而任务还在跑。解释器最后仍然会等池里
    那几条线程（`concurrent.futures.thread` 注册的 `_python_exit` 把它们 join 掉，实测一个
    卡死的下载把进程拖到 91.7 秒），所以代价不是「丢在途下载」，而是**停止序列在任务还没
    写完的时候就已经往下走了**——包括把单实例锁删掉，见 `stop_and_release`（C1）。
    卡 8 的调度线程是 daemon，只需在这里插一次 `scheduler.stop()`（置位 + Event 唤醒，不等
    join），真正的「等任务跑完」由 `runner.shutdown()` 负责。

    `runner.shutdown()` 幂等（R10），所以 `_handle_signal` 与正常退出路径两处都调它、
    信号连按两次都不会重复 join。

    这一层只保证「HTTP 侧的停止有上限」：`runner.shutdown()` 超时会直接返回而工作线程
    还在写盘，所以**删单实例锁不归这里管**，见 `stop_and_release`（C1）。

    `timeout=None` 时在**调用那一刻**读 `tasks.DEFAULT_SHUTDOWN_TIMEOUT`（§10.1 的 30 秒
    只有一个出处），这样用例能把它改短去模拟「排空预算到期」，不必真等 30 秒。
    """
    runner.shutdown(tasks.DEFAULT_SHUTDOWN_TIMEOUT if timeout is None else timeout)
    server.shutdown()


def stop_and_release(
    runner: tasks.TaskRunner,
    server: Any,
    lock: Any,
    exit_timeout: float = tasks.EXIT_WORKER_TIMEOUT,
) -> bool:
    """有序停止 → 关 socket → **等写的人真的散了** → 才删单实例锁（C1 / H3）。

    返回锁是否被删除。

    为什么删锁要单独等一次：`SingleInstanceLock.release()` 是无条件 `os.unlink`，它不
    知道还有没有人在写；而 `runner.shutdown()` 那 30 秒**小于**一个卡死下载的最坏耗时
    （`DEFAULT_DOWNLOAD_TIMEOUT_S` 30 s × `DEFAULT_DOWNLOAD_RETRIES` 3 次 + 退避 ≈ 93 s，
    `updates.update_all` 还串行遍历映射项），于是「排空超时」是常态。原先这里
    `ordered_stop()` 一返回就 `lock.release()` 并记「锁文件已删除」——那一刻端口已经
    释放、本进程的工作线程还在写 `data/profiles/<pid>/…`，第二个实例（systemd 自动重启、
    `docker restart`）立刻进来，两个进程同读同写 `schedule.json` 与逐文件的归档。
    `locks.profile_lock` 是 `threading.RLock`、`write_gate` 只在进程内，**跨进程谁都不挡**：
    这个应用里唯一的跨进程写锁就是这个文件。

    等不散就**保留锁**并说明原因：残留锁能被下一个实例接管
    （`SingleInstanceLock.acquire()` 读 pid → `_process_alive()` 判活，`http.py:139-152`，
    回归用例 `tests/test_api_http.py::test_stale_lock_is_taken_over`），写的人还活着却被
    删掉的锁不能。等散了才删，正常停止不会多等一秒。
    """
    ordered_stop(runner, server)
    server.server_close()
    if runner.wait_for_workers(exit_timeout):
        lock.release()
        logger.info("已停止，锁文件已删除")
        return True
    logger.error(
        "退出前仍有后台任务在写 data/，锁文件保留在 %s 没有删除（已等 %.1f 秒）："
        "删掉它等于放第二个实例进来同写一份权威数据。这些线程结束后进程才会真正退出，"
        "届时残留锁会被下一次启动按 pid 接管。",
        getattr(lock, "path", "?"),
        exit_timeout,
    )
    return False


def _count_profiles() -> int:
    from app.core import device_models

    try:
        return len(device_models.load_models())
    except Exception as exc:  # 注册表损坏时不阻断启动，日志里说明（§5.4）
        logger.warning("读取机型注册表失败：%s", exc)
        return 0


def main(argv: Optional[List[str]] = None) -> int:
    try:
        settings = load_settings(argv)
    except SettingsError as exc:
        print("启动失败：%s" % exc, file=sys.stderr)
        return 2

    # 数据根必须在导入「导入期派生路径」的模块之前切换（例如 dltool.DLTOOL_FILE），
    # 因此 HTTP 层在下面按需导入。
    storage.configure_data_dir(settings.data_dir)
    configure_logging(settings.log_level)
    storage.ensure_profile_layout(migrate_legacy=False)

    from app.server.http import MCServer, SingleInstanceLock
    from app.server.router import build_router

    lock = SingleInstanceLock(settings.data_dir, settings.port)
    try:
        lock.acquire()
    except RuntimeError as exc:
        logger.error("%s", exc)
        print("启动失败：%s" % exc, file=sys.stderr)
        return 3

    # 后台任务队列（卡 6 / §10.1）：worker 数来自 Settings.max_workers，注入进程内单例，
    # `/tasks` 的 handler 与卡 8 的调度器都通过 `tasks.get_runner()` 拿同一个 runner。
    runner = tasks.init_runner(settings.max_workers)

    try:
        server = MCServer(settings, build_router(settings), runner=runner)
    except OSError as exc:
        # 还没提交过任何任务，`runner` 一条工作线程都没起（线程是 submit 时懒建的），
        # 所以这里可以绕开 `stop_and_release` 直接删锁（C1 的等待只对已经起过 HTTP 的
        # 那条退出路径必要）。
        runner.shutdown(1.0)
        lock.release()
        logger.error("无法监听 %s:%d：%s", settings.host, settings.port, exc)
        print("启动失败：无法监听 %s:%d（%s）" % (settings.host, settings.port, exc), file=sys.stderr)
        return 4

    def _handle_signal(signum, _frame):
        logger.info("收到信号 %s，正在停止…", signum)
        # serve_forever 跑在主线程，shutdown() 必须从别的线程调用。
        threading.Thread(target=lambda: ordered_stop(runner, server), daemon=True).start()

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            signal.signal(sig, _handle_signal)
        except (ValueError, OSError):  # 非主线程或不支持的平台
            pass

    logger.info("启动环境：Python %s", sys.version.split()[0])
    logger.info(
        "MCChecker %s listening on http://%s:%d (data=%s, profiles=%d, workers=%d, scheduler=%s)",
        __version__,
        settings.host,
        settings.port,
        settings.data_dir,
        _count_profiles(),
        runner.max_workers,
        "off",
    )

    try:
        server.serve_forever(poll_interval=0.2)
    finally:
        # 与 `_handle_signal` 同一条有序停止（R9：两处都要走，不能只改一处），但删锁
        # 这一步交给 `stop_and_release`：写的人还没散就 unlink，等于把本应用唯一的
        # 跨进程写锁提前撤掉（C1 / H3）。
        stop_and_release(runner, server, lock)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
