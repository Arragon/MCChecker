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
#: V3 工作区上下文（M9-03）：写请求缺显式 wid / wid 冲突。
WORKSPACE_CONTEXT_REQUIRED = "WORKSPACE_CONTEXT_REQUIRED"
WORKSPACE_CONTEXT_MISMATCH = "WORKSPACE_CONTEXT_MISMATCH"

# --- 配置管理域（M5-01，见 docs/MCChecker_CONFIG_DOMAIN_SPEC.md §6.1）---
CONFIG_DISABLED = "CONFIG_DISABLED"
PLAN_STALE = "PLAN_STALE"
POLICY_BLOCKED = "POLICY_BLOCKED"
#: 设备层功能关闭（M7-07/08 按计划延期）。
DEVICE_LAYER_DISABLED = "DEVICE_LAYER_DISABLED"
#: 生命周期状态阻止本次写（退役机型、漂移未确认等，M7-12）。
LIFECYCLE_BLOCKED = "LIFECYCLE_BLOCKED"
DEFINITION_CONFLICT = "DEFINITION_CONFLICT"
MAPPING_AMBIGUOUS = "MAPPING_AMBIGUOUS"
VALUE_OUT_OF_RANGE = "VALUE_OUT_OF_RANGE"
VALUE_LOSSY_CONVERSION = "VALUE_LOSSY_CONVERSION"
OBSERVATION_UNKNOWN_RELEASE = "OBSERVATION_UNKNOWN_RELEASE"
ARTIFACT_TEMPLATE_INCOMPATIBLE = "ARTIFACT_TEMPLATE_INCOMPATIBLE"
IDEMPOTENCY_MISMATCH = "IDEMPOTENCY_MISMATCH"
#: 同一 release 的 canonical D0 已经冻结（M12-03）。
DEFAULT_ALREADY_FROZEN = "DEFAULT_ALREADY_FROZEN"
#: 状态机不允许的流转（M6-11）。与 `INVALID_INPUT` 分开：这不是"参数写错了"，
#: 是"对象现在不在这条路上"，前端据此提示刷新而不是改输入。
CHANGE_STATE_INVALID = "CHANGE_STATE_INVALID"

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
    CONFIG_DISABLED: 503,
    PLAN_STALE: 409,
    POLICY_BLOCKED: 403,
    DEVICE_LAYER_DISABLED: 400,
    LIFECYCLE_BLOCKED: 409,
    DEFINITION_CONFLICT: 409,
    MAPPING_AMBIGUOUS: 422,
    VALUE_OUT_OF_RANGE: 422,
    VALUE_LOSSY_CONVERSION: 422,
    OBSERVATION_UNKNOWN_RELEASE: 422,
    ARTIFACT_TEMPLATE_INCOMPATIBLE: 422,
    IDEMPOTENCY_MISMATCH: 409,
    DEFAULT_ALREADY_FROZEN: 409,
    CHANGE_STATE_INVALID: 409,
    WORKSPACE_CONTEXT_REQUIRED: 428,
    WORKSPACE_CONTEXT_MISMATCH: 409,
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
