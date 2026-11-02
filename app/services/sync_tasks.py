"""M16-12 SyncRun 与 TaskRunner 整合：去重、取消、终态聚合。

一张卡只解决「run 怎么交给现有任务队列跑完并留下可信终态」：

- `submit_sync_run`：校验来源属于同一 W/P，落 run manifest，再投 `sync_run` 任务；
  带幂等键时 run_id 由 `(W,P,key)` 决定，同键同参数返回同一 run，不同参数 409；
- 执行体逐源取数/提交（HTTP 走 M16-13 + M16-24；SDA 在 M16-23 接路由前明确
  `blocked`，不假装成功），每项进度回填现有 `Task.result`；
- `cancel_run`：置取消信号；已提交项保留，未开始项记 `canceled`；
- 终态用 `core.sync_models.aggregate_run_state/aggregate_counts` 聚合，跑完把
  run manifest 的 state/counts 一次写定。

网络在任务线程里、提交在 `sync_commit` 的 W/P 锁内；本模块不持锁做 I/O，也不
自动发布候选/D0/Bn/rn。
"""

from __future__ import annotations

import hashlib
import json
import logging
import threading
from datetime import datetime
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from app.core import storage
from app.core.errors import (
    BUSY,
    INVALID_INPUT,
    NOT_FOUND,
    REVISION_CONFLICT,
    AppError,
)
from app.core.sync_models import (
    REASON_CODES,
    SyncItem,
    SyncRun,
    SyncSource,
    aggregate_counts,
    aggregate_run_state,
    make_item_id,
    make_run_id,
    validate_enum,
    validate_id,
    validate_workspace_id,
)

from . import (
    documents,
    sync_candidates,
    sync_commit,
    sync_http_adapter,
    sync_run_store,
    tasks,
    updates,
    workspace_context,
)
from .context import RequestContext
from .sync_http_adapter import HttpFetchItem, HttpFetchResult

logger = logging.getLogger(__name__)

TRIGGER_DEFAULT = "manual"
#: 本卡尚未接 SDA 路由（M16-23）；这类来源明确 blocked，绝不伪造成功。
SDA_REASON = "collector_offline"
_SDA_REASONS = REASON_CODES
RUN_ID_PREFIX = "run-"
IDEMPOTENCY_MAX = 200


class _Handle:
    __slots__ = ("task", "cancel")

    def __init__(self, task: Any) -> None:
        self.task = task
        self.cancel = threading.Event()


_HANDLES: Dict[Tuple[str, str], _Handle] = {}
_HANDLE_LOCK = threading.Lock()
#: SDA 真取数由 collector 接入方登记（M16-23 只冻结路由，不 import collector）。
_SDA_READER: Optional[Any] = None


def register_sda_reader(reader: Optional[Any]) -> None:
    """登记 SDA 取数回调；None 表示尚未接入，SDA 来源明确 blocked。"""
    global _SDA_READER
    _SDA_READER = reader


def _now() -> str:
    return datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")


def _safe_message(text: Any, limit: int = 500) -> str:
    value = str(text or "").strip()
    return value[:limit]


