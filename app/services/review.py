"""修改建议服务（§6.7 提交部分、§7.8；T-10 / M2-03）。

本卡只做**提交与列表**；审阅通过与生成文件属 M3（`apply_review_results`）。

定位一律用 M1-01 的 `loc`（同一份文档内唯一），不再使用「过滤后子节点下标」那类旧
定位方式（这是 v1 改错节点的根因）。旧读取需要的 `node_path` 继续写旧 ID，
`node_key` 写空字符串，并新增 `locator` / `source_sha256` / `schema_version=2`。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from ..core import reviewing, storage
from ..core.errors import INVALID_INPUT, NOT_FOUND, SOURCE_CHANGED, AppError
from ..services.auth import Actor
from . import documents, revision

PART = "remarks"


def list_items(
    profile_id: str, file_name: Optional[str] = None, status: Optional[str] = None
) -> List[Dict[str, Any]]:
    """`GET /remarks?file=&status=`：按文件与状态过滤，保持磁盘顺序。"""
    with storage.use_profile(profile_id):
        items = storage.load_edit_remarks()
    out: List[Dict[str, Any]] = []
    for item in items:
        if file_name and item.get("source_file") != file_name:
            continue
        if status and str(item.get("status") or "pending") != status:
            continue
        out.append(item)
    return out


def _required_type(fmt: str, node_type: str, proposed: str) -> str:
    """建议值与原类型不符时返回需要的类型名，一致则返回空串。

    类型口径**不在本文件再写一份**（评审 Important 3）：判定来自 `core.reviewing.value_type_hint`，
    与 `apply_edits` 的写入侧共用同一个函数——两份拷贝一旦飘了，就会出现「`/remarks` 接受一条
    建议、`/review/submit` 在机器写锁内把它拒掉」的永远无法应用的建议。方向是 services → core，
    不违反 §3.3 分层红线。留在本层的只有「XML 不做类型检查」这条格式门（§6.7 第 4 步）。
    """
    if fmt != "json":
        return ""
    return reviewing.value_type_hint(node_type, proposed)


def submit(
    profile_id: str,
    actor: Optional[Actor],
    file_name: str,
    loc: str,
    proposed_value: str,
    sha256: str = "",
) -> Dict[str, Any]:
    """提交一条建议并返回落盘后的记录（§6.7 五步）。"""
    proposed = str(proposed_value or "").strip()
    if proposed == "":
        raise AppError(INVALID_INPUT, "请输入建议值", field="proposed_value")
    try:
        doc = documents.load_document(profile_id, file_name, "current")
    except AppError as exc:
        if exc.code == NOT_FOUND:
            raise AppError(NOT_FOUND, "文件不存在", file=file_name)
        raise
    info = doc["file"]
    if sha256 and sha256 != info["sha256"]:
        raise AppError(SOURCE_CHANGED, "文件已更新，请刷新后基于最新版本提交建议")
    fmt = str(info["format"])

    nodes = doc["nodes"]
    if loc not in nodes["loc"]:
        raise AppError(INVALID_INPUT, "只能对有值的节点提交建议", field="loc")
    i = nodes["loc"].index(loc)
    current = nodes["v"][i]
    if current is None:
        raise AppError(INVALID_INPUT, "只能对有值的节点提交建议", field="loc")
    if proposed == current:
        raise AppError(INVALID_INPUT, "建议值与当前值相同", field="proposed_value")
    needed = _required_type(fmt, nodes["t"][i], proposed)
    if needed:
        raise AppError(
            INVALID_INPUT, "该节点需要%s类型的值" % needed, field="proposed_value"
        )

    registered = bool(actor and actor.registered)
    with storage.use_profile(profile_id):
        item_id = storage.add_edit_remark(
            source_file=file_name,
            node_key="",
            node_path=nodes["id"][i],
            node_label=nodes["l"][i],
            original_value=current,
            proposed_value=proposed,
            node_type=nodes["t"][i],
            tree_type=fmt,
            actor_ip=actor.ip if actor else "",
            actor_name=actor.name if actor and registered else "",
            locator=loc,
            source_sha256=info["sha256"],
            schema_version=2,
        )
        items = storage.load_edit_remarks()
    for item in items:
        if item.get("id") == item_id:
            revision.bump(profile_id, PART)
            return item
    raise AppError("STORAGE_FAILURE", "建议写入后未能读回记录", id=item_id)
