"""配置目录接口：软件版本、出口批次、配置族、逻辑文件与快照（M6-01 / M6-02）。

按 `docs/MCChecker_M5_M8_PLAN.md` §4 的落点，配置域的 HTTP 入口按域拆成多个
`api_config_*.py`，本文件负责**目录**（版本/族/逻辑文件/快照/变体）。

handler 只做四件事：门禁 → 授权 → 调 `services.config_catalog` → 组装 `Response`。
所有校验、事务、不可变语义都在 service 层，这里不碰仓储细节。

约定：

* 读接口 `config_read`（任何人），写接口 `config_registry_write`（管理员）；
* 写接口在 `off` / `readonly` 下都被 `config_gate.require_write()` 挡成 503；
* 快照上传是原文字节（`Content-Type: application/octet-stream`），
  原始文件名走 `X-Filename`（HTTP 层已解析到 `ctx.filename`）；
* 错误由 `http.py` 统一翻译成 `{error: {code, message, detail}}`。
"""

from __future__ import annotations

from functools import partial
from typing import Any, Dict, List, Optional

from ..config import Settings
from ..core.errors import INVALID_INPUT, AppError
from ..services import authz, config_catalog, config_gate, config_lifecycle, config_store
from ..services.context import RequestContext
from .api_documents import content_disposition
from .router import Response, Router, json_body, query_param


def _repo(settings: Settings):
    return config_store.repository(settings)


def _write(ctx: RequestContext) -> None:
    """写接口的统一前置：**先授权、后门禁**。

    顺序是有意的：未授权身份不该靠探测得知"配置域是否开启"，因此权限先判；
    有权但档位不足者才拿到 503 `CONFIG_DISABLED`（`detail.mode` 说明原因）。
    """
    authz.authorize(ctx, "config_registry_write")
    config_gate.require_write()


# -- 软件版本 ---------------------------------------------------------------


def list_releases(
    ctx: RequestContext,
    params: Dict[str, str],
    query: Dict[str, List[str]],
    body: Optional[bytes],
    settings: Settings,
) -> Response:
    # GET /api/v1/config/releases
    config_gate.require_read()
    authz.authorize(ctx, "config_read")
    repo = _repo(settings)
    return Response(payload={"items": config_catalog.list_releases(repo)})


def create_release(
    ctx: RequestContext,
    params: Dict[str, str],
    query: Dict[str, List[str]],
    body: Optional[bytes],
    settings: Settings,
) -> Response:
    # POST /api/v1/config/releases  {"version_label","build","predecessor_id","source","notes"}
    _write(ctx)
    payload = json_body(body)
    item = config_catalog.register_release(
        _repo(settings),
        ctx.actor,
        version_label=payload.get("version_label"),
        build=payload.get("build"),
        predecessor_id=payload.get("predecessor_id"),
        source=str(payload.get("source") or config_catalog.SOURCE_MANUAL),
        notes=payload.get("notes"),
        idempotency_key=_idempotency(ctx),
    )
    return Response(status=201, payload={"item": item})


def get_release(
    ctx: RequestContext,
    params: Dict[str, str],
    query: Dict[str, List[str]],
    body: Optional[bytes],
    settings: Settings,
) -> Response:
    # GET /api/v1/config/releases/{rid}
    config_gate.require_read()
    authz.authorize(ctx, "config_read")
    return Response(payload=config_catalog.release_detail(_repo(settings), params["rid"]))


def publish_default_batch(
    ctx: RequestContext,
    params: Dict[str, str],
    query: Dict[str, List[str]],
    body: Optional[bytes],
    settings: Settings,
) -> Response:
    # POST /api/v1/config/releases/{rid}/default  {"snapshot_ids","note","supersedes"}
    _write(ctx)
    payload = json_body(body)
    repo = _repo(settings)
    expected: Dict[str, Any] = {}
    if payload.get("expected_seq") is not None:
        expected["seq"] = int(payload["expected_seq"])
    item = config_catalog.publish_default_batch(
        repo,
        ctx.actor,
        release_id=params["rid"],
        snapshot_ids=payload.get("snapshot_ids"),
        note=payload.get("note"),
        supersedes=payload.get("supersedes"),
        idempotency_key=_idempotency(ctx),
        expected=expected or None,
    )
    return Response(status=201, payload={"item": item})


