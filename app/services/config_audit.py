"""配置域审计检索与来源谱系（M7-11 / INH-1970）。

只读服务：把仓储里的 AuditEvent 按对象、参数、版本、机型、类别、人、时间与理由
筛出来，并沿不可变对象链解释"这次发布是怎么来的"。

两条边界：

1. **按事件记录时的 revision 解析对象**，不在查询时取 head；否则一次改名/退役
   会把旧事件的来源解释成今天的对象。
2. **退役对象默认仍可查**；`include_retired=0` 只是显式收窄，不是默认行为。
"""

from __future__ import annotations

import base64
import hashlib
import json
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Set

from ..core.config_repository import KIND_PREFIX, ConfigRepository
from ..core.errors import INVALID_INPUT, NOT_FOUND, AppError

MAX_LIMIT = 200
CURSOR_VERSION = "v1"

_KEY_TO_SET = {
    "profile_id": "profile_ids",
    "family_id": "family_ids",
    "release_id": "release_ids",
    "logical_file_id": "logical_file_ids",
    "variant_id": "variant_ids",
    "definition_set_id": "definition_set_ids",
    "baseline_id": "baseline_ids",
    "artifact_id": "artifact_ids",
    "parameter_id": "parameter_ids",
    "observation_id": "observation_ids",
    "change_set_id": "change_set_ids",
    "delivery_id": "delivery_ids",
    "verification_id": "verification_ids",
    "definition_id": "definition_ids",
    "snapshot_id": "snapshot_ids",
}

_KIND_TO_SET = {
    "software_release": "release_ids",
    "family": "family_ids",
    "logical_file": "logical_file_ids",
    "file_snapshot": "snapshot_ids",
    "template_variant": "variant_ids",
    "parameter": "parameter_ids",
    "definition_revision": "definition_ids",
    "definition_set": "definition_set_ids",
    "baseline": "baseline_ids",
    "change_set": "change_set_ids",
    "artifact": "artifact_ids",
    "delivery": "delivery_ids",
    "verification": "verification_ids",
    "observation": "observation_ids",
}

_RETIRED_STATUS = {"retired", "inactive", "archived", "disabled"}

_KIND_NAMES = {
    "family": "配置类别",
    "logical_file": "逻辑文件",
    "software_release": "软件版本",
    "file_snapshot": "文件快照",
    "release_batch": "出口批次",
    "template_variant": "模板变体",
    "parameter": "参数",
    "definition_revision": "参数定义",
    "definition_set": "定义集合",
    "tracking_policy": "追踪策略",
    "baseline": "基线",
    "observation": "现场观测",
    "capture_event": "采集事件",
    "change_set": "变更单",
    "artifact": "交付产物",
    "delivery": "交付登记",
    "verification": "回读核验",
}


def _text(value: Any, field: str) -> str:
    text = str(value if value is not None else "").strip()
    if not text:
        raise AppError(INVALID_INPUT, "%s 不能为空" % field, field=field)
    return text


def _optional_text(value: Any) -> str:
    return str(value if value is not None else "").strip()


def _parse_time(value: Any, field: str) -> Optional[datetime]:
    text = _optional_text(value)
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        raise AppError(INVALID_INPUT, "时间必须是 ISO-8601", field=field)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def category_of(action: Any) -> str:
    text = _optional_text(action)
    return text.split(".", 1)[0] if text else ""


def _actor_label(event: Dict[str, Any]) -> str:
    actor = event.get("actor") or {}
    if isinstance(actor, dict):
        return str(actor.get("name") or actor.get("ip") or "")
    return str(actor or "")


def _collect_context(payload: Any, context: Dict[str, Set[str]]) -> None:
    if isinstance(payload, dict):
        for key, value in payload.items():
            target = _KEY_TO_SET.get(key)
            if target and isinstance(value, str) and value.strip():
                context[target].add(value.strip())
            if key == "status" and isinstance(value, str):
                context["statuses"].add(value.strip().lower())
            _collect_context(value, context)
    elif isinstance(payload, list):
        for item in payload:
            _collect_context(item, context)


