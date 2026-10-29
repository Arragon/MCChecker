"""V3 参数提取预览与增量身份匹配（M13-04 / INH-2146）。

边界：

* 只读取 file-selector 的**精确 FileVersion**（file_id/version_id/sha），按
  JSON pointer / XML ns+occurrence 定位；不按文件同名重新读取。
* 只生成“叶名称 + 完整层级”；不生成释义、不读取/写入参数值。`value_kind`
  仅用于分类（null 必须选类型或跳过），原始值不进入计划。
* 预览是**零写**：不建参数、不建目录、不改定义修订。
* 身份匹配顺序：同 file_id + canonical_locator（来源索引）→ 同名同层级 →
  同名异路径 → 多候选歧义。业务 parameter_id 绝不按文件名或值猜测。
* 原始 key 保留大小写；同名叶在不同层级各自成行，不互相覆盖。
"""

from __future__ import annotations

import hashlib
import os
import re
from typing import Any, Dict, List, Optional, Tuple

from ..core import config_mapping, storage
from ..core.errors import INVALID_INPUT, NOT_FOUND, AppError
from . import (
    config_v3_catalog,
    config_v3_parse,
    config_v3_provenance,
    documents,
    file_versions,
)

# -- 分类 -------------------------------------------------------------------

STATUS_NEW = "new"
STATUS_EXISTING = "existing"
STATUS_SAME_NAME_OTHER_PATH = "same_name_other_path"
STATUS_AMBIGUOUS = "ambiguous"
STATUS_TYPE_CONFLICT = "type_conflict"
STATUS_NULL_UNCERTAIN = "null_uncertain"
STATUS_UNSUPPORTED = "unsupported"
STATUSES = (
    STATUS_NEW,
    STATUS_EXISTING,
    STATUS_SAME_NAME_OTHER_PATH,
    STATUS_AMBIGUOUS,
    STATUS_TYPE_CONFLICT,
    STATUS_NULL_UNCERTAIN,
    STATUS_UNSUPPORTED,
)
CONFLICT_STATUSES = (
    STATUS_SAME_NAME_OTHER_PATH,
    STATUS_AMBIGUOUS,
    STATUS_TYPE_CONFLICT,
    STATUS_NULL_UNCERTAIN,
)

ACTION_ADD = "add"
ACTION_LINK = "link"
ACTION_SKIP = "skip"
ACTION_PENDING = "pending"

FILTER_ALL = "all"
FILTER_NEW = "new"
FILTER_EXISTING = "existing"
FILTER_CONFLICT = "conflict"
FILTER_ACTIONABLE = "actionable"
FILTERS = (FILTER_ALL, FILTER_NEW, FILTER_EXISTING, FILTER_CONFLICT, FILTER_ACTIONABLE)

TYPE_OPTIONS = ("string", "number", "boolean", "decimal")

MAX_NAME_LENGTH = 200
MAX_DIRECTORY_SEGMENT = 100
MAX_DIRECTORY_DEPTH = 128
_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")
_NULL = object()


def format_of(name: str) -> str:
    return os.path.splitext(str(name or ""))[1].lstrip(".").lower()


def _value_kind(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, (int, float)):
        return "number"
    return "string"


def _inferred_type(kind: str, source_format: str) -> str:
    """只有 JSON 能安全地从字面量推断类型；XML 文本一律不猜数字。"""
    if source_format != "json":
        return ""
    return {"string": "string", "number": "number", "boolean": "boolean"}.get(kind, "")


def _types_compatible(declared: str, inferred: str) -> bool:
    declared = str(declared or "").strip().lower()
    inferred = str(inferred or "").strip().lower()
    if not declared or not inferred or declared == inferred:
        return True
    numeric = {"number", "decimal", "integer", "int", "float", "double"}
    return declared in numeric and inferred in numeric


def _row_id(canonical: str) -> str:
    return "x-" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]


def _leaf_problem(leaf: str, directory: List[str]) -> Optional[str]:
    if not leaf:
        return "no_leaf_name"
    if leaf != leaf.strip():
        return "name_whitespace"
    if len(leaf) > MAX_NAME_LENGTH or _CONTROL_RE.search(leaf):
        return "name_invalid"
    if len(directory) > MAX_DIRECTORY_DEPTH:
        return "directory_too_deep"
    for segment in directory:
        if not segment or len(segment) > MAX_DIRECTORY_SEGMENT or _CONTROL_RE.search(segment):
            return "directory_segment_invalid"
    return None


def _candidate(row: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "parameter_id": row.get("parameter_id"),
        "name": row.get("name"),
        "directory": list(row.get("directory") or []),
        "type": row.get("type") or "",
    }