def retire_release(
    ctx: RequestContext,
    params: Dict[str, str],
    query: Dict[str, List[str]],
    body: Optional[bytes],
    settings: Settings,
) -> Response:
    # POST /api/v1/config/releases/{rid}/retire  {"reason"}
    _write(ctx)
    payload = json_body(body)
    item = config_catalog.retire_release(
        _repo(settings),
        ctx.actor,
        release_id=params["rid"],
        reason=payload.get("reason"),
        idempotency_key=_idempotency(ctx),
    )
    return Response(payload={"item": item})


# -- 配置族与逻辑文件 -------------------------------------------------------


def list_families(
    ctx: RequestContext,
    params: Dict[str, str],
    query: Dict[str, List[str]],
    body: Optional[bytes],
    settings: Settings,
) -> Response:
    # GET /api/v1/config/families
    config_gate.require_read()
    authz.authorize(ctx, "config_read")
    return Response(payload={"items": config_catalog.list_families(_repo(settings))})


def create_family(
    ctx: RequestContext,
    params: Dict[str, str],
    query: Dict[str, List[str]],
    body: Optional[bytes],
    settings: Settings,
) -> Response:
    # POST /api/v1/config/families  {"name","code","notes"}
    _write(ctx)
    payload = json_body(body)
    item = config_catalog.create_family(
        _repo(settings),
        ctx.actor,
        name=payload.get("name"),
        code=payload.get("code"),
        notes=payload.get("notes"),
        idempotency_key=_idempotency(ctx),
    )
    return Response(status=201, payload={"item": item})


def list_logical_files(
    ctx: RequestContext,
    params: Dict[str, str],
    query: Dict[str, List[str]],
    body: Optional[bytes],
    settings: Settings,
) -> Response:
    # GET /api/v1/config/families/{fid}/files?role=
    config_gate.require_read()
    authz.authorize(ctx, "config_read")
    role = query_param(query, "role") or None
    return Response(
        payload={"items": config_catalog.list_logical_files(_repo(settings), params["fid"], role=role)}
    )


def create_logical_file(
    ctx: RequestContext,
    params: Dict[str, str],
    query: Dict[str, List[str]],
    body: Optional[bytes],
    settings: Settings,
) -> Response:
    # POST /api/v1/config/families/{fid}/files  {"role","display_name","notes"}
    _write(ctx)
    payload = json_body(body)
    item = config_catalog.create_logical_file(
        _repo(settings),
        ctx.actor,
        family_id=params["fid"],
        role=str(payload.get("role") or ""),
        display_name=payload.get("display_name"),
        notes=payload.get("notes"),
        idempotency_key=_idempotency(ctx),
    )
    return Response(status=201, payload={"item": item})


def rename_logical_file(
    ctx: RequestContext,
    params: Dict[str, str],
    query: Dict[str, List[str]],
    body: Optional[bytes],
    settings: Settings,
) -> Response:
    # PATCH /api/v1/config/files/{lid}  {"display_name","reason"}
    _write(ctx)
    payload = json_body(body)
    item = config_catalog.rename_logical_file(
        _repo(settings),
        ctx.actor,
        file_id=params["lid"],
        new_name=payload.get("display_name"),
        reason=payload.get("reason"),
        idempotency_key=_idempotency(ctx),
    )
    return Response(payload={"item": item})


def retire_logical_file(
    ctx: RequestContext,
    params: Dict[str, str],
    query: Dict[str, List[str]],
    body: Optional[bytes],
    settings: Settings,
) -> Response:
    # POST /api/v1/config/files/{lid}/retire  {"reason"}
    _write(ctx)
    payload = json_body(body)
    item = config_catalog.retire_logical_file(
        _repo(settings),
        ctx.actor,
        file_id=params["lid"],
        reason=payload.get("reason"),
        idempotency_key=_idempotency(ctx),
    )
    return Response(payload={"item": item})


