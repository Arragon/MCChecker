"""MCChecker 启动入口（规范 §3.2、§14.2；T-04、C25、F28）。

`main.py` 仍是 `from run import main; main()`，保持旧命令行兼容。

本模块只做：解析配置 → 初始化（数据根、日志、布局、单实例锁、后台任务队列、定时调度）→
启动 HTTP → 有序停止（`ordered_stop` 先停调度器再排空任务队列、最后关 HTTP；
`stop_and_release` 再等后台工作线程散尽才删单实例锁，C1）。业务接口挂在
`app/server/router.py`（M1 起逐步补齐）。
"""

from __future__ import annotations

import json
import logging
import logging.handlers
import os
import signal
import sys
import threading
import time
from typing import Any, List, Optional

from app import __version__
from app.config import SettingsError, load_settings
from app.core.errors import AppError
from app.core import storage
from app.services import scheduler as services_scheduler
from app.services import tasks

logger = logging.getLogger("mcchecker")

LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s [%(request_id)s] %(message)s"


def configure_logging(level: str, data_dir: Optional[str] = None) -> None:
    """控制台 + `data/logs/mcchecker.log`（10 MB × 5，§13）。"""
    from app.core.log_context import RequestIdFilter

    root = logging.getLogger()
    for handler in list(root.handlers):
        root.removeHandler(handler)
        try:
            handler.close()
        except Exception:
            pass
    root.setLevel(getattr(logging, level, logging.INFO))
    formatter = logging.Formatter(LOG_FORMAT)
    filters = [RequestIdFilter()]

    console = logging.StreamHandler()
    console.setFormatter(formatter)
    for item in filters:
        console.addFilter(item)
    root.addHandler(console)

    if not data_dir:
        return
    log_dir = os.path.join(data_dir, "logs")
    os.makedirs(log_dir, exist_ok=True)
    rotating = logging.handlers.RotatingFileHandler(
        os.path.join(log_dir, "mcchecker.log"),
        maxBytes=10 * 1024 * 1024,
        backupCount=5,
        encoding="utf-8",
    )
    rotating.setFormatter(formatter)
    for item in filters:
        rotating.addFilter(item)
    root.addHandler(rotating)


