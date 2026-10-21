"""统一错误类型与错误码。

错误码与 HTTP 状态码的对应关系来自规范 §7.1。`core` 只负责抛出带语义的
`AppError`，由 `server` 层把它翻译成响应体与状态码。
"""

from __future__ import annotations

from typing import Any, Dict

INVALID_INPUT = "INVALID_INPUT"
FORBIDDEN = "FORBIDDEN"
NOT_FOUND = "NOT_FOUND"
FILE_EXISTS = "FILE_EXISTS"
FAVORITE_COVERED = "FAVORITE_COVERED"
BUSY = "BUSY"
REVISION_CONFLICT = "REVISION_CONFLICT"
SOURCE_CHANGED = "SOURCE_CHANGED"
REVIEW_CONFLICT = "REVIEW_CONFLICT"
TOO_LARGE = "TOO_LARGE"
PARSE_FAILED = "PARSE_FAILED"
REVIEW_UNSAFE = "REVIEW_UNSAFE"
DOWNLOAD_FAILED = "DOWNLOAD_FAILED"
STORAGE_FAILURE = "STORAGE_FAILURE"
CORRUPT_DATA = "CORRUPT_DATA"
MAINTENANCE = "MAINTENANCE"

#: 错误码 → HTTP 状态码（规范 §7.1 表格）。
HTTP_STATUS: Dict[str, int] = {
    INVALID_INPUT: 400,
    FORBIDDEN: 403,
    NOT_FOUND: 404,
    FILE_EXISTS: 409,
    FAVORITE_COVERED: 409,
    BUSY: 409,
    REVISION_CONFLICT: 409,
    SOURCE_CHANGED: 409,
    REVIEW_CONFLICT: 409,
    TOO_LARGE: 413,
    PARSE_FAILED: 422,
    REVIEW_UNSAFE: 422,
    DOWNLOAD_FAILED: 502,
    STORAGE_FAILURE: 500,
    CORRUPT_DATA: 503,
    MAINTENANCE: 503,
}


class AppError(Exception):
    """带错误码的业务异常。

    `detail` 会随错误响应一并返回；`detail.field` 存在时前端把错误显示在对应
    输入框下方（规范 §7.1）。
    """

    def __init__(self, code: str, message: str = "", **detail: Any) -> None:
        super().__init__(message or code)
        self.code = code
        self.message = message or code
        self.detail: Dict[str, Any] = dict(detail)

    @property
    def http_status(self) -> int:
        return HTTP_STATUS.get(self.code, 500)

    def to_payload(self) -> Dict[str, Any]:
        return {"code": self.code, "message": self.message, "detail": self.detail}

    def __repr__(self) -> str:
        return f"AppError({self.code!r}, {self.message!r})"


class CorruptDataError(AppError):
    """权威 JSON 损坏。

    必须向上传播，禁止把损坏文件当空列表/空对象继续写（规范 §5.4）。
    """

    def __init__(self, path: str, cause: Any = None) -> None:
        super().__init__(CORRUPT_DATA, "数据文件已损坏，已进入只读保护", path=path)
        self.path = path
        self.cause = cause
