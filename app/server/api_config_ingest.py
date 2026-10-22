"""观测采集接口（M6-07 / M6-08；INH-1952、INH-1953）。

* `POST /config/observations` —— 上传一份现场文件，落成不可变观测（原文字节 + 映射结论）；
* `GET  /config/observations` —— 按机型/逻辑文件/状态列观测；
* `GET  /config/observations/{oid}` —— 详情（含 `r1` 与重新映射后的当前修订）；
* `GET  /config/observations/{oid}/raw` —— 按字节取回原始文件（永远是最初那份）；
* `POST /config/observations/{oid}/remap` —— 用当前定义重算映射，**追加修订**；
* `GET  /config/ingest/summary` —— 采集概览。

权限：读要 `config_read`，写要 `config_registry_write`（配置域写动作）。采集不新造
权限键——写观测和写参数主档都是"改配置域的受管数据"，共用同一档，避免权限矩阵
每加一张卡就膨胀一格。
"""

from __future__ import annotations

from functools import partial
from typing import Any, Dict, List, Optional

from ..config import Settings
from ..core.errors import INVALID_INPUT, AppError
from ..services import authz, config_gate, config_ingest, config_store
from ..services.context import RequestContext
from .api_documents import content_disposition
from .router import Response, Router, json_body, query_param

#: 上传体积上限与旧上传接口一致（同一个 `upload_max_bytes`），不另开口子。
OCTET = "application/octet-stream"


def _read(ctx: RequestContext) -> None:
    config_gate.require_read()
    authz.authorize(ctx, "config_read")


def _write(ctx: RequestContext) -> None:
    """先授权后门禁：未授权身份拿到 403，不泄露特性档位。"""
    authz.authorize(ctx, "config_registry_write")
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


def create_observation(
    ctx: RequestContext,
    params: Dict[str, str],
    query: Dict[str, List[str]],
    body: Optional[bytes],
    settings: Settings,
) -> Response:
    # POST /api/v1/config/observations?profile_id=&filename=&capture_kind=…
    # 正文是原始字节；文件名既可以走查询串也可以走 `X-Filename` 头
    _write(ctx)
    profile_id = _optional(query, "profile_id")
    if not profile_id:
        raise AppError(INVALID_INPUT, "缺少 profile_id", field="profile_id")
    filename = _optional(query, "filename") or str(
        (ctx.headers or {}).get("x-filename") or ""
    ).strip()
    raw = body or b""
    if len(raw) > settings.upload_max_bytes:
        from ..core.errors import TOO_LARGE

        raise AppError(
            TOO_LARGE,
            "文件超过上传上限",
            limit=settings.upload_max_bytes,
            actual=len(raw),
        )
    item = config_ingest.record_capture(
        _repo(settings),
        ctx.actor,
        profile_id=profile_id,
        content=raw,
        capture_kind=_optional(query, "capture_kind") or config_ingest.CAPTURE_MANUAL,
        filename=filename,
        logical_file_id=_optional(query, "logical_file_id"),
        release_id=_optional(query, "release_id"),
        variant_id=_optional(query, "variant_id"),
        device_id=_optional(query, "device_id"),
        source_name=_optional(query, "source_name"),
        captured_at=_optional(query, "captured_at"),
        require_release=_optional(query, "require_release") in ("1", "true"),
        idempotency_key=_idempotency(ctx),
    )
    return Response(status=201, payload={"item": item})


def list_observations(
    ctx: RequestContext,
    params: Dict[str, str],
    query: Dict[str, List[str]],
    body: Optional[bytes],
    settings: Settings,
) -> Response:
    # GET /api/v1/config/observations?profile_id=&logical_file_id=&status=&limit=
    _read(ctx)
    limit = _optional(query, "limit")
    if limit is not None and not str(limit).isdigit():
        raise AppError(INVALID_INPUT, "limit 必须是正整数", field="limit")
    items = config_ingest.list_observations(
        _repo(settings),
        profile_id=_optional(query, "profile_id"),
        logical_file_id=_optional(query, "logical_file_id"),
        status=_optional(query, "status"),
        limit=int(limit) if limit else None,
    )
    return Response(payload={"items": items, "total": len(items)})


