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
from ..core.errors import FILE_EXISTS, INVALID_INPUT, LIFECYCLE_BLOCKED, NOT_FOUND, AppError

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
        "definition_revision": int(release.get("definition_revision", 0)),
        "default_revision_id": release.get("default_revision_id"),
        "default_frozen_at": release.get("default_frozen_at"),
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
            "definition_revision": int(_definition(config)["revision"]),
            "binding_set": {"revision": 1, "frozen": False, "bindings": {}},
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


# ===================== M12-02：参数身份/定义修订/绑定快照 =====================

MAX_DIRECTORY_DEPTH = 128
_DIR_SEGMENT_RE = re.compile(r"^[^\x00-\x1f\x7f/\\]{1,100}$")


def _clean_directory(raw: Any) -> List[str]:
    if raw is None:
        raw = []
    if not isinstance(raw, list):
        raise AppError(INVALID_INPUT, "目录必须是字符串数组", field="directory")
    if len(raw) > MAX_DIRECTORY_DEPTH:
        raise AppError(INVALID_INPUT, "目录层级不能超过 %d 层" % MAX_DIRECTORY_DEPTH, field="directory")
    out = []
    for part in raw:
        text = str(part or "").strip()
        if not _DIR_SEGMENT_RE.match(text):
            raise AppError(INVALID_INPUT, "目录段需为 1-100 个字符且不能含路径分隔符", field="directory")
        out.append(text)
    return out


def _clean_parameter_text(raw: Any, field: str, required: bool = False, limit: int = 200) -> str:
    text = str(raw or "").strip()
    if required and not text:
        raise AppError(INVALID_INPUT, "%s 不能为空" % field, field=field)
    if len(text) > limit or _CONTROL_RE.search(text):
        raise AppError(INVALID_INPUT, "%s 超过长度或含控制字符" % field, field=field)
    return text


def _definition(config: Dict[str, Any]) -> Dict[str, Any]:
    definition = config.setdefault(
        "definition", {"revision": 0, "current": {"revision": 0, "parameters": {}}, "history": []}
    )
    definition.setdefault("current", {"revision": 0, "parameters": {}})
    definition.setdefault("history", [])
    return definition


def _snapshot_definition(config: Dict[str, Any]) -> None:
    definition = _definition(config)
    current = copy.deepcopy(definition["current"])
    definition["revision"] = int(definition.get("revision", 0)) + 1
    current["revision"] = definition["revision"]
    current["created_at"] = _now()
    definition["current"] = current
    definition["history"].append(copy.deepcopy(current))


def _find_parameter(config: Dict[str, Any], parameter_id: str) -> Optional[Dict[str, Any]]:
    return (_definition(config)["current"].get("parameters") or {}).get(str(parameter_id))


def list_parameters(
    profile_id: str, config_id: str, offset: int = 0, limit: Optional[int] = None
) -> Dict[str, Any]:
    with storage.use_profile(profile_id):
        doc = _load(profile_id)
    config = doc["configs"].get(config_id)
    if config is None:
        raise AppError(NOT_FOUND, "配置不存在", config_id=config_id)
    definition = _definition(config)
    rows = list(definition["current"]["parameters"].values())
    start = max(0, int(offset))
    size = None if limit is None else max(1, min(int(limit), 500))
    page = rows[start:] if size is None else rows[start : start + size]
    return {
        "revision": definition["revision"],
        "total": len(rows),
        "offset": start,
        "limit": size,
        "items": page,
    }


