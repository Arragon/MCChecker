"""DL 接口（规范 §7.11；M3-11 / INH-1896）。

handler 只做「解析请求 → 授权 → 调 `services.dl` → 序列化响应」：不碰 `storage`、
不碰修订号（§3.3 / 全局约束）。机型存在性与 `use_profile(pid)` 由 `http.py` 统一处理。

权限（§4.2）：`GET` / `compute` / `extract` = 全部角色；`PUT` = 部署者（`settings_write`）。
CSRF 头由 `http.py` 统一检查，本模块不重复实现（§11.4）。`PUT` 的 `If-Match` 来自
`ctx.headers`（键已归一成小写），缺失时由服务层回 409 `REVISION_CONFLICT`（R17）。
"""

from __future__ import annotations

from typing import Dict, List, Optional

from ..services import authz, dl, dl_history
from ..services.context import RequestContext
from .router import Response, Router, json_body


def register(router: Router) -> None:
    router.add("GET", "/api/v1/p/{pid}/dl", get_state)
    router.add("POST", "/api/v1/p/{pid}/dl/{item}/compute", compute)
    router.add("POST", "/api/v1/p/{pid}/dl/{item}/extract", extract)
    router.add("PUT", "/api/v1/p/{pid}/dl/{item}", save_item)
    router.add("GET", "/api/v1/p/{pid}/dl/{item}/coeff-history", coeff_history)
    router.add("POST", "/api/v1/p/{pid}/dl/{item}/coeffs", save_coefficients)
    router.add("POST", "/api/v1/p/{pid}/dl/{item}/coeff-history/{hid}/apply", apply_coefficients)
    router.add("DELETE", "/api/v1/p/{pid}/dl/{item}/coeff-history/{hid}", delete_history)


def get_state(
    ctx: RequestContext,
    params: Dict[str, str],
    query: Dict[str, List[str]],
    body: Optional[bytes],
) -> Response:
    # GET /api/v1/p/{pid}/dl -> {"rev", "items": {pp_energy, rp_energy, rp_width, ppz}}
    authz.authorize(ctx, "read")
    return Response(payload=dl.get_state(params["pid"]))


def compute(
    ctx: RequestContext,
    params: Dict[str, str],
    query: Dict[str, List[str]],
    body: Optional[bytes],
) -> Response:
    # POST /api/v1/p/{pid}/dl/{item}/compute
    # {"mode": "forward|reverse|multi_check", "values": [number], "params": item}
    # -> forward {"results"} / reverse {"results","exhaustive":false} /
    #    multi_check {"has_multi","cases"}；纯计算，不写文件、不 bump。
    authz.authorize(ctx, "dl_compute")
    payload = json_body(body)
    return Response(
        payload=dl.compute(
            params["pid"],
            params["item"],
            payload.get("mode"),
            payload.get("values"),
            payload.get("params"),
        )
    )


def extract(
    ctx: RequestContext,
    params: Dict[str, str],
    query: Dict[str, List[str]],
    body: Optional[bytes],
) -> Response:
    # POST /api/v1/p/{pid}/dl/{item}/extract  {"binding": {...}}
    # -> {"coeffs": {成功提取的键}, "fields": [{field, coeff, status, value|raw}]}
    # 只预览：缺失/非数值的字段不进 coeffs，更不返回 0（C15）。
    authz.authorize(ctx, "dl_compute")
    payload = json_body(body)
    return Response(
        payload=dl.extract(params["pid"], params["item"], payload.get("binding"))
    )


def save_item(
    ctx: RequestContext,
    params: Dict[str, str],
    query: Dict[str, List[str]],
    body: Optional[bytes],
) -> Response:
    # PUT /api/v1/p/{pid}/dl/{item}  If-Match: <rev>；body 为完整 item -> {"rev", "item"}
    # 仅部署者。If-Match 缺失/不符 → 409 REVISION_CONFLICT（缺失也走 409，R17）。
    authz.authorize(ctx, "settings_write")
    if_match = (ctx.headers or {}).get("if-match")
    return Response(
        payload=dl.save_item(params["pid"], params["item"], json_body(body), if_match, ctx.actor)
    )


def coeff_history(
    ctx: RequestContext,
    params: Dict[str, str],
    query: Dict[str, List[str]],
    body: Optional[bytes],
) -> Response:
    # GET /api/v1/p/{pid}/dl/{item}/coeff-history?aom=delay|width
    # 全部角色。只读，不建历史文件。
    authz.authorize(ctx, "read")
    aom = (query.get("aom") or [None])[0]
    return Response(payload=dl_history.list_history(params["pid"], params["item"], aom))


def save_coefficients(
    ctx: RequestContext,
    params: Dict[str, str],
    query: Dict[str, List[str]],
    body: Optional[bytes],
) -> Response:
    # POST /api/v1/p/{pid}/dl/{item}/coeffs
    # 全部角色。只写多项式系数，可带备注。If-Match 缺失或过期 → 409。
    authz.authorize(ctx, "read")
    if_match = (ctx.headers or {}).get("if-match")
    return Response(
        payload=dl_history.save_coefficients(
            params["pid"], params["item"], json_body(body), if_match, ctx.actor
        )
    )


def apply_coefficients(
    ctx: RequestContext,
    params: Dict[str, str],
    query: Dict[str, List[str]],
    body: Optional[bytes],
) -> Response:
    # POST /api/v1/p/{pid}/dl/{item}/coeff-history/{hid}/apply
    # 全部角色。把该条历史整份写回，不追加历史。
    authz.authorize(ctx, "read")
    if_match = (ctx.headers or {}).get("if-match")
    return Response(
        payload=dl_history.apply_history(
            params["pid"], params["item"], params["hid"], if_match, ctx.actor
        )
    )


def delete_history(
    ctx: RequestContext,
    params: Dict[str, str],
    query: Dict[str, List[str]],
    body: Optional[bytes],
) -> Response:
    # DELETE /api/v1/p/{pid}/dl/{item}/coeff-history/{hid}
    # 仅管理员。只删记录，不改当前参数，也不看 If-Match。
    authz.authorize(ctx, "settings_write")
    return Response(
        payload=dl_history.delete_history(params["pid"], params["item"], params["hid"])
    )
