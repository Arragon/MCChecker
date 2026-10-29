"""V3 参数总览矩阵（M13-02 / INH-2144）。

按 release 的冻结定义与 revision 快照投影：D0 / 选中的 Bn / 本版已发布的
机台 rn。没有就返回空列/未纳入，不用别的版本冒充。
"""

from __future__ import annotations

import copy
from typing import Any, Dict, List, Optional

from ..core import storage
from ..core.errors import NOT_FOUND, AppError
from . import config_v3_catalog, config_v3_drafts


def _definition(config: Dict[str, Any], release: Dict[str, Any]) -> Dict[str, Any]:
    revision = int(release.get("definition_revision", 0))
    for snapshot in (config.get("definition", {}).get("history") or []):
        if int(snapshot.get("revision", -1)) == revision:
            return snapshot
    return {"revision": revision, "parameters": {}}


def _member(values: Dict[str, Any], pid: str, revision_id: str) -> Dict[str, Any]:
    row = copy.deepcopy((values or {}).get(pid) or {})
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
    elif state == "unset":
        status = "unset"
    elif state == "null":
        status = "null"
    cell = {
        "revision_id": revision_id,
        "membership": membership,
        "enabled": enabled,
        "value_state": state,
        "status": status,
    }
    if state == "present" and "value" in row:
        cell["value"] = row["value"]
    return cell


def overview(
    profile_id: str,
    config_id: str,
    release_id: str,
    compare_release_id: str = "",
    baseline_ids: Optional[List[str]] = None,
    machine_ids: Optional[List[str]] = None,
) -> Dict[str, Any]:
    with storage.use_profile(profile_id):
        doc = config_v3_catalog._load(profile_id)
    config = doc["configs"].get(config_id)
    if config is None:
        raise AppError(NOT_FOUND, "配置不存在", config_id=config_id)
    release = (config.get("releases") or {}).get(release_id)
    if release is None:
        raise AppError(NOT_FOUND, "软件版本不存在", release_id=release_id)
    definition = _definition(config, release)
    params = definition.get("parameters") or {}
    columns: List[Dict[str, Any]] = []

    d0 = release.get("d0") or {}
    if release.get("default_revision_id") or d0.get("frozen"):
        columns.append(
            {
                "kind": "default",
                "column_id": "d0-" + release_id,
                "label": "D0",
                "revision_id": release.get("default_revision_id") or ("d0-" + release_id),
                "values": d0.get("frozen_snapshot") or d0.get("values") or {},
            }
        )

    if compare_release_id:
        other = (config.get("releases") or {}).get(compare_release_id)
        if other is not None and other.get("default_revision_id"):
            columns.append(
                {
                    "kind": "compare",
                    "column_id": "d0-" + compare_release_id,
                    "label": "前一版本 D0 " + str(other.get("version")),
                    "revision_id": other["default_revision_id"],
                    "values": (other.get("d0") or {}).get("frozen_snapshot") or {},
                }
            )

    branch = config.setdefault("main_branch", {"head_id": None, "next_seq": 1, "baselines": {}})
    wanted = set(baseline_ids or [])
    for row in (branch.get("baselines") or {}).values():
        if row.get("release_id") != release_id:
            continue
        if wanted and row["baseline_id"] not in wanted:
            continue
        columns.append(
            {
                "kind": "baseline",
                "column_id": row["baseline_id"],
                "label": row["label"],
                "revision_id": row["baseline_id"],
                "values": row.get("values") or {},
            }
        )

    selected_machines = set(machine_ids or [])
    for machine in (config.get("machines") or {}).values():
        if selected_machines and machine["machine_id"] not in selected_machines:
            continue
        matching = [
            r
            for r in (machine.get("revisions") or {}).values()
            if r.get("release_id") == release_id
        ]
        if not matching:
            continue
        rev = matching[-1]  # 插入顺序即发布顺序
        columns.append(
            {
                "kind": "machine",
                "column_id": rev["revision_id"],
                "label": machine["machine_no"] + "·" + rev["label"],
                "revision_id": rev["revision_id"],
                "machine_id": machine["machine_id"],
                "machine_no": machine["machine_no"],
                "values": rev.get("values") or {},
            }
        )

    rows = []
    for pid in sorted(params, key=lambda k: (params[k].get("directory") or [], params[k].get("name") or "")):
        p = params[pid]
        cells = {
            col["column_id"]: _member(col["values"], pid, col["revision_id"])
            for col in columns
        }
        rows.append(
            {
                "parameter_id": pid,
                "name": p.get("name"),
                "description": p.get("description", ""),
                "directory": list(p.get("directory") or []),
                "type": p.get("type", ""),
                "unit": p.get("unit", ""),
                "attrs": copy.deepcopy(p.get("attrs") or {}),
                "source": copy.deepcopy(p.get("source") or {}),
                "cells": cells,
            }
        )
    public_columns = []
    for col in columns:
        public_columns.append({k: v for k, v in col.items() if k != "values"})
    return {
        "config_id": config_id,
        "release_id": release_id,
        "release_version": release.get("version"),
        "definition_revision": definition.get("revision", 0),
        "has_previous": bool(release.get("predecessor_id")),
        "previous_release_id": release.get("predecessor_id"),
        "columns": public_columns,
        "rows": rows,
    }