def ordered_stop(
    runner: tasks.TaskRunner, server: Any, timeout: Optional[float] = None
) -> None:
    """有序停止：**调度器 → 排空任务队列 → 关 HTTP**（R9 / R11 / 卡 8）。

    为什么调度器排在最前：`runner.shutdown()` 等的是「已经投进去的任务」收尾，它没法阻止
    有人继续往里投。调度线程每轮都会投新任务，所以先 `scheduler.stop()`（置位 + Event
    唤醒，**不等 join**，R11）才有确定的排空终点；`services/scheduler.Scheduler.tick()` 在
    停止位之后是空操作，这一条把「停完还往队列里投」的路径整个堵掉。

    然后**先**停止接收新任务并等在途任务收尾，**再**关 HTTP。顺序反了会让 `serve_forever`
    先退出、主线程走完而任务还在跑。解释器最后仍然会等池里那几条线程
    （`concurrent.futures.thread` 注册的 `_python_exit` 把它们 join 掉，实测一个卡死的下载
    把进程拖到 91.7 秒），所以代价不是「丢在途下载」，而是**停止序列在任务还没写完的时候
    就已经往下走了**——包括把单实例锁删掉，见 `stop_and_release`（C1）。
    调度线程自己不吃这份等待：它不是池线程，`_python_exit` 也 join 不到它，但它在
    `stop()` 之后不再投任何东西，而它**不往 `data/` 写任何一个字节**（权威内容的下载与落盘
    全在 `mctask` 工作线程里）。这条线程唯一的文件系统动作是到期时**删除**临时区与派生缓存，
    而两项各有各的判定：派生缓存按 mtime 年龄（只删 7 天前的），临时区按 meta 里的
    `expires_at`，并且跳过「还没写出 `meta.json`」的半成品目录（那正是某次上传正在写的那一半）。
    所以它被解释器直接抛下也不损伤权威数据，详见 `app/services/scheduler.py` 模块 docstring。

    `runner.shutdown()` 幂等（R10），所以 `_handle_signal` 与正常退出路径两处都调它、
    信号连按两次都不会重复 join。`scheduler.stop()` 同样幂等，且调度器还没建
    （`get_scheduler()` 返回 None，例如 `MCServer` 构造失败的那条早退路径）时直接跳过。

    这一层只保证「HTTP 侧的停止有上限」：`runner.shutdown()` 超时会直接返回而工作线程
    还在写盘，所以**删单实例锁不归这里管**，见 `stop_and_release`（C1）。

    `timeout=None` 时在**调用那一刻**读 `tasks.DEFAULT_SHUTDOWN_TIMEOUT`（§10.1 的 30 秒
    只有一个出处），这样用例能把它改短去模拟「排空预算到期」，不必真等 30 秒。
    """
    scheduler = services_scheduler.get_scheduler()
    if scheduler is not None:
        scheduler.stop()
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
    started = time.monotonic()
    drained = runner.wait_for_workers(exit_timeout)
    elapsed = time.monotonic() - started
    if drained:
        lock.release()
        logger.info("已停止，锁文件已删除")
        return True
    logger.error(
        "退出前仍有后台任务在写 data/，锁文件保留在 %s 没有删除（已实测等待 %.1f 秒，预算 %.1f 秒）："
        "删掉它等于放第二个实例进来同写一份权威数据。这些线程结束后进程才会真正退出，"
        "届时残留锁会被下一次启动按 pid 接管。",
        getattr(lock, "path", "?"),
        elapsed,
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

    if settings.restore_backup:
        configure_logging(settings.log_level)
        import importlib.util

        script = os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "scripts", "config_backup.py"
        )
        spec = importlib.util.spec_from_file_location("config_backup_cli", script)
        backup_mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(backup_mod)
        try:
            plan = backup_mod.restore_full(
                settings.restore_backup,
                settings.data_dir,
                dry_run=not settings.restore_apply,
                force=settings.restore_apply,
            )
        except Exception as exc:
            print("全域恢复失败：%s" % exc, file=sys.stderr)
            return 2
        print(json.dumps(plan, ensure_ascii=False, indent=2))
        return 0

    if settings.restore_dir:
        configure_logging(settings.log_level)
        if not settings.dry_run:
            print("恢复只提供 --dry-run（不会自动写回）：加 --dry-run 查看计划", file=sys.stderr)
            return 2
        from app.services import recovery as recovery_service

        try:
            plan = recovery_service.plan_restore(settings.restore_dir)
        except AppError as exc:
            print("恢复计划失败：%s" % exc.message, file=sys.stderr)
            return 2
        print("恢复计划（dry-run，只读；未修改任何文件）：%s（op=%s）" % (plan["entry"], plan["op"]))
        for step in plan["steps"]:
            suffix = "" if step["exists"] else "  [恢复区副本缺失]"
            print("  %s -> %s%s" % (step["source"], step["destination"], suffix))
        for kind, refs in sorted(plan["removed_refs"].items()):
            print("  removed_refs.%s: %s" % (kind, json.dumps(refs, ensure_ascii=False)))
        return 0

    # 热更新放在占锁之前：父进程只监视源码，不听端口、不拿单实例锁。
    # 子进程带 MCCHECKER_RUN_CHILD=1，走下面原来的启动路径。
    if settings.reload and os.environ.get("MCCHECKER_RUN_CHILD") != "1":
        from app.server.reloader import supervise

        return supervise(None if argv is None else list(argv))

    configure_logging(settings.log_level, settings.data_dir)

    from app.server.http import MCServer, SingleInstanceLock
    from app.server.router import build_router

    lock = SingleInstanceLock(settings.data_dir, settings.port)
    try:
        lock.acquire()
    except RuntimeError as exc:
        logger.error("%s", exc)
        print("启动失败：%s" % exc, file=sys.stderr)
        return 3

    # 迁移必须在单实例锁之后：两个实例同时启动时，第二个不能把第一个已校验的目标
    # 当成「可覆盖的副本」，更不能删掉它（M4-04 评审 I1）。
    storage.ensure_profile_layout()

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

    # 定时调度（卡 8 / §10.4）：**排在 server 建好之后**——`MCServer` 构造失败那条早退路径
    # 于是仍然「一条工作线程都没起、可以直接删锁」，不必再回头停调度器。调度线程只投任务、
    # 自己不下载不写盘，所以放在这里也已经开始按 `schedule.json` 排期。
    scheduler = services_scheduler.init_scheduler(runner)

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
        "on" if scheduler.is_alive() else "off",
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
