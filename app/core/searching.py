from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional


@dataclass
class SearchHit:
    """搜索命中分类"""
    hit_type: str  # "node" | "leaf" | "filename" | "note"
    path: str
    label: str
    value: Any = None


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
            # 父节点标签命中也计为 1 个结果，不因 value is None 被误判"无结果"
            matched_cnt = 1
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


def classify_search_hits(
    node: Dict[str, Any],
    query: str,
    note_map: Optional[Dict[str, str]] = None,
) -> List[SearchHit]:
    """分类搜索命中：node(父节点标签)/leaf(叶子值)/note(备注)/filename(文件名)

    父节点命中即使 leaf count=0 也返回结果。
    """
    kw = (query or "").strip().lower()
    if not kw:
        return []
    hits: List[SearchHit] = []
    _classify_recursive(node, kw, note_map or {}, hits)
    return hits


def _classify_recursive(
    node: Dict[str, Any],
    kw: str,
    note_map: Dict[str, str],
    hits: List[SearchHit],
) -> None:
    """递归分类搜索命中"""
    label = str(node.get("label") or "")
    value = node.get("value")
    nid = node.get("id") or ""
    note = note_map.get(nid, "") or node.get("note", "") or ""

    label_hit = kw in label.lower()
    value_hit = value is not None and kw in str(value).lower()
    note_hit = kw in note.lower()

    if label_hit:
        if value is not None:
            hits.append(SearchHit(hit_type="leaf", path=nid, label=label, value=value))
        else:
            hits.append(SearchHit(hit_type="node", path=nid, label=label))
    if value_hit and not label_hit:
        hits.append(SearchHit(hit_type="leaf", path=nid, label=label, value=value))
    if note_hit and not label_hit and not value_hit:
        hits.append(SearchHit(hit_type="note", path=nid, label=label))

    for child in node.get("children", []) or []:
        if isinstance(child, dict):
            _classify_recursive(child, kw, note_map, hits)


def has_search_results(hits: List[SearchHit]) -> bool:
    """判断是否有搜索结果（任何类型的命中都算）"""
    return len(hits) > 0
