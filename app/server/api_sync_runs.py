"""M16-33 工作区级手动/批量同步任务 API。

`POST /api/v3/workspaces/{wid}/sync/runs` 接收与预览相同的范围参数：

- 带 `preview_id` 时先 `sync_preview.validate_preview`，沿用冻结 read_set；
- 否则先按 `scope` 生成预览，再按来源所属 profile 分组；
- 每个 profile 投一个 `sync_run`（同一 W/P、原 actor），`trigger="manual"`；
- 全部成功返回 202；部分机型已有任务时返回 409，detail 只含 profile/run id，
  不携带来源 URL、凭据或任务内部错误文本。

本接口不自动发布候选、D0、Bn 或 rn；执行体的提交仍由 `sync_tasks` /
`sync_commit` 决定。权限沿用 `tasks_run`（只读身份 403）。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from ..core.errors import BUSY, INVALID_INPUT, AppError
from ..services import authz, sync_preview, sync_sources, sync_tasks, workspace_catalog
from ..services.context import RequestContext
from .router import Response, Router, json_body


def register(router: Router) -> None:
    router.add("POST", "/api/v3/workspaces/{wid}/sync/runs", create_run)


def _scope(wid: str) -> None:
    workspace_catalog.get_workspace(wid)


def _preview_summary(preview: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "preview_id": preview.get("preview_id"),
        "total": preview.get("total", 0),
        "excluded": preview.get("excluded") or [],
        "read_set_sha256": preview.get("read_set_sha256"),
    }


def _group_items(preview: Dict[str, Any]) -> List[Tuple[str, List[Dict[str, Any]]]]:
    """按冻结项里的 profile 分组；保持预览顺序，同一 profile 内的来源不重排。"""
    groups: Dict[str, List[Dict[str, Any]]] = {}
    for item in preview.get("items") or []:
        pid = str(item.get("profile_id") or "").strip()
        if not pid:
            raise AppError(INVALID_INPUT, "预览项缺少机型", field="profile_id")
        groups.setdefault(pid, []).append(item)
    return list(groups.items())


def create_run(
    ctx: RequestContext,
    params: Dict[str, str],
    query: Dict[str, List[str]],
    body: Optional[bytes],
) -> Response:
    authz.authorize(ctx, "tasks_run")
    wid = params["wid"]
    _scope(wid)
    payload = json_body(body)
    if not isinstance(payload, dict):
        raise AppError(INVALID_INPUT, "payload 必须是对象", field="payload")

    preview_id = payload.get("preview_id")
    if preview_id not in (None, ""):
        # 冻结 read_set 的 revision 再核一遍；策略/来源/连接/绑定变化都是 409。
        preview = sync_preview.validate_preview(wid, preview_id)
    else:
        preview = sync_preview.create_preview(wid, payload)

    runs: List[Dict[str, Any]] = []
    current_profile: Optional[str] = None
    try:
        for profile_id, items in _group_items(preview):
            current_profile = profile_id
            sources = [
                sync_sources.get_source(wid, item["source_id"]) for item in items
            ]
            run_ctx = RequestContext(
                ip=ctx.ip,
                cid=ctx.cid,
                profile_id=profile_id,
                workspace_id=wid,
                actor=ctx.actor,
                headers=ctx.headers,
            )
            result = sync_tasks.submit_sync_run(run_ctx, sources, trigger="manual")
            runs.append(
                {
                    "profile_id": profile_id,
                    "run": result["run"],
                    "task": result["task"],
                }
            )
    except AppError as exc:
        if exc.code == BUSY and runs:
            # 部分机型已经入队：不谎称整体成功，也不回滚已提交的 run。
            # detail 只保留安全的归属信息，避免把任务错误文本/来源 URL 带出去。
            raise AppError(
                BUSY,
                "部分机型已有同步任务在运行，请稍后重试",
                field="runs",
                started_profiles=[row["profile_id"] for row in runs],
                started_run_ids=[
                    (row.get("run") or {}).get("run_id") for row in runs
                ],
                failed_profile_id=current_profile,
            )
        raise

    return Response(
        status=202,
        payload={
            "preview": _preview_summary(preview),
            "runs": runs,
        },
    )
