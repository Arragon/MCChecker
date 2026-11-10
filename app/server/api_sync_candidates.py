"""M16-26 更新候选只读 API（创建由 sync 执行层调用 service）。"""

from __future__ import annotations

from typing import Dict, List, Optional

from app.core import config_values, storage
from app.core.errors import INVALID_INPUT, NOT_FOUND, REVISION_CONFLICT, AppError
from app.services import (
    authz,
    config_v3_catalog,
    config_v3_drafts,
    config_v3_import,
    sync_candidate_apply,
    sync_candidate_plans,
    sync_candidates,
    sync_sources,
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
            configuration_id=query_param(query, "configuration_id") or None,
            pending_only=query_param(query, "pending_only").lower() in ("true", "1"),
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


def _mapped_values(plan, params):
    values, blockers = {}, []
    for item in plan.get("items") or []:
        parameter_id = item.get("parameter_id")
        matches = item.get("candidates") or []
        if not parameter_id and len(matches) == 1:
            parameter_id = matches[0].get("parameter_id")
        if not parameter_id or parameter_id in values:
            blockers.append({"code": "mapping_ambiguous", "message": "来源参数未映射或重复映射，请先在配置管理中确认映射"})
            continue
        definition = params.get(parameter_id) or {}
        kind = _KIND_BY_TYPE.get(str(definition.get("type") or "").lower(), "string")
        try:
            typed = config_values.parse_typed(item.get("value"), kind, definition.get("unit") or None)
        except AppError:
            blockers.append({"code": "type_invalid", "parameter_id": parameter_id, "message": "来源值无法转换为目标参数类型"})
            continue
        values[parameter_id] = typed.to_json()
    return values, blockers


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
    expected_scope = "default_candidate:%s:%s" % (config_id, release["release_id"])
    if str(candidate.get("target_scope") or "").startswith("default_candidate:") and candidate["target_scope"] != expected_scope:
        raise AppError(INVALID_INPUT, "候选绑定到其他固定配置或软件版本", field="release_id")
    source_guard = None
    try:
        source = sync_sources.get_source(wid, candidate["source_id"])
    except AppError as exc:
        if exc.code != NOT_FOUND:
            raise
        raise AppError(REVISION_CONFLICT, "来源已移除；候选仅可查看，请先重新配置来源", field="source_id")
    else:
        if int(source["revision"]) != int(candidate["binding_revision"]):
            raise AppError(REVISION_CONFLICT, "来源绑定已变化，请重新获取候选", field="source_id")
        source_guard = {"source_id": source["source_id"], "revision": source["revision"]}
    if payload.get("base_revision_id"):
        raise AppError(INVALID_INPUT, "比较基线由原来源文件版本确定，不能指定其他配置修订", field="base_revision_id")
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
    if import_plan["source"]["blob_sha256"] != candidate["content_hash"]:
        raise AppError(INVALID_INPUT, "候选内容指纹与关联文件版本不符", field="file_version_id")
    candidate_values, blockers = _mapped_values(import_plan, params_map)
    base = {}
    base_version_id = candidate.get("base_file_version_id")
    if base_version_id:
        original = config_v3_import.create(wid, pid, config_id,
            {"version_id": base_version_id, "release_id": release["release_id"]})
        base, base_blockers = _mapped_values(original, params_map)
        blockers.extend(base_blockers)
    preview = sync_candidate_plans.create_preview(
        wid,
        pid,
        config_id,
        draft_id,
        params["cid"],
        base,
        candidate_values,
        base_known=bool(base_version_id),
        extra_blockers=blockers,
        source_guard=source_guard,
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
    # The service validates after checking its immutable receipt. A successful
    # write changes its own draft, so validating here would reject lost-response
    # retries before they could retrieve that receipt.
    preview = sync_candidate_plans.stored_preview(wid, preview_id)
    if preview.get("candidate_id") != params["cid"]:
        raise AppError(NOT_FOUND, "预览不属于该候选", field="candidate_id")
    result = sync_candidate_apply.apply_preview(
        wid,
        preview["profile_id"],
        preview_id,
        payload.get("request_id"),
        payload.get("decisions") or {},
        bool(payload.get("create_baseline")),
    )
    return Response(payload=result)