# -- 文件快照 ---------------------------------------------------------------


def list_snapshots(
    ctx: RequestContext,
    params: Dict[str, str],
    query: Dict[str, List[str]],
    body: Optional[bytes],
    settings: Settings,
) -> Response:
    # GET /api/v1/config/snapshots?release_id=&logical_file_id=&status=
    config_gate.require_read()
    authz.authorize(ctx, "config_read")
    return Response(
        payload={
            "items": config_catalog.list_snapshots(
                _repo(settings),
                release_id=query_param(query, "release_id") or None,
                logical_file_id=query_param(query, "logical_file_id") or None,
                status=query_param(query, "status") or None,
            )
        }
    )


def create_snapshot(
    ctx: RequestContext,
    params: Dict[str, str],
    query: Dict[str, List[str]],
    body: Optional[bytes],
    settings: Settings,
) -> Response:
    # POST /api/v1/config/snapshots?release_id=&logical_file_id=&source=&pid=&archive_name=
    # 原文上传：Content-Type: application/octet-stream，原始文件名走 X-Filename
    _write(ctx)
    release_id = query_param(query, "release_id")
    logical_file_id = query_param(query, "logical_file_id")
    if not release_id or not logical_file_id:
        raise AppError(INVALID_INPUT, "缺少 release_id 或 logical_file_id")
    filename = ctx.filename or query_param(query, "filename")
    if not filename:
        raise AppError(INVALID_INPUT, "缺少原始文件名（X-Filename 或 filename 参数）")
    archive_pid = query_param(query, "pid")
    archive_name = query_param(query, "archive_name")
    archive_ref = None
    if archive_pid or archive_name:
        archive_ref = {"pid": archive_pid, "filename": filename, "archive_name": archive_name}
    item = config_catalog.register_snapshot(
        _repo(settings),
        ctx.actor,
        release_id=release_id,
        logical_file_id=logical_file_id,
        data=body or b"",
        original_filename=filename,
        source=str(query_param(query, "source") or config_catalog.SOURCE_ARCHIVE),
        archive_ref=archive_ref,
        captured_at=query_param(query, "captured_at") or None,
        idempotency_key=_idempotency(ctx),
    )
    return Response(status=201, payload={"item": item})


def get_snapshot_raw(
    ctx: RequestContext,
    params: Dict[str, str],
    query: Dict[str, List[str]],
    body: Optional[bytes],
    settings: Settings,
) -> Response:
    # GET /api/v1/config/snapshots/{sid}/raw
    config_gate.require_read()
    authz.authorize(ctx, "config_read")
    repo = _repo(settings)
    snapshot = config_catalog.get_snapshot(repo, params["sid"])
    data = config_catalog.snapshot_bytes(repo, params["sid"])
    return Response(
        body=data,
        content_type="application/xml",
        headers={
            "Content-Disposition": content_disposition(str(snapshot.get("original_filename") or "snapshot")),
            "ETag": '"%s"' % snapshot.get("blob_sha256"),
            "Cache-Control": "no-cache",
            "X-Content-Sha256": str(snapshot.get("blob_sha256") or ""),
        },
    )


# -- 模板变体 ---------------------------------------------------------------


def list_variants(
    ctx: RequestContext,
    params: Dict[str, str],
    query: Dict[str, List[str]],
    body: Optional[bytes],
    settings: Settings,
) -> Response:
    # GET /api/v1/config/variants?family_id=&release_id=
    config_gate.require_read()
    authz.authorize(ctx, "config_read")
    return Response(
        payload={
            "items": config_catalog.list_variants(
                _repo(settings),
                family_id=query_param(query, "family_id") or None,
                release_id=query_param(query, "release_id") or None,
            )
        }
    )


