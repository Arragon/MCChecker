import os
from copy import deepcopy
from typing import Any, Dict, List, Optional, Tuple

from . import parse_cache, parser, storage


MISSING_TEXT = "未检测到，请检查"


def _build_id_index(node: Dict[str, Any], idx: Dict[str, Dict[str, Any]]) -> None:
    node_id = node.get("id")
    if node_id is not None:
        idx[node_id] = node
    for child in node.get("children", []):
        _build_id_index(child, idx)


def _get_tree_for_file(source_path: str, filename: str) -> Optional[Dict[str, Any]]:
    tree = parse_cache.load_tree(source_path)
    if tree is not None:
        return tree
    content = storage.load_config_file(filename)
    if content is None:
        return None
    tree = parser.parse_file(content, filename)
    parse_cache.save_tree(source_path, tree)
    return tree


def _resolve_fav_item(item: Dict[str, Any], id_index: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    resolved = deepcopy(item)
    path = item.get("path") or ""
    node = id_index.get(path)
    if not node:
        resolved["_missing"] = True
    else:
        resolved["_missing"] = False

    children = resolved.get("children")
    if children:
        _resolve_children(children, id_index)
    else:
        if node and node.get("value") is not None:
            resolved["value"] = node.get("value")
        else:
            resolved["value"] = MISSING_TEXT
    return resolved


def _resolve_children(children: List[Dict[str, Any]], id_index: Dict[str, Dict[str, Any]]) -> None:
    for child in children:
        path = child.get("path") or ""
        node = id_index.get(path)
        if node and node.get("value") is not None:
            child["value"] = node.get("value")
        else:
            if not child.get("children"):
                child["value"] = MISSING_TEXT
            else:
                child["value"] = child.get("value")
        if child.get("children"):
            _resolve_children(child["children"], id_index)


def resolve_overview_favorites(favorites: List[Dict[str, Any]]) -> Tuple[Dict[str, List[Dict[str, Any]]], Dict[str, List[Dict[str, Any]]]]:
    active: Dict[str, List[Dict[str, Any]]] = {}
    deleted: Dict[str, List[Dict[str, Any]]] = {}

    grouped: Dict[str, List[Dict[str, Any]]] = {}
    for fav in favorites:
        filename = fav.get("source_file") or ""
        if filename:
            grouped.setdefault(filename, []).append(fav)

    for filename, items in grouped.items():
        source_path = storage.get_config_path(filename)
        if not os.path.exists(source_path):
            deleted[filename] = [deepcopy(f) for f in items]
            continue

        tree = _get_tree_for_file(source_path, filename)
        if tree is None:
            deleted[filename] = [deepcopy(f) for f in items]
            continue

        id_index: Dict[str, Dict[str, Any]] = {}
        _build_id_index(tree, id_index)
        active[filename] = [_resolve_fav_item(f, id_index) for f in items]

    return active, deleted