def _fingerprint(
    workspace_id: str,
    profile_id: str,
    trigger: str,
    parent_run_id: Optional[str],
    plan_id: Optional[str],
    sources: Sequence[Dict[str, Any]],
) -> str:
    payload = {
        "workspace_id": workspace_id,
        "profile_id": profile_id,
        "trigger": trigger,
        "parent_run_id": parent_run_id,
        "plan_id": plan_id,
        "sources": list(sources),
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _run_id_for(workspace_id: str, profile_id: str, key: str) -> str:
    digest = hashlib.sha256(
        ("idem\x1f%s\x1f%s\x1f%s" % (workspace_id, profile_id, key)).encode("utf-8")
    ).hexdigest()[:24]
    return RUN_ID_PREFIX + digest


def _normalize_sources(
    workspace_id: str, profile_id: str, sources: Iterable[Any]
) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    seen = set()
    for raw in sources:
        source = raw if isinstance(raw, SyncSource) else SyncSource.from_dict(raw)
        if source.workspace_id != workspace_id or source.profile_id != profile_id:
            raise AppError(
                INVALID_INPUT,
                "来源不属于当前工作区/机型",
                field="sources",
                source_id=source.source_id,
            )
        if source.source_id in seen:
            raise AppError(INVALID_INPUT, "来源重复", field="sources", source_id=source.source_id)
        seen.add(source.source_id)
        out.append(source.to_dict())
    if not out:
        raise AppError(INVALID_INPUT, "至少需要一个来源", field="sources")
    return out


def _existing_run(workspace_id: str, run_id: str) -> Optional[Dict[str, Any]]:
    try:
        return sync_run_store.get_run(workspace_id, run_id)
    except AppError as exc:
        if exc.code != NOT_FOUND:
            raise
        return None


def _handle_payload(run_id: str, workspace_id: str) -> Optional[Dict[str, Any]]:
    with _HANDLE_LOCK:
        handle = _HANDLES.get((workspace_id, run_id))
    return handle.task.to_payload() if handle is not None else None


def _drop_handle(workspace_id: str, run_id: str) -> None:
    with _HANDLE_LOCK:
        _HANDLES.pop((workspace_id, run_id), None)


def submit_sync_run(
    ctx: RequestContext,
    sources: Iterable[Any],
    trigger: str = TRIGGER_DEFAULT,
    idempotency_key: Optional[Any] = None,
    parent_run_id: Optional[Any] = None,
    plan_id: Optional[Any] = None,
    runner: Optional[tasks.TaskRunner] = None,
    sda_reader: Optional[Any] = None,
) -> Dict[str, Any]:
    """入队一次 Sync run；返回 `{run, task, reused}`，不等待网络。"""
    workspace_id = validate_workspace_id(str(getattr(ctx, "workspace_id", "") or "").strip())
    profile_id = str(getattr(ctx, "profile_id", "") or "").strip()
    if not profile_id:
        raise AppError(INVALID_INPUT, "同步任务必须指定机型", field="profile_id")
    workspace_context.assert_profile(workspace_id, profile_id)
    trigger = validate_enum(trigger, ("manual", "schedule", "retry", "collector"), "trigger")
    parent = None if parent_run_id in (None, "") else validate_id(parent_run_id, "parent_run_id")
    plan = None if plan_id in (None, "") else validate_id(plan_id, "plan_id")
    normalized = _normalize_sources(workspace_id, profile_id, sources)

    key = None
    if idempotency_key not in (None, ""):
        key = str(idempotency_key).strip()
        if not key or len(key) > IDEMPOTENCY_MAX:
            raise AppError(INVALID_INPUT, "idempotency_key 非法", field="idempotency_key")
    fingerprint = _fingerprint(workspace_id, profile_id, trigger, parent, plan, normalized)
    run_id = _run_id_for(workspace_id, profile_id, key) if key else make_run_id()
    existing = _existing_run(workspace_id, run_id)
    if existing is not None:
        stored = str(existing.get("frozen", {}).get("request_fingerprint") or "")
        if stored != fingerprint:
            raise AppError(
                REVISION_CONFLICT,
                "幂等键已用于不同参数",
                field="idempotency_key",
            )
        return {
            "run": existing["run"],
            "task": _handle_payload(run_id, workspace_id),
            "reused": True,
        }

    now = _now()
    run = SyncRun(
        run_id=run_id,
        workspace_id=workspace_id,
        trigger=trigger,
        state="queued",
        counts={"total": len(normalized), "queued": len(normalized)},
        created_at=now,
        parent_run_id=parent,
        plan_id=plan,
        request_id=key,
    )
    sync_run_store.create_run(
        workspace_id,
        run,
        frozen={
            "profile_id": profile_id,
            "request_fingerprint": fingerprint,
            "source_ids": [source["source_id"] for source in normalized],
        },
    )

    handle = _Handle(None)
    target_runner = runner or tasks.get_runner()

    def execute(task: tasks.Task) -> None:
        _execute(
            task,
            workspace_id,
            profile_id,
            run_id,
            normalized,
            handle.cancel,
            sda_reader or _SDA_READER,
        )

    try:
        task = target_runner.submit(ctx, "sync_run", None, execute)
    except AppError as exc:
        _abort_created_run(workspace_id, run_id, exc)
        raise
    handle.task = task
    with _HANDLE_LOCK:
        _HANDLES[(workspace_id, run_id)] = handle
    if task.state in tasks.FINISHED_STATES:
        _drop_handle(workspace_id, run_id)
    try:
        sync_run_store.append_event(
            workspace_id,
            run_id,
            {
                "level": "info",
                "stage": "queued",
                "message": "已入队 %d 个来源" % len(normalized),
            },
        )
    except AppError:
        logger.warning("同步 run 入队事件写入失败：%s", run_id, exc_info=True)
    return {"run": sync_run_store.get_run(workspace_id, run_id)["run"], "task": task.to_payload(), "reused": False}


def cancel_run(workspace_id: str, run_id: str) -> Dict[str, Any]:
    """请求取消；已提交项保持原样，未开始项由执行体记 `canceled`。"""
    wid = validate_workspace_id(workspace_id)
    rid = validate_id(run_id, "run_id")
    current = sync_run_store.get_run(wid, rid)
    if current["run"]["state"] in ("partial", "succeeded", "failed", "canceled", "interrupted"):
        return current
    with _HANDLE_LOCK:
        handle = _HANDLES.get((wid, rid))
    if handle is not None:
        handle.cancel.set()
        try:
            sync_run_store.append_event(
                wid,
                rid,
                {
                    "level": "warning",
                    "stage": "canceled",
                    "reason_code": "canceled",
                    "message": "收到取消请求",
                },
            )
        except AppError:
            logger.warning("同步 run 取消事件写入失败：%s", rid, exc_info=True)
        return sync_run_store.get_run(wid, rid)
    total = int(current["run"].get("counts", {}).get("total") or 0)
    finished = sync_run_store.finish_run(
        wid,
        rid,
        "canceled",
        counts={"total": total, "canceled": total},
        finished_at=_now(),
        receipt={"reason_code": "canceled"},
        expected_manifest_revision=current["manifest_revision"],
    )
    return finished


def recover_interrupted_runs(workspace_id: str) -> int:
    """启动时把没有活任务的 queued/running run 收成 interrupted，绝不谎称成功。"""
    wid = validate_workspace_id(workspace_id)
    with _HANDLE_LOCK:
        active = {run_id for (workspace, run_id) in _HANDLES if workspace == wid}
    recovered = 0
    for item in sync_run_store.list_runs(wid, limit=200)["items"]:
        run = item["run"]
        run_id = str(run["run_id"])
        if run.get("state") not in ("queued", "running") or run_id in active:
            continue
        total = int((run.get("counts") or {}).get("total") or 0)
        try:
            sync_run_store.finish_run(
                wid,
                run_id,
                "interrupted",
                counts={"total": total, "interrupted": total},
                receipt={"reason_code": "interrupted"},
                expected_manifest_revision=item["manifest_revision"],
            )
        except AppError:
            continue
        recovered += 1
    return recovered


def _abort_created_run(workspace_id: str, run_id: str, exc: AppError) -> None:
    try:
        current = sync_run_store.get_run(workspace_id, run_id)
        total = int(current["run"].get("counts", {}).get("total") or 0)
        sync_run_store.finish_run(
            workspace_id,
            run_id,
            "interrupted",
            counts={"total": total, "interrupted": total},
            finished_at=_now(),
            receipt={"reason_code": "interrupted" if exc.code == BUSY else "unknown"},
            expected_manifest_revision=current["manifest_revision"],
        )
    except AppError:
        logger.warning("创建 run 后入队失败，无法收口 manifest：%s", run_id, exc_info=True)


def _mark_running(workspace_id: str, run_id: str) -> bool:
    current = sync_run_store.get_run(workspace_id, run_id)
    if current["run"]["state"] != "queued":
        return False
    sync_run_store.update_run(
        workspace_id,
        run_id,
        patch={"state": "running", "started_at": _now()},
        expected_manifest_revision=current["manifest_revision"],
    )
    return True


def _execute(
    task: tasks.Task,
    workspace_id: str,
    profile_id: str,
    run_id: str,
    sources: List[Dict[str, Any]],
    cancel: threading.Event,
    sda_reader: Optional[Any] = None,
) -> None:
    items: List[Dict[str, Any]] = []
    total = len(sources)
    try:
        if not _mark_running(workspace_id, run_id):
            # run 已被取消/收口：任务侧也要留下取消项，不能以空结果收成 succeeded。
            for source in sources:
                item = _item(
                    run_id,
                    source,
                    "canceled",
                    reason_code="canceled",
                    message="run 已取消，未执行",
                )
                task.result["items"].append(_task_item(source, item))
            task.progress["total"] = total
            task.progress["done"] = total
            return
        for source in sources:
            if cancel.is_set():
                item = _item(
                    run_id,
                    source,
                    "canceled",
                    reason_code="canceled",
                    message="已取消，未开始",
                )
            else:
                item = _run_source(
                    workspace_id, profile_id, run_id, source, cancel, sda_reader
                )
            items.append(item)
            task.result["items"].append(_task_item(source, item))
            task.progress["total"] = total
            task.progress["done"] = len(items)
            _append_item_event(workspace_id, run_id, item)
        state = aggregate_run_state(item["state"] for item in items)
        counts = aggregate_counts(item["state"] for item in items)
        counts["file_updated"] = sum(1 for item in items if item.get("committed"))
        counts["candidate_created"] = sum(1 for item in items if item.get("candidate_id"))
        _finish_run(workspace_id, run_id, state, counts, items=items)
    except Exception:
        logger.exception("同步 run 执行失败：%s", run_id)
        try:
            states = [item["state"] for item in items]
            state = aggregate_run_state(states) if states else "failed"
            counts = aggregate_counts(states) if states else {"total": total, "failed": total}
            _finish_run(
                workspace_id,
                run_id,
                state if state != "running" else "failed",
                counts,
                items=items,
            )
        except Exception:
            logger.exception("同步 run 失败收口也失败：%s", run_id)
        raise
    finally:
        _drop_handle(workspace_id, run_id)


def _finish_run(
    workspace_id: str,
    run_id: str,
    state: str,
    counts: Dict[str, int],
    items: Optional[List[Dict[str, Any]]] = None,
) -> None:
    current = sync_run_store.get_run(workspace_id, run_id)
    if current["run"]["state"] in ("partial", "succeeded", "failed", "canceled", "interrupted"):
        return
    if state not in ("partial", "succeeded", "failed", "canceled", "interrupted"):
        state = "failed"
    sync_run_store.finish_run(
        workspace_id,
        run_id,
        state,
        counts=counts,
        finished_at=_now(),
        items=[
            {
                key: item.get(key)
                for key in (
                    "item_id",
                    "source_id",
                    "profile_id",
                    "state",
                    "stage",
                    "reason_code",
                    "name",
                    "reason",
                    "committed",
                    "candidate_id",
                    "sha256",
                    "bytes",
                )
                if item.get(key) is not None
            }
            for item in (items or [])
        ],
        expected_manifest_revision=current["manifest_revision"],
    )
    try:
        sync_run_store.append_event(
            workspace_id,
            run_id,
            {
                "level": "error" if state == "failed" else "info",
                "stage": "finished",
                "status": state,
                "message": "run 结束：%s" % state,
            },
        )
    except AppError:
        logger.warning("同步 run 结束事件写入失败：%s", run_id, exc_info=True)


def _run_source(
    workspace_id: str,
    profile_id: str,
    run_id: str,
    source: Dict[str, Any],
    cancel: threading.Event,
    sda_reader: Optional[Any] = None,
) -> Dict[str, Any]:
    kind = str(source.get("kind") or "")
    if kind.startswith("sda_"):
        return _run_sda_source(
            workspace_id, profile_id, run_id, source, cancel, sda_reader
        )
    if kind not in ("http_file", "http_record"):
        return _item(
            run_id,
            source,
            "blocked",
            reason_code=SDA_REASON,
            message="未知来源类型",
        )
    try:
        model = SyncSource.from_dict(source)
        if model.kind == "http_file":
            raw = _current_file(workspace_id, profile_id, model.name)
            current = {model.name: raw.data} if raw is not None else None
            fetch = sync_http_adapter.fetch_source(model, current=current)
        else:
            latest = _latest_record(workspace_id, profile_id, model.name)
            fetch = sync_http_adapter.fetch_record(model, latest=latest)
        if cancel.is_set():
            return _item(
                run_id,
                source,
                "canceled",
                reason_code="canceled",
                message="已取消，未提交",
                url=fetch.url,
            )
        if fetch.status == "skipped":
            return _item(
                run_id,
                source,
                "skipped",
                reason_code="disabled",
                message=fetch.message,
                url=fetch.url,
            )
        if fetch.status == "failed":
            return _item(
                run_id,
                source,
                "failed",
                reason_code=fetch.reason_code or "unknown",
                message=fetch.message,
                url=fetch.url,
            )
        base_sha = None
        if model.kind == "http_file":
            raw = _current_file(workspace_id, profile_id, model.name)
            base_sha = raw.sha256 if raw is not None else None
        committed = sync_commit.commit_fetch_result(
            workspace_id, profile_id, source, fetch, base_sha=base_sha
        )
        state = _commit_state(committed)
        reason = committed.get("reason_code")
        if state == "succeeded":
            reason = "validated"
        elif state == "unchanged":
            reason = "unchanged"
        elif state == "blocked":
            reason = reason or "target_missing"
        elif state == "failed":
            reason = reason or fetch.reason_code or "storage_failure"
        digest = fetch.items[0].sha256 if fetch.items else None
        return _item(
            run_id,
            source,
            state,
            reason_code=reason,
            message=committed.get("message") or fetch.message,
            url=fetch.url,
            sha256=digest,
            committed=any(
                item.get("status") == "success" for item in committed.get("items") or []
            ),
            byte_size=fetch.items[0].size if fetch.items else None,
        )
    except AppError as exc:
        return _item(
            run_id,
            source,
            "failed",
            reason_code=exc.code.lower() if exc.code in ("TOO_LARGE",) else "unknown",
            message=exc.message,
        )
    except Exception:
        logger.exception("同步来源执行异常：%s", source.get("source_id"))
        return _item(
            run_id,
            source,
            "failed",
            reason_code="unknown",
            message="来源执行失败（内部错误）",
        )


def _run_sda_source(
    workspace_id: str,
    profile_id: str,
    run_id: str,
    source: Dict[str, Any],
    cancel: threading.Event,
    sda_reader: Optional[Any],
) -> Dict[str, Any]:
    """SDA 路由：collector 只回安全 envelope，提交/候选仍由主应用决定。"""
    kind = str(source.get("kind") or "")
    if sda_reader is None:
        return _item(
            run_id,
            source,
            "blocked",
            reason_code=SDA_REASON,
            message="SDA collector 尚未接入；不伪造成功",
        )
    try:
        result = sda_reader(source, cancel)
        if not isinstance(result, dict):
            return _item(run_id, source, "failed", reason_code="protocol_mismatch", message="SDA 结果形状非法")
        if cancel.is_set():
            return _item(run_id, source, "canceled", reason_code="canceled", message="已取消，未提交")
        status = str(result.get("status") or "")
        reason = str(result.get("reason_code") or "unknown")
        if reason not in _SDA_REASONS:
            reason = "unknown"
        if status == "blocked":
            return _item(run_id, source, "blocked", reason_code=reason, message=result.get("message") or "SDA 阻断")
        if status != "succeeded":
            return _item(run_id, source, "failed", reason_code=reason, message=result.get("message") or "SDA 取数失败")
        if kind == "sda_catalog":
            return _item(
                run_id,
                source,
                "succeeded",
                reason_code="validated",
                message="目录已读取；匿名 catalog 不计为会话验证",
            )
        if kind == "sda_table":
            content = result.get("content")
            if not isinstance(content, (bytes, bytearray)):
                return _item(run_id, source, "failed", reason_code="invalid_payload", message="表内容缺失")
            body = bytes(content)
            digest = str(result.get("sha256") or hashlib.sha256(body).hexdigest())
            try:
                candidate = sync_candidates.create_candidate(
                    workspace_id,
                    profile_id,
                    source["source_id"],
                    int(source.get("revision") or 0),
                    digest,
                    "%s:%s" % (kind, source.get("resource_ref") or source["source_id"]),
                )
            except AppError as exc:
                return _item(run_id, source, "failed", reason_code=exc.code.lower() if exc.code == "TOO_LARGE" else "unknown", message=exc.message)
            return _item(
                run_id,
                source,
                "succeeded",
                reason_code="validated",
                message="表内容已生成候选",
                sha256=digest,
                byte_size=len(body),
                candidate_id=candidate["candidate_id"],
            )
        if kind != "sda_file":
            return _item(run_id, source, "blocked", reason_code="protocol_mismatch", message="不支持的 SDA 来源")

        raw_items = result.get("items")
        if not isinstance(raw_items, (list, tuple)) or not raw_items:
            content = result.get("content")
            raw_items = [
                {
                    "name": source.get("name"),
                    "content": content,
                    "sha256": result.get("sha256"),
                    "size": len(content) if isinstance(content, (bytes, bytearray)) else None,
                }
            ]
        fetched = []
        for entry in raw_items:
            if not isinstance(entry, dict) or not isinstance(entry.get("content"), (bytes, bytearray)):
                return _item(run_id, source, "failed", reason_code="invalid_payload", message="SDA 文件内容缺失")
            body = bytes(entry["content"])
            fetched.append(
                HttpFetchItem(
                    basename=str(entry.get("name") or source.get("name") or "file"),
                    content=body,
                    size=len(body),
                    sha256=str(entry.get("sha256") or hashlib.sha256(body).hexdigest()),
                )
            )
        fetch = HttpFetchResult(
            kind="http_file",
            status="succeeded",
            source_id=source["source_id"],
            profile_id=profile_id,
            message=result.get("message") or "SDA 文件已读取",
            items=tuple(fetched),
        )
        model = SyncSource.from_dict(source)
        raw = _current_file(workspace_id, profile_id, model.name)
        committed = sync_commit.commit_fetch_result(
            workspace_id,
            profile_id,
            source,
            fetch,
            base_sha=raw.sha256 if raw is not None else None,
        )
        state = _commit_state(committed)
        reason_code = committed.get("reason_code")
        if state == "succeeded":
            reason_code = "validated"
        elif state == "unchanged":
            reason_code = "unchanged"
        elif state == "blocked":
            reason_code = reason_code or "target_missing"
        else:
            reason_code = reason_code or "storage_failure"
        return _item(
            run_id,
            source,
            state,
            reason_code=reason_code,
            message=committed.get("message") or fetch.message,
            sha256=fetched[0].sha256,
            committed=any(item.get("status") == "success" for item in committed.get("items") or []),
            byte_size=fetched[0].size,
        )
    except AppError as exc:
        return _item(
            run_id,
            source,
            "failed",
            reason_code="too_large" if exc.code == "TOO_LARGE" else "unknown",
            message=exc.message,
        )
    except Exception:
        logger.exception("SDA 来源执行异常：%s", source.get("source_id"))
        return _item(run_id, source, "failed", reason_code="unknown", message="SDA 来源执行失败（内部错误）")


def _commit_state(committed: Dict[str, Any]) -> str:
    status = committed.get("status")
    if status == "succeeded":
        items = committed.get("items") or []
        if items and all(item.get("status") == "unchanged" for item in items):
            return "unchanged"
        return "succeeded"
    if status == "partial":
        return "failed"
    if status == "blocked":
        return "blocked"
    return "failed"


def _current_file(workspace_id: str, profile_id: str, name: str):
    with storage.use_workspace(workspace_id), storage.use_profile(profile_id):
        try:
            return documents.load_raw(profile_id, name, "current")
        except AppError:
            return None


def _latest_record(workspace_id: str, profile_id: str, name: str) -> Optional[bytes]:
    with storage.use_workspace(workspace_id), storage.use_profile(profile_id):
        return updates._latest_record_bytes(name)


def _item(
    run_id: str,
    source: Dict[str, Any],
    state: str,
    reason_code: Optional[str] = None,
    message: str = "",
    url: Optional[str] = None,
    sha256: Optional[str] = None,
    committed: bool = False,
    byte_size: Optional[int] = None,
    candidate_id: Optional[str] = None,
    stage: str = "finished",
) -> Dict[str, Any]:
    item = SyncItem(
        item_id=make_item_id(run_id, source["source_id"], source["profile_id"]),
        run_id=run_id,
        source_id=source["source_id"],
        workspace_id=source["workspace_id"],
        profile_id=source["profile_id"],
        state=state,
        stage=stage,
        reason_code=reason_code,
        using_last_good=False,
        last_verified_at=_now() if state in ("succeeded", "unchanged") else None,
        candidate_id=candidate_id,
    )
    payload = item.to_dict()
    payload.update(
        {
            "name": source.get("name"),
            "status": state,
            "reason": _safe_message(message),
            "at": _now(),
            "url": url,
            "committed": committed,
        }
    )
    if sha256:
        payload["sha256"] = sha256
    if byte_size is not None:
        payload["bytes"] = byte_size
    return payload


def _task_item(source: Dict[str, Any], item: Dict[str, Any]) -> Dict[str, Any]:
    """现有任务条目的展示字段；同步状态保留在 `state`，`status` 供旧判据聚合。"""
    return {
        "name": source.get("name"),
        "status": item["state"],
        "label": item.get("reason") or item["state"],
        "reason": item.get("reason"),
        "at": item.get("at"),
        "source_id": item.get("source_id"),
        "item_id": item.get("item_id"),
        "stage": item.get("stage"),
        "reason_code": item.get("reason_code"),
        "url": item.get("url"),
        "sha256": item.get("sha256"),
        "committed": bool(item.get("committed")),
    }


def _append_item_event(workspace_id: str, run_id: str, item: Dict[str, Any]) -> None:
    payload = {
        "level": "error" if item["state"] in ("failed", "blocked") else "info",
        "stage": "finished",
        "source_id": item["source_id"],
        "item_id": item["item_id"],
        "status": item["state"],
        "message": _safe_message(item.get("reason") or item["state"]),
    }
    if item.get("reason_code"):
        payload["reason_code"] = item["reason_code"]
    if item.get("sha256"):
        payload["sha256"] = item["sha256"]
    if item.get("bytes") is not None:
        payload["bytes"] = item["bytes"]
    try:
        sync_run_store.append_event(workspace_id, run_id, payload)
    except AppError:
        logger.warning("同步 run 事件写入失败：%s", run_id, exc_info=True)
