"""Read-only parameter workbench queries over the existing configuration store."""

from typing import Any, Dict

from ..core import storage
from ..core.errors import INVALID_INPUT, NOT_FOUND, AppError
from . import config_v3_catalog, config_v3_drafts


def _member_key(member):
    member = member or {}
    state = member.get("value_state", "unset")
    return (member.get("membership", "not_introduced"), member.get("enabled", True), state,
            str(member.get("value")) if state == "present" else None, member.get("intent", "inherit"))


def query(profile_id: str, config_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    try:
        offset = int(payload.get("offset", 0))
        limit = int(payload.get("limit", 30))
    except (TypeError, ValueError):
        raise AppError(INVALID_INPUT, "分页参数需为整数", field="limit")
    if isinstance(payload.get("limit"), (bool, float)) or isinstance(payload.get("offset"), (bool, float)) or offset < 0 or limit not in (30, 50, 100):
        raise AppError(INVALID_INPUT, "每页可选择 30、50 或 100 项", field="limit")
    keyword, directory = payload.get("q", ""), payload.get("directory", "")
    if not isinstance(keyword, str) or not isinstance(directory, str):
        raise AppError(INVALID_INPUT, "搜索和目录需为文本", field="q")
    mode = payload.get("filter", "all")
    if mode not in ("all", "different", "modified"):
        raise AppError(INVALID_INPUT, "不支持的参数筛选", field="filter")
    changed = payload.get("changed_ids", [])
    if not isinstance(changed, list) or any(not isinstance(item, str) for item in changed):
        raise AppError(INVALID_INPUT, "修改项需为参数标识列表", field="changed_ids")
    with storage.use_profile(profile_id):
        doc = config_v3_catalog._load(profile_id)
    config = doc["configs"].get(config_id)
    if config is None:
        raise AppError(NOT_FOUND, "配置不存在", config_id=config_id)
    release, record = config_v3_catalog.configuration_record(config, str(payload.get("revision_id") or ""))
    revision = int(record.get("definition_revision", release["definition_revision"]))
    definition = next((row for row in (config.get("definition") or {}).get("history", []) if row["revision"] == revision), None)
    if definition is None:
        raise AppError(NOT_FOUND, "此配置记录的参数定义不存在", definition_revision=revision)
    reference_id = payload.get("reference_id")
    if reference_id:
        _release, reference = config_v3_catalog.configuration_record(config, str(reference_id))
    else:
        reference = None
    if mode == "different" and reference is None:
        raise AppError(INVALID_INPUT, "当前没有可比较的参照配置", field="filter")
    values = dict(record.get("values") or {})
    edits = payload.get("edits", {})
    if not isinstance(edits, dict):
        raise AppError(INVALID_INPUT, "编辑内容需为参数映射", field="edits")
    parameters = definition.get("parameters") or {}
    source_id = record.get("base_revision_id") or record.get("source_revision_id")
    needs_basis = any(isinstance(patch, dict) and patch.get("intent") == "inherit" for patch in edits.values())
    basis = config_v3_catalog.configuration_record(config, source_id)[1].get("values", {}) if source_id and needs_basis else {}
    for pid, patch in edits.items():
        if pid not in parameters or not isinstance(patch, dict):
            raise AppError(INVALID_INPUT, "编辑内容引用了无效参数", field="edits")
        if patch.get("intent") == "inherit":
            values[pid] = config_v3_drafts.patch_branch_member(values.get(pid), patch, parameters[pid].get("type", ""), basis.get(pid))
        else:
            # Read queries retain invalid input for filtering and recovery. The
            # domain PATCH remains responsible for validating and saving values.
            values[pid] = dict(values.get(pid) or {}, **{key: value for key, value in patch.items()
                               if key in ("membership", "enabled", "value_state", "value", "intent")})
            if values[pid].get("value_state") != "present":
                values[pid].pop("value", None)
    reference_values = (reference or {}).get("values") or {}
    bindings = (release.get("binding_set") or {}).get("bindings") or {}
    by_parameter = {row["parameter_id"]: row for row in bindings.values()}
    keyword = keyword.strip().casefold()
    parts = directory.strip("/").split("/") if directory.strip("/") else []
    changed_ids = set(changed)
    rows = []
    for parameter in (definition.get("parameters") or {}).values():
        pid = parameter["parameter_id"]
        binding = by_parameter.get(pid) or {}
        field = binding.get("locator", "")
        if keyword and keyword not in (parameter["name"] + " " + field).casefold():
            continue
        if parts and (parameter.get("directory") or [])[:len(parts)] != parts:
            continue
        if mode == "modified" and pid not in changed_ids:
            continue
        if mode == "different" and _member_key(values.get(pid)) == _member_key(reference_values.get(pid)):
            continue
        rows.append(dict(parameter, file_field=field, member=values.get(pid), reference=reference_values.get(pid)))
    rows.sort(key=lambda row: (row["name"].casefold(), row["parameter_id"]))
    return {"items": rows[offset:offset + limit], "total": len(rows), "offset": offset, "limit": limit,
            "parameter_total": len(definition.get("parameters") or {}),
            "definition_revision": revision, "reference_id": reference_id or None,
            "revision_id": payload.get("revision_id"), "release_id": release["release_id"]}
