"""V3 工作区上下文解析（M9-03 / INH-2120）。

- v3 路径 `/api/v3/workspaces/{wid}` 以路径为权威，不在这里查存在性；handler 自己
  先 authz 再查（保留“权限先于存在性”）。
- v1 业务 `/api/v1/p/{pid}`：显式 `X-Workspace-Id`/`?workspace=` 生效；读缺省走
  legacy `default`，写缺省返回 428；header/query 冲突 409；未知 wid 404。
- 全局 API（session/profiles/config/tasks/{id}/旧下载）不强制 wid。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional

from ..core import device_models
from ..core.errors import (
    NOT_FOUND,
    WORKSPACE_CONTEXT_MISMATCH,
    WORKSPACE_CONTEXT_REQUIRED,
    AppError,
)
from . import workspace_catalog

LEGACY = workspace_catalog.LEGACY_WORKSPACE_ID
_WRITE_METHODS = ("POST", "PUT", "PATCH", "DELETE")
_GLOBAL_PREFIXES = (
    "/api/v1/session",
    "/api/v1/profiles",
    "/api/v1/config/",
    "/api/file-download",
    "/record_view",
    "/api/v1/tasks/",
)


@dataclass(frozen=True)
class Resolution:
    workspace_id: str
    check_profile: bool
    explicit: bool

    @property
    def legacy(self) -> bool:
        return self.workspace_id == LEGACY


def _header(headers, key: str) -> str:
    value = headers.get(key) if headers is not None else ""
    return str(value or "").strip()


def _query(query: Dict[str, List[str]], key: str) -> str:
    values = query.get(key) or []
    return str(values[-1]).strip() if values else ""


def resolve(
    method: str,
    path: str,
    params: Dict[str, str],
    query: Dict[str, List[str]],
    headers,
) -> Resolution:
    method = method.upper()
    # v3 路径权威；不在 dispatch 查存在性，避免把 403 变成 404。
    wid = (params or {}).get("wid")
    if wid and path.startswith("/api/v3/workspaces/"):
        return Resolution(str(wid), check_profile=False, explicit=True)
    if path == "/api/v3/session":
        return Resolution(_query(query, "workspace") or LEGACY, check_profile=False, explicit=True)

    # 这些全局读接口本身没有 {pid}，但显式 wid 仍要生效（任务 retry / 旧下载 / 记录页）。
    if path.startswith(("/api/v1/tasks/", "/api/file-download", "/record_view")):
        header_wid = _header(headers, "X-Workspace-Id")
        query_wid = _query(query, "workspace")
        if header_wid and query_wid and header_wid != query_wid:
            raise AppError(WORKSPACE_CONTEXT_MISMATCH, "工作区上下文冲突")
        explicit = header_wid or query_wid
        if explicit:
            workspace_catalog.get_workspace(explicit)
            return Resolution(explicit, check_profile=False, explicit=True)
        return Resolution(LEGACY, check_profile=False, explicit=False)

    if path.startswith(_GLOBAL_PREFIXES):
        return Resolution(LEGACY, check_profile=False, explicit=False)

    if not path.startswith("/api/v1/p/"):
        return Resolution(LEGACY, check_profile=False, explicit=False)

    header_wid = _header(headers, "X-Workspace-Id")
    query_wid = _query(query, "workspace")
    if header_wid and query_wid and header_wid != query_wid:
        raise AppError(WORKSPACE_CONTEXT_MISMATCH, "工作区上下文冲突")
    explicit = header_wid or query_wid
    if not explicit:
        if method in _WRITE_METHODS:
            raise AppError(
                WORKSPACE_CONTEXT_REQUIRED,
                "状态变更请求必须指定工作区（X-Workspace-Id）",
            )
        return Resolution(LEGACY, check_profile=True, explicit=False)
    workspace_catalog.get_workspace(explicit)
    return Resolution(explicit, check_profile=True, explicit=True)


def assert_profile(workspace_id: str, profile_id: str) -> None:
    """wid 下 pid 归属：legacy 用全局 device_models，真实 W 用 catalog profiles。"""
    wid = str(workspace_id or "").strip() or LEGACY
    pid = str(profile_id or "").strip()
    if wid == LEGACY:
        if not device_models.exists(pid):
            raise AppError(NOT_FOUND, "机型不存在")
        return
    ws = workspace_catalog.get_workspace(wid)
    if not any(str(p.get("id")) == pid for p in ws.get("profiles") or []):
        raise AppError(NOT_FOUND, "机型不存在")
