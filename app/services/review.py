"""修改建议服务（§6.7、§7.8；T-10 / M2-03、M3-02）。

本模块做两件事：**提交建议**（`submit`，M2-03）与**审阅列表 / 按文件提交**
（`list_review` / `submit_review`，M3-02，§6.7「审阅提交」第 1–9 步）。

定位一律用 M1-01 的 `loc`（同一份文档内唯一），不再使用「过滤后子节点下标」那类旧
定位方式（这是 v1 改错节点的根因）。旧读取需要的 `node_path` 继续写旧 ID，
`node_key` 写空字符串，并新增 `locator` / `source_sha256` / `schema_version=2`。

分层（§3.3）：写回与解析在 `core`（`reviewing.apply_edits`、`parser.to_compact`），
落盘只在 `storage`，本层只做组合、授权之后的编排与状态计算；机型锁由 `profile_write`
提供，锁内没有任何网络 I/O。
"""

from __future__ import annotations

import time
from typing import Any, Dict, List, Optional, Sequence, Tuple

from ..core import parser, reviewing, searching, storage
from ..core.errors import (
    INVALID_INPUT,
    NOT_FOUND,
    PARSE_FAILED,
    REVIEW_CONFLICT,
    SOURCE_CHANGED,
    TOO_LARGE,
    AppError,
)
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


# ============ 审阅列表与按文件提交（§6.7 第 1–9 步、§7.8；M3-02） ============

#: `decisions[].choice` 的「不通过」取值；其余取值一律是该组的建议 ID（§7.8）。
REJECT = "reject"

#: 幂等窗口：§6.7 第 1 步的「最近 10 分钟」。窗口外的记录不再复用。
IDEMPOTENCY_WINDOW_SECONDS = 600


def group_key_of(item: Dict[str, Any]) -> str:
    """审阅分组键（§6.7 第 3 步）：`locator or node_path or node_key`。

    这里**不复用** `storage.list_review_items`：它按 `(source_file, node_key or node_path)`
    分组，而 M2-03 写入的新建议是 `node_key=""` **且带 locator**，旧分组会退化成按旧节点 ID
    并组——同名兄弟共享旧 ID，于是「item 第 1 个」与「item 第 2 个」的建议被并成一组，批准
    其中一条会把另一条连带驳回，写回也可能指错节点（正是 v1 的根因）。
    """
    return str(item.get("locator") or item.get("node_path") or item.get("node_key") or "")


def _pending_items(file_name: Optional[str] = None) -> List[Dict[str, Any]]:
    """磁盘顺序的 pending 建议；旧记录缺 `status` 时按 pending 处理（§5.2）。"""
    out: List[Dict[str, Any]] = []
    for item in storage.load_edit_remarks():
        if str(item.get("status") or "pending") != "pending":
            continue
        if file_name is not None and str(item.get("source_file") or "") != file_name:
            continue
        out.append(item)
    return out


def _group_by_key(items: Sequence[Dict[str, Any]]) -> Dict[str, List[Dict[str, Any]]]:
    groups: Dict[str, List[Dict[str, Any]]] = {}
    for item in items:
        groups.setdefault(group_key_of(item), []).append(item)
    return groups


def _parse_current(content: bytes, name: str) -> Optional[Dict[str, Any]]:
    """当前原文的 compact 文档；解析不了返回 None。

    「读得出但解析不了」不是错误：这份原文的节点无法定位，审阅列表把每一组标成
    `resolvable=false`，管理员于是仍能把建议**驳回**（只有「通过」才需要定位）。
    """
    try:
        return parser.to_compact(content, name)
    except AppError as exc:
        if exc.code in (PARSE_FAILED, TOO_LARGE):
            return None
        raise


def _current(profile_id: str, name: str) -> Tuple[Optional[documents.RawFile], Optional[Dict[str, Any]]]:
    """当前原文的 `(读数, compact)`。

    文件不存在 → `(None, None)`；存在但解析不了 → `(raw, None)`——`raw.sha256` 仍要给出，
    否则前端只能带空 `source_sha` 提交，连「驳回」这条唯一的出路都会被 409 挡死。
    """
    try:
        raw = documents.load_raw(profile_id, name, "current")
    except AppError as exc:
        if exc.code == NOT_FOUND:
            return None, None
        raise
    return raw, _parse_current(raw.data, name)


