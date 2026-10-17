"""审阅修改核心逻辑。"""

from __future__ import annotations

import codecs
import json
import math
import re
import xml.etree.ElementTree as ET
from copy import deepcopy
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from . import parser
from .errors import INVALID_INPUT, PARSE_FAILED, REVIEW_CONFLICT, REVIEW_UNSAFE, AppError


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


def apply_review_updates(tree: Dict[str, Any], approved_items: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
    copied = deepcopy(tree)
    for item in approved_items:
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


# ===========================================================================
# 审阅写回（§6.7 第 5–6 步；M3-01 / INH-1886）
#
# 与上面的旧路径（`serialize_tree`/`apply_review_updates`，从「树」重建正文）无关：
# 新路径**在原文字节上**只改选中节点，注释、处理指令、缩进、声明与编码都按源保留，
# 再用「输出再解析后与原文逐节点比较」证明没有动到别处。旧路径 M4-07 才删。
# ===========================================================================

#: `(loc, new_value, kind)`。`kind` 是该节点在 compact 文档里的原类型 `t`（§6.2）：
#: XML 为 `element` / `attribute` / `text`，JSON 为 `str` / `int` / `float` / `bool` /
#: `NoneType`。调用方（M3-02）必须把它取自**同一份** compact 文档。
Edit = Tuple[str, str, str]

#: `to_compact` 按扩展名判定格式（§6.1），校验两侧用同一个文件名口径。
_DOC_NAME = {"xml": "review.xml", "json": "review.json"}

#: §6.7 第 4 步的整数口径，与 `services/review.py::_INT_RE` 一致。写在 core 里是因为
#: `core` 不得 import `services`（分层红线 §3.3）；用它而不是 `int()`，是为了拒绝
#: `"1_0"`、`" 7 "` 这类 Python 认但审阅口径不认的写法。
_INT_RE = re.compile(r"^-?\d+$")

#: 真正的声明必须紧跟 `version`；否则会把 `<?xml-stylesheet ...?>` 这类合法的根前
#: 处理指令误认成声明，凭空给原文加一行 `<?xml ...?>`。
_XML_DECL_RE = re.compile(rb"<\?xml\s+version\b([^?>]*)\?>", re.IGNORECASE)
_XML_ENCODING_RE = re.compile(rb"encoding\s*=\s*[\"']([\w.:+-]+)[\"']", re.IGNORECASE)


def apply_edits(src: bytes, fmt: str, edits: Sequence[Edit]) -> bytes:
    """在原文 `src` 上应用 `edits`，返回新的原文字节。

    错误口径（卡面接口契约）：

    * `edits` 为空、格式不是 xml/json、建议值与节点原类型不符 → `INVALID_INPUT`。
    * `loc` 定位不到「承载值的节点」→ `REVIEW_CONFLICT`（无法唯一定位）。
    * 语义校验（§6.7 第 6 步）不过 → `REVIEW_UNSAFE`，**不返回任何字节**，
      调用方因此拿不到可写入的成功正文。
    * 原文连读都读不出来 → `PARSE_FAILED`，与显示链路（§6.1）同口径。

    本函数是纯函数：不读不写业务文件（持久化属 `storage`，§3.3），也不调用旧
    `serialize_tree`。已知限制（§6.7）：标准库会统一属性引号、空元素写法与实体
    写法，业务语义由第 6 步校验兜底。
    """
    if not edits:
        raise AppError(INVALID_INPUT, "没有要写回的修改")
    name = _DOC_NAME.get(fmt)
    if name is None:
        raise AppError(INVALID_INPUT, "审阅写回只支持 xml 与 json", fmt=str(fmt))

    try:
        base = parser.to_compact(src, name)["nodes"]
    except LookupError as exc:
        # 声明里的编码名标准库不认识时 expat 抛的是 LookupError（不是 ParseError），
        # 按 §6.1 归入「无法解析」，不让它变成 500。
        raise AppError(PARSE_FAILED, "文件声明的编码无法识别：%s" % exc)

    out = _rewrite_xml(src, edits) if fmt == "xml" else _rewrite_json(src, edits)
    _verify_semantics(base, parser.to_compact(out, name)["nodes"], fmt, edits)
    return out


def _verify_semantics(
    base: Dict[str, List[Any]], after: Dict[str, List[Any]], fmt: str, edits: Sequence[Edit]
) -> None:
    """§6.7 第 6 步：差异必须**恰好等于**本次修改，否则拒绝写回。

    三条断言按「结构 → 差异集合 → 逐个值」的顺序判，先判结构是因为它最能说明
    「这份原文不是我们以为的那份」。`detail` 只带第一个可疑 loc 与计数，
    失败时 loc 集合可能非常大，不能整份塞进错误响应。
    """
    if len(base["p"]) != len(after["p"]) or base["t"] != after["t"]:
        raise AppError(
            REVIEW_UNSAFE,
            "写回后文档结构发生了变化，已拒绝写回",
            before=len(base["p"]),
            after=len(after["p"]),
        )

    before_values = _valued_map(base)
    after_values = _valued_map(after)
    changed = {
        loc
        for loc in set(before_values) | set(after_values)
        if before_values.get(loc) != after_values.get(loc)
    }
    wanted = {loc for loc, _value, _kind in edits}
    if changed != wanted:
        stray = sorted(changed - wanted) or sorted(wanted - changed)
        raise AppError(
            REVIEW_UNSAFE,
            "写回影响了未选中的节点，或选中节点没有变更，已拒绝写回",
            loc=stray[0] if stray else "",
            changed=len(changed),
        )

    for loc, new_value, kind in edits:
        expect = _expected_compact_value(fmt, kind, new_value)
        if after_values.get(loc) != expect:
            raise AppError(
                REVIEW_UNSAFE, "写回后的值与建议值不一致，已拒绝写回", loc=loc, want=expect
            )


def _valued_map(nodes: Dict[str, List[Any]]) -> Dict[str, Optional[str]]:
    """`loc → v`，只收有值节点（§6.6/§6.7 都按「有值节点」比较）。"""
    return {loc: v for loc, v in zip(nodes["loc"], nodes["v"]) if v is not None}


def _expected_compact_value(fmt: str, kind: str, new_value: str) -> str:
    """建议值写回后在 compact 里应当呈现的字符串（§6.2 的 `v` 口径）。"""
    if fmt == "json":
        return _json_target(kind, new_value)[1]
    # 元素与 `#text` 的 `v` 是 strip 后的文本，属性值原样。
    return new_value if kind == "attribute" else new_value.strip()


def _rewrite_xml(src: bytes, edits: Sequence[Edit]) -> bytes:
    """XML 路径：在保留注释/处理指令的树上只改选中节点（§6.7 第 5 步）。"""
    if b"<!DOCTYPE" in src.upper():
        # DTD 里没有「有值节点」，第 6 步看不见它被丢弃（卡面风险条）→ 写回前明确拒绝，
        # 管理员下载原文手工改。
        raise AppError(REVIEW_UNSAFE, "原文包含 DTD 声明，标准库重写会把它丢弃，已拒绝写回")

    builder = ET.XMLParser(target=ET.TreeBuilder(insert_comments=True, insert_pis=True))
    try:
        root = ET.fromstring(src, parser=builder)
    except ET.ParseError as exc:
        # 显示链路可以容错的两类（junk after document element / unbound prefix）在这里
        # 都不能照单全收：前者要截断原文，后者要往文件里写假的 `urn:前缀`。
        raise AppError(REVIEW_UNSAFE, "原文无法按 XML 安全重写：%s" % exc)

    for uri, prefix in parser.collect_ns_prefixes(src).items():
        try:
            ET.register_namespace(prefix, uri)
        except ValueError:
            # `xml` 等保留前缀不许覆盖；这些 URI 交给标准库默认写法，不影响节点值。
            continue

    wanted = {loc: new_value for loc, new_value, _kind in edits}
    hits: Dict[str, Tuple[ET.Element, Optional[str]]] = {}
    _walk_xml_targets(root, wanted, hits)
    missing = sorted(set(wanted) - set(hits))
    if missing:
        raise AppError(
            REVIEW_CONFLICT, "无法唯一定位：%s" % missing[0], loc=missing[0]
        )

    for loc, (elem, attribute) in hits.items():
        new_value = wanted[loc]
        if attribute is None:
            elem.text = new_value
        else:
            elem.set(attribute, new_value)

    declared, encoding = _xml_prolog(src)
    return _keep_bom(src, ET.tostring(root, encoding=encoding, xml_declaration=declared))


def _walk_xml_targets(
    root: ET.Element,
    wanted: Dict[str, str],
    hits: Dict[str, Tuple[ET.Element, Optional[str]]],
) -> None:
    """按 §6.2 的 `loc` 拼法遍历原文，把 `wanted` 里命中的节点记进 `hits`。

    不去解析 `loc` 字符串：命名空间 URI 天然带 `/`（`{http://…}tag`），按 `/` 切会切错。
    这里的 loc 拼法与 occurrence 计数和 `parser._compact_xml` 逐字同源，所以「同名兄弟
    只改第 2 个」不会指错。注释/处理指令节点的 tag 不是字符串，不参与计数——§6.1 第 1 步
    显示时就是用 `insert_comments=False` 解析的，两侧口径一致。

    只登记**承载值的行**（属性行、`#text` 行、叶子元素的值行）：容器元素与空元素在
    compact 里 `v` 是 null，不是「选中节点的值」，按定位不到处理（`REVIEW_CONFLICT`）。
    """
    stack: List[Tuple[ET.Element, str, int]] = [(root, "", 1)]
    while stack:
        elem, parent_loc, occurrence = stack.pop()
        if not isinstance(elem.tag, str):
            continue
        loc = "%s/%s[%d]" % (parent_loc, elem.tag, occurrence)
        children = [child for child in elem if isinstance(child.tag, str)]
        text = (elem.text or "").strip()

        for attribute in elem.attrib:
            if attribute == "xmlns" or attribute.startswith("xmlns:"):
                continue  # §6.2：`xmlns*` 不生成节点
            candidate = loc + "/@" + attribute
            if candidate in wanted:
                hits[candidate] = (elem, attribute)

        if children:
            if text and (loc + "/#text") in wanted:
                hits[loc + "/#text"] = (elem, None)
        elif text and loc in wanted:
            hits[loc] = (elem, None)

        counters: Dict[str, int] = {}
        ordered: List[Tuple[ET.Element, str, int]] = []
        for child in children:
            counters[child.tag] = counters.get(child.tag, 0) + 1
            ordered.append((child, loc, counters[child.tag]))
        stack.extend(reversed(ordered))


def _xml_prolog(src: bytes) -> Tuple[bool, str]:
    """源有没有 XML 声明、声明里写的编码是什么（§6.7 第 5 步：`tostring` 的这两个参数照源来）。

    编码名在这里不用再验一次：原文已经过 `to_compact`，expat 认的编码名必然也是
    Python codec 认的（不认时它抛 `LookupError`，在 `apply_edits` 里就变成 `PARSE_FAILED`）。
    """
    head = src[:512]
    if head.startswith(codecs.BOM_UTF8):
        head = head[len(codecs.BOM_UTF8):]
    match = _XML_DECL_RE.match(head.lstrip())
    if not match:
        return False, "utf-8"
    encoding = _XML_ENCODING_RE.search(match.group(1))
    return True, encoding.group(1).decode("ascii") if encoding else "utf-8"


def _keep_bom(src: bytes, out: bytes) -> bytes:
    """expat 与 `json.loads` 都吃掉 UTF-8 BOM，写回时放回去：BOM 属于原文，不是选中节点。"""
    return codecs.BOM_UTF8 + out if src.startswith(codecs.BOM_UTF8) else out


def _rewrite_json(src: bytes, edits: Sequence[Edit]) -> bytes:
    """JSON 路径：`json.loads` → 按 JSON Pointer 就地替换 → `dumps`（§6.7 第 5 步）。"""
    data = json.loads(src.decode("utf-8-sig"))
    for loc, new_value, kind in edits:
        value, _expect = _json_target(kind, new_value)
        slot = _json_slot(data, loc)
        if slot is None:
            raise AppError(REVIEW_CONFLICT, "无法唯一定位：%s" % loc, loc=loc)
        parent, key = slot
        parent[key] = value
    return _keep_bom(src, json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8"))


def _json_slot(document: Any, loc: str) -> Optional[Tuple[Any, Any]]:
    """按 JSON Pointer 找到**承载值的那个槽位**，返回 `(父容器, 键或下标)`；否则 None。

    与 `parser._json_children` 同口径：容器（object/array）与根在 compact 里 `v` 是
    null，不是「选中节点的值」，所以不是写回目标。`loc` 里的裸 `/` 一定是分隔符——
    键名里的 `/` 在生成 `loc` 时已被转义成 `~1`（§6.2），还原用 `parser.pointer_unescape`。
    """
    if not loc.startswith("/"):
        return None  # 根 loc `""`：compact 的根节点恒无值
    tokens = [parser.pointer_unescape(raw) for raw in loc.split("/")[1:]]
    if not tokens:
        return None

    parent: Any = None
    key: Any = None
    node = document
    for token in tokens:
        child = _json_child(node, token)
        if child is None:
            return None
        parent, key, node = node, child[0], child[1]

    if isinstance(node, (dict, list)):
        return None
    return parent, key


def _json_child(container: Any, token: str) -> Optional[Tuple[Any, Any]]:
    """容器里按 token 取子节点，返回 `(键或下标, 值)`；取不到返回 None。"""
    if isinstance(container, dict):
        if token not in container:
            return None
        return token, container[token]
    if isinstance(container, list):
        index = _array_index(token)
        if index is None or index >= len(container):
            return None
        return index, container[index]
    return None


def _array_index(token: str) -> Optional[int]:
    """数组下标 token：只接受 compact 生成的规范十进制形式（`01`、`-` 都不认）。"""
    if not token.isdigit():
        return None
    index = int(token)
    return index if str(index) == token else None


def _json_target(kind: str, new_value: str) -> Tuple[Any, str]:
    """建议值 → `(要写进 JSON 的值, 它在 compact 里应有的字符串)`（§6.2、§6.7 第 4 步）。

    与 `services/review.py::_required_type` 同一口径：服务层先按原类型拦，这里是
    core 的兜底，转换不了就 `INVALID_INPUT`，不把数字写成字符串——那样节点的 `t` 会
    变质，第 6 步只能事后拒绝。`NoneType` 的转换按 §6.7 第 5 步保留，但 null 是只读值
    （§6.7 第 4 步注），真提交「改成 null」会被第 6 步当成无变更拒绝。
    """
    if kind == "int":
        if not _INT_RE.match(new_value):
            raise AppError(INVALID_INPUT, "建议值无法写成整数：%s" % new_value)
        number = int(new_value)
        return number, str(number)
    if kind == "float":
        try:
            number = float(new_value)
        except ValueError:
            raise AppError(INVALID_INPUT, "建议值无法写成数字：%s" % new_value)
        if not math.isfinite(number):
            raise AppError(INVALID_INPUT, "建议值必须是有限数字：%s" % new_value)
        return number, str(number)
    if kind == "bool":
        if new_value == "true":
            return True, "true"
        if new_value == "false":
            return False, "false"
        raise AppError(INVALID_INPUT, "建议值必须是 true 或 false：%s" % new_value)
    if kind == "NoneType":
        return None, "null"
    return new_value, new_value