def _event_context(repo: ConfigRepository, event: Dict[str, Any]) -> Dict[str, Any]:
    context: Dict[str, Any] = {
        "kinds": set(),
        "ids": set(),
        "profile_ids": set(),
        "family_ids": set(),
        "release_ids": set(),
        "logical_file_ids": set(),
        "variant_ids": set(),
        "definition_set_ids": set(),
        "definition_ids": set(),
        "baseline_ids": set(),
        "change_set_ids": set(),
        "artifact_ids": set(),
        "parameter_ids": set(),
        "observation_ids": set(),
        "delivery_ids": set(),
        "verification_ids": set(),
        "snapshot_ids": set(),
        "statuses": set(),
        "objects": [],
    }
    for ref in event.get("objects") or []:
        kind = _optional_text((ref or {}).get("kind"))
        object_id = _optional_text((ref or {}).get("id"))
        revision = _optional_text((ref or {}).get("revision")) or None
        if not kind or not object_id:
            continue
        context["kinds"].add(kind)
        context["ids"].add(object_id)
        target = _KIND_TO_SET.get(kind)
        if target:
            context[target].add(object_id)
        payload = repo.get(kind, object_id, revision)
        if payload is None:
            context["objects"].append(
                {
                    "kind": kind,
                    "kind_label": _KIND_NAMES.get(kind, kind),
                    "id": object_id,
                    "revision": revision,
                    "missing": True,
                }
            )
            continue
        context["objects"].append(
            {
                "kind": kind,
                "kind_label": _KIND_NAMES.get(kind, kind),
                "id": object_id,
                "revision": revision,
                "label": _object_label(kind, payload),
                "state": payload.get("state") or payload.get("status") or "",
            }
        )
        _collect_context(payload, context)
        if kind == "baseline":
            for parameter_id in (payload.get("values") or {}).keys():
                context["parameter_ids"].add(str(parameter_id))
        if kind == "definition_set":
            for definition_id in payload.get("definition_ids") or []:
                context["definition_ids"].add(str(definition_id))
    context["retired"] = bool(context["statuses"] & _RETIRED_STATUS) or any(
        bool((obj or {}).get("retired_at")) for obj in event.get("objects") or []
    )
    return context


def _object_label(kind: str, payload: Dict[str, Any]) -> str:
    for key in ("display_name", "name", "filename", "version_label", "title", "reason"):
        value = payload.get(key)
        if value:
            return str(value)
    if kind == "baseline":
        return "%s %s" % (payload.get("scope") or "base", payload.get("id") or "")
    return str(payload.get("id") or "")


def _matches(
    event: Dict[str, Any],
    context: Dict[str, Any],
    *,
    action: str,
    category: str,
    object_id: str,
    object_kind: str,
    parameter_id: str,
    release_id: str,
    family_id: str,
    profile_id: str,
    include_common: bool,
    actor: str,
    reason: str,
    since: Optional[datetime],
    until: Optional[datetime],
    include_retired: bool,
) -> bool:
    if action and _optional_text(event.get("action")) != action:
        return False
    if category and category_of(event.get("action")) != category:
        return False
    if object_id and object_id not in context["ids"]:
        return False
    if object_kind and object_kind not in context["kinds"]:
        return False
    if parameter_id and parameter_id not in context["parameter_ids"]:
        return False
    if release_id and release_id not in context["release_ids"]:
        return False
    if family_id and family_id not in context["family_ids"]:
        return False
    if profile_id:
        if profile_id not in context["profile_ids"]:
            if not (include_common and not context["profile_ids"]):
                return False
    if not include_retired and context["retired"]:
        return False
    if actor:
        haystack = _actor_label(event).casefold()
        if actor.casefold() not in haystack:
            return False
    if reason:
        if reason.casefold() not in _optional_text(event.get("reason")).casefold():
            return False
    stamp = _parse_time(event.get("at"), "at")
    if since is not None and (stamp is None or stamp < since):
        return False
    if until is not None and (stamp is None or stamp > until):
        return False
    return True