def ensure_variant(
    ctx: RequestContext,
    params: Dict[str, str],
    query: Dict[str, List[str]],
    body: Optional[bytes],
    settings: Settings,
) -> Response:
    # POST /api/v1/config/variants  {"family_id","release_id","template_snapshot_id"}
    _write(ctx)
    payload = json_body(body)
    item = config_catalog.ensure_variant(
        _repo(settings),
        ctx.actor,
        family_id=str(payload.get("family_id") or ""),
        release_id=str(payload.get("release_id") or ""),
        template_snapshot_id=str(payload.get("template_snapshot_id") or ""),
        idempotency_key=_idempotency(ctx),
    )
    return Response(status=201, payload={"item": item})


# -- 公共 -------------------------------------------------------------------


def _idempotency(ctx: RequestContext) -> Optional[str]:
    """`Idempotency-Key` 头（可选）；不合法直接拒绝，避免脏键落盘。"""
    headers = ctx.headers or {}
    key = str(headers.get("idempotency-key") or "").strip()
    if not key:
        return None
    if len(key) > 128:
        raise AppError(INVALID_INPUT, "Idempotency-Key 过长（上限 128 字符）")
    return key


def retire_family(
    ctx: RequestContext,
    params: Dict[str, str],
    query: Dict[str, List[str]],
    body: Optional[bytes],
    settings: Settings,
) -> Response:
    # POST /api/v1/config/families/{fid}/retire {"reason","acknowledge_refs"}
    _write(ctx)
    payload = json_body(body) if body else {}
    return Response(
        payload={
            "item": config_lifecycle.retire_family(
                _repo(settings), ctx.actor,
                family_id=params["fid"],
                reason=payload.get("reason"),
                acknowledge_refs=bool(payload.get("acknowledge_refs")),
            )
        }
    )


def reactivate_family(
    ctx: RequestContext,
    params: Dict[str, str],
    query: Dict[str, List[str]],
    body: Optional[bytes],
    settings: Settings,
) -> Response:
    # POST /api/v1/config/families/{fid}/reactivate
    _write(ctx)
    return Response(
        payload={
            "item": config_lifecycle.restore_family(
                _repo(settings), ctx.actor, family_id=params["fid"]
            )
        }
    )


def register(router: Router, settings: Settings) -> None:
    """挂载配置目录路由（在骨架路由之后注册，避免抢占）。"""
    # 版本
    router.add("GET", "/api/v1/config/releases", partial(list_releases, settings=settings))
    router.add("POST", "/api/v1/config/releases", partial(create_release, settings=settings))
    router.add(
        "POST",
        "/api/v1/config/releases/{rid}/default",
        partial(publish_default_batch, settings=settings),
    )
    router.add(
        "POST", "/api/v1/config/releases/{rid}/retire", partial(retire_release, settings=settings)
    )
    router.add("GET", "/api/v1/config/releases/{rid}", partial(get_release, settings=settings))
    # 配置族与逻辑文件
    router.add("GET", "/api/v1/config/families", partial(list_families, settings=settings))
    router.add("POST", "/api/v1/config/families", partial(create_family, settings=settings))
    router.add(
        "POST", "/api/v1/config/families/{fid}/retire", partial(retire_family, settings=settings)
    )
    router.add(
        "POST",
        "/api/v1/config/families/{fid}/reactivate",
        partial(reactivate_family, settings=settings),
    )
    router.add(
        "GET", "/api/v1/config/families/{fid}/files", partial(list_logical_files, settings=settings)
    )
    router.add(
        "POST", "/api/v1/config/families/{fid}/files", partial(create_logical_file, settings=settings)
    )
    router.add("PATCH", "/api/v1/config/files/{lid}", partial(rename_logical_file, settings=settings))
    router.add(
        "POST", "/api/v1/config/files/{lid}/retire", partial(retire_logical_file, settings=settings)
    )
    # 快照与变体
    router.add("GET", "/api/v1/config/snapshots", partial(list_snapshots, settings=settings))
    router.add("POST", "/api/v1/config/snapshots", partial(create_snapshot, settings=settings))
    router.add(
        "GET", "/api/v1/config/snapshots/{sid}/raw", partial(get_snapshot_raw, settings=settings)
    )
    router.add("GET", "/api/v1/config/variants", partial(list_variants, settings=settings))
    router.add("POST", "/api/v1/config/variants", partial(ensure_variant, settings=settings))
