"""XML/JSON 配置文件解析引擎

将 XML 和 JSON 文件统一转换为树形结构，供前端展示。
树形结构格式: {"id": str, "label": str, "value": Optional[str], "children": list, "attrs": dict}

新增 parse_xml_full / parse_json_full 返回 ParsedDocument，保留完整语义和 locator。
"""

import hashlib
import json
import os
import io
import xml.etree.ElementTree as ET
from typing import Any, Dict, List, Optional

from .models import FileRef, FileKind, SourceSnapshot, NodeRef, ParsedDocument

_PARSER_VERSION = "2.0-full"

_LARGE_XML_BYTES_THRESHOLD = 200_000
_LARGE_XML_ATTR_ALLOWLIST = {"description", "editPrivilege", "default", "min", "max", "incMin", "incMax"}

# ---------------------------------------------------------------------------
# 内存缓存：同 content hash 复用 ParsedDocument，避免重复解析
# ---------------------------------------------------------------------------
_PARSED_DOC_CACHE_MAX = 32  # LRU 缓存容量
_parsed_doc_cache: dict[str, ParsedDocument] = {}
_parsed_doc_cache_order: list[str] = []  # LRU 顺序（最近使用在末尾）


def get_parsed_document(source: bytes, filename: str, fmt: str = "") -> ParsedDocument:
    """获取 ParsedDocument，同 content hash 复用缓存

    如果 fmt 为空，根据文件名后缀推断。
    """
    source_hash = compute_content_hash(source)

    if source_hash in _parsed_doc_cache:
        # LRU: 移到末尾
        _parsed_doc_cache_order.remove(source_hash)
        _parsed_doc_cache_order.append(source_hash)
        return _parsed_doc_cache[source_hash]

    if not fmt:
        ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
        fmt = "json" if ext == "json" else "xml"

    if fmt == "json":
        doc = parse_json_full(source, filename, source_hash=source_hash)
    else:
        doc = parse_xml_full(source, filename, source_hash=source_hash)

    # 写入缓存，淘汰最旧的
    _parsed_doc_cache[source_hash] = doc
    _parsed_doc_cache_order.append(source_hash)
    while len(_parsed_doc_cache_order) > _PARSED_DOC_CACHE_MAX:
        evict = _parsed_doc_cache_order.pop(0)
        _parsed_doc_cache.pop(evict, None)

    return doc


def clear_parsed_document_cache() -> int:
    """清空 ParsedDocument 内存缓存，返回清理数量"""
    count = len(_parsed_doc_cache)
    _parsed_doc_cache.clear()
    _parsed_doc_cache_order.clear()
    return count


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


def parse_path(file_path: str, filename: Optional[str] = None) -> Dict[str, Any]:
    if filename is None:
        filename = os.path.basename(file_path)
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if ext == "xml":
        return parse_xml_path(file_path, filename)
    if ext == "json":
        with open(file_path, "rb") as f:
            return parse_json(f.read(), filename)
    with open(file_path, "rb") as f:
        content = f.read()
    return parse_file(content, filename)



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


def _parse_xml_iterparse(source: Any, filename: str, allowed_attr_names: Optional[set[str]] = None) -> Dict[str, Any]:
    root_node: Optional[Dict[str, Any]] = None
    stack: list[Dict[str, Any]] = []
    path_stack: list[str] = []

    for event, elem in ET.iterparse(source, events=("start", "end")):
        tag = elem.tag
        if event == "start":
            parent_path = path_stack[-1] if path_stack else "root"
            node_path = f"{parent_path}/{tag}"

            node: Dict[str, Any] = {
                "id": node_path,
                "label": tag,
                "value": None,
                "children": [],
                "attrs": {"type": "element"},
                "_has_child_elements": False,
            }

            for attr_name, attr_value in elem.attrib.items():
                if allowed_attr_names is not None and attr_name not in allowed_attr_names:
                    continue
                node["children"].append(
                    {
                        "id": f"{node_path}@{attr_name}",
                        "label": f"@{attr_name}",
                        "value": attr_value,
                        "children": [],
                        "attrs": {"type": "attribute"},
                    }
                )

            if stack:
                stack[-1]["_has_child_elements"] = True
                stack[-1]["children"].append(node)

            stack.append(node)
            path_stack.append(node_path)
        else:
            node = stack.pop()
            node_path = path_stack.pop()
            text = (elem.text or "").strip()
            if text:
                if node.get("_has_child_elements"):
                    node["children"].insert(
                        0,
                        {
                            "id": f"{node_path}#text",
                            "label": "#text",
                            "value": text,
                            "children": [],
                            "attrs": {"type": "text"},
                        },
                    )
                else:
                    node["value"] = text
            node.pop("_has_child_elements", None)
            elem.clear()
            if not stack:
                root_node = node

    if root_node is None:
        raise ValueError(f"无法解析文件 {filename}: XML 内容为空")

    return {
        "id": "root",
        "label": filename,
        "value": None,
        "children": [root_node],
        "attrs": {"type": "xml"},
    }


