"""V3 Workspace 接口（M9-02 / INH-2119）。

- `GET /api/v3/session?workspace=&profile=`：全局身份 + 当前工作区/机型；
- `GET /api/v3/workspaces` / `GET /api/v3/workspaces/{wid}`：只读元数据；
- `POST /api/v3/workspaces` / `PATCH /api/v3/workspaces/{wid}`：管理员；
- `GET /api/v3/workspaces/{wid}/context`：工作区上下文。

本卡只做元数据与 session/context；v1 `/session` 与 v1 profile CRUD 不改。
"""

from __future__ import annotations

from datetime import datetime
from functools import partial
from typing import Any, Dict, List, Optional

from .. import __version__
from ..config import Settings
from ..core import storage
from ..core.errors import INVALID_INPUT, NOT_FOUND, AppError
from ..services import auth, authz, recovery, scheduler as services_scheduler, workspace_catalog, profiles
from ..services.context import RequestContext
from .router import Response, Router, json_body


def register(router: Router, settings: Settings) -> None:
    router.add("GET", "/api/v3/session", partial(get_session, settings=settings))
    router.add("GET", "/api/v3/workspaces", list_workspaces)
    router.add("POST", "/api/v3/workspaces", create_workspace)
    router.add("GET", "/api/v3/workspaces/{wid}", get_workspace)
    router.add("PATCH", "/api/v3/workspaces/{wid}", update_workspace)
    router.add("GET", "/api/v3/workspaces/{wid}/context", partial(get_context, settings=settings))
    router.add("GET", "/api/v3/workspaces/{wid}/profiles", list_workspace_profiles)
    router.add("POST", "/api/v3/workspaces/{wid}/profiles", create_workspace_profile)
    router.add("PATCH", "/api/v3/workspaces/{wid}/profiles/{pid}", rename_workspace_profile)
    router.add("DELETE", "/api/v3/workspaces/{wid}/profiles/{pid}", delete_workspace_profile)


def _requested(query: Dict[str, List[str]], key: str) -> str:
    values = query.get(key) or []
    return str(values[-1]).strip() if values else ""


def _workspace_from_query(query: Dict[str, List[str]]) -> Dict[str, Any]:
    wid = _requested(query, "workspace") or workspace_catalog.LEGACY_WORKSPACE_ID
    return workspace_catalog.get_workspace(wid)


def _profiles_for(workspace: Dict[str, Any]) -> List[Dict[str, Any]]:
    """legacy 工作区沿用 v1 机型表，其余工作区用 catalog 里的机型。"""
    if str(workspace.get("id")) == workspace_catalog.LEGACY_WORKSPACE_ID:
        return workspace_catalog.list_profiles(str(workspace.get("id")))
    return [dict(p) for p in workspace.get("profiles") or []]


def _profile_for(workspace: Dict[str, Any], query: Dict[str, List[str]]) -> str:
    requested = _requested(query, "profile")
    if str(workspace.get("id")) == workspace_catalog.LEGACY_WORKSPACE_ID:
        # v1 /session 兼容：请求的机型不存在时回落到默认机型，不报 404。
        ids = [str(p.get("id")) for p in _profiles_for(workspace)]
        if requested and requested in ids:
            return requested
        default = str(workspace.get("default_profile_id") or workspace_catalog.DEFAULT_PROFILE_ID)
        return default if default in ids else (ids[0] if ids else default)
    pid = requested or workspace.get("default_profile_id") or workspace_catalog.DEFAULT_PROFILE_ID
    if not any(str(p.get("id")) == pid for p in workspace.get("profiles") or []):
        raise AppError(NOT_FOUND, "机型不存在", profile=pid)
    return pid


def _session_payload(
    ctx: RequestContext,
    workspace: Dict[str, Any],
    profile_id: str,
    settings: Settings,
) -> Response:
    actor = auth.resolve_actor(ctx.ip, profile_id)
    headers: Dict[str, str] = {}
    if not ctx.cid:
        headers["Set-Cookie"] = auth.session_cookie(auth.new_session_cid())
    profiles = _profiles_for(workspace)
    profile = next(
        (p for p in profiles if str(p.get("id")) == profile_id),
        {"id": profile_id, "name": profile_id},
    )
    return Response(
        payload={
            "identity": {
                "ip": actor.ip,
                "name": actor.name,
                "registered": actor.registered,
                "is_deployer": actor.is_deployer,
                "is_admin": actor.is_admin,
                "role_label": actor.role_label,
                "capabilities": authz.capabilities(actor),
            },
            "workspace": workspace,
            "workspaces": workspace_catalog.list_workspaces(),
            "profile": dict(profile),
            "profiles": [dict(p) for p in profiles],
            "server": {
                "version": __version__,
                "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "scheduler": services_scheduler.is_running(),
            },
            "limits": {
                "upload_max_bytes": settings.upload_max_bytes,
                "tab_limit": settings.tab_limit,
                "temp_ttl_hours": settings.temp_ttl_hours,
            },
        },
        headers=headers,
    )


def get_session(
    ctx: RequestContext,
    params: Dict[str, str],
    query: Dict[str, List[str]],
    body: Optional[bytes],
    settings: Settings,
) -> Response:
    authz.authorize(ctx, "read")
    workspace = _workspace_from_query(query)
    return _session_payload(ctx, workspace, _profile_for(workspace, query), settings)


