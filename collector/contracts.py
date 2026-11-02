"""M16-16 SDA adapter 的固定契约（合成基线）。

真实 method/body/CSRF 字段仍待 E2 授权内网补齐；本文件只冻结 MCChecker 侧
固定路径、能力枚举与安全 reason_code，不接受任意 URL/JavaScript/本地路径。
"""

from __future__ import annotations

ADAPTER_KINDS = ("catalog", "table", "file")
CAPABILITIES = ("catalog_read", "table_read", "file_read")

PATHS = {
    "catalog": "/synthetic/catalog",
    "table": "/synthetic/table",
    "file": "/synthetic/file",
}

RESOURCE_FIELD = {
    "catalog": None,
    "table": "resource_ref",
    "file": "resource_ref",
}

REASON_AUTH_FAILED = "auth_failed"
REASON_BUSINESS_ERROR = "business_error"
REASON_PERMISSION_DENIED = "permission_denied"
REASON_REDIRECT_BLOCKED = "redirect_blocked"
REASON_INVALID_PAYLOAD = "invalid_payload"
REASON_PROTOCOL_MISMATCH = "protocol_mismatch"
REASON_TOO_LARGE = "too_large"
REASON_NETWORK_ERROR = "network_error"

SAFE_RESPONSE_HEADERS = ("content-type", "cache-control", "content-length")