def parse_xml_path(file_path: str, filename: str) -> Dict[str, Any]:
    allowed = None
    try:
        if os.path.getsize(file_path) >= _LARGE_XML_BYTES_THRESHOLD:
            allowed = set(_LARGE_XML_ATTR_ALLOWLIST)
    except OSError:
        allowed = None
    try:
        return _parse_xml_iterparse(file_path, filename, allowed_attr_names=allowed)
    except ET.ParseError as e:
        if "unbound prefix" not in str(e) and "junk after document element" not in str(e):
            raise
    with open(file_path, "rb") as f:
        content = f.read()
    return parse_xml(content, filename)


def parse_xml(content: bytes, filename: str) -> Dict[str, Any]:
    """解析 XML 文件为树形结构"""
    allowed = set(_LARGE_XML_ATTR_ALLOWLIST) if len(content) >= _LARGE_XML_BYTES_THRESHOLD else None
    try:
        return _parse_xml_iterparse(io.BytesIO(content), filename, allowed_attr_names=allowed)
    except ET.ParseError as e:
        msg = str(e)
        if "junk after document element" in msg:
            cut = content.rfind(b">")
            if cut >= 0:
                return _parse_xml_iterparse(io.BytesIO(content[: cut + 1]), filename, allowed_attr_names=allowed)
        if "unbound prefix" not in msg:
            raise
    text = content.decode("utf-8-sig")
    text = _preprocess_xml_namespaces(text)
    return _parse_xml_iterparse(io.BytesIO(text.encode("utf-8")), filename, allowed_attr_names=allowed)


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


# ---------------------------------------------------------------------------
# 完整语义解析（返回 ParsedDocument）
# ---------------------------------------------------------------------------

def compute_content_hash(data: bytes) -> str:
    """计算内容的 SHA-256 hash"""
    return hashlib.sha256(data).hexdigest()


def _make_file_ref(filename: str, content_hash: str, fmt: str) -> FileRef:
    """根据文件名推断 FileKind 并构建 FileRef"""
    kind = FileKind.CURRENT
    return FileRef(profile_id="default", kind=kind, name=filename)


def _make_source(filename: str, content_hash: str, fmt: str,
                 source_handle: str = "") -> SourceSnapshot:
    ref = _make_file_ref(filename, content_hash, fmt)
    return SourceSnapshot(
        file_ref=ref,
        content_hash=content_hash,
        format=fmt,
        parser_version=_PARSER_VERSION,
        source_handle=source_handle or filename,
    )


# ---- XML full -----------------------------------------------------------

def parse_xml_full(source: bytes, filename: str = "",
                   source_hash: str = "") -> ParsedDocument:
    """完整 XML 解析，保留所有语义（全部 attribute、text/tail、命名空间）

    不做大文件属性裁剪，返回 ParsedDocument。
    """
    if not source_hash:
        source_hash = compute_content_hash(source)

    # 复用已有 namespace 修复逻辑
    text_bytes = source
    try:
        root = ET.fromstring(text_bytes)
    except ET.ParseError as e:
        msg = str(e)
        if "unbound prefix" in msg:
            text = source.decode("utf-8-sig")
            text = _preprocess_xml_namespaces(text)
            text_bytes = text.encode("utf-8")
            root = ET.fromstring(text_bytes)
        elif "junk after document element" in msg:
            cut = source.rfind(b">")
            if cut >= 0:
                root = ET.fromstring(source[:cut + 1])
            else:
                raise
        else:
            raise

    # 构建完整树（保留所有 attribute）
    occurrence_map: Dict[str, int] = {}
    tree = _build_full_xml_tree(root, source_hash, occurrence_map)

    wrapper = {
        "id": "root",
        "label": filename or "<xml>",
        "value": None,
        "children": [tree],
        "attrs": {"type": "xml"},
    }

    src = _make_source(filename, source_hash, "xml")
    return ParsedDocument(source=src, root=wrapper, format="xml")


