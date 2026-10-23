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
        api_config,
        api_config_approval,
        api_config_artifacts,
        api_config_delivery,
        api_config_promote,
        api_config_restore,
        api_config_upgrade,
        api_config_baselines,
        api_config_catalog,
        api_config_changes,
        api_config_ingest,
        api_config_reconcile,
        api_config_registry,
        api_config_tracking,
        api_delete,
        api_dl,
        api_documents,
        api_favorites,
        api_history,
        api_people,
        api_profiles,
        api_remarks,
        api_review,
        api_revision,
        api_schedule,
        api_search,
        api_session,
        api_sources,
        api_tasks,
        api_temp,
        api_tools,
        api_upload,
        legacy,
    )

    router = Router()
    # §7.12：旧路由必须在 `/api/v1` 之前匹配（下载/记录页与新版接口不共享路径，
    # 但顺序写在这里，避免以后新增通配路由时被抢先）。
    legacy.register(router)
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
    api_tools.register(router)
    api_profiles.register(router)
    router.add("GET", "/api/v1/p/{pid}/revision", api_revision.get_revision)
    # 配置管理域（M5-08）：全局 `/api/v1/config/*` 与机型 `/api/v1/p/{pid}/config/*`。
    # 门禁在 handler 入口判定（默认 off），因此注册顺序不影响旧接口。
    api_config.register(router, settings)
    # 配置目录（M6-01/M6-02）：软件版本、出口批次、配置族、逻辑文件、快照、变体。
    api_config_catalog.register(router, settings)
    # 参数登记（M6-03/M6-04）：参数主档、定义修订、定义集合。
    api_config_registry.register(router, settings)
    # 现场采集与观测（M6-07/M6-08）：不可变 Observation + 重新映射。
    api_config_ingest.register(router, settings)
    # 基线（M6-09）：通用/机型/设备草稿、继承意图与就绪度。
    api_config_baselines.register(router, settings)
    # 文件内现场对账（M6-10）：现场原值 / 批准基线 / 拟值三栏。
    api_config_reconcile.register(router, settings)
    # 追踪策略（M6-05）：archive_only / managed 的类别级与机型级覆盖。
    api_config_tracking.register(router, settings)
    # 变更单（M6-11）：draft/submit/withdraw/reopen 状态机。
    api_config_changes.register(router, settings)
    # 批准发布（M6-12）：单独的 `config_change_approve`，与提交严格分开。
    api_config_approval.register(router, settings)
    # 产物与下载（M6-13/M6-14）：只读已批准基线，下载不重生成。
    api_config_artifacts.register(router, settings)
    # 跨版本升级（M7-02/03）：计划只读，落地建草稿。
    api_config_upgrade.register(router, settings)
    # 经验上提（M7-05）：产出变更单；设备层入口明确报"未启用"。
    api_config_promote.register(router, settings)
    # 回滚 / 跨变体迁移 / 出口补发（M7-06）：一律新增对象，不改写历史。
    api_config_restore.register(router, settings)
    # 交付记录与回读核验（M7-09/M7-10）：登记是人工的，核验结果逐次留档。
    api_config_delivery.register(router, settings)
    return router
