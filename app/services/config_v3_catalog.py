"""V3 FixedConfig / SoftwareRelease 版本图（M12-01 / INH-2136）。

每个 W/P 一份 `configs_v3/catalog.json`：
* FixedConfig：`code` 在 W/P 内唯一，跨 W/P 独立；
* SoftwareRelease：`version` 在 FixedConfig 内唯一，`predecessor_id` 是**显式**父；
  新建 release 只生成空 D0 草稿（values 全 unset），不继承旧版实际值；
* graph 只返回真实 predecessor 边，不按 UI 位置伪造连线。
"""

from __future__ import annotations

import copy
import os
import re
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from ..core import fsutil, locks, storage
from ..core.errors import FILE_EXISTS, INVALID_INPUT, NOT_FOUND, AppError

SCHEMA_VERSION = 1
_CODE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")


def _root(profile_id: str) -> str:
    return os.path.join(storage.get_profile_data_dir(profile_id), "configs_v3")


def _path(profile_id: str) -> str:
    return os.path.join(_root(profile_id), "catalog.json")


def _empty() -> Dict[str, Any]:
    return {"schema": SCHEMA_VERSION, "configs": {}}


def _load(profile_id: str) -> Dict[str, Any]:
    raw = fsutil.read_json(_path(profile_id), _empty())
    if not isinstance(raw, dict) or not isinstance(raw.get("configs"), dict):
        raise AppError(INVALID_INPUT, "配置目录已损坏", profile=profile_id)
    return raw


def _save(profile_id: str, doc: Dict[str, Any]) -> None:
    fsutil.atomic_write_json(_path(profile_id), doc)


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _clean_code(raw: Any) -> str:
    code = str(raw or "").strip()
    if not _CODE_RE.match(code):
        raise AppError(INVALID_INPUT, "配置标识需为 1-64 位字母/数字/._-", field="code")
    return code


def _clean_name(raw: Any) -> str:
    name = str(raw or "").strip()
    if not name or len(name) > 100 or _CONTROL_RE.search(name):
        raise AppError(INVALID_INPUT, "配置名称需为 1-100 个字符", field="name")
    return name


def _public_config(config: Dict[str, Any], detail: bool = False) -> Dict[str, Any]:
    releases = list((config.get("releases") or {}).values())
    row = {
        "config_id": config["config_id"],
        "code": config["code"],
        "name": config["name"],
        "description": config.get("description", ""),
        "created_at": config["created_at"],
        "updated_at": config.get("updated_at", config["created_at"]),
        "release_count": len(releases),
    }
    if detail:
        row["releases"] = [
            _public_release(rel) for rel in sorted(releases, key=lambda r: r["order"])
        ]
    return row


def _public_release(release: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "release_id": release["release_id"],
        "version": release["version"],
        "order": release["order"],
        "predecessor_id": release.get("predecessor_id"),
        "created_at": release["created_at"],
        "d0": copy.deepcopy(release["d0"]),
        "baseline_ids": list(release.get("baseline_ids") or []),
        "merge_parent_ids": list(release.get("merge_parent_ids") or []),
    }


def list_configs(profile_id: str) -> List[Dict[str, Any]]:
    with storage.use_profile(profile_id):
        doc = _load(profile_id)
    return [_public_config(row) for row in doc["configs"].values()]


def get_config(profile_id: str, config_id: str) -> Dict[str, Any]:
    with storage.use_profile(profile_id):
        doc = _load(profile_id)
    row = doc["configs"].get(str(config_id))
    if row is None:
        raise AppError(NOT_FOUND, "配置不存在", config_id=config_id)
    return _public_config(row, detail=True)


def create_config(profile_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    code = _clean_code(payload.get("code"))
    name = _clean_name(payload.get("name"))
    description = str(payload.get("description") or "").strip()
    with locks.write_gate.write(), storage.use_profile(profile_id), storage.profile_write(profile_id):
        doc = _load(profile_id)
        if any(str(row.get("code")) == code for row in doc["configs"].values()):
            raise AppError(FILE_EXISTS, "同一工作区/机型已存在相同配置标识", field="code")
        cid = "cfg-" + uuid.uuid4().hex[:12]
        now = _now()
        doc["configs"][cid] = {
            "config_id": cid,
            "code": code,
            "name": name,
            "description": description,
            "created_at": now,
            "updated_at": now,
            "releases": {},
        }
        _save(profile_id, doc)
        return _public_config(doc["configs"][cid], detail=True)


def list_releases(profile_id: str, config_id: str) -> List[Dict[str, Any]]:
    config = get_config(profile_id, config_id)
    return config["releases"]


def create_release(profile_id: str, config_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    version = str(payload.get("version") or "").strip()
    if not version or len(version) > 64 or _CONTROL_RE.search(version):
        raise AppError(INVALID_INPUT, "版本号需为 1-64 个字符", field="version")
    predecessor = str(payload.get("predecessor_id") or "").strip() or None
    order_raw = payload.get("order")
    with locks.write_gate.write(), storage.use_profile(profile_id), storage.profile_write(profile_id):
        doc = _load(profile_id)
        config = doc["configs"].get(str(config_id))
        if config is None:
            raise AppError(NOT_FOUND, "配置不存在", config_id=config_id)
        releases = config.setdefault("releases", {})
        if any(str(row.get("version")) == version for row in releases.values()):
            raise AppError(FILE_EXISTS, "同一配置已存在相同版本号", field="version")
        if predecessor is not None and predecessor not in releases:
            raise AppError(INVALID_INPUT, "前驱版本不存在或不属于该配置", field="predecessor_id")
        if order_raw is None:
            order = max((int(row.get("order", 0)) for row in releases.values()), default=0) + 1
        else:
            if isinstance(order_raw, bool) or not isinstance(order_raw, int) or order_raw < 1:
                raise AppError(INVALID_INPUT, "order 必须是正整数", field="order")
            order = order_raw
        if any(int(row.get("order", 0)) == order for row in releases.values()):
            raise AppError(FILE_EXISTS, "同一配置已存在相同 order", field="order")
        rid = "rel-" + uuid.uuid4().hex[:12]
        now = _now()
        releases[rid] = {
            "release_id": rid,
            "version": version,
            "order": order,
            "predecessor_id": predecessor,
            "created_at": now,
            "d0": {"values": {}, "frozen": False, "revision": 0},
            "baseline_ids": [],
            "merge_parent_ids": [],
        }
        config["updated_at"] = now
        _save(profile_id, doc)
        return _public_release(releases[rid])


def graph(profile_id: str, config_id: str) -> Dict[str, Any]:
    config = get_config(profile_id, config_id)
    nodes = [
        {
            "release_id": row["release_id"],
            "version": row["version"],
            "order": row["order"],
            "predecessor_id": row["predecessor_id"],
            "d0_frozen": bool(row["d0"].get("frozen")),
        }
        for row in config["releases"]
    ]
    edges = [
        {"from": row["predecessor_id"], "to": row["release_id"], "kind": "predecessor"}
        for row in config["releases"]
        if row["predecessor_id"]
    ]
    return {"config_id": config_id, "nodes": nodes, "edges": edges}
