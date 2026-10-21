"""启动配置（规范 §3.6、T-04；C25、F28）。

优先级：命令行参数 > 环境变量 > 默认值。

非法值一律抛 `SettingsError`（消息里带原因，直接进启动日志），**不静默回退**：
端口写错却跑在旧端口上，比启动直接失败更难排查。

本模块只解析配置；启动 HTTP 服务是 `run.py` 的事（M0-09）。也不再读取
`NICEGUI_STORAGE_SECRET`（该文件保留不动，规范 §3.6）。
"""

from __future__ import annotations

import argparse
import os
import sys
from dataclasses import dataclass
from typing import List, Optional, Tuple

#: 仓库根目录（本文件位于 `<repo>/app/config.py`）。
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

DEFAULT_HOST = "0.0.0.0"
#: 与现网实际端口一致（F28 要求默认值不变）。
DEFAULT_PORT = 50002
DEFAULT_DATA_DIR = os.path.join(REPO_ROOT, "data")
DEFAULT_WEB_DIR = os.path.join(REPO_ROOT, "app", "web")
DEFAULT_UPLOAD_MAX_MB = 50
DEFAULT_DOWNLOAD_TIMEOUT_S = 30
DEFAULT_DOWNLOAD_RETRIES = 3
DEFAULT_TEMP_TTL_HOURS = 24
DEFAULT_TAB_LIMIT = 15
DEFAULT_MAX_WORKERS = 4
DEFAULT_LOG_LEVEL = "INFO"

VALID_LOG_LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")


class SettingsError(ValueError):
    """启动配置非法；`str(exc)` 就是给运维看的原因。"""


@dataclass(frozen=True)
class Settings:
    """运行时配置（字段名与规范 §3.6 一致）。

    3.9 兼容：不使用 dataclass 的 `slots` 参数，序列类型用 `Tuple` 而不是 `tuple[...]`。
    """

    host: str = DEFAULT_HOST
    port: int = DEFAULT_PORT
    data_dir: str = DEFAULT_DATA_DIR
    web_dir: str = DEFAULT_WEB_DIR
    trusted_proxies: Tuple[str, ...] = ()
    upload_max_bytes: int = DEFAULT_UPLOAD_MAX_MB * 1024 * 1024
    download_timeout_s: int = DEFAULT_DOWNLOAD_TIMEOUT_S
    download_retries: int = DEFAULT_DOWNLOAD_RETRIES
    download_allow: Tuple[str, ...] = ()
    temp_ttl_hours: int = DEFAULT_TEMP_TTL_HOURS
    tab_limit: int = DEFAULT_TAB_LIMIT
    max_workers: int = DEFAULT_MAX_WORKERS
    log_level: str = DEFAULT_LOG_LEVEL
    restore_dir: str = ""
    dry_run: bool = False


def _text(raw: object, what: str) -> str:
    value = str(raw).strip()
    if not value:
        raise SettingsError("%s 不能为空" % what)
    return value


def _int(raw: object, what: str, minimum: int, maximum: Optional[int] = None) -> int:
    text = str(raw).strip()
    if not text:
        raise SettingsError("%s 不能为空" % what)
    try:
        value = int(text)
    except ValueError:
        raise SettingsError("%s=%s 不是整数" % (what, text))
    if maximum is None:
        if value < minimum:
            raise SettingsError("%s=%d 不能小于 %d" % (what, value, minimum))
        return value
    if not minimum <= value <= maximum:
        raise SettingsError("%s=%d 超出范围 %d–%d" % (what, value, minimum, maximum))
    return value


def _csv(raw: object) -> Tuple[str, ...]:
    return tuple(part.strip() for part in str(raw).split(",") if part.strip())


def _log_level(raw: object, what: str) -> str:
    value = str(raw).strip().upper()
    if value not in VALID_LOG_LEVELS:
        raise SettingsError(
            "%s=%s 不是合法日志级别（可选 %s）" % (what, raw, "/".join(VALID_LOG_LEVELS))
        )
    return value


def _abs_dir(value: str) -> str:
    """数据根固定成绝对路径：它在运行期被多个模块使用，不能依赖当时的工作目录。"""
    return os.path.abspath(os.path.expanduser(value))


def _parse_cli(argv: List[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="run.py", description="MCChecker 启动参数（规范 §3.6）")
    parser.add_argument("--host", default=None, help="监听地址（默认 %s）" % DEFAULT_HOST)
    parser.add_argument("--port", default=None, help="监听端口（默认 %d）" % DEFAULT_PORT)
    parser.add_argument(
        "--data-dir",
        dest="data_dir",
        default=None,
        help="数据根目录（默认 %s）" % DEFAULT_DATA_DIR,
    )
    parser.add_argument("--restore", dest="restore_dir", default=None, help="只打印恢复计划（必须配 --dry-run）")
    parser.add_argument("--dry-run", dest="dry_run", action="store_true", help="只读恢复计划，不写任何文件")
    return parser.parse_args(argv)


def load_settings(argv: Optional[List[str]] = None) -> Settings:
    """解析配置：命令行 > 环境变量 > 默认值。

    传入 `argv` 便于测试；传 `None` 时读 `sys.argv[1:]`。
    """
    cli = _parse_cli(list(sys.argv[1:]) if argv is None else list(argv))
    if cli.dry_run and not cli.restore_dir:
        raise SettingsError("--dry-run 只能与 --restore 一起使用")
    env = os.environ

    host = DEFAULT_HOST
    port = DEFAULT_PORT
    data_dir = DEFAULT_DATA_DIR
    trusted_proxies: Tuple[str, ...] = ()
    upload_max_bytes = DEFAULT_UPLOAD_MAX_MB * 1024 * 1024
    download_allow: Tuple[str, ...] = ()
    log_level = DEFAULT_LOG_LEVEL

    if "MCHECKER_HOST" in env:
        host = _text(env["MCHECKER_HOST"], "MCHECKER_HOST")
    if "MCHECKER_PORT" in env:
        port = _int(env["MCHECKER_PORT"], "MCHECKER_PORT", 1, 65535)
    if "MCHECKER_DATA_DIR" in env:
        data_dir = _text(env["MCHECKER_DATA_DIR"], "MCHECKER_DATA_DIR")
    if "MCHECKER_TRUSTED_PROXIES" in env:
        trusted_proxies = _csv(env["MCHECKER_TRUSTED_PROXIES"])
    if "MCHECKER_UPLOAD_MAX_MB" in env:
        upload_max_bytes = _int(env["MCHECKER_UPLOAD_MAX_MB"], "MCHECKER_UPLOAD_MAX_MB", 1) * 1024 * 1024
    if "MCHECKER_DOWNLOAD_ALLOW" in env:
        download_allow = _csv(env["MCHECKER_DOWNLOAD_ALLOW"])
    if "MCHECKER_LOG_LEVEL" in env:
        log_level = _log_level(env["MCHECKER_LOG_LEVEL"], "MCHECKER_LOG_LEVEL")

    if cli.host is not None:
        host = _text(cli.host, "--host")
    if cli.port is not None:
        port = _int(cli.port, "--port", 1, 65535)
    if cli.data_dir is not None:
        data_dir = _text(cli.data_dir, "--data-dir")
    restore_dir = _abs_dir(cli.restore_dir) if cli.restore_dir else ""

    return Settings(
        host=host,
        port=port,
        data_dir=_abs_dir(data_dir),
        trusted_proxies=trusted_proxies,
        upload_max_bytes=upload_max_bytes,
        download_allow=download_allow,
        log_level=log_level,
        restore_dir=restore_dir,
        dry_run=bool(cli.dry_run),
    )
