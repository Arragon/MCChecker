"""历史版本与修改记录列表（§7.4、§6.6；T-12 / M2-07）。

只读：列出当前版本 + 归档（按修改时间倒序）、修改记录（倒序），并给出相邻版本的
**差异摘要**。摘要缓存写在 `cache/diff_v2/`（派生数据），不再产生旧的
`.diff.json`。逐行 `items` 属于 M2-08 的 `compare_documents`，修改记录内容的真正
拉取属于 M3-09。

计数键用 `loc`，同名兄弟不会互相覆盖（C11）；绑定标注沿用 v1 的
「文件名 / 标签路径」形态，去掉首段后与 `value_path` 对齐。
"""

from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from ..core import differ, fsutil, parser, storage
from ..core.errors import NOT_FOUND, AppError

CACHE_DIRNAME = "diff_v2"

#: (显示名, 原文字节)
Side = Tuple[str, bytes]


def archives(profile_id: str, name: str) -> Dict[str, Any]:
    """`GET /p/{pid}/files/{name}/archives`：当前版本 + 归档，每个归档与紧邻的较新版本比较。"""
    clean = fsutil.validate_filename(name)
    with storage.use_profile(profile_id):
        current_path = storage.get_config_path(clean)
        if not os.path.isfile(current_path):
            raise AppError(NOT_FOUND, "文件不存在", name=clean)
        current_bytes = _read(current_path)
        bindings = storage.load_bindings()
        versions = _list_versions(os.path.join(storage.get_archive_root_dir(profile_id), clean))

        items: List[Dict[str, Any]] = []
        for index, entry in enumerate(versions):
            older: Side = (entry["filename"], _read(entry["path"]))
            newer: Side = (clean, current_bytes) if index == 0 else (
                versions[index - 1]["filename"],
                _read(versions[index - 1]["path"]),
            )
            items.append(
                {
                    "filename": entry["filename"],
                    "date": entry["date"],
                    "size": entry["size"],
                    "compared_to": newer[0],
                    "diff": _summary(
                        profile_id,
                        older,
                        newer,
                        bindings,
                        clean,
                        legacy=storage.load_archive_diff(clean, entry["filename"]),
                    ),
                }
            )

        stat = os.stat(current_path)
        return {
            "current": {
                "name": clean,
                "updated_at": _fmt_time(stat.st_mtime),
                "size": stat.st_size,
                "sha256": _sha256(current_bytes),
            },
            "items": items,
        }


def records(profile_id: str, name: str) -> Dict[str, Any]:
    """`GET /p/{pid}/files/{name}/records`：修改记录倒序，每条与上一条比较。"""
    clean = fsutil.validate_filename(name)
    with storage.use_profile(profile_id):
        if not os.path.isfile(storage.get_config_path(clean)):
            raise AppError(NOT_FOUND, "文件不存在", name=clean)
        bindings = storage.load_bindings()
        # 不用 storage.get_record_dir()：它会 makedirs，读列表不该在数据目录里留下新目录。
        versions = _list_versions(
            os.path.join(storage.get_records_root_dir(profile_id), clean)
        )
        items: List[Dict[str, Any]] = []
        for index, entry in enumerate(versions):
            newer: Side = (entry["filename"], _read(entry["path"]))
            prev = versions[index + 1] if index + 1 < len(versions) else None
            older: Optional[Side] = (prev["filename"], _read(prev["path"])) if prev else None
            items.append(
                {
                    "filename": entry["filename"],
                    "date": entry["date"],
                    "size": entry["size"],
                    "diff_prev": None
                    if older is None
                    else _summary(profile_id, older, newer, bindings, clean),
                }
            )
        mapping = {
            str(item.get("name") or ""): item for item in storage.load_config_mapping()
        }
        return {
            "record_url": str((mapping.get(clean) or {}).get("record_url") or ""),
            "items": items,
        }


# ===================== 差异摘要 =====================