def get_observation(
    ctx: RequestContext,
    params: Dict[str, str],
    query: Dict[str, List[str]],
    body: Optional[bytes],
    settings: Settings,
) -> Response:
    # GET /api/v1/config/observations/{oid}
    _read(ctx)
    repo = _repo(settings)
    item = config_ingest.get_observation(repo, params["oid"])
    revisions = repo.history("observation", params["oid"])
    return Response(
        payload={
            "item": item,
            "revisions": revisions,
            # `r1` 永远是采集那一刻的结论：重新映射不会改写它
            "original": (
                config_ingest.get_observation(repo, params["oid"], revisions[0])
                if revisions
                else None
            ),
        }
    )


def get_observation_raw(
    ctx: RequestContext,
    params: Dict[str, str],
    query: Dict[str, List[str]],
    body: Optional[bytes],
    settings: Settings,
) -> Response:
    # GET /api/v1/config/observations/{oid}/raw
    _read(ctx)
    repo = _repo(settings)
    item = config_ingest.get_observation(repo, params["oid"])
    raw = config_ingest.observation_bytes(repo, params["oid"])
    return Response(
        body=raw,
        content_type=OCTET,
        headers={
            "Content-Disposition": content_disposition(
                str(item.get("filename") or "observation.dat")
            ),
            "X-Content-Sha256": str(item["blob_sha256"]),
            "ETag": '"%s"' % item["blob_sha256"],
            "Cache-Control": "no-cache",
        },
    )


def remap_observation(
    ctx: RequestContext,
    params: Dict[str, str],
    query: Dict[str, List[str]],
    body: Optional[bytes],
    settings: Settings,
) -> Response:
    # POST /api/v1/config/observations/{oid}/remap  {"release_id","variant_id","reason","expected_revision"}
    _write(ctx)
    payload: Dict[str, Any] = json_body(body)
    item = config_ingest.remap_observation(
        _repo(settings),
        ctx.actor,
        observation_id=params["oid"],
        release_id=payload.get("release_id"),
        variant_id=payload.get("variant_id"),
        expected_revision=payload.get("expected_revision"),
        reason=payload.get("reason"),
        idempotency_key=_idempotency(ctx),
    )
    return Response(status=201, payload={"item": item})


def get_summary(
    ctx: RequestContext,
    params: Dict[str, str],
    query: Dict[str, List[str]],
    body: Optional[bytes],
    settings: Settings,
) -> Response:
    # GET /api/v1/config/ingest/summary?profile_id=
    _read(ctx)
    payload = config_ingest.ingest_summary(
        _repo(settings), _optional(query, "profile_id")
    )
    payload["writable"] = config_gate.mode() == config_gate.MODE_ON
    payload["mode"] = config_gate.mode()
    return Response(payload=payload)


def register(router: Router, settings: Settings) -> None:
    router.add(
        "GET",
        "/api/v1/config/observations",
        partial(list_observations, settings=settings),
    )
    router.add(
        "POST",
        "/api/v1/config/observations",
        partial(create_observation, settings=settings),
    )
    router.add(
        "GET",
        "/api/v1/config/observations/{oid}",
        partial(get_observation, settings=settings),
    )
    router.add(
        "GET",
        "/api/v1/config/observations/{oid}/raw",
        partial(get_observation_raw, settings=settings),
    )
    router.add(
        "POST",
        "/api/v1/config/observations/{oid}/remap",
        partial(remap_observation, settings=settings),
    )
    router.add(
        "GET", "/api/v1/config/ingest/summary", partial(get_summary, settings=settings)
    )
