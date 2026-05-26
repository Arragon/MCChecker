"""XML/JSON 配置文件解析引擎

将 XML 和 JSON 文件统一转换为树形结构，供前端展示。
树形结构格式: {"id": str, "label": str, "value": Optional[str], "children": list, "attrs": dict}
"""

import json
import xml.etree.ElementTree as ET
from typing import Any, Dict, List, Optional


def parse_file(content: bytes, filename: str) -> Dict[str, Any]:
    """解析文件内容为统一树形结构"""
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if ext == "json":
        return parse_json(content, filename)
    elif ext == "xml":
        return parse_xml(content, filename)
    else:
        try:
            return parse_json(content, filename)
        except (json.JSONDecodeError, UnicodeDecodeError):
            pass
        try:
            return parse_xml(content, filename)
        except ET.ParseError:
            pass
        raise ValueError(f"无法解析文件 {filename}: 不支持的格式或内容无效")


def parse_json(content: bytes, filename: str) -> Dict[str, Any]:
    """解析 JSON 文件为树形结构"""
    text = content.decode("utf-8-sig")
    data = json.loads(text)
    return {
        "id": "root",
        "label": filename,
        "value": None,
        "children": _json_to_tree(data, "root"),
        "attrs": {"type": "json"},
    }


def _preprocess_xml_namespaces(text: str) -> str:
    """Fix unbound namespace prefixes in XML."""
    import re as _re
    used = set(_re.findall(r"<(/?)(\w+):", text))
    declared = set(_re.findall(r"xmlns:(\w+)=", text))
    missing = {p for _, p in used} - declared
    if not missing:
        return text
    ns = " ".join(f"xmlns:{p}=\"urn:{p}\"" for p in sorted(missing))
    pos = 0
    if text.startswith("<?xml"):
        pos = text.index("?>") + 2
    header = text[:pos]
    body = text[pos:]
    body = _re.sub(
        r"(<(?:\w+:)?\w+)",
        r"\1 " + ns + " ",
        body,
        count=1,
    )
    return header + body
def parse_xml(content: bytes, filename: str) -> Dict[str, Any]:
    """解析 XML 文件为树形结构"""
    text = content.decode("utf-8-sig")
    try:
        root = ET.fromstring(text)
    except ET.ParseError as e:
        if "unbound prefix" not in str(e):
            raise
        text = _preprocess_xml_namespaces(text)
        root = ET.fromstring(text)
    return {
        "id": "root",
        "label": filename,
        "value": None,
        "children": [_xml_node_to_tree(root, "root")],
        "attrs": {"type": "xml"},
    }


def _json_to_tree(data: Any, path: str) -> List[Dict[str, Any]]:
    """递归转换 JSON 数据为树形节点列表"""
    nodes = []
    if isinstance(data, dict):
        for key, value in data.items():
            node_path = f"{path}.{key}"
            if isinstance(value, (dict, list)):
                node: Dict[str, Any] = {
                    "id": node_path,
                    "label": str(key),
                    "value": None,
                    "children": _json_to_tree(value, node_path),
                    "attrs": {"type": "array" if isinstance(value, list) else "object"},
                }
            else:
                node = {
                    "id": node_path,
                    "label": str(key),
                    "value": _format_primitive(value),
                    "children": [],
                    "attrs": {"type": type(value).__name__},
                }
            nodes.append(node)
    elif isinstance(data, list):
        for idx, item in enumerate(data):
            node_path = f"{path}[{idx}]"
            node = {
                "id": node_path,
                "label": f"[{idx}]",
                "value": None,
                "children": [],
                "attrs": {"type": "array"},
            }
            if isinstance(item, (dict, list)):
                node["children"] = _json_to_tree(item, node_path)
            else:
                node["value"] = _format_primitive(item)
                node["attrs"]["type"] = type(item).__name__
            nodes.append(node)
    return nodes


def _xml_node_to_tree(element: ET.Element, path: str) -> Dict[str, Any]:
    """递归转换 XML 节点为树形结构"""
    tag = element.tag
    node_path = f"{path}/{tag}"

    # 属性子节点
    attr_children = []
    for attr_name, attr_value in element.attrib.items():
        attr_children.append({
            "id": f"{node_path}@{attr_name}",
            "label": f"@{attr_name}",
            "value": attr_value,
            "children": [],
            "attrs": {"type": "attribute"},
        })

    # 子元素
    child_nodes = [_xml_node_to_tree(child, node_path) for child in element]

    # 文本内容
    text = (element.text or "").strip()

    all_children = attr_children + child_nodes

    value = None
    if text and not child_nodes:
        value = text
    elif text and child_nodes:
        all_children.insert(0, {
            "id": f"{node_path}#text",
            "label": "#text",
            "value": text,
            "children": [],
            "attrs": {"type": "text"},
        })

    return {
        "id": node_path,
        "label": tag,
        "value": value,
        "children": all_children,
        "attrs": {"type": "element"},
    }


def _format_primitive(value: Any) -> str:
    """格式化原始值为字符串"""
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def flatten_tree(tree: Dict[str, Any], parent_path: str = "") -> List[Dict[str, Any]]:
    """将树形结构扁平化为列表"""
    results = []
    current_path = f"{parent_path}/{tree['label']}" if parent_path else tree["label"]
    results.append({
        "path": current_path,
        "label": tree["label"],
        "value": tree.get("value"),
        "attrs": tree.get("attrs", {}),
        "id": tree["id"],
    })
    for child in tree.get("children", []):
        results.extend(flatten_tree(child, current_path))
    return results


def get_all_values(tree: Dict[str, Any]) -> Dict[str, str]:
    """获取树中所有有值的节点，返回 {路径: 值} 的映射"""
    result = {}
    _collect_values(tree, "", result)
    return result


def _collect_values(node: Dict[str, Any], prefix: str, result: Dict[str, str]):
    """递归收集有值的节点"""
    path = f"{prefix}/{node['label']}" if prefix else node["label"]
    if node.get("value") is not None:
        result[path] = node["value"]
    for child in node.get("children", []):
        _collect_values(child, path, result)
