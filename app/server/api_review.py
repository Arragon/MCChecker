"""审阅列表与按文件提交接口（§7.8 的 `GET /review`、`POST /review/submit`；M3-02）。

handler 只做「解析请求 → 调服务 → 序列化响应」（§3.3 分层红线）：分组、幂等、冲突判定、
写回与落盘全在 `app/services/review.py`。

权限：两个接口都是管理员档（§4.2 的 `review`；部署者若不在管理员名单同样 403）。
CSRF：写请求依赖 `app/server/http.py` 已有的 `X-Requested-With` 硬条件，这里不重复实现。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from ..services import authz, review
from ..services.context import RequestContext
from .router import Response, json_body


def register(router) -> None:
    router.add("GET", "/api/v1/p/{pid}/review", list_review)
    router.add("POST", "/api/v1/p/{pid}/review/submit", submit_review)


def list_review(
    ctx: RequestContext,
    params: Dict[str, str],
    query: Dict[str, List[str]],
    body: Optional[bytes],
) -> Response:
    # GET /api/v1/p/{pid}/review —— 待审节点按文件分组（§7.8）
    authz.authorize(ctx, "review")
    return Response(payload=review.list_review(params["pid"]))


def submit_review(
    ctx: RequestContext,
    params: Dict[str, str],
    query: Dict[str, List[str]],
    body: Optional[bytes],
) -> Response:
    # POST /api/v1/p/{pid}/review/submit
    #   {"request_id","file","source_sha","decisions":[{"group_key","choice"}]}
    #   -> {"generated_file": str|null, "approved": int, "rejected": int}
    authz.authorize(ctx, "review")
    payload: Dict[str, Any] = json_body(body)
    return Response(payload=review.submit_review(params["pid"], ctx.actor, payload))
