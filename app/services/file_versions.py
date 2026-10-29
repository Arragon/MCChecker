"""FileResource / FileVersion 不可变身份（M11-01 / INH-2130）。

身份是**派生**的，不新增文件存储、不写第二张表：
* `file_id` = `f-` + sha256(workspace | profile | 文件名) 前 16 位；
* `version_id` = `v-` + sha256(file_id | blob sha256) 前 16 位。

因此同名覆盖后 `file_id` 不变、`version_id` 随内容变化；旧 sha 仍能用
`find_version_by_sha` 找回。v1 的 `/files`、`/archives`、`/records`、`/raw`
完全沿用原路径与文件名，不需要兼容分支。
"""

from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime
from typing import Any, Dict, List, Optional

from ..core import fsutil, storage
from ..core.errors import NOT_FOUND, SOURCE_CHANGED, AppError
from . import documents

LIFECYCLE_FILE = "file_lifecycle.json"
SCHEMA_VERSION = 1


def _workspace_id() -> str:
    return storage.get_active_workspace() or "default"


def _digest(*parts: str) -> str:
    raw = "\0".join(str(part) for part in parts).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:16]


def file_id(profile_id: str, name: str) -> str:
    """稳定 FileResource id：同一 W/P/文件名永远相同（删除后重新上传换 generation）。"""
    row = _live_row(_load_lifecycle(profile_id), name)
    if row and row.get("file_id"):
        return str(row["file_id"])
    return "f-" + _digest(_workspace_id(), profile_id, name, "0")


def _lifecycle_path(profile_id: str) -> str:
    return os.path.join(storage.get_profile_data_dir(profile_id), LIFECYCLE_FILE)


def _load_lifecycle(profile_id: str) -> Dict[str, Any]:
    raw = fsutil.read_json(_lifecycle_path(profile_id), {})
    if not isinstance(raw, dict):
        raw = {}
    files = raw.get("files")
    tombstones = raw.get("tombstones")
    return {
        "schema": SCHEMA_VERSION,
        "files": files if isinstance(files, dict) else {},
        "tombstones": tombstones if isinstance(tombstones, dict) else {},
    }


def _write_lifecycle(profile_id: str, doc: Dict[str, Any]) -> None:
    fsutil.atomic_write_json(_lifecycle_path(profile_id), doc)


def _live_row(doc: Dict[str, Any], name: str) -> Optional[Dict[str, Any]]:
    row = (doc.get("files") or {}).get(name)
    if isinstance(row, dict) and not row.get("deleted"):
        return row
    return None


def begin_upload(profile_id: str, name: str) -> str:
    """上传成功后登记/复活 FileResource；删除过的同名文件换新 file_id。"""
    with storage.use_profile(profile_id), storage.profile_write(profile_id):
        doc = _load_lifecycle(profile_id)
        row = (doc.get("files") or {}).get(name)
        if isinstance(row, dict) and not row.get("deleted"):
            return str(row.get("file_id") or file_id(profile_id, name))
        generation = int(row.get("generation", 0)) + 1 if isinstance(row, dict) else 0
        new_id = "f-" + _digest(_workspace_id(), profile_id, name, str(generation))
        doc.setdefault("files", {})[name] = {
            "file_id": new_id,
            "generation": generation,
            "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "deleted": False,
        }
        _write_lifecycle(profile_id, doc)
        return new_id


