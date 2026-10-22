"""参数登记接口：参数主档、定义修订与定义集合（M6-04 / INH-1949）。

handler 只做门禁 → 授权 → 调 `services.config_catalog` → 组装响应。四个要点：

* **四种检索维度**：`GET /config/parameters?q=` 同时匹配用户名称 / 旧名（别名）/
  XML 键 / JSON 键，并回 `matched` 说明命中哪一维；
* **写入必带理由**：`reason` 缺省时服务层会生成一条"重命名 / 追加定义"的说明，
  审计里不出现空理由的写操作；
* **`expected_revision` 一路带到仓储**：过期即 409 `REVISION_CONFLICT`，
  前端保留草稿（M5-11 已实现），因此并发编辑不会静默覆盖；
* **定义修订只追加**：`POST /definitions` 永远新建 `r<n>`，从不原地改。
"""

from __future__ import annotations

from functools import partial
from typing import Any, Dict, List, Optional

from ..config import Settings
from ..core.errors import INVALID_INPUT, AppError
from ..services import authz, config_catalog, config_gate, config_store
from ..services.context import RequestContext
from .router import Response, Router, json_body, query_param


def _repo(settings: Settings):
    return config_store.repository(settings)


def _read(ctx: RequestContext) -> None:
    config_gate.require_read()
    authz.authorize(ctx, "config_read")


def _write(ctx: RequestContext) -> None:
    """先授权后门禁（与目录接口同口径，避免泄露特性档位）。"""
    authz.authorize(ctx, "config_registry_write")
    config_gate.require_write()


def _flag(query: Dict[str, List[str]], key: str, default: bool = True) -> bool:
    raw = query_param(query, key)
    if not raw:
        return default
    return raw not in ("0", "false", "no")


def _idempotency(ctx: RequestContext) -> Optional[str]:
    headers = ctx.headers or {}
    key = str(headers.get("idempotency-key") or "").strip()
    if not key:
        return None
    if len(key) > 128:
        raise AppError(INVALID_INPUT, "Idempotency-Key 过长（上限 128 字符）")
    return key


# -- 参数主档 ---------------------------------------------------------------


def list_parameters(
    ctx: RequestContext,
    params: Dict[str, str],
    query: Dict[str, List[str]],
    body: Optional[bytes],
    settings: Settings,
) -> Response:
    # GET /api/v1/config/parameters?q=&family_id=&include_retired=0
    _read(ctx)
    repo = _repo(settings)
    items = config_catalog.search_parameters(
        repo,
        query_param(query, "q"),
        family_id=query_param(query, "family_id") or None,
        include_retired=_flag(query, "include_retired"),
    )
    return Response(
        payload={
            "items": items,
            "query": query_param(query, "q"),
            "dimensions": ["name", "alias", "xml", "json"],
        }
    )


def create_parameter(
    ctx: RequestContext,
    params: Dict[str, str],
    query: Dict[str, List[str]],
    body: Optional[bytes],
    settings: Settings,
) -> Response:
    # POST /api/v1/config/parameters  {"family_id","name","category","notes"}
    _write(ctx)
    payload = json_body(body)
    item = config_catalog.create_parameter(
        _repo(settings),
        ctx.actor,
        family_id=str(payload.get("family_id") or ""),
        name=payload.get("name"),
        category=payload.get("category"),
        notes=payload.get("notes"),
        idempotency_key=_idempotency(ctx),
    )
    return Response(status=201, payload={"item": item})


def get_parameter(
    ctx: RequestContext,
    params: Dict[str, str],
    query: Dict[str, List[str]],
    body: Optional[bytes],
    settings: Settings,
) -> Response:
    # GET /api/v1/config/parameters/{pmid}
    _read(ctx)
    repo = _repo(settings)
    parameter = config_catalog.get_parameter(repo, params["pmid"])
    return Response(
        payload={
            "item": parameter,
            "definitions": config_catalog.list_definitions(repo, parameter["id"]),
            "current": config_catalog.current_definition(repo, parameter["id"]),
        }
    )


