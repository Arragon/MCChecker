"""收藏服务（§7.7；T-09 / M2-01）。

要点：

* 实时值按**旧节点 ID**（`favorite["path"]`）匹配。同名兄弟共享旧 ID，因此取先序
  **最后一个**——与 v1 `favorites_live._build_id_index` 的「后写覆盖」结果一致，
  也是 §7.7 明文规定的行为。
* 快照、覆盖判断、label/value 取值全部基于 compact 文档（`services.documents`），
  不再依赖旧的嵌套树 + `cache/parse_tree`（那是 v1 的 `favorites_live`，留给 v1 页面）。
* 落盘一律走 `storage` 的收藏接口：原子写 + 机型锁 + 损坏 JSON 只读保护已在里面处理。
* 写成功后递增 `favorites` 修订计数（§7.1）。
"""

from __future__ import annotations

import copy
from typing import Any, Dict, List, Optional, Tuple

from ..core import storage
from ..core.errors import FAVORITE_COVERED, INVALID_INPUT, NOT_FOUND, AppError
from . import documents, revision

PART = "favorites"


def _doc_or_none(profile_id: str, name: str) -> Optional[Dict[str, Any]]:
    """当前版本能否读到（文件不存在 → None，其它错误照常抛出）。"""
    try:
        return documents.load_document(profile_id, name, "current")
    except AppError as exc:
        if exc.code == NOT_FOUND:
            return None
        raise


def _index_by_id(nodes: Dict[str, List[Any]]) -> Dict[str, List[int]]:
    """旧 ID → 先序下标列表（同名兄弟会命中多个）。"""
    index: Dict[str, List[int]] = {}
    for i, node_id in enumerate(nodes["id"]):
        index.setdefault(node_id, []).append(i)
    return index


def _snapshot_children(nodes: Dict[str, List[Any]], parent: int) -> List[Dict[str, Any]]:
    """把 `parent` 的直接子节点（递归）快照成 v1 已有的 `children` 结构。"""
    out: List[Dict[str, Any]] = []
    parents = nodes["p"]
    for child in range(parent + 1, len(parents)):
        if parents[child] != parent:
            continue
        out.append(
            {
                "label": nodes["l"][child],
                "value": nodes["v"][child],
                "path": nodes["id"][child],
                "children": _snapshot_children(nodes, child),
            }
        )
    return out


def _covered_paths(item: Dict[str, Any]) -> List[str]:
    """一个收藏项（含其子树快照）覆盖到的全部旧 ID。"""
    paths: List[str] = []
    stack = list(item.get("children") or [])
    while stack:
        node = stack.pop()
        paths.append(node.get("path") or "")
        stack.extend(node.get("children") or [])
    return paths


def _resolve_live(item: Dict[str, Any], nodes: Optional[Dict[str, List[Any]]], index: Optional[Dict[str, List[int]]]) -> Dict[str, Any]:
    """按 §7.7 填 `saved_value` / `value` / `missing`；文件读不到时整项标缺失。"""
    live = copy.deepcopy(item)
    live["saved_value"] = item.get("value")
    if nodes is None or index is None:
        live["missing"] = True
        return live
    hits = index.get(item.get("path") or "")
    live["missing"] = not hits
    if hits:
        live["value"] = nodes["v"][hits[-1]]
    for child in live.get("children") or []:
        _resolve_child_live(child, nodes, index)
    return live


def _resolve_child_live(child: Dict[str, Any], nodes: Dict[str, List[Any]], index: Dict[str, List[int]]) -> None:
    hits = index.get(child.get("path") or "")
    child["missing"] = not hits
    if hits:
        child["value"] = nodes["v"][hits[-1]]
    for sub in child.get("children") or []:
        _resolve_child_live(sub, nodes, index)


def list_groups(profile_id: str) -> List[Dict[str, Any]]:
    """§7.7 的 `groups`：按源文件分组，存在的文件在前，同组内按文件名排序。"""
    with storage.use_profile(profile_id):
        favorites = storage.load_favorites()
    groups: Dict[str, List[Dict[str, Any]]] = {}
    for fav in favorites:
        name = fav.get("source_file") or ""
        if not name:
            continue
        groups.setdefault(name, []).append(fav)

    out: List[Dict[str, Any]] = []
    for name, items in groups.items():
        doc = _doc_or_none(profile_id, name)
        nodes = doc["nodes"] if doc else None
        index = _index_by_id(nodes) if nodes else None
        out.append(
            {
                "file": name,
                "exists": doc is not None,
                "updated_at": doc["file"]["updated_at"] if doc else None,
                "items": [_resolve_live(item, nodes, index) for item in items],
            }
        )
    out.sort(key=lambda g: (0 if g["exists"] else 1, g["file"]))
    return out


def add(profile_id: str, file: str, node_id: str) -> Tuple[Dict[str, Any], bool]:
    """收藏一个节点。返回 `(项, 是否新建)`；已存在时返回原项且 `False`（§7.7）。"""
    doc = _doc_or_none(profile_id, file)
    if doc is None:
        raise AppError(NOT_FOUND, "文件不存在", file=file)
    nodes = doc["nodes"]
    hits = _index_by_id(nodes).get(node_id)
    if not hits:
        raise AppError(NOT_FOUND, "节点不存在", id=node_id)
    i = hits[-1]

    with storage.use_profile(profile_id):
        favorites = storage.load_favorites()
        for fav in favorites:
            if fav.get("source_file") == file and fav.get("path") == node_id:
                return fav, False
        for fav in favorites:
            if fav.get("source_file") != file or fav.get("path") == node_id:
                continue
            if node_id in _covered_paths(fav):
                raise AppError(
                    FAVORITE_COVERED,
                    "该节点已被父级收藏「%s」包含" % (fav.get("label") or fav.get("path")),
                    parent=fav.get("path"),
                )
        children = _snapshot_children(nodes, i) if nodes["v"][i] is None else []
        if nodes["v"][i] is None and not children:
            raise AppError(INVALID_INPUT, "该节点下没有可收藏的变量", id=node_id)
        storage.add_favorite(
            node_id, nodes["l"][i], nodes["v"][i], file, note="", children=children
        )
        saved = storage.load_favorites()
    item = next(
        f for f in saved if f.get("source_file") == file and f.get("path") == node_id
    )
    revision.bump(profile_id, PART)
    return item, True


def set_note(profile_id: str, file: str, path: str, note: str) -> Dict[str, Any]:
    """更新收藏备注（§7.7 的 PATCH）。"""
    with storage.use_profile(profile_id):
        if not storage.update_favorite_note(path, file, note or ""):
            raise AppError(NOT_FOUND, "收藏不存在", path=path)
        saved = storage.load_favorites()
    revision.bump(profile_id, PART)
    return next(
        f for f in saved if f.get("source_file") == file and f.get("path") == path
    )


def remove(profile_id: str, file: str, path: Optional[str] = None) -> int:
    """删除一项收藏；不带 `path` 时清空该文件的全部收藏。返回删除条数。"""
    with storage.use_profile(profile_id):
        before = storage.load_favorites()
        if path:
            after = [
                f
                for f in before
                if not (f.get("source_file") == file and f.get("path") == path)
            ]
        else:
            after = [f for f in before if f.get("source_file") != file]
        if len(after) != len(before):
            storage.save_favorites(after)
        removed = len(before) - len(after)
    if removed:
        revision.bump(profile_id, PART)
    return removed
