"""日志脱敏与统一字段

核心模块通过 log_operation 记录操作日志，
sanitize_log_value 确保不泄露 token、secret、URL userinfo 等敏感信息。
"""

import logging
import re
from typing import Optional

# 预编译正则，避免每次调用都重新编译
_RE_CREDENTIAL = re.compile(
    r'(token|key|secret|password|apikey|api_key|access_token)\s*=\s*\S+',
    re.IGNORECASE,
)
_RE_URL_USERINFO = re.compile(r'://[^@\s]+@')


def sanitize_log_value(value: str) -> str:
    """脱敏日志值

    移除：
    - token/key/password/secret 等键值对的值
    - URL 中的 userinfo (user:pass@host)

    示例:
        >>> sanitize_log_value("token=abc123secret")
        'token=[REDACTED]'
        >>> sanitize_log_value("http://admin:pw@host/path")
        'http://[REDACTED]@host/path'
    """
    value = _RE_CREDENTIAL.sub(r'\1=[REDACTED]', value)
    value = _RE_URL_USERINFO.sub('://[REDACTED]@', value)
    return value


def log_operation(
    logger: logging.Logger,
    operation_id: str,
    profile: str,
    actor: str,
    target: str,
    result: str,
    duration_ms: float,
    error_code: Optional[str] = None,
) -> None:
    """统一操作日志格式

    所有核心操作（导入、更新、审阅、下载等）完成后调用，
    保证日志字段一致，便于后续检索和告警。

    Args:
        logger: 模块 logger
        operation_id: 操作标识（如 "import", "update", "review"）
        profile: 当前 profile 名称
        actor: 操作者标识
        target: 操作目标（文件名/路径等）
        result: 结果状态（"ok", "error", "timeout" 等）
        duration_ms: 耗时毫秒数
        error_code: 错误码（仅失败时填写）
    """
    logger.info(
        "op=%s profile=%s actor=%s target=%s result=%s duration=%.1fms error=%s",
        sanitize_log_value(operation_id),
        sanitize_log_value(profile),
        sanitize_log_value(actor),
        sanitize_log_value(target),
        result,
        duration_ms,
        error_code or "none",
    )