def _indexes(nodes: Optional[Dict[str, List[Any]]]) -> Tuple[Dict[str, int], Dict[str, List[int]]]:
    """`loc → 下标` 与 `旧 ID → 下标列表`；后者用来认出「同名兄弟」（§6.2）。"""
    if nodes is None:
        return {}, {}
    by_loc: Dict[str, int] = {}
    by_id: Dict[str, List[int]] = {}
    for i, node_id in enumerate(nodes["id"]):
        by_loc[nodes["loc"][i]] = i
        by_id.setdefault(node_id, []).append(i)
    return by_loc, by_id


def _locate(
    nodes: Optional[Dict[str, List[Any]]],
    locator: str,
    node_path: str,
    by_loc: Dict[str, int],
    by_id: Dict[str, List[int]],
) -> Tuple[int, bool]:
    """`(下标, 是否 legacy)`；下标 -1 表示无法唯一定位（§6.7 第 4 步、R7）。

    有 locator 且当前文档里还有这个 loc 就用它；locator 失效时仍按旧 ID 试一次，**恰好 1 个**
    匹配才算可定位——多个匹配意味着不知道该改哪一个，只能驳回。`legacy` 只表示「这条建议没有
    locator」，用来打「旧版」徽标（`review.js:87`），不参与能不能通过的判定。口径与
    `mock-api.js` 的 `locateRemark` 一致。
    """
    if nodes is None:
        return -1, not locator
    if locator and locator in by_loc:
        return by_loc[locator], False
    hits = by_id.get(node_path or "")
    if hits and len(hits) == 1:
        return hits[0], not locator
    return -1, not locator


def _group_locate(
    items: Sequence[Dict[str, Any]],
    nodes: Optional[Dict[str, List[Any]]],
    by_loc: Dict[str, int],
    by_id: Dict[str, List[int]],
) -> Tuple[int, bool]:
    """按组的第一条建议定位（同组建议共享同一个 locator，§6.7 第 3 步）。"""
    first = items[0]
    return _locate(
        nodes, str(first.get("locator") or ""), str(first.get("node_path") or ""), by_loc, by_id
    )


def _remark_view(item: Dict[str, Any]) -> Dict[str, Any]:
    """§7.8 的一条建议（`review.js:104` 只读这五个键）。"""
    return {
        "id": str(item.get("id") or ""),
        "actor_display": str(item.get("actor_display") or ""),
        "actor_ip": str(item.get("actor_ip") or ""),
        "proposed_value": str(item.get("proposed_value") or ""),
        "created_at": str(item.get("created_at") or ""),
    }


def _node_view(
    group_key: str,
    items: Sequence[Dict[str, Any]],
    nodes: Optional[Dict[str, List[Any]]],
    index: int,
    legacy: bool,
) -> Dict[str, Any]:
    """§7.8 的一个待审节点；`index` 来自 `_locate`（-1 = 定位不到）。

    `stale` 与 `resolvable` 是 `review.js:103` 唯一读的两个判定布尔，也是服务端放行「通过」
    的条件，所以列表与提交必须共用这份算法（R6/R7/R8）。
    """
    first = items[0]
    node_path = str(first.get("node_path") or "")
    locator = str(first.get("locator") or "")
    original = str(first.get("original_value") or "")
    located = index >= 0 and nodes is not None
    current = nodes["v"][index] if located else None
    return {
        "group_key": group_key,
        "loc": nodes["loc"][index] if located else (locator or None),
        "id": node_path,
        "label": str(first.get("node_label") or ""),
        "path": searching.display_path(nodes, index) if located else node_path,
        "node_type": str(first.get("node_type") or ""),
        "original_value": original,
        # R6：节点无值是 JSON `null`（`review.js:137` 判的是 `=== null`），不能给空串。
        "current_value": current,
        # §7.8：`stale = resolvable && current_value != original_value`。
        "stale": bool(located and current != original),
        "legacy": bool(legacy),
        "resolvable": bool(located),
        "remarks": [_remark_view(item) for item in items],
    }


def list_review(profile_id: str) -> Dict[str, Any]:
    """`GET /p/{pid}/review` 的 payload：`{"files": […]}`（§7.8）。

    仅管理员——授权在 handler 里做。每个文件只读一次原文、只解析一次；未决定的组保持原样
    列出，已经通过/驳回的建议不再出现（第 3 步的 pending 口径）。
    """
    with storage.use_profile(profile_id):
        grouped: Dict[str, Dict[str, List[Dict[str, Any]]]] = {}
        for item in _pending_items():
            name = str(item.get("source_file") or "")
            if not name:
                continue  # 手改坏数据里没有文件名的记录：列出来也无处提交，只能跳过
            grouped.setdefault(name, {}).setdefault(group_key_of(item), []).append(item)

        files: List[Dict[str, Any]] = []
        for name in sorted(grouped):
            raw, compact = _current(profile_id, name)
            nodes = compact["nodes"] if compact else None
            by_loc, by_id = _indexes(nodes)
            nodes_out: List[Dict[str, Any]] = []
            for key, items in grouped[name].items():
                index, legacy = _group_locate(items, nodes, by_loc, by_id)
                nodes_out.append(_node_view(key, items, nodes, index, legacy))
            files.append(
                {
                    "file": name,
                    "exists": raw is not None,
                    "current_sha": raw.sha256 if raw is not None else None,
                    "nodes": nodes_out,
                }
            )
    return {"files": files}


