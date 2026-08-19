"""审阅修改核心逻辑。"""

from __future__ import annotations

import json
import re
import xml.etree.ElementTree as ET
from copy import deepcopy
from typing import Any, Dict, Iterable, List


_ARRAY_LABEL_RE = re.compile(r"^\[(\d+)\]$")


def make_node_key(parent_key: str, child_index: int) -> str:
    return f"{parent_key}.{child_index}" if parent_key else str(child_index)


def get_node_by_key(tree: Dict[str, Any], node_key: str) -> Dict[str, Any]:
    key_text = str(node_key or "").strip()
    if not key_text:
        raise ValueError("节点标识不能为空")

    node = tree
    for part in key_text.split("."):
        try:
            index = int(part)
        except ValueError as e:
            raise ValueError("节点标识格式不正确") from e
        children = node.get("children") or []
        if index < 0 or index >= len(children):
            raise ValueError("节点标识不存在")
        node = children[index]
    return node


def get_node_by_path(tree: Dict[str, Any], node_path: str) -> Dict[str, Any]:
    """按解析树节点 id（稳定路径）在树中查找节点。

    相比 get_node_by_key（基于数组索引），路径 id 在搜索筛选、源文件更新、
    解析缓存重建后依然稳定，可避免定位漂移 / 越界。
    """
    target = str(node_path or "").strip()
    if not target:
        raise ValueError("节点路径不能为空")

    def _walk(node: Dict[str, Any]) -> Dict[str, Any] | None:
        if str(node.get("id") or "") == target:
            return node
        for child in node.get("children") or []:
            if not isinstance(child, dict):
                continue
            found = _walk(child)
            if found is not None:
                return found
        return None

    result = _walk(tree)
    if result is None:
        raise ValueError("节点路径不存在")
    return result


def apply_review_updates(tree: Dict[str, Any], approved_items: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
    copied = deepcopy(tree)
    for item in approved_items:
        # 优先使用稳定的节点路径（解析树 id）定位；仅当缺失时回退到旧索引键。
        node_path = item.get("node_path") or item.get("node_key") or ""
        try:
            node = get_node_by_path(copied, node_path)
        except ValueError:
            node = get_node_by_key(copied, item.get("node_key") or "")
        node["value"] = str(item.get("proposed_value") or "")
    return copied


def serialize_tree(tree: Dict[str, Any], filename: str) -> bytes:
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    tree_type = str((tree.get("attrs") or {}).get("type") or ext).lower()
    if tree_type == "xml" or ext == "xml":
        return _serialize_xml_tree(tree)
    if tree_type == "json" or ext == "json":
        return _serialize_json_tree(tree)
    raise ValueError("暂不支持该文件类型的审阅输出")


def _serialize_json_tree(tree: Dict[str, Any]) -> bytes:
    data = _json_children_to_data(tree.get("children") or [])
    return json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")


def _json_children_to_data(children: List[Dict[str, Any]]) -> Any:
    if not children:
        return {}
    if _is_array_children(children):
        ordered = sorted(children, key=lambda item: _array_label_index(item.get("label") or ""))
        return [_json_node_to_data(child) for child in ordered]
    return {str(child.get("label") or ""): _json_node_to_data(child) for child in children}


def _json_node_to_data(node: Dict[str, Any]) -> Any:
    children = node.get("children") or []
    node_type = str((node.get("attrs") or {}).get("type") or "")

    if children:
        if node_type == "array" or _is_array_children(children):
            ordered = sorted(children, key=lambda item: _array_label_index(item.get("label") or ""))
            return [_json_node_to_data(child) for child in ordered]
        return {str(child.get("label") or ""): _json_node_to_data(child) for child in children}

    if node.get("value") is None:
        if node_type == "array":
            return []
        if node_type == "object":
            return {}
        return None

    return _parse_json_primitive(str(node.get("value") or ""), node_type)


def _parse_json_primitive(value: str, node_type: str) -> Any:
    if node_type == "bool":
        return value.strip().lower() == "true"
    if node_type == "NoneType":
        return None
    if node_type == "int":
        return int(value)
    if node_type == "float":
        return float(value)
    return value


def _is_array_children(children: List[Dict[str, Any]]) -> bool:
    if not children:
        return False
    indexes = []
    for child in children:
        match = _ARRAY_LABEL_RE.match(str(child.get("label") or ""))
        if not match:
            return False
        indexes.append(int(match.group(1)))
    return sorted(indexes) == list(range(len(indexes)))


def _array_label_index(label: str) -> int:
    match = _ARRAY_LABEL_RE.match(label)
    if not match:
        raise ValueError("数组节点标签不合法")
    return int(match.group(1))


def _serialize_xml_tree(tree: Dict[str, Any]) -> bytes:
    roots = tree.get("children") or []
    if not roots:
        raise ValueError("XML 树为空")
    root = _xml_node_to_element(roots[0])
    try:
        ET.indent(root, space="  ")
    except Exception:
        pass
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)


def _xml_node_to_element(node: Dict[str, Any]) -> ET.Element:
    elem = ET.Element(str(node.get("label") or "node"))
    text_value = str(node.get("value")) if node.get("value") is not None else None

    for child in node.get("children") or []:
        label = str(child.get("label") or "")
        if label.startswith("@"):
            elem.set(label[1:], str(child.get("value") or ""))
            continue
        if label == "#text":
            text_value = str(child.get("value") or "")
            continue
        elem.append(_xml_node_to_element(child))

    if text_value is not None:
        elem.text = text_value
    return elem