def _present(event: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
    item = dict(event)
    item["category"] = category_of(event.get("action"))
    item["actor_label"] = _actor_label(event)
    item["objects_detail"] = context["objects"]
    item["retired"] = bool(context["retired"])
    return item


def _fingerprint(filters: Dict[str, Any], limit: int) -> str:
    raw = json.dumps(
        {"filters": filters, "limit": limit},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:20]


def _encode_cursor(seq: int, fingerprint: str) -> str:
    raw = "%s|%d|%s" % (CURSOR_VERSION, seq, fingerprint)
    return base64.urlsafe_b64encode(raw.encode("utf-8")).decode("ascii").rstrip("=")


def _decode_cursor(cursor: str, fingerprint: str) -> int:
    text = _optional_text(cursor)
    if not text:
        return 0
    padded = text + "=" * ((4 - len(text) % 4) % 4)
    try:
        raw = base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8")
    except Exception:
        raise AppError(INVALID_INPUT, "cursor 参数无效", field="cursor")
    parts = raw.split("|")
    if len(parts) != 3 or parts[0] != CURSOR_VERSION or parts[2] != fingerprint:
        raise AppError(INVALID_INPUT, "cursor 与本次查询不匹配", field="cursor")
    try:
        return int(parts[1])
    except ValueError:
        raise AppError(INVALID_INPUT, "cursor 参数无效", field="cursor")


def query(
    repo: ConfigRepository,
    *,
    action: Optional[str] = None,
    category: Optional[str] = None,
    object_id: Optional[str] = None,
    object_kind: Optional[str] = None,
    parameter_id: Optional[str] = None,
    release_id: Optional[str] = None,
    family_id: Optional[str] = None,
    profile_id: Optional[str] = None,
    include_common: bool = True,
    actor: Optional[str] = None,
    reason: Optional[str] = None,
    since: Any = None,
    until: Any = None,
    include_retired: bool = True,
    limit: int = 50,
    cursor: Optional[str] = None,
) -> Dict[str, Any]:
    """按条件检索审计事件。返回 `{items, next_cursor, scanned, truncated}`。"""
    try:
        wanted_limit = int(limit)
    except (TypeError, ValueError):
        raise AppError(INVALID_INPUT, "limit 必须是整数", field="limit")
    if wanted_limit < 1 or wanted_limit > MAX_LIMIT:
        raise AppError(
            INVALID_INPUT,
            "limit 超出范围",
            field="limit",
            min=1,
            max=MAX_LIMIT,
        )

    filters = {
        "action": _optional_text(action),
        "category": _optional_text(category),
        "object_id": _optional_text(object_id),
        "object_kind": _optional_text(object_kind),
        "parameter_id": _optional_text(parameter_id),
        "release_id": _optional_text(release_id),
        "family_id": _optional_text(family_id),
        "profile_id": _optional_text(profile_id),
        "include_common": bool(include_common),
        "actor": _optional_text(actor),
        "reason": _optional_text(reason),
        "since": _optional_text(since),
        "until": _optional_text(until),
        "include_retired": bool(include_retired),
    }
    fingerprint = _fingerprint(filters, wanted_limit)
    start_seq = _decode_cursor(cursor or "", fingerprint)
    since_dt = _parse_time(since, "since")
    until_dt = _parse_time(until, "until")

    items: List[Dict[str, Any]] = []
    scanned = 0
    next_before: Optional[int] = start_seq or None
    next_cursor: Optional[str] = None
    while len(items) < wanted_limit:
        page = repo.audit_events(
            limit=max(200, wanted_limit * 2), before_seq=next_before
        )
        page_items = page.get("items") or []
        if not page_items:
            break
        for event in page_items:
            scanned += 1
            context = _event_context(repo, event)
            if not _matches(
                event,
                context,
                action=filters["action"],
                category=filters["category"],
                object_id=filters["object_id"],
                object_kind=filters["object_kind"],
                parameter_id=filters["parameter_id"],
                release_id=filters["release_id"],
                family_id=filters["family_id"],
                profile_id=filters["profile_id"],
                include_common=filters["include_common"],
                actor=filters["actor"],
                reason=filters["reason"],
                since=since_dt,
                until=until_dt,
                include_retired=filters["include_retired"],
            ):
                continue
            items.append(_present(event, context))
            if len(items) >= wanted_limit:
                seq = int(event.get("seq") or 0)
                if seq > 1:
                    next_cursor = _encode_cursor(seq, fingerprint)
                break
        if len(items) >= wanted_limit:
            break
        next_before = page.get("next_before")
        if not next_before:
            break

    return {
        "items": items,
        "next_cursor": next_cursor,
        "scanned": scanned,
        "truncated": bool(next_cursor),
    }


def _kind_of(object_id: str) -> str:
    text = _optional_text(object_id)
    prefix, _, tail = text.partition("_")
    if not prefix or len(tail) != 6 or not tail.isdigit():
        raise AppError(INVALID_INPUT, "对象 ID 格式非法", field="object_id")
    for kind, expected in KIND_PREFIX.items():
        if expected == prefix:
            return kind
    raise AppError(NOT_FOUND, "未知的对象类型", object_id=text)


def _references(payload: Any, target_id: str) -> bool:
    if isinstance(payload, dict):
        for key, value in payload.items():
            if str(key) == target_id:
                return True
            if _references(value, target_id):
                return True
    elif isinstance(payload, list):
        return any(_references(item, target_id) for item in payload)
    elif isinstance(payload, str):
        return payload == target_id
    return False


_FORWARD_FIELDS = {
    "release_id": "software_release",
    "baseline_id": "baseline",
    "previous_revision_id": "baseline",
    "variant_id": "template_variant",
    "definition_set_id": "definition_set",
    "template_snapshot_id": "file_snapshot",
    "snapshot_id": "file_snapshot",
    "logical_file_id": "logical_file",
    "family_id": "family",
    "artifact_id": "artifact",
    "observation_id": "observation",
    "parameter_id": "parameter",
    "change_set_id": "change_set",
}


def _related(repo: ConfigRepository, kind: str, object_id: str) -> Dict[str, Any]:
    """列出与目标对象有直接关系的对象（正向引用 + 反向引用）。

    管理域是单进程、小规模对象图；这里刻意用扫描而不是维护第二套索引，避免索引
    与权威对象分叉。对象规模变大时（M8-05 基准）再引入同一语义的索引。
    """
    related: Dict[str, List[Dict[str, Any]]] = {}
    seen = set()

    def add(candidate_kind: str, candidate_id: Any) -> None:
        cid = _optional_text(candidate_id)
        if not cid or cid == object_id or (candidate_kind, cid) in seen:
            return
        payload = repo.get(candidate_kind, cid)
        if payload is None:
            return
        seen.add((candidate_kind, cid))
        bucket = related.setdefault(candidate_kind, [])
        if len(bucket) >= 100:
            return
        bucket.append(
            {
                "kind": candidate_kind,
                "kind_label": _KIND_NAMES.get(candidate_kind, candidate_kind),
                "id": cid,
                "revision": payload.get("revision"),
                "label": _object_label(candidate_kind, payload),
                "state": payload.get("state") or payload.get("status") or "",
            }
        )

    target = repo.get(kind, object_id) or {}
    for field, candidate_kind in _FORWARD_FIELDS.items():
        add(candidate_kind, target.get(field))
    if kind == "baseline":
        for parameter_id in (target.get("values") or {}).keys():
            add("parameter", parameter_id)
    if kind == "change_set":
        for change in target.get("changes") or []:
            add("parameter", (change or {}).get("parameter_id"))
    if kind == "artifact":
        for edit in target.get("edits") or []:
            add("parameter", (edit or {}).get("parameter_id"))
    for candidate_kind in KIND_PREFIX:
        if candidate_kind == kind:
            continue
        for item in repo.list_objects(candidate_kind):
            if str(item.get("id")) == object_id:
                continue
            if not _references(item, object_id):
                continue
            add(candidate_kind, item.get("id"))
    lineage: List[str] = []
    if kind == "baseline":
        current = repo.get("baseline", object_id)
        seen = set()
        while current is not None and str(current.get("id")) not in seen:
            seen.add(str(current.get("id")))
            lineage.append(str(current.get("id")))
            previous = _optional_text(current.get("previous_revision_id"))
            current = repo.get("baseline", previous) if previous else None
        lineage.reverse()
    return {"related": related, "lineage": lineage}


def timeline(
    repo: ConfigRepository, object_id: Any, revision: Optional[str] = None
) -> Dict[str, Any]:
    """单对象时间线：修订、审计事件与来源谱系。"""
    oid = _text(object_id, "object_id")
    kind = _kind_of(oid)
    payload = repo.get(kind, oid, _optional_text(revision) or None)
    if payload is None:
        raise AppError(NOT_FOUND, "对象不存在", object_id=oid)
    revisions = []
    for rev in repo.history(kind, oid):
        item = repo.get(kind, oid, rev)
        if item is None:
            continue
        revisions.append(
            {
                "revision": rev,
                "created_at": item.get("created_at") or item.get("updated_at") or "",
                "created_by": item.get("created_by") or item.get("updated_by") or "",
                "state": item.get("state") or item.get("status") or "",
            }
        )
    events = query(repo, object_id=oid, limit=MAX_LIMIT)
    provenance = _related(repo, kind, oid)
    return {
        "object": payload,
        "object_kind": kind,
        "object_kind_label": _KIND_NAMES.get(kind, kind),
        "revisions": revisions,
        "events": events["items"],
        "provenance": provenance,
    }
