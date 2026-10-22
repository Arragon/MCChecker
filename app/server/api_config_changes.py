"""变更单接口（M6-11 / INH-1956）。

* `GET  /p/{pid}/config/changes` —— 列表（`?state=&baseline_id=`）
* `POST /p/{pid}/config/changes` —— 建草稿
* `GET  /p/{pid}/config/changes/{cid}` —— 详情（逐项改动 + 过期判定 + 可走的边）
* `POST /p/{pid}/config/changes/{cid}` —— 改草稿（追加修订）
* `POST /p/{pid}/config/changes/{cid}/submit` —— 提交（冻结）
* `POST /p/{pid}/config/changes/{cid}/withdraw` —— 撤回（终态）
* `POST /p/{pid}/config/changes/{cid}/reopen` —— 重开一版草稿

批准发布（`/approve`）属 M6-12，放在 `api_config_approval.py`，因为它要的能力键
不同（`config_change_approve` vs `config_change_submit`）——同一文件里两套门禁
最容易写错。

路径带 `{pid}` 是因为变更单是"某个机型要发什么"的读模型；对象本身带 `profile_id`，
这里只做**一致性校验**：拿 A 机型的路径去操作 B 机型的单，直接 404。
"""

from __future__ import annotations

from functools import partial
from typing import Any, Dict, List, Optional

from ..config import Settings
from ..core.errors import INVALID_INPUT, NOT_FOUND, AppError
from ..services import authz, config_changes, config_gate, config_store
from ..services.context import RequestContext
from .router import Response, Router, json_body, query_param


def _read(ctx: RequestContext) -> None:
    config_gate.require_read()
    authz.authorize(ctx, "config_read")


def _write(ctx: RequestContext) -> None:
    """先授权后门禁：未授权身份拿到 403，不泄露特性档位。"""
    authz.authorize(ctx, "config_change_submit")
    config_gate.require_write()


def _repo(settings: Settings):
    return config_store.repository(settings)


def _idempotency(ctx: RequestContext) -> Optional[str]:
    key = str((ctx.headers or {}).get("idempotency-key") or "").strip()
    if not key:
        return None
    if len(key) > 128:
        raise AppError(INVALID_INPUT, "Idempotency-Key 过长（上限 128 字符）")
    return key


def _optional(query: Dict[str, List[str]], name: str) -> Optional[str]:
    value = query_param(query, name)
    return value if value else None


def _same_profile(item: Dict[str, Any], pid: str) -> Dict[str, Any]:
    """路径里的机型与对象上的机型必须一致，否则当作不存在。

    例外是**通用层**变更（`profile_id` 为空）：它不属于任何机型，但一改就影响
    所有继承它的机型，因此在任一机型路径下都可见、可操作。权限仍是管理员那一层，
    路径只是"从哪个机型看过去"的坐标。
    """
    owner = str(item.get("profile_id") or "")
    if owner and owner != str(pid):
        raise AppError(NOT_FOUND, "变更单不存在", change_id=item.get("id"))
    return item


def list_changes(
    ctx: RequestContext,
    params: Dict[str, str],
    query: Dict[str, List[str]],
    body: Optional[bytes],
    settings: Settings,
) -> Response:
    # GET /api/v1/p/{pid}/config/changes?state=&baseline_id=
    _read(ctx)
    items = config_changes.list_change_sets(
        _repo(settings),
        profile_id=params["pid"],
        baseline_id=_optional(query, "baseline_id"),
        state=_optional(query, "state"),
        include_common=True,
    )
    return Response(
        payload={
            "items": items,
            "total": len(items),
            "states": list(config_changes.STATES),
            "decisions": list(config_changes.DECISIONS),
            "algorithms": list(config_changes.ALGORITHMS),
        }
    )


def create_change(
    ctx: RequestContext,
    params: Dict[str, str],
    query: Dict[str, List[str]],
    body: Optional[bytes],
    settings: Settings,
) -> Response:
    # POST /api/v1/p/{pid}/config/changes
    #   {"baseline_id","changes":[{parameter_id,to,decision,reason}],"title","reason",
    #    "logical_file_id","observation_id","algorithm","expected_baseline_revision"}
    _write(ctx)
    payload: Dict[str, Any] = json_body(body)
    repo = _repo(settings)
    item = config_changes.create_change_set(
        repo,
        ctx.actor,
        baseline_id=payload.get("baseline_id"),
        changes=payload.get("changes"),
        title=payload.get("title"),
        reason=payload.get("reason"),
        logical_file_id=payload.get("logical_file_id"),
        observation_id=payload.get("observation_id"),
        algorithm=payload.get("algorithm"),
        expected_baseline_revision=payload.get("expected_baseline_revision"),
        idempotency_key=_idempotency(ctx),
    )
    _same_profile(item, params["pid"])
    return Response(status=201, payload={"item": item})


