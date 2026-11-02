"""M16-06 旧更新源/按机型计划的只读迁移计划（dry-run）。

只读取旧 `config_mapping.json` / `schedule.json` 与既有 FileResource，输出
拟迁移的 SyncSource / policy override / 缺口。**不写 data、不创建 sync 目录、
不改旧文件/归档/记录。** 执行/备份/回退归 M16-37。
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Dict, List, Optional

from app.core import storage
from app.core.errors import AppError
from app.core.sync_models import make_source_id, validate_workspace_id

from . import file_versions, workspace_catalog, workspace_context

DEFAULT_INTERVAL = 6


def _profile_ids(workspace_id: str, profile_ids: Optional[List[str]]) -> List[str]:
    if profile_ids:
        for pid in profile_ids:
            workspace_context.assert_profile(workspace_id, pid)
        return list(profile_ids)
    return [str(row["id"]) for row in workspace_catalog.list_profiles(workspace_id)]


def _read_legacy(workspace_id: str, profile_id: str) -> Dict[str, Any]:
    with storage.use_workspace(workspace_id), storage.use_profile(profile_id):
        mapping = storage.load_config_mapping()
        schedule = storage.load_schedule()
    if not isinstance(mapping, list):
        mapping = []
    if not isinstance(schedule, dict):
        schedule = {}
    return {"mapping": mapping, "schedule": schedule}


def _resources(workspace_id: str, profile_id: str, names: List[str]) -> Dict[str, Any]:
    """按名解析现存 FileResource；缺失/非法名留给 `warnings`，不打断整份计划。

    刻意不走 `file_versions.list_resources()`：它经 `documents.list_files()` 读整份
    mapping，旧文件里只要有一条非对象条目就会在计划阶段炸掉，而这正是迁移要报告的
    数据问题，不是迁移本身该失败的理由。
    """
    out: Dict[str, Any] = {}
    with storage.use_workspace(workspace_id), storage.use_profile(profile_id):
        for name in names:
            try:
                out[name] = file_versions.current_identity(profile_id, name)
            except AppError:
                continue
    return out


def plan_profile(workspace_id: str, profile_id: str) -> Dict[str, Any]:
    wid = validate_workspace_id(workspace_id)
    workspace_context.assert_profile(wid, profile_id)
    legacy = _read_legacy(wid, profile_id)
    valid_names = [
        str(item.get("name") or "").strip()
        for item in legacy["mapping"]
        if isinstance(item, dict)
    ]
    resources = _resources(wid, profile_id, [name for name in valid_names if name])
    interval = legacy["schedule"].get("interval_hours") or DEFAULT_INTERVAL
    if isinstance(interval, bool) or not isinstance(interval, int) or interval < 1 or interval > 2160:
        interval = DEFAULT_INTERVAL
    enabled = bool(legacy["schedule"].get("enabled"))
    sources: List[Dict[str, Any]] = []
    warnings: List[Dict[str, Any]] = []
    seen = set()
    for index, item in enumerate(legacy["mapping"]):
        if not isinstance(item, dict):
            warnings.append({"profile_id": profile_id, "index": index, "reason": "invalid_mapping_item"})
            continue
        name = str(item.get("name") or "").strip()
        url = str(item.get("url") or "").strip()
        if not name or not url:
            warnings.append(
                {
                    "profile_id": profile_id,
                    "index": index,
                    "name": name or None,
                    "reason": "missing_name_or_url",
                }
            )
            continue
        if name in seen:
            warnings.append(
                {
                    "profile_id": profile_id,
                    "index": index,
                    "name": name,
                    "reason": "duplicate_name",
                }
            )
            continue
        seen.add(name)
        resource = resources.get(name)
        target = None
        target_status = "missing"
        old_sha = None
        if resource:
            target = {"kind": "file_archive", "file_id": resource["file_id"]}
            target_status = "ready"
            old_sha = resource.get("blob_sha256")
        else:
            warnings.append(
                {
                    "profile_id": profile_id,
                    "name": name,
                    "reason": "target_file_missing",
                }
            )
        sources.append(
            {
                "source_id": make_source_id(wid, profile_id, name),
                "workspace_id": wid,
                "profile_id": profile_id,
                "name": name,
                "kind": "http_file",
                "resource_ref": url,
                "record_source": str(item.get("record_url") or "").strip() or None,
                "keyword": str(item.get("keyword") or "").strip(),
                "target": target,
                "target_status": target_status,
                "old_sha256": old_sha,
                "schedule_mode": "inherit",
            }
        )
    return {
        "profile_id": profile_id,
        "old_schedule": {
            "enabled": enabled,
            "interval_hours": interval,
        },
        "sources": sources,
        "warnings": warnings,
        "source_count": len(sources),
    }


def plan_workspace(workspace_id: str, profile_ids: Optional[List[str]] = None) -> Dict[str, Any]:
    wid = validate_workspace_id(workspace_id)
    profiles = _profile_ids(wid, profile_ids)
    plans = [plan_profile(wid, pid) for pid in profiles]
    sources = [source for plan in plans for source in plan["sources"]]
    warnings = [warning for plan in plans for warning in plan["warnings"]]
    overrides = {
        plan["profile_id"]: dict(plan["old_schedule"])
        for plan in plans
    }
    body = {
        "schema": 1,
        "workspace_id": wid,
        "profile_ids": profiles,
        "profile_plans": plans,
        "policy_overrides": overrides,
        "sources": sources,
        "warnings": warnings,
        "writes": 0,
    }
    digest = hashlib.sha256(
        json.dumps(body, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()[:16]
    body["plan_id"] = "mig-" + digest
    return body
