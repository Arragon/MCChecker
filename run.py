"""MCChecker 启动入口（规范 §3.2、§14.2；T-04、C25、F28）。

`main.py` 仍是 `from run import main; main()`，保持旧命令行兼容。

本模块只做：解析配置 → 初始化（数据根、日志、布局、单实例锁）→ 启动 HTTP →
有序停止。业务接口挂在 `app/server/router.py`（M1 起逐步补齐）。
"""

from __future__ import annotations

import logging
import signal
import sys
import threading
from typing import List, Optional

from app import __version__
from app.config import SettingsError, load_settings
from app.core import storage

logger = logging.getLogger("mcchecker")

LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"


def configure_logging(level: str) -> None:
    logging.basicConfig(level=getattr(logging, level, logging.INFO), format=LOG_FORMAT)


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

    try:
        server = MCServer(settings, build_router(settings))
    except OSError as exc:
        lock.release()
        logger.error("无法监听 %s:%d：%s", settings.host, settings.port, exc)
        print("启动失败：无法监听 %s:%d（%s）" % (settings.host, settings.port, exc), file=sys.stderr)
        return 4

    def _handle_signal(signum, _frame):
        logger.info("收到信号 %s，正在停止…", signum)
        # serve_forever 跑在主线程，shutdown() 必须从别的线程调用。
        threading.Thread(target=server.shutdown, daemon=True).start()

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            signal.signal(sig, _handle_signal)
        except (ValueError, OSError):  # 非主线程或不支持的平台
            pass

    logger.info("启动环境：Python %s", sys.version.split()[0])
    logger.info(
        "MCChecker %s listening on http://%s:%d (data=%s, profiles=%d, scheduler=%s)",
        __version__,
        settings.host,
        settings.port,
        settings.data_dir,
        _count_profiles(),
        "off",
    )

    try:
        server.serve_forever(poll_interval=0.2)
    finally:
        server.server_close()
        lock.release()
        logger.info("已停止，锁文件已删除")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
