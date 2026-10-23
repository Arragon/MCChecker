"""配置域审计检索与来源谱系接口（M7-11 / INH-1970）。

只读入口：

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/v1/p/{pid}/config/audit` | 机型视图（默认带上通用层事件） |
| GET | `/api/v1/config/audit` | 跨机型全局视图 |
| GET | `/api/v1/config/history/{object_id}` | 单对象时间线与来源 |
| GET | `/api/v1/config/history/{object_id}/{revision}` | 指定修订 |

所有查询走 `config_read`，不提供任何写路径；普通日志不是审计来源。
"""

from __future__ import annotations

from functools import partial
from typing import Any, Dict, List, Optional

from ..config import Settings
from ..core.errors import INVALID_INPUT, AppError
from ..services import authz, config_audit, config_gate, config_store
from ..services.context import RequestContext
from .router import Response, Router, query_param


def _read(ctx: RequestContext) -> None:
    config_gate.require_read()
    authz.authorize(ctx, "config_read")


def _repo(settings: Settings):
    return config_store.repository(settings)


def _optional(query: Dict[str, List[str]], name: str) -> Optional[str]:
    value = query_param(query, name)
    return value if value else None


def _bool(query: Dict[str, List[str]], name: str, default: bool) -> bool:
    value = _optional(query, name)
    if value is None:
        return default
    return str(value).strip().lower() not in ("0", "false", "no", "off")


def _limit(query: Dict[str, List[str]]) -> int:
    value = _optional(query, "limit")
    if value is None:
        return 50
    try:
        return int(value)
    except ValueError:
        raise AppError(INVALID_INPUT, "limit 必须是整数", field="limit")


def _query_kwargs(
    query: Dict[str, List[str]], *, profile_id: Optional[str] = None
) -> Dict[str, Any]:
    return {
        "action": _optional(query, "action"),
        "category": _optional(query, "category"),
        "object_id": _optional(query, "object_id"),
        "object_kind": _optional(query, "object_kind"),
        "parameter_id": _optional(query, "parameter_id"),
        "release_id": _optional(query, "release_id"),
        "family_id": _optional(query, "family_id"),
        "profile_id": profile_id,
        "include_common": _bool(query, "include_common", True),
        "actor": _optional(query, "actor"),
        "reason": _optional(query, "reason"),
        "since": _optional(query, "since"),
        "until": _optional(query, "until"),
        "include_retired": _bool(query, "include_retired", True),
        "limit": _limit(query),
        "cursor": _optional(query, "cursor"),
    }


def list_profile_audit(
    ctx: RequestContext,
    params: Dict[str, str],
    query: Dict[str, List[str]],
    body: Optional[bytes],
    settings: Settings,
) -> Response:
    # GET /api/v1/p/{pid}/config/audit
    _read(ctx)
    kwargs = _query_kwargs(query, profile_id=params["pid"])
    return Response(payload=config_audit.query(_repo(settings), **kwargs))


def list_global_audit(
    ctx: RequestContext,
    params: Dict[str, str],
    query: Dict[str, List[str]],
    body: Optional[bytes],
    settings: Settings,
) -> Response:
    # GET /api/v1/config/audit
    _read(ctx)
    return Response(payload=config_audit.query(_repo(settings), **_query_kwargs(query)))


def history(
    ctx: RequestContext,
    params: Dict[str, str],
    query: Dict[str, List[str]],
    body: Optional[bytes],
    settings: Settings,
) -> Response:
    # GET /api/v1/config/history/{object_id}
    _read(ctx)
    return Response(
        payload=config_audit.timeline(_repo(settings), params["object_id"])
    )


def history_revision(
    ctx: RequestContext,
    params: Dict[str, str],
    query: Dict[str, List[str]],
    body: Optional[bytes],
    settings: Settings,
) -> Response:
    # GET /api/v1/config/history/{object_id}/{revision}
    _read(ctx)
    return Response(
        payload=config_audit.timeline(
            _repo(settings), params["object_id"], params.get("revision")
        )
    )


def register(router: Router, settings: Settings) -> None:
    router.add(
        "GET", "/api/v1/p/{pid}/config/audit", partial(list_profile_audit, settings=settings)
    )
    router.add(
        "GET", "/api/v1/config/audit", partial(list_global_audit, settings=settings)
    )
    # 具体到修订的路径先注册，避免被单段路径提前匹配
    router.add(
        "GET",
        "/api/v1/config/history/{object_id}/{revision}",
        partial(history_revision, settings=settings),
    )
    router.add(
        "GET", "/api/v1/config/history/{object_id}", partial(history, settings=settings)
    )
