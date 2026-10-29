"""V3 参数差异与历程（M13-03 / INH-2145）。

按 stable parameter_id 比较两个真实 revision 快照；D0 previous=前一个发布版
D0，Bn/rn previous=各自真实 parent。release_pair 比较与谱系 parent 分开：
调用方显式给 against 才做跨版对比，不改写 previous。
"""

from __future__ import annotations

import copy
from typing import Any, Dict, List, Optional, Tuple

from ..core import storage
from ..core.errors import NOT_FOUND, AppError
from . import config_v3_catalog


def _definition_by_rev(config: Dict[str, Any], revision: int) -> Dict[str, Any]:
    for snap in (config.get("definition", {}).get("history") or []):
        if int(snap.get("revision", -1)) == int(revision):
            return snap
    return {"revision": int(revision), "parameters": {}}


def _state(values: Dict[str, Any], pid: str, revision_id: str) -> Dict[str, Any]:
    row = copy.deepcopy(values.get(pid) or {})
    membership = row.get("membership", "not_introduced")
    enabled = bool(row.get("enabled", True))
    state = row.get("value_state", "unset")
    status = "ok"
    if membership == "removed":
        status = "removed"
    elif membership == "not_introduced":
        status = "not_introduced"
    elif not enabled:
        status = "disabled"
    elif state in ("unset", "null"):
        status = state
    out = {
        "revision_id": revision_id,
        "membership": membership,
        "enabled": enabled,
        "value_state": state,
        "status": status,
    }
    if state == "present" and "value" in row:
        out["value"] = row["value"]
    return out


def resolve(config: Dict[str, Any], revision_id: str) -> Dict[str, Any]:
    rid = str(revision_id or "")
    if rid.startswith("d0-"):
        release_id = rid[3:]
        release = (config.get("releases") or {}).get(release_id)
        if release is None:
            raise AppError(NOT_FOUND, "D0 修订不存在", revision_id=rid)
        if not release.get("default_revision_id"):
            raise AppError(NOT_FOUND, "D0 尚未冻结", revision_id=rid)
        d0 = release.get("d0") or {}
        prev = release.get("predecessor_id")
        return {
            "revision_id": rid,
            "kind": "default",
            "label": "D0 " + str(release.get("version")),
            "definition_revision": int(release.get("definition_revision", 0)),
            "values": copy.deepcopy(d0.get("frozen_snapshot") or {}),
            "previous_id": ("d0-" + prev) if prev else None,
            "at": release.get("default_frozen_at") or release.get("created_at"),
        }
    branch = config.setdefault("main_branch", {"head_id": None, "next_seq": 1, "baselines": {}})
    row = (branch.get("baselines") or {}).get(rid)
    if row is not None:
        return {
            "revision_id": rid,
            "kind": "baseline",
            "label": row.get("label"),
            "definition_revision": int(row.get("definition_revision", 0)),
            "values": copy.deepcopy(row.get("values") or {}),
            "previous_id": row.get("parent_id"),
            "at": row.get("published_at"),
        }
    for machine in (config.get("machines") or {}).values():
        row = (machine.get("revisions") or {}).get(rid)
        if row is not None:
            return {
                "revision_id": rid,
                "kind": "machine",
                "label": machine.get("machine_no") + "·" + row.get("label", ""),
                "definition_revision": int(row.get("definition_revision", 0)),
                "values": copy.deepcopy(row.get("values") or {}),
                "previous_id": row.get("parent_id"),
                "at": row.get("published_at"),
            }
    raise AppError(NOT_FOUND, "修订不存在", revision_id=rid)


def _snapshot(config: Dict[str, Any], revision: Dict[str, Any]) -> Dict[str, Any]:
    definition = _definition_by_rev(config, revision["definition_revision"])
    params = definition.get("parameters") or {}
    return {
        "revision": revision,
        "definition": definition,
        "params": params,
        "values": revision["values"],
    }


