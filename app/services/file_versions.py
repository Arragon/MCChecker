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
import os
from typing import Any, Dict, List, Optional

from ..core import storage
from . import documents


def _workspace_id() -> str:
    return storage.get_active_workspace() or "default"


def _digest(*parts: str) -> str:
    raw = "\0".join(str(part) for part in parts).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:16]


def file_id(profile_id: str, name: str) -> str:
    """稳定 FileResource id：同一 W/P/文件名永远相同。"""
    return "f-" + _digest(_workspace_id(), profile_id, name)


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