def mark_deleted(profile_id: str, name: str, identity: Dict[str, Any]) -> None:
    """文件删除后的 tombstone：冻结引用仍可按 file_id/version_id 判 missing。"""
    with storage.use_profile(profile_id), storage.profile_write(profile_id):
        doc = _load_lifecycle(profile_id)
        row = (doc.get("files") or {}).get(name) or {}
        row.update(
            {
                "file_id": identity["file_id"],
                "generation": int(row.get("generation", 0)),
                "deleted": True,
                "deleted_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            }
        )
        doc.setdefault("files", {})[name] = row
        doc.setdefault("tombstones", {}).setdefault(identity["file_id"], []).append(
            {
                "version_id": identity["version_id"],
                "blob_sha256": identity["blob_sha256"],
                "version_label": identity["version_label"],
                "deleted_at": row["deleted_at"],
            }
        )
        _write_lifecycle(profile_id, doc)


def is_missing(profile_id: str, file_id_value: str, version_id: str) -> bool:
    doc = _load_lifecycle(profile_id)
    for row in (doc.get("tombstones") or {}).get(str(file_id_value), []) or []:
        if isinstance(row, dict) and str(row.get("version_id")) == str(version_id):
            return True
    return False


def _ref_parts(version: str) -> tuple:
    raw = str(version or "current")
    if raw.startswith("archive:"):
        return "archive", raw[len("archive:"):], "archive:" + raw[len("archive:"):]
    if raw.startswith("record:"):
        return "record", raw[len("record:"):], "record:" + raw[len("record:"):]
    return "current", "current", "current"


def identity(profile_id: str, name: str, version: str = "current") -> Dict[str, Any]:
    """解析一个 FileVersion 的身份与元信息（只读）。"""
    kind, label, ref = _ref_parts(version)
    raw = documents.load_raw(profile_id, name, ref)
    fid = file_id(profile_id, name)
    vid = "v-" + _digest(fid, raw.sha256)
    return {
        "workspace_id": _workspace_id(),
        "profile_id": profile_id,
        "file_id": fid,
        "version_id": vid,
        "source_kind": kind,
        "version_label": label,
        "ref": ref,
        "name": name,
        "blob_sha256": raw.sha256,
        "byte_size": raw.size,
        "format": os.path.splitext(name)[1].lstrip(".").lower(),
        "updated_at": raw.updated_at,
    }


def current_identity(profile_id: str, name: str) -> Dict[str, Any]:
    return identity(profile_id, name, "current")


def _archives(profile_id: str, name: str) -> List[str]:
    directory = os.path.join(storage.get_archive_root_dir(profile_id), name)
    return _files_in(directory)


def _records(profile_id: str, name: str) -> List[str]:
    directory = os.path.join(storage.get_records_root_dir(profile_id), name)
    return _files_in(directory)


def _files_in(directory: str) -> List[str]:
    if not os.path.isdir(directory):
        return []
    out = [
        entry
        for entry in os.listdir(directory)
        if not entry.endswith(".diff.json")
        and os.path.isfile(os.path.join(directory, entry))
    ]
    out.sort()
    return out


def versions(profile_id: str, name: str) -> List[Dict[str, Any]]:
    """current + 全部 archive + record 的 FileVersion 身份列表（只读）。"""
    rows = [current_identity(profile_id, name)]
    for filename in _archives(profile_id, name):
        rows.append(identity(profile_id, name, "archive:" + filename))
    for filename in _records(profile_id, name):
        rows.append(identity(profile_id, name, "record:" + filename))
    return rows


def find_version_by_sha(
    profile_id: str, name: str, sha256: str
) -> Optional[Dict[str, Any]]:
    """按内容 sha 找**当前或历史**里仍存在的那一版；找不到返回 None。"""
    wanted = str(sha256 or "").lower()
    if not wanted:
        return None
    for row in versions(profile_id, name):
        if row["blob_sha256"] == wanted:
            return row
    return None


def find_version(profile_id: str, version_id: str) -> Optional[Dict[str, Any]]:
    """按不可变 version_id 找到对应 FileVersion（含 name/ref）；找不到返回 None。"""
    wanted = str(version_id or "").strip()
    if not wanted:
        return None
    for item in list_resources(profile_id):
        for row in versions(profile_id, item["name"]):
            if row["version_id"] == wanted:
                return row
    return None


def list_resources(profile_id: str) -> List[Dict[str, Any]]:
    """当前文件列表 + 每个 FileResource 的 current identity（只读）。"""
    out = []
    for item in documents.list_files(profile_id):
        row = current_identity(profile_id, item["name"])
        row.update(
            {
                "archive_count": item.get("archive_count", 0),
                "record_count": item.get("record_count", 0),
            }
        )
        out.append(row)
    return out