def create_parameter(profile_id: str, config_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    name = _clean_parameter_text(payload.get("name"), "name", required=True, limit=200)
    description = _clean_parameter_text(payload.get("description", ""), "description", limit=1000)
    unit = _clean_parameter_text(payload.get("unit", ""), "unit", limit=50)
    type_name = _clean_parameter_text(payload.get("type", ""), "type", limit=50)
    directory = _clean_directory(payload.get("directory"))
    attrs = payload.get("attrs") or {}
    if not isinstance(attrs, dict):
        raise AppError(INVALID_INPUT, "attrs 必须是对象", field="attrs")
    source = payload.get("source") or {}
    if not isinstance(source, dict):
        raise AppError(INVALID_INPUT, "source 必须是对象", field="source")
    with locks.write_gate.write(), storage.use_profile(profile_id), storage.profile_write(profile_id):
        doc = _load(profile_id)
        config = doc["configs"].get(config_id)
        if config is None:
            raise AppError(NOT_FOUND, "配置不存在", config_id=config_id)
        parameters = _definition(config)["current"].setdefault("parameters", {})
        if any(row.get("name") == name and row.get("directory") == directory for row in parameters.values()):
            raise AppError(FILE_EXISTS, "同一目录下已存在同名参数", field="name")
        pid = "p-" + uuid.uuid4().hex[:12]
        row = {
            "parameter_id": pid,
            "name": name,
            "description": description,
            "directory": directory,
            "type": type_name,
            "unit": unit,
            "attrs": copy.deepcopy(attrs),
            "source": copy.deepcopy(source),
            "created_at": _now(),
        }
        parameters[pid] = row
        _snapshot_definition(config)
        config["updated_at"] = _now()
        _save(profile_id, doc)
        return copy.deepcopy(_definition(config)["current"]["parameters"][pid])


def update_parameter(
    profile_id: str, config_id: str, parameter_id: str, payload: Dict[str, Any]
) -> Dict[str, Any]:
    with locks.write_gate.write(), storage.use_profile(profile_id), storage.profile_write(profile_id):
        doc = _load(profile_id)
        config = doc["configs"].get(config_id)
        if config is None:
            raise AppError(NOT_FOUND, "配置不存在", config_id=config_id)
        current = _definition(config)["current"].setdefault("parameters", {})
        row = current.get(str(parameter_id))
        if row is None:
            raise AppError(NOT_FOUND, "参数不存在", parameter_id=parameter_id)
        if "name" in payload:
            row["name"] = _clean_parameter_text(payload.get("name"), "name", required=True, limit=200)
        if "description" in payload:
            row["description"] = _clean_parameter_text(payload.get("description"), "description", limit=1000)
        if "unit" in payload:
            row["unit"] = _clean_parameter_text(payload.get("unit"), "unit", limit=50)
        if "type" in payload:
            row["type"] = _clean_parameter_text(payload.get("type"), "type", limit=50)
        if "directory" in payload:
            row["directory"] = _clean_directory(payload.get("directory"))
        if "attrs" in payload:
            if not isinstance(payload.get("attrs"), dict):
                raise AppError(INVALID_INPUT, "attrs 必须是对象", field="attrs")
            row["attrs"] = copy.deepcopy(payload["attrs"])
        duplicates = [
            other
            for other in current.values()
            if other["parameter_id"] != row["parameter_id"]
            and other.get("name") == row.get("name")
            and other.get("directory") == row.get("directory")
        ]
        if duplicates:
            raise AppError(FILE_EXISTS, "同一目录下已存在同名参数", field="name")
        _snapshot_definition(config)
        config["updated_at"] = _now()
        _save(profile_id, doc)
        return copy.deepcopy(_definition(config)["current"]["parameters"][row["parameter_id"]])


def definition_revisions(profile_id: str, config_id: str) -> List[Dict[str, Any]]:
    with storage.use_profile(profile_id):
        doc = _load(profile_id)
    config = doc["configs"].get(config_id)
    if config is None:
        raise AppError(NOT_FOUND, "配置不存在", config_id=config_id)
    definition = _definition(config)
    return [
        {
            "revision": row["revision"],
            "created_at": row.get("created_at"),
            "parameter_count": len(row.get("parameters") or {}),
        }
        for row in definition["history"]
    ]


def definition_revision(profile_id: str, config_id: str, revision: int) -> Dict[str, Any]:
    with storage.use_profile(profile_id):
        doc = _load(profile_id)
    config = doc["configs"].get(config_id)
    if config is None:
        raise AppError(NOT_FOUND, "配置不存在", config_id=config_id)
    for row in _definition(config)["history"]:
        if int(row.get("revision", -1)) == int(revision):
            return copy.deepcopy(row)
    raise AppError(NOT_FOUND, "定义修订不存在", revision=revision)


def _binding_set(release: Dict[str, Any]) -> Dict[str, Any]:
    data = release.setdefault(
        "binding_set", {"revision": 1, "frozen": False, "bindings": {}}
    )
    data.setdefault("bindings", {})
    data.setdefault("revision", 1)
    data.setdefault("frozen", False)
    return data


def bindings_for(profile_id: str, config_id: str, release_id: str) -> Dict[str, Any]:
    with storage.use_profile(profile_id):
        doc = _load(profile_id)
    config = doc["configs"].get(config_id)
    if config is None:
        raise AppError(NOT_FOUND, "配置不存在", config_id=config_id)
    release = (config.get("releases") or {}).get(release_id)
    if release is None:
        raise AppError(NOT_FOUND, "软件版本不存在", release_id=release_id)
    return copy.deepcopy(_binding_set(release))


def create_binding(
    profile_id: str, config_id: str, release_id: str, payload: Dict[str, Any]
) -> Dict[str, Any]:
    parameter_id = str(payload.get("parameter_id") or "").strip()
    locator = _clean_parameter_text(payload.get("locator", ""), "locator", required=True, limit=2000)
    file_id_value = _clean_parameter_text(payload.get("file_id", ""), "file_id", limit=100)
    version_id = _clean_parameter_text(payload.get("version_id", ""), "version_id", limit=100)
    if payload.get("confirmed") is not True:
        raise AppError(INVALID_INPUT, "绑定候选必须显式确认", field="confirmed")
    with locks.write_gate.write(), storage.use_profile(profile_id), storage.profile_write(profile_id):
        doc = _load(profile_id)
        config = doc["configs"].get(config_id)
        if config is None:
            raise AppError(NOT_FOUND, "配置不存在", config_id=config_id)
        release = (config.get("releases") or {}).get(release_id)
        if release is None:
            raise AppError(NOT_FOUND, "软件版本不存在", release_id=release_id)
        binding_set = _binding_set(release)
        if binding_set["frozen"] or release.get("d0", {}).get("frozen"):
            raise AppError(FILE_EXISTS, "绑定集已冻结", field="bindings")
        if _find_parameter(config, parameter_id) is None:
            raise AppError(NOT_FOUND, "参数不存在", parameter_id=parameter_id)
        if any(
            row.get("parameter_id") == parameter_id or row.get("locator") == locator
            for row in binding_set["bindings"].values()
        ):
            raise AppError(FILE_EXISTS, "绑定位置或参数已存在", field="locator")
        binding_id = "b-" + uuid.uuid4().hex[:12]
        binding_set["bindings"][binding_id] = {
            "binding_id": binding_id,
            "parameter_id": parameter_id,
            "locator": locator,
            "file_id": file_id_value,
            "version_id": version_id,
            "confirmed": True,
            "created_at": _now(),
        }
        binding_set["revision"] = int(binding_set["revision"]) + 1
        config["updated_at"] = _now()
        _save(profile_id, doc)
        return copy.deepcopy(binding_set)


def freeze_bindings(profile_id: str, config_id: str, release_id: str) -> Dict[str, Any]:
    """冻结后的 binding_set 不可修改（M12-03 发布时调用）。"""
    with locks.write_gate.write(), storage.use_profile(profile_id), storage.profile_write(profile_id):
        doc = _load(profile_id)
        config = doc["configs"].get(config_id)
        release = (config or {}).get("releases", {}).get(release_id)
        if release is None:
            raise AppError(NOT_FOUND, "软件版本不存在", release_id=release_id)
        binding_set = _binding_set(release)
        binding_set["frozen"] = True
        _save(profile_id, doc)
        return copy.deepcopy(binding_set)


def commit_extraction(
    profile_id: str,
    config_id: str,
    rows: List[Dict[str, Any]],
    release_id: Optional[str] = None,
    include_existing_missing_bindings: bool = False,
    bindings: Optional[List[Dict[str, Any]]] = None,
    add_members: bool = False,
    precheck: Optional[Any] = None,
) -> Dict[str, Any]:
    """一次事务提交提取计划（M13-06 / INH-2148）。

    参数、定义修订、来源索引、可选成员与补绑全部在同一个 catalog doc 上完成；
    任何异常发生在 `_save` 之前都不会留下半批数据。已有名称/层级/释义/值/绑定不改。
    """
    with locks.write_gate.write(), storage.use_profile(profile_id), storage.profile_write(profile_id):
        if precheck is not None:
            precheck()
        doc = _load(profile_id)
        config = doc["configs"].get(config_id)
        if config is None:
            raise AppError(NOT_FOUND, "配置不存在", config_id=config_id)
        release = None
        if release_id:
            release = (config.get("releases") or {}).get(str(release_id))
            if release is None:
                raise AppError(NOT_FOUND, "软件版本不存在", release_id=release_id)
            if release.get("d0", {}).get("frozen"):
                raise AppError(LIFECYCLE_BLOCKED, "目标软件版本的 D0 已冻结，拒绝写入")
        parameters = _definition(config)["current"].setdefault("parameters", {})
        keys = {(row.get("name"), tuple(row.get("directory") or [])) for row in parameters.values()}
        locators = set()
        for row in parameters.values():
            source = row.get("source") or {}
            if source.get("file_id") and source.get("canonical_locator"):
                locators.add((source["file_id"], source["canonical_locator"]))
        created: List[Dict[str, Any]] = []
        skipped: List[Dict[str, Any]] = []
        for raw in rows:
            name = _clean_parameter_text(raw.get("name"), "name", required=True, limit=200)
            directory = _clean_directory(raw.get("directory"))
            key = (name, tuple(directory))
            source = raw.get("source") or {}
            locator_key = (source.get("file_id"), source.get("canonical_locator"))
            if key in keys or (locator_key[0] and locator_key[1] and locator_key in locators):
                skipped.append({"name": name, "directory": directory, "reason": "already_exists"})
                continue
            pid = "p-" + uuid.uuid4().hex[:12]
            row = {
                "parameter_id": pid,
                "name": name,
                "description": "",
                "directory": directory,
                "type": _clean_parameter_text(raw.get("type", ""), "type", limit=50),
                "unit": "",
                "attrs": {},
                "source": copy.deepcopy(source),
                "created_at": _now(),
            }
            parameters[pid] = row
            keys.add(key)
            if locator_key[0] and locator_key[1]:
                locators.add(locator_key)
            created.append({"parameter_id": pid, "name": name, "directory": directory})
        bound = 0
        binding_conflicts = 0
        if release is not None and include_existing_missing_bindings:
            binding_set = _binding_set(release)
            if binding_set.get("frozen"):
                raise AppError(LIFECYCLE_BLOCKED, "目标软件版本的绑定集已冻结")
            occupied_params = {row.get("parameter_id") for row in binding_set["bindings"].values()}
            occupied_locators = {row.get("locator") for row in binding_set["bindings"].values()}
            for spec in bindings or []:
                pid = str(spec.get("parameter_id") or "")
                locator = str(spec.get("locator") or "")
                if not pid or not locator:
                    continue
                if pid in occupied_params or locator in occupied_locators:
                    binding_conflicts += 1
                    continue
                binding_id = "b-" + uuid.uuid4().hex[:12]
                binding_set["bindings"][binding_id] = {
                    "binding_id": binding_id,
                    "parameter_id": pid,
                    "locator": locator,
                    "file_id": str(spec.get("file_id") or ""),
                    "version_id": str(spec.get("version_id") or ""),
                    "confirmed": True,
                    "created_at": _now(),
                }
                occupied_params.add(pid)
                occupied_locators.add(locator)
                bound += 1
            if bound:
                binding_set["revision"] = int(binding_set.get("revision", 1)) + 1
        members = 0
        if release is not None and add_members and created:
            d0 = release.setdefault("d0", {"values": {}, "frozen": False, "revision": 0})
            values = d0.setdefault("values", {})
            for row in created:
                if row["parameter_id"] not in values:
                    values[row["parameter_id"]] = {
                        "membership": "included", "enabled": True, "value_state": "unset",
                    }
                    members += 1
            if members:
                d0["revision"] = int(d0.get("revision", 0)) + 1
        revision = int(_definition(config)["revision"])
        if created:
            _snapshot_definition(config)
            revision = int(_definition(config)["revision"])
            if release is not None and add_members:
                release["definition_revision"] = revision
        if created or bound or members:
            config["updated_at"] = _now()
        _save(profile_id, doc)
        return {
            "created": len(created),
            "skipped": 0,
            "unchanged": len(rows) - len(created),
            "bound": bound,
            "binding_conflicts": binding_conflicts,
            "members": members,
            "revision": revision,
            "created_items": created,
            "skipped_items": skipped,
        }