def _summary(
    profile_id: str,
    base: Side,
    target: Side,
    bindings: List[Dict[str, Any]],
    logical_name: str,
    legacy: Optional[Dict[str, Any]] = None,
) -> Optional[Dict[str, Any]]:
    """相邻两个版本的差异摘要，优先读 `cache/diff_v2/`（§6.6）。

    读不到缓存就计算并写入；只有**现在也算不出来**（某一侧解析失败）时，才退回旧的
    `.diff.json` 并标 `structural: false`——那是 v1 口径的「旧统计」。

    `logical_name` 是所属配置文件名：归档/记录的文件名各不相同，但绑定表记的是逻辑
    文件名（§6.6 第 3 步）。
    """
    has_changes = base[1] != target[1]
    key = _cache_key(base[1], target[1], bindings)
    cached = _cache_read(profile_id, key)
    if cached is not None:
        return cached

    if not has_changes:
        payload = {
            "has_changes": False,
            "summary": {"added": 0, "removed": 0, "modified": 0, "bound": 0},
            "structural": True,
        }
        _cache_write(profile_id, key, payload)
        return payload

    base_nodes = _nodes(base[0], base[1])
    target_nodes = _nodes(target[0], target[1])
    if base_nodes is None or target_nodes is None:
        return _legacy_diff(legacy, has_changes)

    summary = differ.summarize_documents(
        base_nodes, target_nodes, logical_name, logical_name, bindings
    )
    payload = {
        "has_changes": has_changes,
        "summary": summary["summary"],
        "structural": True,
    }
    _cache_write(profile_id, key, payload)
    return payload


def _legacy_diff(legacy: Optional[Dict[str, Any]], has_changes: bool) -> Optional[Dict[str, Any]]:
    """旧 `.diff.json` 的键名是 `*_count`，且没有可确认的绑定版本 → 只能当旧统计显示。

    连旧缓存也没有时返回 `None`（§7.4 允许 `diff: null`）——算不出来就说算不出来，
    不能拿一串 0 冒充「没有差异」。
    """
    if not isinstance(legacy, dict):
        return None
    old = legacy.get("summary") or {}
    return {
        "has_changes": bool(legacy.get("has_changes", has_changes)),
        "summary": {
            "added": int(old.get("added_count", 0) or 0),
            "removed": int(old.get("removed_count", 0) or 0),
            "modified": int(old.get("modified_count", 0) or 0),
            "bound": int(old.get("bound_count", 0) or 0),
        },
        "structural": False,
    }


def _nodes(name: str, content: bytes) -> Optional[Dict[str, List[Any]]]:
    try:
        return parser.to_compact(content, name)["nodes"]
    except Exception:
        return None


def _cache_key(base: bytes, target: bytes, bindings: List[Dict[str, Any]]) -> str:
    bindings_hash = hashlib.sha1(
        json.dumps(bindings, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()
    return hashlib.sha1(
        (_sha256(base) + _sha256(target) + str(differ.DIFFER_VERSION) + bindings_hash).encode("utf-8")
    ).hexdigest()


def _cache_path(profile_id: str, key: str) -> str:
    return os.path.join(storage.get_cache_root_dir(profile_id), CACHE_DIRNAME, key + ".json")


def _cache_read(profile_id: str, key: str) -> Optional[Dict[str, Any]]:
    path = _cache_path(profile_id, key)
    if not os.path.exists(path):
        return None
    try:
        with open(path, "rb") as fh:
            payload = json.loads(fh.read().decode("utf-8"))
    except (OSError, ValueError):
        # 派生数据：坏了就重算，不影响权威内容。
        return None
    if not isinstance(payload, dict) or "summary" not in payload:
        return None
    return payload


def _cache_write(profile_id: str, key: str, payload: Dict[str, Any]) -> None:
    try:
        fsutil.atomic_write_bytes(
            _cache_path(profile_id, key),
            json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        )
    except OSError:
        pass


# ===================== 列目录（只读，不创建目录）=====================


def _list_versions(directory: str) -> List[Dict[str, Any]]:
    """按修改时间倒序列出目录里的版本文件。

    只用 `get_archive_root_dir` / `get_record_dir` 拼路径再判断 `isdir`：`storage.get_archive_dir()`
    会 `makedirs`，读一次历史列表不该在数据目录里留下新目录或新归档（仓库自身口径，执法见
    `tests/test_readonly_paths.py`；规范 §5.4 讲的是 `core/fsutil.py` 的写入可靠性，不是这一条，别引它）。
    """
    if not os.path.isdir(directory):
        return []
    entries: List[Dict[str, Any]] = []
    for name in os.listdir(directory):
        if name.endswith(".diff.json"):
            continue
        path = os.path.join(directory, name)
        if not os.path.isfile(path):
            continue
        mtime = os.path.getmtime(path)
        entries.append(
            {
                "filename": name,
                "path": path,
                "size": os.path.getsize(path),
                "date": datetime.fromtimestamp(mtime).strftime("%Y-%m-%d %H:%M:%S"),
                "mtime": mtime,
            }
        )
    entries.sort(key=lambda item: item["mtime"], reverse=True)
    return entries


def _read(path: str) -> bytes:
    with open(path, "rb") as fh:
        return fh.read()


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _fmt_time(timestamp: float) -> str:
    return datetime.fromtimestamp(timestamp).strftime("%Y-%m-%d %H:%M:%S")
