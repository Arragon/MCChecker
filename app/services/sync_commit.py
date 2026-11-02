"""M16-24 可信同步提交：把 HTTP/SDA 取回的字节交给既有 V3 文件/记录路径。

- 只处理 `file_archive` / `http_record`；候选/观测归 M16-26/29。
- 提交前重新校验 item 的 size/sha；XML/JSON 解析、根指纹、锁内 current 竞态
  复用 `updates._commit_config`（含 config_policies 守卫与归档）。
- 不写 cron/保活/候选状态；不自动发布 D0/Bn/rn。
"""

from __future__ import annotations

import hashlib
import os
from typing import Any, Dict, List, Optional

from app.core import fsutil, storage
from app.core.errors import INVALID_INPUT, AppError
from app.services import revision, updates, workspace_context

from .sync_http_adapter import HttpFetchItem, HttpFetchResult


def _validate_item(item: HttpFetchItem) -> None:
    if not isinstance(item.content, (bytes, bytearray)):
        raise AppError(INVALID_INPUT, "item content 必须是 bytes", field="content")
    body = bytes(item.content)
    if len(body) != item.size:
        raise AppError(INVALID_INPUT, "item size 与内容不符", field="size")
    if hashlib.sha256(body).hexdigest() != item.sha256:
        raise AppError(INVALID_INPUT, "item sha256 与内容不符", field="sha256")


def _aggregate(items: List[Dict[str, Any]]) -> str:
    statuses = [item["status"] for item in items]
    if not statuses:
        return "failed"
    if all(status in ("success", "unchanged") for status in statuses):
        return "succeeded"
    if any(status in ("success", "unchanged") for status in statuses):
        return "partial"
    return "failed"


def _commit_file(
    workspace_id: str,
    profile_id: str,
    source: Dict[str, Any],
    item: HttpFetchItem,
    base_sha: Optional[str],
) -> Dict[str, Any]:
    name = item.basename or source.get("name")
    try:
        name = fsutil.validate_filename(name)
    except AppError as exc:
        return {"name": str(name), "status": "rejected", "reason": exc.message}
    _validate_item(item)
    with storage.use_workspace(workspace_id), storage.use_profile(profile_id):
        outcome = updates._commit_config(profile_id, name, bytes(item.content), base_sha=base_sha)
        if outcome.get("status") == "success":
            revision.bump(profile_id, "files")
    return {
        "name": name,
        "status": outcome.get("status"),
        "reason": outcome.get("reason"),
        "archived": outcome.get("archived"),
        "size": item.size,
        "sha256": item.sha256,
    }


def _commit_record(
    workspace_id: str,
    profile_id: str,
    source: Dict[str, Any],
    item: HttpFetchItem,
) -> Dict[str, Any]:
    name = source.get("name") or item.basename
    try:
        name = fsutil.validate_filename(name)
    except AppError as exc:
        return {"name": str(name), "status": "rejected", "reason": exc.message}
    _validate_item(item)
    with storage.use_workspace(workspace_id), storage.use_profile(profile_id):
        latest = updates._latest_record_bytes(name)
        if latest is not None and hashlib.sha256(latest).hexdigest() == item.sha256:
            return {
                "name": name,
                "status": "unchanged",
                "reason": "内容与最新一条修改记录一致，未新建记录文件",
                "size": item.size,
                "sha256": item.sha256,
            }
        filename = storage.save_record_file(
            name,
            bytes(item.content),
            source_url=source.get("record_source"),
        )
        revision.bump(profile_id, "files")
    return {
        "name": name,
        "status": "success",
        "reason": "已写入修改记录 %s" % filename,
        "size": item.size,
        "sha256": item.sha256,
    }


def commit_fetch_result(
    workspace_id: str,
    profile_id: str,
    source: Dict[str, Any],
    result: HttpFetchResult,
    base_sha: Optional[str] = None,
) -> Dict[str, Any]:
    workspace_context.assert_profile(workspace_id, profile_id)
    if not isinstance(result, HttpFetchResult):
        raise AppError(INVALID_INPUT, "result 必须是 HttpFetchResult", field="result")
    if result.status not in ("succeeded", "unchanged"):
        return {
            "status": "failed",
            "source_id": result.source_id,
            "reason_code": result.reason_code,
            "message": result.message,
            "items": [],
        }
    target = source.get("target") or {}
    if result.kind == "http_file":
        if target.get("kind") != "file_archive":
            return {
                "status": "blocked",
                "source_id": result.source_id,
                "reason_code": "target_missing",
                "message": "http_file 只提交 file_archive；候选/观测归 M16-26/29",
                "items": [],
            }
        items = [
            _commit_file(workspace_id, profile_id, source, item, base_sha)
            for item in result.items
        ]
    elif result.kind == "http_record":
        items = [
            _commit_record(workspace_id, profile_id, source, item)
            for item in result.items
        ]
    else:
        return {
            "status": "blocked",
            "source_id": result.source_id,
            "reason_code": "protocol_mismatch",
            "message": "unsupported fetch result kind",
            "items": [],
        }
    return {
        "status": _aggregate(items),
        "source_id": result.source_id,
        "items": items,
    }
