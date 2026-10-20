"""路由表与路径参数（规范 §3.4、§7）。

只负责「方法 + 路径 → handler」的匹配：鉴权、读写文件都在 services/core，
handler 不写文件（§3.3）。业务接口从 M1 起按 §3.2 拆到 `api_*.py`。
"""

from __future__ import annotations

import json
import re
import urllib.parse
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

from ..config import Settings
from ..core.errors import INVALID_INPUT, AppError
from ..services.context import RequestContext


@dataclass
class Response:
    """handler 的返回值：JSON 载荷或原始字节，外加状态码与响应头。

    `payload` 与 `body` 二选一：给了 `body` 就直接发字节（原文下载、已序列化
    的 compact 文档），否则把 `payload` 序列化成 JSON。
    """

    status: int = 200
    payload: Any = None
    body: Optional[bytes] = None
    content_type: Optional[str] = "application/json; charset=utf-8"
    headers: Dict[str, str] = field(default_factory=dict)


#: handler(ctx, params, query, body) -> Response（§3.4 第 4、6 步）。
Handler = Callable[
    [RequestContext, Dict[str, str], Dict[str, List[str]], Optional[bytes]],
    Response,
]

_PARAM_RE = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)\}")


def query_param(query: Dict[str, List[str]], key: str, default: str = "") -> str:
    """查询参数：同名多次时取最后一个（后写覆盖），空值回落 `default`（§7.4）。"""
    values = query.get(key)
    if not values:
        return default
    value = str(values[-1]).strip()
    return value or default


def json_body(body: Optional[bytes]) -> Dict[str, Any]:
    """解析 JSON 请求体（写接口共用）。空体按 `{}`，非法 JSON → 400（§7.1）。"""
    if not body:
        return {}
    try:
        parsed = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        raise AppError(INVALID_INPUT, "请求体必须是合法的 JSON")
    if not isinstance(parsed, dict):
        raise AppError(INVALID_INPUT, "请求体必须是 JSON 对象")
    return parsed


class Router:
    """有序路由表：先注册的先匹配，路径参数形如 `/p/{pid}/files/{name}`。"""

    def __init__(self) -> None:
        self._routes: List[Tuple[str, "re.Pattern[str]", Tuple[str, ...], Handler]] = []

    def add(self, method: str, pattern: str, handler: Handler) -> None:
        names: List[str] = []

        def _placeholder(match: "re.Match[str]") -> str:
            names.append(match.group(1))
            return r"(?P<%s>[^/]+)" % match.group(1)

        regex = re.compile("^" + _PARAM_RE.sub(_placeholder, pattern) + "$")
        self._routes.append((method.upper(), regex, tuple(names), handler))

    def match(self, method: str, path: str) -> Tuple[Optional[Handler], Dict[str, str]]:
        """未匹配返回 `(None, {})`（§3.4 第 2 步 → 404 `NOT_FOUND`）。"""
        wanted = method.upper()
        for route_method, regex, names, handler in self._routes:
            if route_method != wanted:
                continue
            found = regex.match(path)
            if found:
                return handler, {
                    name: urllib.parse.unquote(found.group(name)) for name in names
                }
        return None, {}


def build_router(settings: Settings) -> Router:
    """组装路由表。

    M0-09 只有占位会话；M1 起按 §3.2 逐步挂上业务接口（当前：会话、文件列表、
    compact 文档、原文下载、收藏、修订号、搜索）。
    """
    from . import (
        api_bindings,
        api_compare,
        api_delete,
        api_dl,
        api_documents,
        api_favorites,
        api_history,
        api_people,
        api_remarks,
        api_review,
        api_revision,
        api_schedule,
        api_search,
        api_session,
        api_sources,
        api_tasks,
        api_temp,
        api_upload,
    )

    router = Router()
    api_session.register(router, settings)
    router.add("GET", "/api/v1/p/{pid}/files", api_documents.list_files)
    router.add("GET", "/api/v1/p/{pid}/files/{name}/document", api_documents.get_document)
    router.add("GET", "/api/v1/p/{pid}/files/{name}/raw", api_documents.get_raw)
    api_favorites.register(router)
    api_remarks.register(router)
    api_review.register(router)
    api_search.register(router)
    api_history.register(router)
    api_compare.register(router)
    api_temp.register(router, settings)
    api_upload.register(router, settings)
    api_delete.register(router)
    api_sources.register(router)
    api_tasks.register(router)
    api_schedule.register(router)
    api_people.register(router)
    api_dl.register(router)
    api_bindings.register(router)
    router.add("GET", "/api/v1/p/{pid}/revision", api_revision.get_revision)
    return router