def patch_parameter(
    ctx: RequestContext,
    params: Dict[str, str],
    query: Dict[str, List[str]],
    body: Optional[bytes],
    settings: Settings,
) -> Response:
    # PATCH /api/v1/config/parameters/{pmid}  {"name","status","reason","expected_revision"}
    _write(ctx)
    payload = json_body(body)
    repo = _repo(settings)
    parameter = config_catalog.get_parameter(repo, params["pmid"])
    reason = payload.get("reason")
    expected = payload.get("expected_revision") or None

    changed = []
    if "name" in payload and str(payload.get("name") or "").strip():
        if str(payload["name"]).strip() != parameter.get("name"):
            parameter = config_catalog.rename_parameter(
                repo,
                ctx.actor,
                parameter_id=parameter["id"],
                new_name=payload["name"],
                reason=reason,
                idempotency_key=_idempotency(ctx),
                expected_revision=expected,
            )
            changed.append("name")
    if "status" in payload and payload.get("status"):
        if str(payload["status"]) != parameter.get("status"):
            parameter = config_catalog.set_parameter_status(
                repo,
                ctx.actor,
                parameter_id=parameter["id"],
                status=str(payload["status"]),
                reason=reason,
                idempotency_key=_idempotency(ctx),
                expected_revision=expected,
            )
            changed.append("status")
    if not changed:
        # 没有任何实际变化：不写库、不产生审计（幂等的空操作不该留痕）
        return Response(payload={"item": parameter, "changed": []})
    return Response(payload={"item": parameter, "changed": changed})


# -- 定义修订 ---------------------------------------------------------------


def list_definitions(
    ctx: RequestContext,
    params: Dict[str, str],
    query: Dict[str, List[str]],
    body: Optional[bytes],
    settings: Settings,
) -> Response:
    # GET /api/v1/config/parameters/{pmid}/definitions
    _read(ctx)
    repo = _repo(settings)
    parameter = config_catalog.get_parameter(repo, params["pmid"])
    return Response(
        payload={
            "items": config_catalog.list_definitions(repo, parameter["id"]),
            "current": config_catalog.current_definition(repo, parameter["id"]),
        }
    )


def create_definition(
    ctx: RequestContext,
    params: Dict[str, str],
    query: Dict[str, List[str]],
    body: Optional[bytes],
    settings: Settings,
) -> Response:
    # POST /api/v1/config/parameters/{pmid}/definitions
    # {"xml_name","xml_loc","json_key","json_pointer","type","unit","constraints","remarks",
    #  "reason","expected_definition_id"}
    _write(ctx)
    payload = json_body(body)
    expected_definition_id = None
    if "expected_definition_id" in payload:
        # 显式传 null / "" 表示"我看到的时候还没有定义"，与"不校验"是两件事
        expected_definition_id = str(payload.get("expected_definition_id") or "")
    item = config_catalog.define_parameter(
        _repo(settings),
        ctx.actor,
        parameter_id=params["pmid"],
        xml_name=payload.get("xml_name"),
        xml_loc=payload.get("xml_loc"),
        json_key=payload.get("json_key"),
        json_pointer=payload.get("json_pointer"),
        value_type=str(payload.get("type") or "decimal"),
        unit=payload.get("unit"),
        constraints=payload.get("constraints"),
        remarks=payload.get("remarks"),
        idempotency_key=_idempotency(ctx),
        expected_definition_id=expected_definition_id,
    )
    return Response(status=201, payload={"item": item})


def retire_definition(
    ctx: RequestContext,
    params: Dict[str, str],
    query: Dict[str, List[str]],
    body: Optional[bytes],
    settings: Settings,
) -> Response:
    # POST /api/v1/config/definitions/{did}/retire  {"reason"}
    _write(ctx)
    payload = json_body(body)
    item = config_catalog.retire_definition(
        _repo(settings),
        ctx.actor,
        definition_id=params["did"],
        reason=payload.get("reason"),
        idempotency_key=_idempotency(ctx),
    )
    return Response(payload={"item": item})


