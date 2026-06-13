from __future__ import annotations

from typing import Any, Dict, Optional


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