def _read_payload(payload: Dict[str, Any]) -> Tuple[str, str, str, List[Dict[str, str]]]:
    """§7.8 请求体的形状检查：缺字段 → 400 + `detail.field`。

    服务层自己验一遍：handler 之外还有直接调服务的调用方（与测试），而带着空
    `request_id` / `source_sha` 走进幂等与冲突判定会更难解释。
    """
    request_id = str(payload.get("request_id") or "").strip()
    if not request_id:
        raise AppError(INVALID_INPUT, "缺少 request_id", field="request_id")
    file_name = str(payload.get("file") or "").strip()
    if not file_name:
        raise AppError(INVALID_INPUT, "缺少 file", field="file")
    source_sha = str(payload.get("source_sha") or "").strip()
    if not source_sha:
        raise AppError(INVALID_INPUT, "缺少 source_sha", field="source_sha")
    raw = payload.get("decisions")
    if not isinstance(raw, list):
        raise AppError(INVALID_INPUT, "decisions 必须是数组", field="decisions")
    decisions: List[Dict[str, str]] = []
    for entry in raw:
        if not isinstance(entry, dict):
            raise AppError(INVALID_INPUT, "decisions 的每一项都必须是对象", field="decisions")
        group_key = str(entry.get("group_key") or "").strip()
        choice = str(entry.get("choice") or "").strip()
        if not group_key or not choice:
            raise AppError(
                INVALID_INPUT,
                "每个决定都要给出 group_key 与 choice",
                field="decisions",
                group_key=group_key,
            )
        decisions.append({"group_key": group_key, "choice": choice})
    return request_id, file_name, source_sha, decisions


def _submit_locked(
    profile_id: str,
    actor: Optional[Actor],
    file_name: str,
    source_sha: str,
    decisions: Sequence[Dict[str, str]],
) -> Dict[str, Any]:
    """§6.7 第 2–8 步，全部在调用方持有的机型锁内。"""
    raw, compact = _current(profile_id, file_name)
    if raw is None:
        raise AppError(NOT_FOUND, "源文件不存在", file=file_name)
    if raw.sha256 != source_sha:
        raise AppError(REVIEW_CONFLICT, "源文件在你审阅期间已被更新，请刷新后重新核对")

    nodes = compact["nodes"] if compact else None
    by_loc, by_id = _indexes(nodes)
    groups = _group_by_key(_pending_items(file_name))

    approved_ids: List[str] = []
    rejected_ids: List[str] = []
    edits: List[Tuple[str, str, str]] = []
    #: 已决定「通过」的节点下标 → group_key。两个不同的组（locator 组 + 按旧 ID 命中的 legacy
    #: 组）可能定位到同一个节点，同一个 group_key 也可能在 `decisions` 里出现两次；两种情况
    #: 都会给同一个 loc 两个值，`apply_edits` 只能落一个 → 必须先拒掉，不能让第 6 步事后拒绝
    #: 变成 422（那是「写回不安全」，不是「请求自相矛盾」）。
    targeted: Dict[int, str] = {}
    for decision in decisions:
        group_key = decision["group_key"]
        group = groups.get(group_key)
        if not group:
            # 允许部分提交，也接受「这个组刚刚被别人决定了」：不是客户端错误（第 3 步）。
            continue
        if decision["choice"] == REJECT:
            rejected_ids.extend(str(item.get("id") or "") for item in group)
            continue
        chosen = next(
            (item for item in group if str(item.get("id") or "") == decision["choice"]), None
        )
        if chosen is None:
            raise AppError(
                INVALID_INPUT,
                "choice 只能是该组的建议 ID 或 reject",
                field=group_key,
                group_key=group_key,
            )
        index, legacy = _group_locate(group, nodes, by_loc, by_id)
        node = _node_view(group_key, group, nodes, index, legacy)
        if node["stale"] or not node["resolvable"]:
            # 第 7 步：stale / 定位不到的节点只能驳回。前端已经禁用了按钮（R7），这是服务端
            # 那一半——报 400 指出是哪个组，不静默驳回，也不伪装成 409。
            raise AppError(
                INVALID_INPUT,
                "该节点的当前值已变化或无法唯一定位，只能驳回",
                field=group_key,
                group_key=group_key,
                reason="stale" if node["stale"] else "unresolvable",
            )
        if index in targeted:
            raise AppError(
                INVALID_INPUT,
                "两个决定指向同一个节点，只能各通过一条建议",
                field=group_key,
                group_key=group_key,
                other_group_key=targeted[index],
            )
        if nodes["v"][index] != str(chosen.get("original_value") or ""):
            # 第 4 步：同组各条建议的「提交时原值」可能不同（先后提交的间隔里文件被动过）。
            raise AppError(
                REVIEW_CONFLICT,
                "该节点的当前值与建议提交时的原值不一致",
                loc=node["loc"],
                label=node["label"],
            )
        targeted[index] = group_key
        # `kind` 取自**同一份** compact 文档的 `t`（卡 1 的接口契约），不是建议里存的字符串。
        edits.append((node["loc"], str(chosen.get("proposed_value") or ""), nodes["t"][index]))
        approved_ids.append(str(chosen.get("id") or ""))
        rejected_ids.extend(str(item.get("id") or "") for item in group if item is not chosen)

    # 第 5–6 步先跑完：`apply_edits` 内含第 6 步的再解析比对，任一组不过就在这里抛出，
    # 下面的写文件与状态更新都不会发生（§6.7 第 6 步「不写任何文件，不改任何状态」）。
    generated: Optional[str] = None
    if edits:
        content = reviewing.apply_edits(raw.data, compact["format"], edits)
        generated = storage.save_review_generated_file(file_name, content)

    approved = rejected = 0
    if approved_ids or rejected_ids:
        counts = storage.apply_review_results(
            approved_ids=approved_ids,
            rejected_ids=rejected_ids,
            reviewer_name=actor.name if actor else "",
            reviewer_ip=actor.ip if actor else "",
            # 一个文件一次提交只生成一份文件（§6.7 第 8 步的 generated_file 口径）。
            generated_files={file_name: generated} if generated else None,
        )
        approved, rejected = counts["approved"], counts["rejected"]
    if approved or rejected:
        revision.bump(profile_id, PART)
    return {"generated_file": generated, "approved": approved, "rejected": rejected}


