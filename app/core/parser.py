"""XML/JSON 配置文件解析引擎

将 XML 和 JSON 文件统一转换为树形结构，供前端展示。
树形结构格式: {"id": str, "label": str, "value": Optional[str], "children": list, "attrs": dict}
"""

from __future__ import annotations

import json
import os
import io
import xml.etree.ElementTree as ET
from typing import Any, Dict, List, Optional, Tuple

from .errors import PARSE_FAILED, TOO_LARGE, AppError

_LARGE_XML_BYTES_THRESHOLD = 200_000
_LARGE_XML_ATTR_ALLOWLIST = {"description", "editPrivilege", "default", "min", "max", "incMin", "incMax"}

#: compact 文档的解析器版本（§6.1）。缓存 schema 变化时必须递增。
PARSER_VERSION = 2

#: §6.1 安全上限（超限返回 `TOO_LARGE`）。
MAX_NODES = 2_000_000
MAX_DEPTH = 512


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
    data = json.loads(
        text,
        object_pairs_hook=_strict_pairs,
        parse_constant=_reject_json_constant,
    )
    return {
        "id": "root",
        "label": filename,
        "value": None,
        "children": _json_to_tree(data, "root"),
        "attrs": {"type": "json"},
    }


def _strict_pairs(pairs: List[Tuple[str, Any]]) -> Dict[str, Any]:
    """重复键是歧义，直接拒绝（V3 §4.1 / INH-2132）。"""
    out: Dict[str, Any] = {}
    for key, value in pairs:
        if key in out:
            raise AppError(PARSE_FAILED, "JSON 存在重复键：%s" % key)
        out[key] = value
    return out


def _reject_json_constant(value: str) -> Any:
    raise AppError(PARSE_FAILED, "JSON 不允许 %s" % value)


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


