from __future__ import annotations

from typing import Any, Dict, List, Optional


def display_path(nodes: Dict[str, List[Any]], index: int) -> str:
    """节点自身与各级祖先的 label 链（不含文件名），用 ` › ` 连接。"""
    parents = nodes["p"]
    labels = nodes["l"]
    parts: List[str] = []
    i = index
    while i > 0:
        parts.append(str(labels[i]))
        i = parents[i]
    return " › ".join(reversed(parts))


def search_compact(
    nodes: Dict[str, List[Any]],
    keyword: str,
    note_map: Optional[Dict[str, str]] = None,
) -> List[Dict[str, Any]]:
    """扫描一个 compact 文档的并列数组，返回命中项（§7.6）。

    * 判定顺序 label → value → note，第一个命中的写进 `match`。
    * **每个命中节点计 1**（含容器节点）——旧版只在有值节点上计数，父节点名字
      命中却显示「未找到」。
    * `note_map` 以旧节点 ID 为键，因此同名兄弟会一起命中备注（§7.7 的关联方式）。

    这里**不做**整篇预筛——`services.search` 已经在反序列化之前按响应体字节筛过一轮。
    """
    kw = (keyword or "").strip().lower()
    if not kw:
        return []
    types = nodes["t"]
    labels = nodes["l"]
    values = nodes["v"]
    ids = nodes["id"]
    locs = nodes["loc"]

    notes = note_map or {}
    out: List[Dict[str, Any]] = []
    for i in range(1, len(labels)):
        label = labels[i]
        value = values[i]
        if kw in label.lower():
            match = "label"
        elif value is not None and kw in value.lower():
            match = "value"
        else:
            note = notes.get(ids[i], "")
            if not note or kw not in note.lower():
                continue
            match = "note"
        out.append(
            {
                "id": ids[i],
                "loc": locs[i],
                "path": display_path(nodes, i),
                "label": label,
                "value": value,
                "type": types[i],
                "note": notes.get(ids[i], ""),
                "match": match,
            }
        )
    return out


def build_note_map(favorites: list[dict], source_file: str) -> Dict[str, str]:
    note_map: Dict[str, str] = {}
    for fav in favorites:
        if fav.get("source_file") != source_file:
            continue
        path = fav.get("path")
        if not path:
            continue
        note = (fav.get("note") or "").strip()
        if note:
            note_map[path] = note
    return note_map


# 以下三个函数服务 v1 的嵌套树页面（`app/pages/`），M4-07/M4-08 删除页面时一起移除。


def filter_tree_and_count(
    node: Dict[str, Any],
    keyword: str,
    note_map: Optional[Dict[str, str]] = None,
) -> tuple[Optional[Dict[str, Any]], int]:
    kw = (keyword or "").strip().lower()
    if not kw:
        return node, 0

    def _filter(n: Dict[str, Any]) -> tuple[Optional[Dict[str, Any]], int]:
        label = str(n.get("label") or "")
        value = n.get("value")
        nid = n.get("id") or ""
        note = ""
        if note_map and nid:
            note = note_map.get(nid, "")

        hay = (label + " " + ("" if value is None else str(value)) + " " + note).lower()
        matched = kw in hay

        if matched:
            out = dict(n)
            if note:
                out["note"] = note
            matched_cnt = 1 if value is not None else 0
            return out, matched_cnt

        children = n.get("children") or []
        filtered_children = []
        matched_cnt = 0
        for c in children:
            if not isinstance(c, dict):
                continue
            fc, c_cnt = _filter(c)
            if fc is not None:
                filtered_children.append(fc)
            matched_cnt += c_cnt

        if not filtered_children:
            return None, 0

        out = {
            "id": n.get("id"),
            "label": n.get("label"),
            "value": n.get("value"),
            "children": filtered_children,
            "attrs": n.get("attrs", {}),
        }
        if note:
            out["note"] = note
        return out, matched_cnt

    return _filter(node)


def filter_tree(node: Dict[str, Any], keyword: str, note_map: Optional[Dict[str, str]] = None) -> Optional[Dict[str, Any]]:
    filtered, _ = filter_tree_and_count(node, keyword, note_map)
    return filtered


def count_value_nodes(node: Dict[str, Any]) -> int:
    cnt = 0
    if node.get("value") is not None:
        cnt += 1
    for c in node.get("children", []) or []:
        if isinstance(c, dict):
            cnt += count_value_nodes(c)
    return cnt