def _index_parameters(parameters: List[Dict[str, Any]]) -> Tuple[Dict[str, Dict[str, Any]], Dict[str, List[Dict[str, Any]]]]:
    by_locator: Dict[str, Dict[str, Any]] = {}
    by_name: Dict[str, List[Dict[str, Any]]] = {}
    for row in parameters:
        name = str(row.get("name") or "")
        by_name.setdefault(name, []).append(row)
        source = row.get("source") or {}
        locator = source.get("canonical_locator") if isinstance(source, dict) else None
        file_id = source.get("file_id") if isinstance(source, dict) else None
        if locator and file_id:
            by_locator["%s|%s" % (file_id, locator)] = row
    return by_locator, by_name


def _base_item(row: Dict[str, Any], source_format: str) -> Optional[Dict[str, Any]]:
    locator = row.get("locator") or {}
    canonical = config_mapping.canonical_locator(locator)
    try:
        leaf, directory, kind = config_mapping.locator_leaf(locator)
    except AppError:
        leaf, directory, kind = "", [], str(locator.get("kind") or "")
    item: Dict[str, Any] = {
        "row_id": _row_id(canonical),
        "canonical_locator": canonical,
        "display_path": row.get("display_path") or "",
        "leaf_name": leaf,
        "directory": directory,
        "locator_kind": kind,
        "value_kind": _value_kind(row.get("value")),
        "status": STATUS_NEW,
        "suggested_action": ACTION_ADD,
        "suggested_type": "",
        "existing_parameter_id": None,
        "existing_parameter": None,
        "matched_by": None,
        "candidates": [],
        "reasons": [],
        "type_options": [],
    }
    if kind in ("attribute", "element_text"):
        item["status"] = STATUS_UNSUPPORTED
        item["suggested_action"] = ACTION_SKIP
        item["reasons"].append(
            "attribute_not_parameter" if kind == "attribute" else "mixed_text_not_parameter"
        )
        return item
    problem = _leaf_problem(leaf, directory)
    if problem:
        item["status"] = STATUS_UNSUPPORTED
        item["suggested_action"] = ACTION_SKIP
        item["reasons"].append(problem)
        return item
    if source_format not in ("json", "xml"):
        item["status"] = STATUS_UNSUPPORTED
        item["suggested_action"] = ACTION_SKIP
        item["reasons"].append("unsupported_format")
        return item
    item["suggested_type"] = _inferred_type(item["value_kind"], source_format)
    return item


def _classify(
    item: Dict[str, Any],
    by_locator: Dict[str, Dict[str, Any]],
    by_name: Dict[str, List[Dict[str, Any]]],
    file_id: str,
    source_format: str,
) -> None:
    if item["status"] == STATUS_UNSUPPORTED:
        return
    leaf = item["leaf_name"]
    directory = item["directory"]
    inferred = item["suggested_type"]
    key = "%s|%s" % (file_id, item["canonical_locator"])
    match = by_locator.get(key)
    if match is None:
        match = next(
            (
                row
                for row in by_name.get(leaf, [])
                if list(row.get("directory") or []) == directory
            ),
            None,
        )
        matched_by = "name_directory" if match is not None else None
    else:
        matched_by = "source_locator"
    if match is not None:
        item["existing_parameter_id"] = match.get("parameter_id")
        item["existing_parameter"] = _candidate(match)
        item["matched_by"] = matched_by
        declared = str(match.get("type") or "")
        if inferred and not _types_compatible(declared, inferred):
            item["status"] = STATUS_TYPE_CONFLICT
            item["suggested_action"] = ACTION_PENDING
            item["reasons"].append("declared_type_conflict")
        else:
            item["status"] = STATUS_EXISTING
            item["suggested_action"] = ACTION_LINK
            if not inferred:
                item["suggested_type"] = declared
        return
    candidates = by_name.get(leaf) or []
    if candidates:
        item["candidates"] = [_candidate(row) for row in candidates]
        directories = {tuple(row.get("directory") or []) for row in candidates}
        if len(candidates) == 1:
            item["status"] = STATUS_SAME_NAME_OTHER_PATH
            item["reasons"].append("same_name_other_path")
        else:
            item["status"] = STATUS_AMBIGUOUS
            item["reasons"].append("multiple_name_candidates")
        item["suggested_action"] = ACTION_PENDING
        return
    if item["value_kind"] == "null":
        item["status"] = STATUS_NULL_UNCERTAIN
        item["suggested_action"] = ACTION_PENDING
        item["reasons"].append("null_needs_type")
        item["type_options"] = list(TYPE_OPTIONS)
        return
    item["status"] = STATUS_NEW
    item["suggested_action"] = ACTION_ADD


