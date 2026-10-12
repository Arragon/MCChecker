"""统一业务错误码

所有核心模块抛出的异常都应继承 AppError，
UI 层和日志层通过 code 字段做一致处理。
"""

from dataclasses import dataclass, field
from typing import Optional


class ErrorCode:
    """业务错误码常量，避免散落字符串"""

    INVALID_INPUT = "INVALID_INPUT"
    FORBIDDEN = "FORBIDDEN"
    NOT_FOUND = "NOT_FOUND"
    SOURCE_CHANGED = "SOURCE_CHANGED"
    REVIEW_CONFLICT = "REVIEW_CONFLICT"
    TOO_LARGE = "TOO_LARGE"
    BUSY = "BUSY"
    DOWNLOAD_FAILED = "DOWNLOAD_FAILED"
    STORAGE_FAILURE = "STORAGE_FAILURE"
    CORRUPT_DATA = "CORRUPT_DATA"


@dataclass
class AppError(Exception):
    """统一业务异常基类

    Attributes:
        code: 错误码，对应 ErrorCode 常量
        message: 人类可读描述
        retryable: 是否可重试
        context: 附加上下文（不输出到用户日志的敏感字段）
    """

    code: str
    message: str
    retryable: bool = False
    context: Optional[dict] = field(default=None, repr=False)

    def __str__(self) -> str:
        return f"[{self.code}] {self.message}"


# ── 具体错误子类 ────────────────────────────────────────────

class InvalidInputError(AppError):
    """输入校验失败：路径越界、格式错误等"""

    def __init__(self, message: str, **ctx):
        super().__init__(ErrorCode.INVALID_INPUT, message, context=ctx or None)


class ForbiddenError(AppError):
    """无权限执行操作"""

    def __init__(self, message: str, **ctx):
        super().__init__(ErrorCode.FORBIDDEN, message, context=ctx or None)


class NotFoundError(AppError):
    """资源不存在"""

    def __init__(self, message: str, **ctx):
        super().__init__(ErrorCode.NOT_FOUND, message, retryable=False, context=ctx or None)


class SourceChangedError(AppError):
    """并发写冲突：源文件在操作期间被修改"""

    def __init__(self, message: str, **ctx):
        super().__init__(ErrorCode.SOURCE_CHANGED, message, context=ctx or None)


class ReviewConflictError(AppError):
    """审阅冲突：多人同时审阅或审阅状态不一致"""

    def __init__(self, message: str, **ctx):
        super().__init__(ErrorCode.REVIEW_CONFLICT, message, context=ctx or None)


class TooLargeError(AppError):
    """文件/内容超出大小限制"""

    def __init__(self, message: str, **ctx):
        super().__init__(ErrorCode.TOO_LARGE, message, context=ctx or None)


class BusyError(AppError):
    """系统繁忙：操作正在进行，稍后可重试"""

    def __init__(self, message: str, **ctx):
        super().__init__(ErrorCode.BUSY, message, retryable=True, context=ctx or None)


class DownloadFailedError(AppError):
    """下载失败"""

    def __init__(self, message: str, **ctx):
        super().__init__(ErrorCode.DOWNLOAD_FAILED, message, context=ctx or None)


class StorageFailureError(AppError):
    """持久化存储层错误"""

    def __init__(self, message: str, **ctx):
        super().__init__(ErrorCode.STORAGE_FAILURE, message, context=ctx or None)


class CorruptDataError(AppError):
    """数据损坏：解析或校验失败"""

    def __init__(self, message: str, **ctx):
        super().__init__(ErrorCode.CORRUPT_DATA, message, context=ctx or None)
