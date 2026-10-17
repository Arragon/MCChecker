"""路由表与路径参数（规范 §3.4、§7）。

只负责「方法 + 路径 → handler」的匹配：鉴权、读写文件都在 services/core，
handler 不写文件（§3.3）。业务接口从 M1 起按 §3.2 拆到 `api_*.py`。
"""

from __future__ import annotations

import re
import urllib.parse
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

from ..config import Settings
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
    compact 文档、原文下载）。
    """
    from . import api_documents, api_session

    router = Router()
    api_session.register(router, settings)
    router.add("GET", "/api/v1/p/{pid}/files", api_documents.list_files)
    router.add("GET", "/api/v1/p/{pid}/files/{name}/document", api_documents.get_document)
    router.add("GET", "/api/v1/p/{pid}/files/{name}/raw", api_documents.get_raw)
    return router
