"""M16-11 脱敏 run 事件日志（JSONL）。

- 每个 run 一个 `events.jsonl`；事件 seq 单调递增。
- 只允许白名单字段；写前走 `validate_safe_details`，拒绝 password/Cookie/CSRF。
- 末尾截断的行在下次 append 时被丢弃并重写；中间损坏仍抛 CorruptDataError。
- 读取是只读的，不创建 runs 目录。
"""

from __future__ import annotations

import json
import os
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from app.core import fsutil
from app.core.errors import INVALID_INPUT, AppError, CorruptDataError
from app.core.sync_models import (
    validate_id,
    validate_reason_code,
    validate_safe_details,
    validate_sha256,
    validate_timestamp,
)

from . import sync_store
from .sync_paths import ensure_dir, run_dir

EVENT_LEVELS = ("debug", "info", "warning", "error")
EVENT_FIELDS = frozenset(
    {
        "seq",
        "at",
        "level",
        "stage",
        "source_id",
        "item_id",
        "status",
        "reason_code",
        "message",
        "bytes",
        "sha256",
    }
)


def _now() -> str:
    return datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")


def _path(workspace_id: str, run_id: str) -> str:
    return os.path.join(run_dir(workspace_id, run_id), "events.jsonl")


def _validate(event: Any, seq: int) -> Dict[str, Any]:
    if not isinstance(event, dict):
        raise AppError(INVALID_INPUT, "event 必须是对象", field="event")
    unknown = set(event) - EVENT_FIELDS
    if unknown:
        raise AppError(INVALID_INPUT, "event 含未知字段", field="event", keys=sorted(unknown))
    level = str(event.get("level") or "info")
    if level not in EVENT_LEVELS:
        raise AppError(INVALID_INPUT, "event.level 非法", field="level")
    normalized: Dict[str, Any] = {
        "seq": seq,
        "at": validate_timestamp(event.get("at") or _now(), "at", allow_none=False),
        "level": level,
    }
    stage = event.get("stage")
    if stage is not None:
        text = str(stage).strip()
        if not text or len(text) > 40:
            raise AppError(INVALID_INPUT, "event.stage 非法", field="stage")
        normalized["stage"] = text
    for key in ("source_id", "item_id"):
        value = event.get(key)
        if value is not None:
            normalized[key] = validate_id(value, key)
    status = event.get("status")
    if status is not None:
        text = str(status).strip()
        if not text or len(text) > 40:
            raise AppError(INVALID_INPUT, "event.status 非法", field="status")
        normalized["status"] = text
    reason = validate_reason_code(event.get("reason_code"))
    if reason is not None:
        normalized["reason_code"] = reason
    message = event.get("message")
    if message is not None:
        text = str(message).strip()
        if len(text) > 500:
            raise AppError(INVALID_INPUT, "event.message 过长", field="message")
        normalized["message"] = text
    raw_bytes = event.get("bytes")
    if raw_bytes is not None:
        if isinstance(raw_bytes, bool) or not isinstance(raw_bytes, int) or raw_bytes < 0:
            raise AppError(INVALID_INPUT, "event.bytes 必须是非负整数", field="bytes")
        normalized["bytes"] = raw_bytes
    digest = validate_sha256(event.get("sha256"), "sha256", allow_none=True)
    if digest is not None:
        normalized["sha256"] = digest
    validate_safe_details(normalized, "event")
    return normalized


def _read_raw(path: str) -> Tuple[List[Dict[str, Any]], bool]:
    if not os.path.exists(path):
        return [], False
    with open(path, "r", encoding="utf-8") as handle:
        lines = handle.read().splitlines()
    events: List[Dict[str, Any]] = []
    for index, line in enumerate(lines):
        if not line.strip():
            continue
        try:
            raw = json.loads(line)
        except ValueError:
            if index == len(lines) - 1:
                return events, True
            raise CorruptDataError(path)
        if not isinstance(raw, dict):
            raise CorruptDataError(path)
        seq = raw.get("seq")
        if isinstance(seq, bool) or not isinstance(seq, int) or seq < 1:
            raise CorruptDataError(path)
        events.append(_validate(raw, seq))
    events.sort(key=lambda item: item["seq"])
    for index in range(1, len(events)):
        if events[index]["seq"] <= events[index - 1]["seq"]:
            raise CorruptDataError(path)
    return events, False


def append_event(workspace_id: str, run_id: str, event: Dict[str, Any]) -> Dict[str, Any]:
    path = _path(workspace_id, run_id)
    ensure_dir(os.path.dirname(path))
    with sync_store.workspace_lock(workspace_id):
        events, _truncated = _read_raw(path)
        seq = (events[-1]["seq"] + 1) if events else 1
        normalized = _validate(event, seq)
        events.append(normalized)
        text = "\n".join(
            json.dumps(item, ensure_ascii=False, sort_keys=True) for item in events
        ) + "\n"
        fsutil.atomic_write_text(path, text)
        return normalized


def read_events(
    workspace_id: str,
    run_id: str,
    after_seq: int = 0,
    limit: int = 200,
) -> List[Dict[str, Any]]:
    if after_seq < 0:
        raise AppError(INVALID_INPUT, "after_seq 不能为负", field="after_seq")
    if limit < 1 or limit > 1000:
        raise AppError(INVALID_INPUT, "limit 必须在 1–1000", field="limit")
    events, _truncated = _read_raw(_path(workspace_id, run_id))
    return [item for item in events if item["seq"] > after_seq][:limit]