def diff(
    profile_id: str, config_id: str, revision_id: str, against: str = ""
) -> Dict[str, Any]:
    with storage.use_profile(profile_id):
        doc = config_v3_catalog._load(profile_id)
    config = doc["configs"].get(config_id)
    if config is None:
        raise AppError(NOT_FOUND, "配置不存在", config_id=config_id)
    right = resolve(config, revision_id)
    other_id = against or right.get("previous_id")
    left = resolve(config, other_id) if other_id else None
    rows = []
    left_snap = _snapshot(config, left) if left else {"params": {}, "values": {}}
    right_snap = _snapshot(config, right)
    for pid in sorted(set(left_snap["params"]) | set(right_snap["params"])):
        lp = left_snap["params"].get(pid) or {}
        rp = right_snap["params"].get(pid) or {}
        lv = _state(left_snap["values"], pid, left["revision_id"]) if left else None
        rv = _state(right_snap["values"], pid, right["revision_id"])
        categories: List[str] = []
        if left is None or pid not in left_snap["params"]:
            categories.append("added")
        elif pid not in right_snap["params"]:
            categories.append("removed")
        if left is not None and pid in left_snap["params"] and pid in right_snap["params"]:
            if lp.get("name") != rp.get("name"):
                categories.append("renamed")
            if list(lp.get("directory") or []) != list(rp.get("directory") or []):
                categories.append("hierarchy_changed")
            if str(lp.get("type") or "") != str(rp.get("type") or ""):
                categories.append("type_changed")
            if (
                str(lp.get("unit") or "") != str(rp.get("unit") or "")
                or (lp.get("attrs") or {}) != (rp.get("attrs") or {})
                or (lp.get("source") or {}) != (rp.get("source") or {})
            ):
                categories.append("attributes_changed")
            if lv.get("membership") != rv.get("membership"):
                categories.append("intent_changed")
                if rv.get("membership") == "removed":
                    categories.append("removed")
            if lv.get("enabled") != rv.get("enabled"):
                categories.append("enabled_changed")
            if lv.get("status") != rv.get("status") or lv.get("value") != rv.get("value"):
                categories.append("value_changed")
        if categories:
            rows.append(
                {
                    "parameter_id": pid,
                    "categories": categories,
                    "name_before": lp.get("name"),
                    "name_after": rp.get("name"),
                    "before": lv,
                    "after": rv,
                    "definition_before": copy.deepcopy(lp),
                    "definition_after": copy.deepcopy(rp),
                }
            )
    return {
        "config_id": config_id,
        "revision_id": revision_id,
        "against": other_id,
        "previous_id": right.get("previous_id"),
        "release_pair": bool(against),
        "rows": rows,
    }


def parameter_history(profile_id: str, config_id: str, parameter_id: str) -> Dict[str, Any]:
    with storage.use_profile(profile_id):
        doc = config_v3_catalog._load(profile_id)
    config = doc["configs"].get(config_id)
    if config is None:
        raise AppError(NOT_FOUND, "配置不存在", config_id=config_id)
    revisions: List[Dict[str, Any]] = []
    for release in (config.get("releases") or {}).values():
        if release.get("default_revision_id"):
            revisions.append(resolve(config, release["default_revision_id"]))
    for row in ((config.get("main_branch") or {}).get("baselines") or {}).values():
        revisions.append(resolve(config, row["baseline_id"]))
    for machine in (config.get("machines") or {}).values():
        for row in (machine.get("revisions") or {}).values():
            revisions.append(resolve(config, row["revision_id"]))
    revisions.sort(key=lambda r: (str(r.get("at") or ""), r["revision_id"]))
    rows = []
    for rev in revisions:
        snap = _snapshot(config, rev)
        param = snap["params"].get(parameter_id)
        value = _state(snap["values"], parameter_id, rev["revision_id"])
        if param is None and value["membership"] == "not_introduced":
            continue
        rows.append(
            {
                "revision_id": rev["revision_id"],
                "kind": rev["kind"],
                "label": rev["label"],
                "at": rev.get("at"),
                "previous_id": rev.get("previous_id"),
                "definition": copy.deepcopy(param),
                "value": value,
            }
        )
    if not rows:
        raise AppError(NOT_FOUND, "参数不存在", parameter_id=parameter_id)
    return {
        "config_id": config_id,
        "parameter_id": parameter_id,
        "items": rows,
    }