def _json_to_tree(data: Any, path: str, depth: int = 0) -> List[Dict[str, Any]]:
    """递归转换 JSON 数据为树形节点列表"""
    if depth > MAX_DEPTH:
        raise AppError(TOO_LARGE, "文档嵌套超过上限（%d 层）" % MAX_DEPTH)
    nodes = []
    if isinstance(data, dict):
        for key, value in data.items():
            node_path = f"{path}.{key}"
            if isinstance(value, (dict, list)):
                node: Dict[str, Any] = {
                    "id": node_path,
                    "label": str(key),
                    "value": None,
                    "children": _json_to_tree(value, node_path, depth + 1),
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
                node["children"] = _json_to_tree(item, node_path, depth + 1)
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


# ===========================================================================
# compact 文档（§6.1、§6.2）——前端结构树/详情/差异统一消费的先序扁平格式
#
# 与旧树接口（`parse_file`）的关系：旧接口保留不动（收藏/绑定/旧建议用的
# 旧节点 ID 由这里同样生成），新功能一律用 `loc`。
# ===========================================================================


class _Nodes:
    """compact 文档的 6 个并行数组（§6.2）。

    各列等长，节点用下标引用；`p[i]` 是父节点下标（根为 -1）。
    """

    __slots__ = ("p", "t", "l", "v", "id", "loc")

    def __init__(self) -> None:
        self.p: List[int] = []
        self.t: List[str] = []
        self.l: List[str] = []
        self.v: List[Optional[str]] = []
        self.id: List[str] = []
        self.loc: List[str] = []

    def push(
        self,
        parent: int,
        node_type: str,
        label: str,
        value: Optional[str],
        node_id: str,
        loc: str,
    ) -> int:
        if len(self.p) >= MAX_NODES:
            raise AppError(TOO_LARGE, "文档节点数超过上限（%d）" % MAX_NODES)
        self.p.append(parent)
        self.t.append(node_type)
        self.l.append(label)
        self.v.append(value)
        self.id.append(node_id)
        self.loc.append(loc)
        return len(self.p) - 1

    def to_dict(self) -> Dict[str, List[Any]]:
        return {"p": self.p, "t": self.t, "l": self.l, "v": self.v, "id": self.id, "loc": self.loc}


def to_compact(content: bytes, filename: str) -> Dict[str, Any]:
    """解析为 compact 文档（§6.1）。

    返回 `{"format": "xml"|"json", "warnings": [...], "nodes": {...}}`。

    与旧 `parse_file` 的差别：**始终完整解析**，不再按 200 KB 丢弃白名单外的
    属性（C04）。节点数 > 2 000 000 或深度 > 512 → `TOO_LARGE`；无法解析、
    含实体声明 → `PARSE_FAILED`。遍历用显式栈，深文档不会触发 RecursionError。
    """
    fmt = _detect_format(content, filename)
    if fmt == "json":
        nodes = _compact_json(content, filename)
        warnings: List[str] = []
    else:
        nodes, warnings = _compact_xml(content, filename)
    return {"format": fmt, "warnings": warnings, "nodes": nodes.to_dict()}


def _detect_format(content: bytes, filename: str) -> str:
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if ext == "json":
        return "json"
    if ext == "xml":
        return "xml"
    try:
        json.loads(content.decode("utf-8-sig"))
        return "json"
    except (UnicodeDecodeError, json.JSONDecodeError):
        pass
    try:
        _xml_root_from(content)
        return "xml"
    except ET.ParseError:
        pass
    raise AppError(PARSE_FAILED, "无法解析文件 %s：不支持的格式或内容无效" % filename)


def _format_compact_value(value: Any) -> str:
    """标量格式化（§6.2）：`None→"null"`、`True/False` 小写、其余 `str(value)`。"""
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def _pointer_escape(key: str) -> str:
    """JSON Pointer 转义（RFC 6901，§6.2）：`~`→`~0`、`/`→`~1`。"""
    return key.replace("~", "~0").replace("/", "~1")


def pointer_unescape(token: str) -> str:
    """JSON Pointer 反转义（RFC 6901）：`~1`→`/`、`~0`→`~`。

    与 `_pointer_escape` 严格互逆；**顺序不能颠倒**：必须先 `~1`→`/`、再 `~0`→`~`，
    否则 `~01`（真键名是 `~1`）会被还原成 `/`。`core.reviewing.apply_edits` 靠它把
    `loc` 还原成真实键名，所以只能由本模块提供，避免第二套转义规则。
    """
    return token.replace("~1", "/").replace("~0", "~")


def _compact_json(content: bytes, filename: str) -> _Nodes:
    try:
        data = json.loads(
            content.decode("utf-8-sig"),
            object_pairs_hook=_strict_pairs,
            parse_constant=_reject_json_constant,
        )
    except AppError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise AppError(PARSE_FAILED, "JSON 解析失败：%s" % exc)

    nodes = _Nodes()
    root = nodes.push(-1, "json", filename, None, "root", "")
    if not isinstance(data, (dict, list)):
        # 标量文档：与模板一致，只有根节点
        return nodes

    # 显式栈先序：容器子项逆序入栈，弹出即处理；容器在弹出时才展开自己的子项，
    # 因此子树一定排在下一个同级之前（与 mock/docparse.js 的顺序一致）。
    stack: List[Tuple[Any, int, str, str, str, int]] = [
        (value, root, child_id, child_loc, label, 1)
        for value, child_id, child_loc, label in reversed(_json_children(data, "root", ""))
    ]
    while stack:
        value, parent, pid, ploc, label, depth = stack.pop()
        if depth > MAX_DEPTH:
            raise AppError(TOO_LARGE, "文档嵌套超过上限（%d 层）" % MAX_DEPTH)
        if isinstance(value, (dict, list)):
            index = nodes.push(
                parent,
                "array" if isinstance(value, list) else "object",
                label,
                None,
                pid,
                ploc,
            )
            stack.extend(
                (child, index, child_id, child_loc, child_label, depth + 1)
                for child, child_id, child_loc, child_label in reversed(
                    _json_children(value, pid, ploc)
                )
            )
        else:
            nodes.push(
                parent,
                type(value).__name__,
                label,
                _format_compact_value(value),
                pid,
                ploc,
            )
    return nodes


def _json_children(container: Any, pid: str, ploc: str) -> List[Tuple[Any, str, str, str]]:
    """按源顺序列出容器的直接子项：`(值, 旧ID, loc, 显示标签)`（§6.2）。"""
    children: List[Tuple[Any, str, str, str]] = []
    if isinstance(container, dict):
        for key, item in container.items():
            name = str(key)
            children.append((item, pid + "." + name, ploc + "/" + _pointer_escape(name), name))
    else:
        for index, item in enumerate(container):
            label = "[%d]" % index
            children.append((item, "%s[%d]" % (pid, index), "%s/%d" % (ploc, index), label))
    return children


def _xml_root_from(data: bytes) -> ET.Element:
    """一次性解析为元素树，丢弃注释（§6.1 第 1 步）。"""
    parser = ET.XMLParser(target=ET.TreeBuilder(insert_comments=False))
    return ET.fromstring(data, parser=parser)


def _collect_ns_prefixes(data: bytes) -> Dict[str, str]:
    """`uri → prefix`（首次出现者），用于显示标签（§6.1 第 3 步）。"""
    mapping: Dict[str, str] = {}
    for _event, payload in ET.iterparse(io.BytesIO(data), events=("start-ns",)):
        prefix, uri = payload
        if uri and uri not in mapping:
            mapping[uri] = prefix
    return mapping


def collect_ns_prefixes(content: bytes) -> Dict[str, str]:
    """`uri → prefix`（首次出现者），§6.1 第 3 步的公开入口。

    `_collect_ns_prefixes` 的薄封装：`core.reviewing.apply_edits` 要用它把原文的
    前缀注册回去（§6.7 第 5 步），必须与显示标签**同源**，所以这里不复制实现、
    也不让别的 core 模块跨文件调私有函数。
    """
    return _collect_ns_prefixes(content)


def _display_label(tag: str, ns_prefix: Dict[str, str]) -> str:
    """显示标签：有前缀用 `prefix:local`；无前缀用 local；未知 uri 用 ET 原始 tag（§6.2）。"""
    if not tag.startswith("{"):
        return tag
    uri, _, local = tag[1:].partition("}")
    prefix = ns_prefix.get(uri)
    return prefix + ":" + local if prefix else tag


def _parse_xml_root(content: bytes) -> Tuple[ET.Element, List[str], Dict[str, str]]:
    """解析 XML 根元素，保留旧容错（unbound prefix / junk after document element）。"""
    upper = content.upper()
    if b"<!DOCTYPE" in upper and b"<!ENTITY" in upper:
        raise AppError(PARSE_FAILED, "文件包含实体声明，出于安全原因不予解析")

    data = content
    warnings: List[str] = []
    try:
        root = _xml_root_from(data)
    except ET.ParseError as exc:
        message = str(exc)
        if "junk after document element" in message:
            # 截到报错位置（= 文档元素结束处）再重试。用 expat 给的 position 而不是
            # "最后一个 >"：尾部若还有 `/>`，后者会截到残余内容之后，等于没截。
            offset = _position_offset(data, getattr(exc, "position", None))
            if offset <= 0:
                raise
            data = data[:offset]
            warnings.append(
                "文件末尾有多余内容，已截取到文档元素结束处解析（仅影响显示，原文未修改）"
            )
            root = _xml_root_from(data)
        elif "unbound prefix" in message:
            text = _preprocess_xml_namespaces(data.decode("utf-8-sig"))
            data = text.encode("utf-8")
            warnings.append(
                "文件包含未声明的命名空间前缀，已自动补全后解析（仅影响显示，原文未修改）"
            )
            root = _xml_root_from(data)
        else:
            raise
    return root, warnings, _collect_ns_prefixes(data)


def _position_offset(data: bytes, position: Optional[Tuple[int, int]]) -> int:
    """把 expat 的 `(line, column)`（1 起）换算成字节偏移。"""
    if not position:
        return -1
    line, column = position
    offset = 0
    for _ in range(max(line - 1, 0)):
        newline = data.find(b"\n", offset)
        if newline < 0:
            return len(data)
        offset = newline + 1
    return min(offset + max(column, 0), len(data))


def _compact_xml(content: bytes, filename: str) -> Tuple[_Nodes, List[str]]:
    try:
        root_elem, warnings, ns_prefix = _parse_xml_root(content)
    except ET.ParseError as exc:
        raise AppError(PARSE_FAILED, "XML 解析失败：%s" % exc)

    nodes = _Nodes()
    root = nodes.push(-1, "xml", filename, None, "root", "")

    # 显式栈先序：元素行 → 属性行（源顺序，跳过 xmlns*）→ `#text` 行 → 子元素
    stack: List[Tuple[ET.Element, int, str, str, int, int]] = [
        (root_elem, root, "root", "", 1, 1)
    ]
    while stack:
        elem, parent, pid, ploc, occurrence, depth = stack.pop()
        if depth > MAX_DEPTH:
            raise AppError(TOO_LARGE, "文档嵌套超过上限（%d 层）" % MAX_DEPTH)

        tag = elem.tag
        node_id = pid + "/" + tag
        node_loc = "%s/%s[%d]" % (ploc, tag, occurrence)
        has_child_elements = len(elem) > 0
        text = (elem.text or "").strip()
        value = None if (has_child_elements or not text) else text

        index = nodes.push(
            parent, "element", _display_label(tag, ns_prefix), value, node_id, node_loc
        )

        for attr_name, attr_value in elem.attrib.items():
            if attr_name == "xmlns" or attr_name.startswith("xmlns:"):
                continue
            # id/loc 用 ET 原始属性名；显示标签与元素一致，已知 uri 时用 prefix:local
            nodes.push(
                index,
                "attribute",
                "@" + _display_label(attr_name, ns_prefix),
                attr_value,
                node_id + "@" + attr_name,
                node_loc + "/@" + attr_name,
            )

        if has_child_elements and text:
            nodes.push(index, "text", "#text", text, node_id + "#text", node_loc + "/#text")

        counters: Dict[str, int] = {}
        children: List[Tuple[ET.Element, int, str, str, int, int]] = []
        for child in elem:
            counters[child.tag] = counters.get(child.tag, 0) + 1
            children.append((child, index, node_id, node_loc, counters[child.tag], depth + 1))
        stack.extend(reversed(children))

    return nodes, warnings
