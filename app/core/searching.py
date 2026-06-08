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


def filter_tree(node: Dict[str, Any], keyword: str, note_map: Optional[Dict[str, str]] = None) -> Optional[Dict[str, Any]]:
    kw = (keyword or "").strip().lower()
    if not kw:
        return node

    label = str(node.get("label") or "")
    value = node.get("value")
    nid = node.get("id") or ""
    note = ""
    if note_map and nid:
        note = note_map.get(nid, "")

    hay = (label + " " + ("" if value is None else str(value)) + " " + note).lower()
    matched = kw in hay

    children = node.get("children") or []
    filtered_children = []
    for c in children:
        if not isinstance(c, dict):
            continue
        fc = filter_tree(c, kw, note_map)
        if fc is not None:
            filtered_children.append(fc)

    if not matched and not filtered_children:
        return None

    out = {
        "id": node.get("id"),
        "label": node.get("label"),
        "value": node.get("value"),
        "children": filtered_children,
        "attrs": node.get("attrs", {}),
    }
    if note:
        out["note"] = note
    return out


def count_value_nodes(node: Dict[str, Any]) -> int:
    cnt = 0
    if node.get("value") is not None:
        cnt += 1
    for c in node.get("children", []) or []:
        if isinstance(c, dict):
            cnt += count_value_nodes(c)
    return cnt

