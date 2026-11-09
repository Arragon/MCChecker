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
        "intent": row.get("intent", "inherit"),
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
    offset: int = 0,
    limit: Optional[int] = None,
    catalog_all: bool = False,
    keyword: str = "",
    directory: str = "",
    only_changes: bool = False,
    machine_revision_ids: Optional[List[str]] = None,
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
    if catalog_all:
        params = (config.get("definition", {}).get("current") or {}).get("parameters") or {}
    columns: List[Dict[str, Any]] = []

    d0 = release.get("d0") or {}
    if release.get("default_revision_id") or d0.get("frozen") or catalog_all:
        columns.append(
            {
                "kind": "default",
                "column_id": "d0-" + release_id,
                "label": "D0" if release.get("default_revision_id") or d0.get("frozen") else "D0 草稿",
                "revision_id": release.get("default_revision_id") or ("d0-" + release_id),
                "values": d0.get("frozen_snapshot") or d0.get("values") or {},
                "definition_revision": release.get("definition_revision", 0),
            }
        )

    if compare_release_id:
        other = (config.get("releases") or {}).get(compare_release_id)
        if other is not None and (other.get("default_revision_id") or catalog_all):
            columns.append(
                {
                    "kind": "compare",
                    "column_id": "d0-" + compare_release_id,
                    "label": "对比版本 D0 " + str(other.get("version")) + (" · 草稿" if not other.get("default_revision_id") else ""),
                    "revision_id": other.get("default_revision_id") or "d0-" + compare_release_id,
                    "values": (other.get("d0") or {}).get("frozen_snapshot") or (other.get("d0") or {}).get("values") or {},
                    "definition_revision": other.get("definition_revision", 0),
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
                "definition_revision": row.get("definition_revision", 0),
            }
        )

    selected_machines = set(machine_ids or [])
    selected_revisions = set(machine_revision_ids or [])
    found_revisions = set()
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
        chosen = [r for r in matching if r["revision_id"] in selected_revisions]
        found_revisions.update(r["revision_id"] for r in chosen)
        rev = chosen[-1] if chosen else matching[-1]
        columns.append(
            {
                "kind": "machine",
                "column_id": rev["revision_id"],
                "label": machine["machine_no"] + "·" + rev["label"],
                "revision_id": rev["revision_id"],
                "machine_id": machine["machine_id"],
                "machine_no": machine["machine_no"],
                "values": rev.get("values") or {},
                "definition_revision": rev.get("definition_revision", 0),
            }
        )

    if selected_revisions - found_revisions:
        raise AppError(NOT_FOUND, "机台修订不存在或不属于所选版本", field="machine_revisions")

    ordered = sorted(
        params,
        key=lambda k: (params[k].get("directory") or [], params[k].get("name") or ""),
    )
    bindings = {b["parameter_id"]: b for b in ((release.get("binding_set") or {}).get("bindings") or {}).values()}
    term = str(keyword or "").lower().strip()
    folder = str(directory or "").strip("/")
    previous = next((col for col in columns if col["kind"] == "compare"), None)
    previous_definition = _definition(config, other) if compare_release_id and other is not None else {}
    previous_bindings = {b["parameter_id"]: b for b in (((other or {}).get("binding_set") or {}).get("bindings") or {}).values()} if compare_release_id else {}
    def changed(pid: str) -> bool:
        if previous is None:
            return False
        before = _member(previous["values"], pid, "")
        after = _member(d0.get("frozen_snapshot") or d0.get("values") or {}, pid, "")
        current_parameter = (definition.get("parameters") or {}).get(pid)
        old_parameter = (previous_definition.get("parameters") or {}).get(pid)
        fields = ("name", "directory", "type", "unit", "attrs")
        metadata_changed = any((current_parameter or {}).get(key) != (old_parameter or {}).get(key) for key in fields)
        old_binding = previous_bindings.get(pid)
        binding_changed = (bindings.get(pid) or {}).get("locator") != (old_binding or {}).get("locator")
        return before != after or metadata_changed or binding_changed
    if term:
        ordered = [pid for pid in ordered if term in (str(params[pid].get("name", "")) + " " + pid + " " + "/".join(params[pid].get("directory") or []) + " " + str(bindings.get(pid, {}).get("locator", ""))).lower()]
    if folder:
        ordered = [pid for pid in ordered if "/".join(params[pid].get("directory") or []) == folder or "/".join(params[pid].get("directory") or []).startswith(folder + "/")]
    if only_changes:
        ordered = [pid for pid in ordered if changed(pid)]
    total = len(ordered)
    start = max(0, int(offset))
    size = None if limit is None else max(1, min(int(limit), 500))
    visible = ordered[start:] if size is None else ordered[start : start + size]
    rows = []
    definitions = {snapshot.get("revision"): snapshot.get("parameters") or {} for snapshot in (config.get("definition", {}).get("history") or [])}
    for pid in visible:
        p = params[pid]
        cells = {
            col["column_id"]: _member(col["values"], pid, col["revision_id"])
            for col in columns
        }
        for col in columns:
            source_parameter = definitions.get(col.get("definition_revision"), {}).get(pid) or {}
            cells[col["column_id"]].update({"type": source_parameter.get("type", ""), "unit": source_parameter.get("unit", ""), "defined": pid in definitions.get(col.get("definition_revision"), {})})
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
                "binding": copy.deepcopy(bindings.get(pid)),
                "changed": changed(pid),
            }
        )
    public_columns = []
    for col in columns:
        public_columns.append({k: v for k, v in col.items() if k != "values"})
    page = rows
    return {
        "config_id": config_id,
        "release_id": release_id,
        "release_version": release.get("version"),
        "definition_revision": definition.get("revision", 0),
        "catalog_revision": (config.get("definition") or {}).get("revision", 0),
        "has_previous": bool(release.get("predecessor_id")),
        "previous_release_id": release.get("predecessor_id"),
        "columns": public_columns,
        "total": total,
        "offset": start,
        "limit": size,
        "rows": page,
    }
