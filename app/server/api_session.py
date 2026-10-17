"""会话接口（规范 §7.2）。

M0-09 只返回**占位结构**：不读 cookie、不解析 IP、不查权限矩阵。完整身份
（`mc_cid`、本机地址判定、`X-Forwarded-For`、角色）在 M1-03 用
`services/auth.py` + `services/context.py` 替换本模块实现。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict

from .. import __version__
from ..config import Settings
from ..core import device_models


def session_payload(settings: Settings) -> Dict[str, Any]:
    profile = {"id": device_models.DEFAULT_MODEL_ID, "name": device_models.DEFAULT_MODEL_NAME}
    return {
        "identity": {
            "ip": "",
            "name": "",
            "registered": False,
            "is_deployer": False,
            "is_admin": False,
            "role_label": "访客",
        },
        "profile": dict(profile),
        "profiles": [dict(profile)],
        "server": {
            "version": __version__,
            "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "scheduler": False,
        },
        "limits": {
            "upload_max_bytes": settings.upload_max_bytes,
            "tab_limit": settings.tab_limit,
            "temp_ttl_hours": settings.temp_ttl_hours,
        },
    }
