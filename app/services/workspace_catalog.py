"""V3 Workspace catalog（M9-02 / INH-2119）。

一个 `data/workspace_catalog.json` 保存工作区元数据；业务数据根在
`data/workspaces/<wid>/profiles/<pid>/`。legacy 工作区（id `default`）在 catalog
不存在时只在内存合成，根仍是 `data/profiles/`，避免首读自动写真实数据。

本模块只做元数据与根路径；身份/权限仍由 `services.auth`/`authz` 全局判定。
"""

from __future__ import annotations

import copy
import os
import re
import threading
import uuid
from typing import Any, Dict, List, Optional

from ..core import fsutil, locks, storage
from ..core.errors import FILE_EXISTS, INVALID_INPUT, NOT_FOUND, AppError, CorruptDataError

LEGACY_WORKSPACE_ID = "default"
LEGACY_WORKSPACE_NAME = "默认工作区"
DEFAULT_PROFILE_ID = storage.DEFAULT_PROFILE
DEFAULT_PROFILE_NAME = "默认机型"
MAX_NAME = 50
MAX_DESCRIPTION = 300
SCHEMA_VERSION = 1

_NAME_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")
_WORKSPACE_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_LOCK = threading.RLock()


def catalog_path() -> str:
    """运行期拼路径：测试/`--data-dir` 重绑 `storage.DATA_DIR` 后才能跟着走。"""
    return os.path.join(storage.DATA_DIR, "workspace_catalog.json")


def workspace_root(workspace_id: str) -> str:
    wid = _validated_id(workspace_id)
    if wid == LEGACY_WORKSPACE_ID:
        return storage.PROFILES_DIR
    return os.path.join(storage.WORKSPACES_DIR, wid, "profiles")


def profile_root(workspace_id: str, profile_id: str) -> str:
    wid = _validated_id(workspace_id)
    pid = storage._normalize_profile_id(profile_id)
    return os.path.join(workspace_root(wid), pid)


def _validated_id(workspace_id: Any) -> str:
    text = str(workspace_id or "").strip()
    if not _WORKSPACE_ID_RE.match(text) or text in (".", ".."):
        raise AppError(NOT_FOUND, "工作区不存在")
    return text


def _validated_name(name: Any) -> str:
    text = str(name or "").strip()
    if not text or len(text) > MAX_NAME or _NAME_CONTROL_RE.search(text):
        raise AppError(
            INVALID_INPUT,
            "工作区名称需为 1-%d 个字符，且不含控制字符" % MAX_NAME,
            field="name",
        )
    return text


def _validated_description(description: Any) -> str:
    text = str(description or "").strip()
    if len(text) > MAX_DESCRIPTION:
        raise AppError(
            INVALID_INPUT,
            "工作区说明不能超过 %d 个字符" % MAX_DESCRIPTION,
            field="description",
        )
    return text


def _legacy_item() -> Dict[str, Any]:
    return {
        "id": LEGACY_WORKSPACE_ID,
        "name": LEGACY_WORKSPACE_NAME,
        "description": "",
        "default_profile_id": DEFAULT_PROFILE_ID,
        "profiles": [{"id": DEFAULT_PROFILE_ID, "name": DEFAULT_PROFILE_NAME}],
        "data_revision": 0,
        "created_at": "",
        "updated_at": "",
        "legacy": True,
    }


def _load_doc() -> Dict[str, Any]:
    raw = fsutil.read_json(catalog_path(), {"schema": SCHEMA_VERSION, "workspaces": []})
    if not isinstance(raw, dict) or not isinstance(raw.get("workspaces"), list):
        raise CorruptDataError(catalog_path())
    items = []
    for row in raw["workspaces"]:
        if not isinstance(row, dict) or not row.get("id"):
            raise CorruptDataError(catalog_path())
        items.append(copy.deepcopy(row))
    return {"schema": SCHEMA_VERSION, "workspaces": items}


def _public(item: Dict[str, Any]) -> Dict[str, Any]:
    row = copy.deepcopy(item)
    row.pop("legacy", None)
    return row


def _stored_legacy(doc: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    return next((w for w in doc["workspaces"] if w.get("id") == LEGACY_WORKSPACE_ID), None)


def _all_items(doc: Dict[str, Any]) -> List[Dict[str, Any]]:
    legacy = _stored_legacy(doc) or _legacy_item()
    others = [w for w in doc["workspaces"] if w.get("id") != LEGACY_WORKSPACE_ID]
    return [legacy] + others


def list_workspaces() -> List[Dict[str, Any]]:
    with _LOCK:
        return [_public(w) for w in _all_items(_load_doc())]


def get_workspace(workspace_id: str) -> Dict[str, Any]:
    wid = _validated_id(workspace_id)
    with _LOCK:
        for row in _all_items(_load_doc()):
            if row.get("id") == wid:
                return _public(row)
    raise AppError(NOT_FOUND, "工作区不存在")


def _name_taken(doc: Dict[str, Any], name: str, exclude_id: str = "") -> bool:
    for row in _all_items(doc):
        if row.get("id") != exclude_id and row.get("name") == name:
            return True
    return False


def _write_doc(doc: Dict[str, Any]) -> None:
    fsutil.atomic_write_json(catalog_path(), doc)


def _ensure_default_profile(workspace_id: str) -> None:
    path = profile_root(workspace_id, DEFAULT_PROFILE_ID)
    os.makedirs(path, exist_ok=True)


def create_workspace(name: Any, description: Any = "") -> Dict[str, Any]:
    clean_name = _validated_name(name)
    clean_description = _validated_description(description)
    with locks.write_gate.write(), _LOCK:
        doc = _load_doc()
        if _name_taken(doc, clean_name):
            raise AppError(FILE_EXISTS, "已存在同名工作区", field="name")
        wid = "w-" + uuid.uuid4().hex[:8]
        item = {
            "id": wid,
            "name": clean_name,
            "description": clean_description,
            "default_profile_id": DEFAULT_PROFILE_ID,
            "profiles": [{"id": DEFAULT_PROFILE_ID, "name": DEFAULT_PROFILE_NAME}],
            "data_revision": 0,
            "created_at": _now(),
            "updated_at": _now(),
        }
        doc["workspaces"].append(item)
        _ensure_default_profile(wid)
        _write_doc(doc)
        return _public(item)


def update_workspace(workspace_id: str, name: Any, description: Any = "") -> Dict[str, Any]:
    wid = _validated_id(workspace_id)
    clean_name = _validated_name(name)
    clean_description = _validated_description(description)
    with locks.write_gate.write(), _LOCK:
        doc = _load_doc()
        if _name_taken(doc, clean_name, exclude_id=wid):
            raise AppError(FILE_EXISTS, "已存在同名工作区", field="name")
        row = next((w for w in doc["workspaces"] if w.get("id") == wid), None)
        if row is None:
            if wid != LEGACY_WORKSPACE_ID:
                raise AppError(NOT_FOUND, "工作区不存在")
            row = _legacy_item()
            doc["workspaces"].append(row)
        row["name"] = clean_name
        row["description"] = clean_description
        row["updated_at"] = _now()
        _write_doc(doc)
        return _public(row)


def _now() -> str:
    from datetime import datetime

    return datetime.utcnow().replace(microsecond=0).isoformat() + "Z"
