"""请求级日志上下文（§13；M4-03 / INH-1905）。

HTTP 分发线程在请求开始时设置 `request_id`，日志格式里的 `[%(request_id)s]` 由
`RequestIdFilter` 注入；非请求线程（启动、调度）默认 `-`。
"""

from __future__ import annotations

import contextvars
import logging
from typing import Optional

_REQUEST_ID: contextvars.ContextVar[str] = contextvars.ContextVar(
    "mcchecker_request_id", default="-"
)


def set_request_id(value: str):
    return _REQUEST_ID.set(str(value or "-"))


def reset_request_id(token) -> None:
    try:
        _REQUEST_ID.reset(token)
    except (ValueError, RuntimeError):
        pass


def get_request_id() -> str:
    return _REQUEST_ID.get()


class RequestIdFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = get_request_id()
        return True


def business(
    event: str,
    *,
    profile: str = "-",
    actor: str = "-",
    target: str = "-",
    result: str = "-",
    duration_ms: Optional[float] = None,
) -> None:
    """结构化业务日志：字段固定为 `profile/actor/target/result/duration_ms`。"""
    logging.getLogger("mcchecker.business").info(
        "event=%s profile=%s actor=%s target=%s result=%s duration_ms=%s",
        event,
        profile or "-",
        actor or "-",
        target or "-",
        result or "-",
        "-" if duration_ms is None else "%.1f" % duration_ms,
    )
