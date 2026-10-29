"""后台任务接口（规范 §7.10、§10.1；卡 6 / M3-07 / INH-1892）。

handler 只做「解析参数 → 授权 → 调 `services.tasks` / `services.updates` → 组装
`Response`」：不碰 `storage`（§3.3），也不在这里等下载——`POST` 入队后立刻 202，
下载与机型锁都在任务线程里（§10.1）。机型存在性与 `use_profile` 由 `http.py` 统一处理。

权限（§4.2）：`POST` = 部署者（`tasks_run`）；两个 `GET` 全部角色可读（进度要能被
任何人在顶栏看到）。CSRF 头由 `http.py` 检查，本模块不重复实现（§11.4）。

`GET /tasks/{id}` 的路径里没有 `{pid}`：`http.py` 会按 default 机型解析身份，但任务
归属在队列里，读它不需要机型权限（§7.10「全部」）。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from ..core import fsutil
from ..core.errors import INVALID_INPUT, NOT_FOUND, AppError
from ..services import authz, tasks, updates
from ..services.context import RequestContext
from .router import Response, Router, json_body, query_param

#: §7.10：`update_all` 不需要 `name`，其余两类必须给。
TYPE_FIELD = "type"
NAME_FIELD = "name"
#: 文件名不合法时的错误落点（前端按 `detail.field` 显示在输入框下方，§7.1）。
NO_SOURCE_MESSAGE = "该文件没有配置更新源"
#: 互斥与状态词汇（给 handler 的 400 文案用，避免把元组拼串写死两遍）。
_TYPE_HINT = "/".join(tasks.TASK_TYPES)


def register(router: Router) -> None:
    router.add("POST", "/api/v1/p/{pid}/tasks", create_task)
    router.add("GET", "/api/v1/p/{pid}/tasks", list_tasks)
    router.add("GET", "/api/v1/tasks/{id}", get_task)


def create_task(
    ctx: RequestContext,
    params: Dict[str, str],
    query: Dict[str, List[str]],
    body: Optional[bytes],
) -> Response:
    # POST /api/v1/p/{pid}/tasks  {"type","name"} -> 202 {"task"}；重复 -> 409 BUSY
    authz.authorize(ctx, "tasks_run")
    payload = json_body(body)
    ttype = str(payload.get(TYPE_FIELD) or "").strip()
    if ttype not in tasks.TASK_TYPES:
        raise AppError(
            INVALID_INPUT, "任务类型只能是 %s" % _TYPE_HINT, field=TYPE_FIELD, wanted=ttype
        )
    handler = tasks.handler_for(ttype)
    if handler is None:  # 声明了类型但没人实现：属接入缺口，不能悄悄排一个空任务
        raise AppError(INVALID_INPUT, "任务类型尚未接入", field=TYPE_FIELD, wanted=ttype)

    target: Optional[str] = None
    if ttype != "update_all":
        target = _validated_target(payload.get(NAME_FIELD))
        _require_mapping_entry(params["pid"], target)

    task = tasks.get_runner().submit(ctx, ttype, target, handler)
    return Response(status=202, payload={"task": task.to_payload()})


def list_tasks(
    ctx: RequestContext,
    params: Dict[str, str],
    query: Dict[str, List[str]],
    body: Optional[bytes],
) -> Response:
    # GET /api/v1/p/{pid}/tasks[?active=1] -> {"items": [task]}
    authz.authorize(ctx, "read")
    active_only = query_param(query, "active") == "1"
    runner = tasks.get_runner()
    workspace = ctx.workspace_id or "default"
    return Response(
        payload={
            "items": [
                task.to_payload()
                for task in runner.list(params["pid"], active_only)
                if getattr(task, "workspace", "default") == workspace
            ]
        }
    )


def get_task(
    ctx: RequestContext,
    params: Dict[str, str],
    query: Dict[str, List[str]],
    body: Optional[bytes],
) -> Response:
    # GET /api/v1/tasks/{id} -> {"task"}
    authz.authorize(ctx, "read")
    task = tasks.get_runner().get(params["id"])
    if task is None or getattr(task, "workspace", "default") != (ctx.workspace_id or "default"):
        raise AppError(NOT_FOUND, "任务不存在或已结束太久", id=params["id"])
    return Response(payload={"task": task.to_payload()})


def _validated_target(name: Any) -> str:
    try:
        return fsutil.validate_filename(name)
    except AppError as exc:
        # §11.2：非法名一律 400 落在「文件名」字段上，绝不拿去拼路径。
        exc.detail["field"] = NAME_FIELD
        raise


def _require_mapping_entry(pid: str, name: str) -> None:
    """映射里没有这个名字就别排队了。

    不查的话任务会「成功」地跑完并留下一条 `not_found` 条目——界面显示「已完成」，
    用户却不知道什么都没发生。这里读的是 `updates.list_sources`（服务层的 §7.9 读接口），
    handler 不碰 `storage`（§3.3）。
    """
    if not any(item["name"] == name for item in updates.list_sources(pid)):
        raise AppError(NOT_FOUND, NO_SOURCE_MESSAGE, name=name)
