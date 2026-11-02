"""M16-18 运维凭据授权：`integration_credentials_manage` 的部署端路径。

独立于全局 admin/deployer；只有配置了部署端高熵 operator token 且请求方
提供匹配 token 时，凭据管理动作才放行。token 不写日志/错误正文。
"""

from __future__ import annotations

import hmac
import os
from typing import Any

from app.core.errors import FORBIDDEN, AppError

OPERATOR_ENV = "MCHECKER_OPERATOR_TOKEN"
OPERATOR_CAPABILITY = "integration_credentials_manage"


def operator_enabled() -> bool:
    return bool(str(os.environ.get(OPERATOR_ENV) or "").strip())


def require_operator(token: Any) -> None:
    expected = str(os.environ.get(OPERATOR_ENV) or "").strip()
    if not expected:
        raise AppError(FORBIDDEN, "运维凭据未配置", field="operator_token")
    supplied = str(token or "").strip()
    if not supplied or not hmac.compare_digest(supplied, expected):
        raise AppError(FORBIDDEN, "需要独立运维授权", field="operator_token")