def _idempotency_hit(profile_id: str, request_id: str) -> Optional[Dict[str, Any]]:
    """命中窗口内的记录就返回上次结果（第 1 步），否则 None。"""
    with storage.use_profile(profile_id):
        record = storage.load_review_idempotency().get(request_id)
    if not isinstance(record, dict):
        return None
    at, result = record.get("at"), record.get("result")
    if not isinstance(result, dict) or not isinstance(at, (int, float)):
        return None
    if time.time() - float(at) > IDEMPOTENCY_WINDOW_SECONDS:
        return None
    return dict(result)


def _record_idempotency(profile_id: str, request_id: str, result: Dict[str, Any]) -> None:
    """登记本次结果，并顺手把窗口外的记录清掉（表必须落盘：卡面要求重启后仍然生效）。"""
    now = time.time()
    cutoff = now - IDEMPOTENCY_WINDOW_SECONDS
    with storage.use_profile(profile_id):
        table = {
            key: value
            for key, value in storage.load_review_idempotency().items()
            if isinstance(value, dict)
            and isinstance(value.get("at"), (int, float))
            and float(value["at"]) >= cutoff
        }
        table[request_id] = {"at": now, "result": dict(result)}
        storage.save_review_idempotency(table)


def submit_review(
    profile_id: str, actor: Optional[Actor], payload: Dict[str, Any]
) -> Dict[str, Any]:
    """按文件提交审阅结果（§6.7 第 1–9 步），返回 §7.8 的响应体。

    第 3–8 步都在机型锁内（`profile_write` 与 `apply_review_results` 内部的锁是同一把
    RLock，可重入），锁内只有本地读写与 CPU 计算，没有网络 I/O。
    """
    request_id, file_name, source_sha, decisions = _read_payload(payload)
    cached = _idempotency_hit(profile_id, request_id)
    if cached is not None:
        return cached
    with storage.profile_write(profile_id), storage.use_profile(profile_id):
        # 命中判定也在锁内再来一次：同一个 request_id 的并发重试被这把锁串行化，
        # 后一次只能读到前一次登记的结果，不会生成第二份文件。
        cached = _idempotency_hit(profile_id, request_id)
        if cached is not None:
            return cached
        result = _submit_locked(profile_id, actor, file_name, source_sha, decisions)
        _record_idempotency(profile_id, request_id, result)
    return result
