"""路由表与路径参数（规范 §3.4、§7）。

只负责「方法 + 路径 → handler」的匹配：鉴权、读写文件都在 services/core，
handler 不写文件（§3.3）。业务接口从 M1 起按 §3.2 拆到 `api_*.py`。
"""

from __future__ import annotations

import re
import urllib.parse
from typing import Any, Callable, Dict, List, Optional, Tuple

from ..config import Settings

#: handler(params, query, body) -> (status, payload)；body 只对 BODY_METHODS 非 None。
#: M1 起会按 §3.4 第 4 步扩展成 handler(ctx, params, query, body)。
Handler = Callable[[Dict[str, str], Dict[str, List[str]], Optional[bytes]], Tuple[int, Any]]

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

    M0-09 只挂占位会话；业务接口从 M1（读取链路）开始按 §3.2 加到 `api_*.py`。
    """
    from . import api_session

    router = Router()
    router.add(
        "GET",
        "/api/v1/session",
        lambda params, query, body: (200, api_session.session_payload(settings)),
    )
    return router
