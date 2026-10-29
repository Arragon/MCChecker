"""V3 精确模板导出（M14-04 / INH-2153）。

模板按不可变 FileVersion 读取，绑定按已确认 binding_set；冻结修订的值逐个
写回模板，未匹配字段保持原样。preview_token 绑定输出 digest，commit 才生成
不可变 ExportArtifact；下载永不覆盖原文件。
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import threading
import uuid
import xml.etree.ElementTree as ET
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, List, Optional, Tuple

from ..core import config_mapping, fsutil, storage
from ..core.errors import INVALID_INPUT, NOT_FOUND, PARSE_FAILED, REVIEW_CONFLICT, AppError
from . import config_v3_catalog, config_v3_extract, config_v3_merge, config_v3_provenance, documents, file_versions

_LOCK = threading.RLock()
_PLANS: Dict[str, Dict[str, Any]] = {}


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _typed_value(row: Dict[str, Any], definition: Dict[str, Any]) -> Any:
    state = str(row.get("value_state") or "unset")
    if state == "null":
        return None
    raw = row.get("value")
    type_name = str(definition.get("type") or "").lower()
    try:
        if type_name in ("number", "decimal", "float", "int", "integer"):
            text = str(raw).strip()
            if type_name in ("int", "integer"):
                return int(Decimal(text))
            return float(Decimal(text))
        if type_name in ("bool", "boolean"):
            if isinstance(raw, bool):
                return raw
            return str(raw).strip().lower() in ("1", "true", "yes", "on")
    except (InvalidOperation, ValueError) as exc:
        raise AppError(INVALID_INPUT, "值无法转换为模板类型", field="value") from exc
    return raw


def _set_json(document: Any, pointer: str, value: Any) -> None:
    parts = config_mapping.parse_pointer(pointer)
    node = document
    for index, part in enumerate(parts):
        last = index == len(parts) - 1
        if isinstance(node, dict):
            if last:
                if part not in node:
                    raise AppError(INVALID_INPUT, "模板缺少 JSON 路径", field="locator", path=pointer)
                node[part] = value
                return
            if part not in node:
                raise AppError(INVALID_INPUT, "模板缺少 JSON 路径", field="locator", path=pointer)
            node = node[part]
        elif isinstance(node, list):
            try:
                position = int(part)
                child = node[position]
            except (ValueError, IndexError) as exc:
                raise AppError(INVALID_INPUT, "模板缺少 JSON 数组下标", field="locator", path=pointer) from exc
            if last:
                node[position] = value
                return
            node = child
        else:
            raise AppError(INVALID_INPUT, "模板路径穿过标量", field="locator", path=pointer)


def _xml_steps(display_path: str) -> Tuple[List[Tuple[str, int]], str]:
    text = str(display_path or "")
    if not text.startswith("/"):
        raise AppError(INVALID_INPUT, "XML 绑定必须是从根开始的路径", field="locator")
    attribute = ""
    if "/@" in text:
        text, attribute = text.rsplit("/@", 1)
    steps = []
    for segment in [part for part in text.split("/") if part]:
        name, _, occurrence = segment.partition("[")
        try:
            number = int(occurrence.rstrip("]") or "1")
        except ValueError as exc:
            raise AppError(INVALID_INPUT, "XML 路径序号非法", field="locator") from exc
        steps.append((name, number))
    return steps, attribute


def _set_xml(root: ET.Element, display_path: str, value: Any) -> None:
    steps, attribute = _xml_steps(display_path)
    node = root
    if not steps:
        raise AppError(INVALID_INPUT, "XML 路径为空", field="locator")
    first_name, first_occ = steps[0]
    if node.tag.split("}")[-1] != first_name:
        raise AppError(INVALID_INPUT, "模板根节点与绑定路径不一致", field="locator")
    for name, occurrence in steps[1:]:
        matches = [child for child in list(node) if child.tag.split("}")[-1] == name]
        if occurrence > len(matches):
            raise AppError(INVALID_INPUT, "模板缺少 XML 节点", field="locator", path=display_path)
        node = matches[occurrence - 1]
    if attribute:
        node.set(attribute, "" if value is None else str(value))
    else:
        node.text = "" if value is None else str(value)


def _set_ini(text: str, locator: str, value: Any) -> str:
    section, _, key = str(locator).partition("/")
    lines = text.splitlines()
    current = ""
    for index, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            current = stripped[1:-1].strip()
            continue
        if current == section and stripped.split("=", 1)[0].strip() == key and "=" in line:
            prefix = line.split("=", 1)[0]
            lines[index] = prefix + "=" + ("" if value is None else str(value))
            return "\n".join(lines) + ("\n" if text.endswith("\n") else "")
    raise AppError(INVALID_INPUT, "模板缺少 INI section/key", field="locator", path=locator)


def _export_dir(pid: str) -> str:
    return os.path.join(storage.get_profile_data_dir(pid), "configs_v3", "exports")


def create_plan(profile_id: str, config_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    release_id = str(payload.get("release_id") or "").strip()
    revision_id = str(payload.get("revision_id") or "").strip()
    version_id = str(payload.get("version_id") or "").strip()
    confirmed = bool(payload.get("confirm_removed"))
    if not (release_id and revision_id and version_id):
        raise AppError(INVALID_INPUT, "release_id/revision_id/version_id 必填", field="version_id")
    with storage.use_profile(profile_id):
        doc = config_v3_catalog._load(profile_id)
        revision = config_v3_merge.resolve_revision(profile_id, config_id, revision_id)
        identified = file_versions.find_version(profile_id, version_id)
        if identified is None:
            raise AppError(NOT_FOUND, "模板版本不存在", version_id=version_id)
        name = identified["name"]
        fmt = config_v3_extract.format_of(name)
        if fmt not in ("json", "xml", "ini"):
            raise AppError(INVALID_INPUT, "模板只支持 JSON/XML/INI", field="version_id")
        ref = config_v3_provenance.make_ref(profile_id, name, identified["ref"])
        verified = config_v3_provenance.assert_usable(ref)
        raw = documents.load_raw(profile_id, name, identified["ref"])
        if raw.sha256 != verified["blob_sha256"]:
            raise AppError(REVIEW_CONFLICT, "模板内容已变化", field="version_id")
    config = doc["configs"].get(config_id)
    release = (config.get("releases") or {}).get(release_id)
    if release is None:
        raise AppError(NOT_FOUND, "软件版本不存在", release_id=release_id)
    definitions = (config_v3_catalog._definition(config).get("current", {}).get("parameters") or {})
    bindings = ((release.get("binding_set") or {}).get("bindings") or {}).values()
    values = revision["values"]
    issues = []
    blocked = []
    for binding in bindings:
        if binding.get("file_id") and binding["file_id"] != identified["file_id"]:
            continue
        parameter_id = str(binding.get("parameter_id") or "")
        row = values.get(parameter_id) or {}
        state = str(row.get("membership") or "not_introduced")
        if state != "included" or not row.get("enabled", True):
            blocked.append({"parameter_id": parameter_id, "reason": state})
            continue
        if str(row.get("value_state") or "unset") not in ("present", "null"):
            issues.append({"parameter_id": parameter_id, "reason": "missing_value"})
    if blocked and not confirmed:
        raise AppError(INVALID_INPUT, "存在停用/移除字段，需要显式确认", field="confirm_removed",
                       blocked=blocked)
    if issues:
        raise AppError(INVALID_INPUT, "绑定参数缺值", field="values", issues=issues)
    blocked_ids = {row["parameter_id"] for row in blocked}
    output = raw.data
    if fmt == "json":
        try:
            document = json.loads(output.decode("utf-8"))
        except (ValueError, UnicodeDecodeError) as exc:
            raise AppError(PARSE_FAILED, "模板 JSON 无法解析") from exc
    elif fmt == "xml":
        try:
            root = ET.fromstring(output)
        except ET.ParseError as exc:
            raise AppError(PARSE_FAILED, "模板 XML 无法解析") from exc
    else:
        text = output.decode("utf-8-sig")
    applied = 0
    for binding in bindings:
        if binding.get("file_id") and binding["file_id"] != identified["file_id"]:
            continue
        parameter_id = str(binding.get("parameter_id") or "")
        if parameter_id in blocked_ids:
            continue
        row = values.get(parameter_id) or {}
        if str(row.get("value_state") or "unset") not in ("present", "null"):
            continue
        locator = str(binding.get("locator") or "")
        target = _typed_value(row, definitions.get(parameter_id) or {}) if fmt == "json" \
            else (None if str(row.get("value_state")) == "null" else row.get("value"))
        if fmt == "json":
            _set_json(document, locator[5:] if locator.startswith("json:") else locator, target)
        elif fmt == "xml":
            _set_xml(root, locator, target)
        else:
            text = _set_ini(text, locator, target)
        applied += 1
    if fmt == "json":
        output = json.dumps(document, ensure_ascii=False, indent=2).encode("utf-8")
    elif fmt == "xml":
        output = ET.tostring(root, encoding="utf-8")
        try:
            ET.fromstring(output)
        except ET.ParseError as exc:
            raise AppError(PARSE_FAILED, "导出 XML 重新解析失败") from exc
    else:
        output = text.encode("utf-8")
    digest = hashlib.sha256(output).hexdigest()
    plan = {
        "plan_id": "ex-" + uuid.uuid4().hex[:16],
        "workspace_id": storage.get_active_workspace(),
        "profile_id": profile_id, "config_id": config_id,
        "release_id": release_id, "revision": copy.deepcopy(revision),
        "template": {"name": name, "file_id": identified["file_id"],
                     "version_id": identified["version_id"], "sha256": identified["blob_sha256"],
                     "format": fmt},
        "output": output, "digest": digest, "applied": applied,
        "blocked": blocked, "confirmed": confirmed,
        "preview_token": hashlib.sha256((digest + revision_id).encode()).hexdigest(),
        "created_at": _now(),
    }
    with _LOCK:
        _PLANS[plan["plan_id"]] = plan
    return _public(plan)


def _public(plan: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "plan_id": plan["plan_id"], "config_id": plan["config_id"],
        "release_id": plan["release_id"], "template": copy.deepcopy(plan["template"]),
        "revision_id": plan["revision"]["revision_id"], "digest": plan["digest"],
        "preview_token": plan["preview_token"], "applied": plan["applied"],
        "blocked": copy.deepcopy(plan["blocked"]),
        "preview": plan["output"].decode("utf-8", "replace")[:4000],
    }


def commit(profile_id: str, config_id: str, plan_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    with _LOCK:
        plan = _PLANS.get(str(plan_id))
        if plan is None or plan["profile_id"] != profile_id or plan["config_id"] != config_id:
            raise AppError(NOT_FOUND, "导出计划不存在", plan_id=plan_id)
        if str(payload.get("preview_token") or "") != plan["preview_token"]:
            raise AppError(REVIEW_CONFLICT, "预览令牌不匹配，请重新预览", field="preview_token")
    artifact_id = "ea-" + uuid.uuid4().hex[:12]
    fmt = plan["template"]["format"]
    filename = "config-%s.%s" % (artifact_id, fmt)
    directory = _export_dir(profile_id)
    os.makedirs(directory, exist_ok=True)
    fsutil.atomic_write_bytes(os.path.join(directory, filename), plan["output"])
    meta = {
        "artifact_id": artifact_id, "config_id": config_id, "release_id": plan["release_id"],
        "revision_id": plan["revision"]["revision_id"], "template": plan["template"],
        "digest": plan["digest"], "filename": filename, "created_at": _now(),
        "deployed": False,
    }
    fsutil.atomic_write_json(os.path.join(directory, artifact_id + ".json"), meta)
    return {"artifact": meta, "download": "/api/v3/workspaces/%s/profiles/%s/exports/%s/download"
            % (plan["workspace_id"], profile_id, artifact_id)}


def artifact(profile_id: str, artifact_id: str) -> Dict[str, Any]:
    path = os.path.join(_export_dir(profile_id), artifact_id + ".json")
    if not os.path.isfile(path):
        raise AppError(NOT_FOUND, "导出产物不存在", artifact_id=artifact_id)
    with open(path, "r", encoding="utf-8") as handle:
        return json.load(handle)


def artifact_bytes(profile_id: str, artifact_id: str) -> Tuple[bytes, str]:
    meta = artifact(profile_id, artifact_id)
    filename = str(meta.get("filename") or "")
    if os.path.basename(filename) != filename:
        raise AppError(INVALID_INPUT, "产物文件名非法", artifact_id=artifact_id)
    path = os.path.join(_export_dir(profile_id), filename)
    with open(path, "rb") as handle:
        return handle.read(), filename