def _mark_duplicate_source_leaves(items: List[Dict[str, Any]]) -> None:
    groups: Dict[Tuple[str, Tuple[str, ...]], List[Dict[str, Any]]] = {}
    for item in items:
        if item["status"] in (STATUS_NEW, STATUS_NULL_UNCERTAIN):
            key = (item["leaf_name"], tuple(item["directory"]))
            groups.setdefault(key, []).append(item)
    for rows in groups.values():
        if len(rows) < 2:
            continue
        for item in rows:
            item["status"] = STATUS_AMBIGUOUS
            item["suggested_action"] = ACTION_PENDING
            item["reasons"].append("duplicate_source_leaf")


def classify_rows(
    rows: List[Dict[str, Any]],
    parameters: List[Dict[str, Any]],
    file_id: str,
    source_format: str,
) -> List[Dict[str, Any]]:
    """把解析行分类成提取计划（纯函数，不读写存储）。"""
    by_locator, by_name = _index_parameters(parameters)
    items = []
    for row in rows:
        item = _base_item(row, source_format)
        if item is not None:
            _classify(item, by_locator, by_name, file_id, source_format)
            items.append(item)
    _mark_duplicate_source_leaves(items)
    return items


def counts_of(items: List[Dict[str, Any]]) -> Dict[str, int]:
    counts = {status: 0 for status in STATUSES}
    for item in items:
        counts[item["status"]] += 1
    counts["conflict"] = sum(counts[status] for status in CONFLICT_STATUSES)
    counts["total"] = len(items)
    return counts


def filter_items(items: List[Dict[str, Any]], name: str) -> List[Dict[str, Any]]:
    name = str(name or FILTER_ALL).strip().lower()
    if name not in FILTERS:
        raise AppError(INVALID_INPUT, "未知的筛选条件", field="filter", allowed=list(FILTERS))
    if name == FILTER_ALL:
        return items
    if name == FILTER_NEW:
        return [item for item in items if item["status"] == STATUS_NEW]
    if name == FILTER_EXISTING:
        return [item for item in items if item["status"] == STATUS_EXISTING]
    if name == FILTER_CONFLICT:
        return [item for item in items if item["status"] in CONFLICT_STATUSES]
    return [
        item
        for item in items
        if item["status"] == STATUS_NEW or item["status"] in CONFLICT_STATUSES
    ]


def preview(
    profile_id: str,
    config_id: str,
    version_id: str,
    offset: int = 0,
    limit: int = 100,
    filter_name: str = FILTER_ALL,
) -> Dict[str, Any]:
    """只读提取预览：精确 FileVersion → 全量分类 → 分页。"""
    vid = str(version_id or "").strip()
    if not vid:
        raise AppError(INVALID_INPUT, "必须指定文件版本", field="version_id")
    with storage.use_profile(profile_id):
        identified = file_versions.find_version(profile_id, vid)
        if identified is None:
            raise AppError(NOT_FOUND, "文件版本不存在或不属于当前工作区/机型", version_id=vid)
        name = identified["name"]
        source_format = format_of(name)
        if source_format not in ("json", "xml"):
            raise AppError(
                INVALID_INPUT,
                "只有 JSON/XML 支持名称层级提取",
                field="version_id",
                format=source_format,
            )
        ref = config_v3_provenance.make_ref(profile_id, name, identified["ref"])
        verified = config_v3_provenance.assert_usable(ref)
        raw = documents.load_raw(profile_id, name, identified["ref"])
        if raw.sha256 != verified["blob_sha256"]:
            raise AppError(NOT_FOUND, "来源文件内容已变化，请重新选择", missing=True)
        if source_format == "xml":
            rows = config_v3_parse.xml_locators(raw.data)
        else:
            rows = config_v3_parse.json_locators(raw.data)
        catalog = config_v3_catalog.list_parameters(profile_id, config_id)
    items = classify_rows(rows, catalog["items"], identified["file_id"], source_format)
    counts = counts_of(items)
    filtered = filter_items(items, filter_name)
    start = max(0, int(offset))
    size = max(1, min(int(limit), 1000))
    return {
        "source": {
            "workspace_id": identified.get("workspace_id"),
            "profile_id": profile_id,
            "name": name,
            "file_id": identified["file_id"],
            "version_id": identified["version_id"],
            "version_label": identified.get("version_label"),
            "blob_sha256": identified["blob_sha256"],
            "format": source_format,
        },
        "revision": catalog["revision"],
        "counts": counts,
        "filter": str(filter_name or FILTER_ALL).strip().lower(),
        "total": len(filtered),
        "offset": start,
        "limit": size,
        "items": filtered[start : start + size],
    }
