"""M16-26 更新候选只读 API（创建由 sync 执行层调用 service）。"""

from __future__ import annotations

from typing import Dict, List, Optional

from app.core import config_values, storage
from app.core.errors import INVALID_INPUT, NOT_FOUND, AppError
from app.services import (
    authz,
    config_v3_catalog,
    config_v3_diff,
    config_v3_drafts,
    config_v3_import,
    sync_candidate_apply,
    sync_candidate_plans,
    sync_candidates,
    workspace_catalog,
)
from ..services.context import RequestContext
from .router import Response, Router, json_body, query_param


def register(router: Router) -> None:
    router.add("GET", "/api/v3/workspaces/{wid}/sync/candidates", list_candidates)
    router.add("GET", "/api/v3/workspaces/{wid}/sync/candidates/{cid}", get_candidate)
    router.add(
        "POST",
        "/api/v3/workspaces/{wid}/sync/candidates/{cid}/apply-preview",
        candidate_preview,
    )
    router.add(
        "POST",
        "/api/v3/workspaces/{wid}/sync/candidates/{cid}/apply",
        candidate_apply,
    )


def _scope(wid: str) -> None:
    workspace_catalog.get_workspace(wid)


def _int(raw: str, default: int) -> int:
    try:
        return int(str(raw).strip())
    except (TypeError, ValueError):
        return default


def list_candidates(
    ctx: RequestContext,
    params: Dict[str, str],
    query: Dict[str, List[str]],
    body: Optional[bytes],
) -> Response:
    authz.authorize(ctx, "read")
    wid = params["wid"]
    _scope(wid)
    return Response(
        payload=sync_candidates.list_candidates(
            wid,
            profile_id=query_param(query, "profile_id") or None,
            state=query_param(query, "state") or None,
            limit=max(1, min(_int(query_param(query, "limit"), 50), 200)),
            cursor=query_param(query, "cursor") or "0",
        )
    )


def get_candidate(
    ctx: RequestContext,
    params: Dict[str, str],
    query: Dict[str, List[str]],
    body: Optional[bytes],
) -> Response:
    authz.authorize(ctx, "read")
    wid = params["wid"]
    _scope(wid)
    return Response(payload=sync_candidates.get_candidate(wid, params["cid"]))


_KIND_BY_TYPE = {
    "number": "decimal",
    "decimal": "decimal",
    "float": "decimal",
    "int": "int",
    "integer": "int",
    "bool": "bool",
    "boolean": "bool",
    "string": "string",
    "enum": "string",
    "raw": "string",
}


def _definition_params(wid: str, pid: str, config_id: str, draft_id: str):
    with storage.use_workspace(wid), storage.use_profile(pid):
        doc = config_v3_catalog._load(pid)
        config, release = config_v3_drafts._find_release(doc, config_id, draft_id)
        definition = config_v3_drafts._definition_for(release, config)
    return config, release, definition.get("parameters") or {}


def _raw_entries(values, params):
    out = {}
    for parameter_id, row in (values or {}).items():
        if not isinstance(row, dict) or parameter_id not in params:
            continue
        if row.get("membership", "not_introduced") != "included" or not row.get("enabled", True):
            continue
        if row.get("value_state") != "present":
            continue
        definition = params.get(parameter_id) or {}
        kind = _KIND_BY_TYPE.get(str(definition.get("type") or "").lower(), "string")
        try:
            typed = config_values.parse_typed(
                row.get("value"), kind, definition.get("unit") or None
            )
        except AppError:
            continue
        out[parameter_id] = {
            "value": typed.to_json(),
            "intent": row.get("intent") or "inherit",
        }
    return out


def candidate_preview(
    ctx: RequestContext,
    params: Dict[str, str],
    query: Dict[str, List[str]],
    body: Optional[bytes],
) -> Response:
    authz.authorize(ctx, "read")
    wid = params["wid"]
    _scope(wid)
    candidate = sync_candidates.get_candidate(wid, params["cid"])
    payload = json_body(body)
    config_id = str(payload.get("config_id") or "").strip()
    draft_id = str(payload.get("draft_id") or "").strip()
    if not config_id or not draft_id:
        raise AppError(INVALID_INPUT, "config_id 与 draft_id 必填", field="config_id")
    if not candidate.get("file_version_id"):
        raise AppError(
            INVALID_INPUT,
            "候选没有关联文件版本，无法生成参数差异",
            field="file_version_id",
        )
    pid = candidate["profile_id"]
    config, release, params_map = _definition_params(wid, pid, config_id, draft_id)
    try:
        import_plan = config_v3_import.create(
            wid,
            pid,
            config_id,
            {"version_id": candidate["file_version_id"], "release_id": release["release_id"]},
        )
    except AppError as exc:
        raise AppError(
            INVALID_INPUT,
            "无法从候选文件版本生成参数值：%s" % exc.message,
            field="file_version_id",
        )
    candidate_values = {}
    for item in import_plan.get("items") or []:
        parameter_id = item.get("parameter_id")
        candidates = item.get("candidates") or []
        if not parameter_id and len(candidates) == 1:
            parameter_id = candidates[0].get("parameter_id")
        if not parameter_id or item.get("value") is None:
            continue
        definition = params_map.get(parameter_id) or {}
        kind = _KIND_BY_TYPE.get(str(definition.get("type") or "").lower(), "string")
        try:
            typed = config_values.parse_typed(
                item.get("value"), kind, definition.get("unit") or None
            )
        except AppError:
            continue
        candidate_values[parameter_id] = typed.to_json()

    base = {}
    base_revision_id = str(payload.get("base_revision_id") or "").strip()
    if base_revision_id:
        base_revision = config_v3_diff.resolve(config, base_revision_id)
        base = _raw_entries(base_revision.get("values"), params_map)
    else:
        d0 = release.get("d0") or {}
        if d0.get("frozen") and isinstance(d0.get("frozen_snapshot"), dict):
            base = _raw_entries(d0.get("frozen_snapshot"), params_map)
        else:
            with storage.use_workspace(wid):
                draft = config_v3_drafts.get_draft(pid, config_id, draft_id)
            base = sync_candidate_plans._current_entries(draft, params_map)
    preview = sync_candidate_plans.create_preview(
        wid,
        pid,
        config_id,
        draft_id,
        params["cid"],
        base,
        candidate_values,
    )
    return Response(status=201, payload=preview)


def candidate_apply(
    ctx: RequestContext,
    params: Dict[str, str],
    query: Dict[str, List[str]],
    body: Optional[bytes],
) -> Response:
    authz.authorize(ctx, "settings_write")
    wid = params["wid"]
    _scope(wid)
    payload = json_body(body)
    preview_id = str(payload.get("preview_id") or "").strip()
    if not preview_id:
        raise AppError(INVALID_INPUT, "preview_id 必填", field="preview_id")
    preview = sync_candidate_plans.get_preview(wid, preview_id)
    if preview.get("candidate_id") != params["cid"]:
        raise AppError(NOT_FOUND, "预览不属于该候选", field="candidate_id")
    result = sync_candidate_apply.apply_preview(
        wid,
        preview["profile_id"],
        preview_id,
        payload.get("request_id"),
        payload.get("decisions") or {},
    )
    return Response(payload=result)