def get_context(
    ctx: RequestContext,
    params: Dict[str, str],
    query: Dict[str, List[str]],
    body: Optional[bytes],
    settings: Settings,
) -> Response:
    authz.authorize(ctx, "read")
    workspace = workspace_catalog.get_workspace(params["wid"])
    return _session_payload(ctx, workspace, _profile_for(workspace, query), settings)


def list_workspaces(
    ctx: RequestContext,
    params: Dict[str, str],
    query: Dict[str, List[str]],
    body: Optional[bytes],
) -> Response:
    authz.authorize(ctx, "read")
    return Response(payload={"items": workspace_catalog.list_workspaces()})


def get_workspace(
    ctx: RequestContext,
    params: Dict[str, str],
    query: Dict[str, List[str]],
    body: Optional[bytes],
) -> Response:
    authz.authorize(ctx, "read")
    return Response(payload={"workspace": workspace_catalog.get_workspace(params["wid"])})


def create_workspace(
    ctx: RequestContext,
    params: Dict[str, str],
    query: Dict[str, List[str]],
    body: Optional[bytes],
) -> Response:
    authz.authorize(ctx, "settings_write")
    payload = json_body(body)
    item = workspace_catalog.create_workspace(
        payload.get("name"), payload.get("description", "")
    )
    return Response(status=201, payload={"workspace": item})


def update_workspace(
    ctx: RequestContext,
    params: Dict[str, str],
    query: Dict[str, List[str]],
    body: Optional[bytes],
) -> Response:
    authz.authorize(ctx, "settings_write")
    payload = json_body(body)
    item = workspace_catalog.update_workspace(
        params["wid"], payload.get("name"), payload.get("description", "")
    )
    return Response(payload={"workspace": item})


def list_workspace_profiles(
    ctx: RequestContext,
    params: Dict[str, str],
    query: Dict[str, List[str]],
    body: Optional[bytes],
) -> Response:
    authz.authorize(ctx, "read")
    wid = params["wid"]
    workspace_catalog.get_workspace(wid)
    if wid == workspace_catalog.LEGACY_WORKSPACE_ID:
        return Response(payload={"items": profiles.list_profiles()["items"]})
    return Response(payload={"items": workspace_catalog.list_profiles(wid)})


def create_workspace_profile(
    ctx: RequestContext,
    params: Dict[str, str],
    query: Dict[str, List[str]],
    body: Optional[bytes],
) -> Response:
    authz.authorize(ctx, "settings_write")
    wid = params["wid"]
    workspace_catalog.get_workspace(wid)
    payload = json_body(body)
    if wid == workspace_catalog.LEGACY_WORKSPACE_ID:
        return Response(status=201, payload=profiles.create_profile(payload, ctx.actor))
    copy_from = payload.get("copy_from")
    if copy_from is not None:
        copy_from = str(copy_from).strip() or None
        if copy_from and not any(
            str(p.get("id")) == copy_from for p in workspace_catalog.list_profiles(wid)
        ):
            raise AppError(INVALID_INPUT, "要复制的机型不存在", field="copy_from")
    item = workspace_catalog.add_profile(wid, payload.get("name"))
    if copy_from:
        try:
            with storage.use_workspace(wid):
                storage.copy_profile_data(copy_from, item["id"])
        except BaseException:
            workspace_catalog.remove_profile(wid, item["id"])
            with storage.use_workspace(wid):
                storage.delete_profile_data(item["id"])
            raise
    return Response(status=201, payload={"profile": item})


def rename_workspace_profile(
    ctx: RequestContext,
    params: Dict[str, str],
    query: Dict[str, List[str]],
    body: Optional[bytes],
) -> Response:
    authz.authorize(ctx, "settings_write")
    wid = params["wid"]
    workspace_catalog.get_workspace(wid)
    payload = json_body(body)
    if wid == workspace_catalog.LEGACY_WORKSPACE_ID:
        return Response(payload=profiles.rename_profile(params["pid"], payload))
    item = workspace_catalog.rename_profile(wid, params["pid"], payload.get("name"))
    return Response(payload={"profile": item})


def delete_workspace_profile(
    ctx: RequestContext,
    params: Dict[str, str],
    query: Dict[str, List[str]],
    body: Optional[bytes],
) -> Response:
    authz.authorize(ctx, "settings_write")
    wid = params["wid"]
    workspace_catalog.get_workspace(wid)
    pid = params["pid"]
    if wid == workspace_catalog.LEGACY_WORKSPACE_ID:
        actor = ctx.actor
        return Response(
            payload=profiles.delete_profile(
                pid,
                actor_name=(actor.name if actor is not None else ""),
                actor_ip=ctx.ip,
            )
        )
    if not any(str(p.get("id")) == pid for p in workspace_catalog.list_profiles(wid)):
        raise AppError(NOT_FOUND, "机型不存在")
    if pid == workspace_catalog.DEFAULT_PROFILE_ID:
        raise AppError(INVALID_INPUT, "默认机型不能删除", field="id")
    with storage.use_workspace(wid), storage.profile_write(pid):
        entry = recovery.move_profile_to_recovery(
            pid,
            actor_name=(ctx.actor.name if ctx.actor is not None else ""),
            actor_ip=ctx.ip,
            workspace_id=wid,
        )
        try:
            workspace_catalog.remove_profile(wid, pid)
        except BaseException:
            recovery.restore_profile_entry(entry, pid)
            raise
    return Response(payload={"deleted": True, "recovery": entry})
