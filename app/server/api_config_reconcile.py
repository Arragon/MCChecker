"""文件内现场对账接口（M6-10 / INH-1955）。

`GET /api/v1/p/{pid}/config/reconcile?logical_file_id=&filename=&baseline_id=&observation_id=`

路径里带 `{pid}` 是因为读的是**某个机型**的现场数据——作用域的唯一来源是路径
（规范 §2.1），所以这个接口不用查询串里的 `pid`。
"""

from __future__ import annotations

from functools import partial
from typing import Dict, List, Optional

from ..config import Settings
from ..services import authz, config_gate, config_reconcile, config_store
from ..services.context import RequestContext
from .router import Response, Router, query_param


def reconcile(
    ctx: RequestContext,
    params: Dict[str, str],
    query: Dict[str, List[str]],
    body: Optional[bytes],
    settings: Settings,
) -> Response:
    config_gate.require_read()
    authz.authorize(ctx, "config_read")
    payload = config_reconcile.reconcile(
        config_store.repository(settings),
        profile_id=params["pid"],
        logical_file_id=query_param(query, "logical_file_id") or None,
        filename=query_param(query, "filename") or None,
        baseline_id=query_param(query, "baseline_id") or None,
        observation_id=query_param(query, "observation_id") or None,
    )
    payload["writable"] = config_gate.mode() == config_gate.MODE_ON
    payload["mode"] = config_gate.mode()
    return Response(payload=payload)


def register(router: Router, settings: Settings) -> None:
    router.add(
        "GET",
        "/api/v1/p/{pid}/config/reconcile",
        partial(reconcile, settings=settings),
    )