def _build_full_xml_tree(elem: ET.Element, source_hash: str,
                         occurrence_map: Dict[str, int]) -> Dict[str, Any]:
    """递归构建完整 XML 树，保留全部 attribute、text、tail、命名空间"""
    tag = elem.tag  # 已展开的命名空间 {uri}local
    occurrence_map[tag] = occurrence_map.get(tag, 0) + 1
    occurrence = occurrence_map[tag]

    locator = _make_xml_locator(tag, occurrence)

    # 属性子节点
    attr_children: List[Dict[str, Any]] = []
    for attr_name, attr_value in elem.attrib.items():
        attr_children.append({
            "id": f"{locator}@{attr_name}",
            "label": f"@{attr_name}",
            "value": attr_value,
            "children": [],
            "attrs": {"type": "attribute"},
            "locator": f"{locator}@{attr_name}",
        })

    # 子元素
    child_nodes: List[Dict[str, Any]] = []
    for child in elem:
        child_nodes.append(
            _build_full_xml_tree(child, source_hash, occurrence_map)
        )

    text = (elem.text or "").strip()
    tail = (elem.tail or "").strip()

    all_children = attr_children + child_nodes
    value = None
    if text and not child_nodes:
        value = text
    elif text and child_nodes:
        all_children.insert(0, {
            "id": f"{locator}#text",
            "label": "#text",
            "value": text,
            "children": [],
            "attrs": {"type": "text"},
            "locator": f"{locator}#text",
        })

    node: Dict[str, Any] = {
        "id": locator,
        "label": tag,
        "value": value,
        "children": all_children,
        "attrs": {"type": "element"},
        "tag": tag,
        "occurrence": occurrence,
        "locator": locator,
    }
    if tail:
        node["tail"] = tail
    return node


def _make_xml_locator(tag: str, occurrence: int) -> str:
    """XML locator: 展开命名空间 + 兄弟 occurrence"""
    return f"xml:{tag}[{occurrence}]"


# ---- JSON full ----------------------------------------------------------

def parse_json_full(source: bytes, filename: str = "",
                    source_hash: str = "") -> ParsedDocument:
    """完整 JSON 解析，保留所有类型信息（int/float/bool/null/str）"""
    if not source_hash:
        source_hash = compute_content_hash(source)

    text = source.decode("utf-8-sig")
    data = json.loads(text)

    tree = _build_full_json_tree(data, source_hash, path="$")

    wrapper = {
        "id": "root",
        "label": filename or "<json>",
        "value": None,
        "children": tree if isinstance(tree, list) else [tree],
        "attrs": {"type": "json"},
    }

    src = _make_source(filename, source_hash, "json")
    return ParsedDocument(source=src, root=wrapper, format="json")


def _build_full_json_tree(data: Any, source_hash: str, path: str) -> Any:
    """递归构建完整 JSON 树，保留原始类型"""
    if isinstance(data, dict):
        children: Dict[str, Any] = {}
        for k, v in data.items():
            child_path = _json_pointer(path, str(k))
            children[str(k)] = _build_full_json_tree(v, source_hash, child_path)
        return {
            "type": "object",
            "value_type": "object",
            "children": children,
            "locator": path,
        }
    elif isinstance(data, list):
        items = [
            _build_full_json_tree(v, source_hash, f"{path}/{i}")
            for i, v in enumerate(data)
        ]
        return {
            "type": "array",
            "value_type": "array",
            "children": items,
            "locator": path,
        }
    else:
        return {
            "type": "scalar",
            "value_type": type(data).__name__,
            "value": data,
            "locator": path,
        }


def _json_pointer(parent: str, key: str) -> str:
    """JSON Pointer with RFC 6901 escaping: ~ -> ~0, / -> ~1"""
    escaped = key.replace("~", "~0").replace("/", "~1")
    return f"{parent}/{escaped}"