def get_change(
    ctx: RequestContext,
    params: Dict[str, str],
    query: Dict[str, List[str]],
    body: Optional[bytes],
    settings: Settings,
) -> Response:
    # GET /api/v1/p/{pid}/config/changes/{cid}
    _read(ctx)
    repo = _repo(settings)
    detail = config_changes.detail(repo, params["cid"])
    _same_profile(detail["item"], params["pid"])
    return Response(payload=detail)


def update_change(
    ctx: RequestContext,
    params: Dict[str, str],
    query: Dict[str, List[str]],
    body: Optional[bytes],
    settings: Settings,
) -> Response:
    # POST /api/v1/p/{pid}/config/changes/{cid}
    #   {"changes"?,"title"?,"reason"?,"expected_revision"?}
    _write(ctx)
    payload: Dict[str, Any] = json_body(body)
    repo = _repo(settings)
    item = config_changes.update_change_set(
        repo,
        ctx.actor,
        change_id=params["cid"],
        changes=payload.get("changes"),
        title=payload.get("title"),
        reason=payload.get("reason"),
        expected_revision=payload.get("expected_revision"),
        idempotency_key=_idempotency(ctx),
    )
    _same_profile(item, params["pid"])
    return Response(status=201, payload={"item": item})


def submit_change(
    ctx: RequestContext,
    params: Dict[str, str],
    query: Dict[str, List[str]],
    body: Optional[bytes],
    settings: Settings,
) -> Response:
    # POST /api/v1/p/{pid}/config/changes/{cid}/submit  {"reason","expected_revision"}
    _write(ctx)
    payload: Dict[str, Any] = json_body(body) if body else {}
    repo = _repo(settings)
    item = config_changes.submit_change_set(
        repo,
        ctx.actor,
        change_id=params["cid"],
        reason=payload.get("reason"),
        expected_revision=payload.get("expected_revision"),
        idempotency_key=_idempotency(ctx),
    )
    _same_profile(item, params["pid"])
    return Response(status=201, payload={"item": item})


def withdraw_change(
    ctx: RequestContext,
    params: Dict[str, str],
    query: Dict[str, List[str]],
    body: Optional[bytes],
    settings: Settings,
) -> Response:
    # POST /api/v1/p/{pid}/config/changes/{cid}/withdraw  {"reason"}
    _write(ctx)
    payload: Dict[str, Any] = json_body(body) if body else {}
    repo = _repo(settings)
    item = config_changes.withdraw_change_set(
        repo,
        ctx.actor,
        change_id=params["cid"],
        reason=payload.get("reason"),
        expected_revision=payload.get("expected_revision"),
        idempotency_key=_idempotency(ctx),
    )
    _same_profile(item, params["pid"])
    return Response(status=201, payload={"item": item})


def reopen_change(
    ctx: RequestContext,
    params: Dict[str, str],
    query: Dict[str, List[str]],
    body: Optional[bytes],
    settings: Settings,
) -> Response:
    # POST /api/v1/p/{pid}/config/changes/{cid}/reopen  {"reason"}
    _write(ctx)
    payload: Dict[str, Any] = json_body(body) if body else {}
    repo = _repo(settings)
    item = config_changes.reopen_change_set(
        repo,
        ctx.actor,
        change_id=params["cid"],
        reason=payload.get("reason"),
        expected_revision=payload.get("expected_revision"),
        idempotency_key=_idempotency(ctx),
    )
    _same_profile(item, params["pid"])
    return Response(status=201, payload={"item": item})


def register(router: Router, settings: Settings) -> None:
    router.add(
        "GET",
        "/api/v1/p/{pid}/config/changes",
        partial(list_changes, settings=settings),
    )
    router.add(
        "POST",
        "/api/v1/p/{pid}/config/changes",
        partial(create_change, settings=settings),
    )
    router.add(
        "GET",
        "/api/v1/p/{pid}/config/changes/{cid}",
        partial(get_change, settings=settings),
    )
    router.add(
        "POST",
        "/api/v1/p/{pid}/config/changes/{cid}",
        partial(update_change, settings=settings),
    )
    router.add(
        "POST",
        "/api/v1/p/{pid}/config/changes/{cid}/submit",
        partial(submit_change, settings=settings),
    )
    router.add(
        "POST",
        "/api/v1/p/{pid}/config/changes/{cid}/withdraw",
        partial(withdraw_change, settings=settings),
    )
    router.add(
        "POST",
        "/api/v1/p/{pid}/config/changes/{cid}/reopen",
        partial(reopen_change, settings=settings),
    )
