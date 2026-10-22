"""产物接口（M6-14 / INH-1959）。

* `POST /p/{pid}/config/artifacts` —— 从已批准基线生成（或复用）产物；
* `GET  /p/{pid}/config/artifacts` —— 列表；
* `GET  /p/{pid}/config/artifacts/{aid}` —— 详情（含校验结果与逐项改动）；
* `GET  /p/{pid}/config/artifacts/{aid}/raw` —— 下载**原始字节**（不重生成）。

下载响应带 `X-Content-Sha256` 与 `ETag`：同一份产物重复下载必须拿到同一串字节，
调用方可以据此自证"这次下的和上次是同一份"。
"""

from __future__ import annotations

from functools import partial
from typing import Any, Dict, List, Optional

from ..config import Settings
from ..core.errors import INVALID_INPUT, NOT_FOUND, AppError
from ..services import authz, config_artifacts, config_gate, config_store
from ..services.context import RequestContext
from .api_documents import content_disposition
from .router import Response, Router, json_body, query_param

OCTET = "application/octet-stream"

CONTENT_TYPES = {
    "xml": "application/xml; charset=utf-8",
    "json": "application/json; charset=utf-8",
}


def _read(ctx: RequestContext) -> None:
    config_gate.require_read()
    authz.authorize(ctx, "config_read")


def _write(ctx: RequestContext) -> None:
    """生成产物是写动作：要 `config_artifact_generate`，与提交/批准分开。"""
    authz.authorize(ctx, "config_artifact_generate")
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
    owner = str(item.get("profile_id") or "")
    if owner and owner != str(pid):
        raise AppError(NOT_FOUND, "产物不存在", artifact_id=item.get("id"))
    return item


def list_artifacts(
    ctx: RequestContext,
    params: Dict[str, str],
    query: Dict[str, List[str]],
    body: Optional[bytes],
    settings: Settings,
) -> Response:
    # GET /api/v1/p/{pid}/config/artifacts?baseline_id=
    _read(ctx)
    items = config_artifacts.list_artifacts(
        _repo(settings),
        profile_id=params["pid"],
        baseline_id=_optional(query, "baseline_id"),
    )
    return Response(payload={"items": items, "total": len(items)})


def generate_artifact(
    ctx: RequestContext,
    params: Dict[str, str],
    query: Dict[str, List[str]],
    body: Optional[bytes],
    settings: Settings,
) -> Response:
    # POST /api/v1/p/{pid}/config/artifacts  {"baseline_id"}
    _write(ctx)
    payload: Dict[str, Any] = json_body(body) if body else {}
    result = config_artifacts.generate_artifact(
        _repo(settings),
        ctx.actor,
        profile_id=params["pid"],
        baseline_id=payload.get("baseline_id"),
        idempotency_key=_idempotency(ctx),
    )
    _same_profile(result["item"], params["pid"])
    # 复用已知产物用 200（"没有任何东西被创建"），新建用 201
    return Response(status=200 if result["reused"] else 201, payload=result)


def get_artifact(
    ctx: RequestContext,
    params: Dict[str, str],
    query: Dict[str, List[str]],
    body: Optional[bytes],
    settings: Settings,
) -> Response:
    # GET /api/v1/p/{pid}/config/artifacts/{aid}
    _read(ctx)
    detail = config_artifacts.detail(_repo(settings), params["aid"])
    _same_profile(detail["item"], params["pid"])
    return Response(payload=detail)


def get_artifact_raw(
    ctx: RequestContext,
    params: Dict[str, str],
    query: Dict[str, List[str]],
    body: Optional[bytes],
    settings: Settings,
) -> Response:
    # GET /api/v1/p/{pid}/config/artifacts/{aid}/raw
    _read(ctx)
    repo = _repo(settings)
    item = config_artifacts.get_artifact(repo, params["aid"])
    _same_profile(item, params["pid"])
    raw = config_artifacts.artifact_bytes(repo, params["aid"])
    return Response(
        body=raw,
        content_type=CONTENT_TYPES.get(str(item.get("format")), OCTET),
        headers={
            "Content-Disposition": content_disposition(
                str(item.get("filename") or "artifact.bin")
            ),
            "X-Content-Sha256": str(item["blob_sha256"]),
            "ETag": '"%s"' % item["blob_sha256"],
            "Cache-Control": "no-cache",
        },
    )


def register(router: Router, settings: Settings) -> None:
    router.add(
        "GET",
        "/api/v1/p/{pid}/config/artifacts",
        partial(list_artifacts, settings=settings),
    )
    router.add(
        "POST",
        "/api/v1/p/{pid}/config/artifacts",
        partial(generate_artifact, settings=settings),
    )
    router.add(
        "GET",
        "/api/v1/p/{pid}/config/artifacts/{aid}",
        partial(get_artifact, settings=settings),
    )
    router.add(
        "GET",
        "/api/v1/p/{pid}/config/artifacts/{aid}/raw",
        partial(get_artifact_raw, settings=settings),
    )