def verify_definition(
    ctx: RequestContext,
    params: Dict[str, str],
    query: Dict[str, List[str]],
    body: Optional[bytes],
    settings: Settings,
) -> Response:
    # POST /api/v1/config/definitions/{did}/verify?snapshot_id=
    _write(ctx)
    snapshot_id = query_param(query, "snapshot_id")
    if not snapshot_id:
        raise AppError(INVALID_INPUT, "缺少 snapshot_id")
    repo = _repo(settings)
    definition = config_catalog.get_definition(repo, params["did"])
    report = config_catalog.verify_definition_against_template(repo, definition, snapshot_id)
    return Response(payload={"item": report})


# -- 定义集合 ---------------------------------------------------------------


def list_definition_sets(
    ctx: RequestContext,
    params: Dict[str, str],
    query: Dict[str, List[str]],
    body: Optional[bytes],
    settings: Settings,
) -> Response:
    # GET /api/v1/config/definition-sets?release_id=&variant_id=
    _read(ctx)
    repo = _repo(settings)
    release_id = query_param(query, "release_id") or None
    variant_id = query_param(query, "variant_id") or None
    items = config_catalog.list_definition_sets(
        repo, release_id=release_id, variant_id=variant_id
    )
    latest = (
        config_catalog.latest_definition_set(repo, release_id, variant_id)
        if release_id
        else None
    )
    return Response(
        payload={"items": items, "latest_id": latest["id"] if latest else None}
    )


def create_definition_set(
    ctx: RequestContext,
    params: Dict[str, str],
    query: Dict[str, List[str]],
    body: Optional[bytes],
    settings: Settings,
) -> Response:
    # POST /api/v1/config/definition-sets  {"release_id","variant_id","definition_ids","note"}
    _write(ctx)
    payload = json_body(body)
    item = config_catalog.create_definition_set(
        _repo(settings),
        ctx.actor,
        release_id=str(payload.get("release_id") or ""),
        variant_id=payload.get("variant_id"),
        definition_ids=payload.get("definition_ids"),
        note=payload.get("note"),
        idempotency_key=_idempotency(ctx),
    )
    return Response(status=201, payload={"item": item})


def get_definition_set(
    ctx: RequestContext,
    params: Dict[str, str],
    query: Dict[str, List[str]],
    body: Optional[bytes],
    settings: Settings,
) -> Response:
    # GET /api/v1/config/definition-sets/{dsid}
    _read(ctx)
    return Response(payload=config_catalog.definition_set_detail(_repo(settings), params["dsid"]))


def register(router: Router, settings: Settings) -> None:
    """挂载参数登记路由（在目录路由之后，路径互不重叠）。"""
    router.add(
        "GET", "/api/v1/config/parameters", partial(list_parameters, settings=settings)
    )
    router.add(
        "POST", "/api/v1/config/parameters", partial(create_parameter, settings=settings)
    )
    router.add(
        "GET",
        "/api/v1/config/parameters/{pmid}/definitions",
        partial(list_definitions, settings=settings),
    )
    router.add(
        "POST",
        "/api/v1/config/parameters/{pmid}/definitions",
        partial(create_definition, settings=settings),
    )
    router.add(
        "GET", "/api/v1/config/parameters/{pmid}", partial(get_parameter, settings=settings)
    )
    router.add(
        "PATCH", "/api/v1/config/parameters/{pmid}", partial(patch_parameter, settings=settings)
    )
    router.add(
        "POST",
        "/api/v1/config/definitions/{did}/retire",
        partial(retire_definition, settings=settings),
    )
    router.add(
        "POST",
        "/api/v1/config/definitions/{did}/verify",
        partial(verify_definition, settings=settings),
    )
    router.add(
        "GET",
        "/api/v1/config/definition-sets",
        partial(list_definition_sets, settings=settings),
    )
    router.add(
        "POST",
        "/api/v1/config/definition-sets",
        partial(create_definition_set, settings=settings),
    )
    router.add(
        "GET",
        "/api/v1/config/definition-sets/{dsid}",
        partial(get_definition_set, settings=settings),
    )
