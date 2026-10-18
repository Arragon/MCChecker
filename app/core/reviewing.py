"""审阅修改核心逻辑。"""

from __future__ import annotations

import codecs
import json
import math
import re
import xml.etree.ElementTree as ET
from copy import deepcopy
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple
from xml.parsers import expat

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
# 新路径**在原文字节上**只改选中节点，注释、处理指令、缩进、声明与编码都按源保留
# （根元素之外的两端也按字节原位拼回，见 `_xml_root_span`），再用「输出再解析后与原文
# 逐节点比较」证明没有动到别处。旧路径 M4-07 才删。
# ===========================================================================

#: `(loc, new_value, kind)`。`kind` 是该节点在 compact 文档里的原类型 `t`（§6.2）：
#: XML 为 `element` / `attribute` / `text`，JSON 为 `str` / `int` / `float` / `bool` /
#: `NoneType`。调用方（M3-02）必须把它取自**同一份** compact 文档。
Edit = Tuple[str, str, str]

#: `to_compact` 按扩展名判定格式（§6.1），校验两侧用同一个文件名口径。
_DOC_NAME = {"xml": "review.xml", "json": "review.json"}

#: §6.7 第 4 步的类型口径，**唯一定义处**：服务层 `services/review.py::_required_type`
#: 与写入侧 `_json_target` 都调 `value_type_hint`。以前 core/services 各写一份（同正则、
#: 同 `float()`+`isfinite`、同严格 `true|false`、同 `NoneType`），口径一旦飘了就会出现
#: 「`/remarks` 接受一条建议、`/review/submit` 在机器写锁内把它拒掉」的永远无法应用的建议。
#: 去重方向是 services → core，不违反 §3.3 分层红线。用 `INT_RE` 而不是裸 `int()`，是为了
#: 拒绝 `"1_0"`、`" 7 "` 这类 Python 认但审阅口径不认的写法。
INT_RE = re.compile(r"^-?\d+$")
TYPE_HINTS = {"int": "整数", "float": "数字", "bool": "true 或 false", "NoneType": "null"}

#: 真正的声明必须紧跟 `version`；否则会把 `<?xml-stylesheet ...?>` 这类合法的根前处理指令
#: 里出现的 `encoding="…"` 认成声明的编码。
_XML_DECL_RE = re.compile(rb"<\?xml\s+version\b([^?>]*)\?>", re.IGNORECASE)
_XML_ENCODING_RE = re.compile(rb"encoding\s*=\s*[\"']([\w.:+-]+)[\"']", re.IGNORECASE)

#: DTD 只可能出现在根元素之前，所以只在 prolog 区找：整文件子串扫描会把 CDATA、注释或
#: 属性值里的 `<!DOCTYPE` 字面量误判成 DTD 而假拒绝，还会 `src.upper()` 复制整份文档。
_XML_DOCTYPE_RE = re.compile(r"<!DOCTYPE", re.IGNORECASE)


def value_type_hint(kind: str, new_value: str) -> str:
    """建议值与节点原类型不符时返回需要的类型名，一致返回空串（§6.7 第 4 步）。

    XML 的 `element`/`attribute`/`text` 都是字符串，落到最后的 `return ""`；是否只对 JSON
    启用这条口径由调用方决定（服务层的 `fmt` 门），本函数不看格式。
    """
    if kind == "int":
        return "" if INT_RE.match(new_value) else TYPE_HINTS["int"]
    if kind == "float":
        try:
            number = float(new_value)
        except ValueError:
            return TYPE_HINTS["float"]
        return "" if math.isfinite(number) else TYPE_HINTS["float"]
    if kind == "bool":
        return "" if new_value in ("true", "false") else TYPE_HINTS["bool"]
    if kind == "NoneType":
        return "" if new_value == "null" else TYPE_HINTS["NoneType"]
    return ""


def apply_edits(src: bytes, fmt: str, edits: Sequence[Edit]) -> bytes:
    """在原文 `src` 上应用 `edits`，返回新的原文字节。

    错误口径（卡面接口契约）：

    * `edits` 为空、格式不是 xml/json、建议值与节点原类型不符 → `INVALID_INPUT`。
    * `loc` 定位不到「承载值的节点」→ `REVIEW_CONFLICT`（无法唯一定位）。
    * 语义校验（§6.7 第 6 步）不过 → `REVIEW_UNSAFE`，**不返回任何字节**，
      调用方因此拿不到可写入的成功正文。
    * 原文连读都读不出来 → `PARSE_FAILED`，与显示链路（§6.1）同口径。

    本函数不读写业务文件（持久化属 `storage`，§3.3），也不调用旧 `serialize_tree`；但它
    **不是纯函数**：`ET.register_namespace` 改的是进程级 `_namespace_map`，注册只增不减。
    每次写回都先把当前文档自己的前缀登记进去，所以同一份原文在同一进程内产出一致；并发
    写回时别的线程可能把同一 URI 的前缀拼写覆盖掉，只影响写法、不影响节点值与 `loc`。

    保真范围：第 6 步校验保证的是**节点级业务语义**（有值节点的值、节点数与 `t` 序列），
    **不保证字节保真**——标准库会统一属性引号、空元素写法与实体写法（§6.7 已知限制）。
    写法之外的东西不在这三类里，一律按原文保住：声明本身与根元素之外的注释/PI/尾随文本
    按字节原位拼回，元素内夹在值中间的注释/PI 原地保留。
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
    encoding = _xml_output_encoding(src)
    root_start, root_end = _xml_root_span(src, encoding)
    prolog, epilog = src[:root_start], src[root_end:]
    # prolog 按原文编码解码后再找：UTF-16 文档里 `<!DOCTYPE` 的字节写法不是 ASCII 那串，
    # 直接对字节做正则会在这一类文件上漏判。区间本来就只到根元素为止，解码量很小。
    if _XML_DOCTYPE_RE.search(prolog.decode(encoding, "ignore")):
        # DTD 里没有「有值节点」，第 6 步看不见它被丢弃（卡面风险条）→ 写回前明确拒绝，
        # 管理员下载原文手工改。只看根元素之前那段：DTD 不许出现在别处，而根内的
        # CDATA/注释里出现这个字面量是合法的，整文件扫描会造成假拒绝。
        # 用正则而不是 `prolog.upper()` 里找子串：后者要为一次判断复制整段文本。
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
            _write_effective_text(elem, new_value)
        else:
            elem.set(attribute, new_value)

    # 正文只重建根元素，声明与根外的两端按字节拼回：`tostring` 不会保留 prolog/epilog，
    # 而它们不是「有值节点」，第 6 步拦不住丢失（B3）。
    body = ET.tostring(root, encoding=encoding, xml_declaration=False)
    return prolog + body + epilog


def _span_markers(encoding: str) -> Tuple[bytes, bytes, bytes]:
    """`<`、`</`、`>` 在该编码下的字节写法。

    区间的两端要按字节核对，而 UTF-16 下一个字符占两个字节；其余 expat 认的编码
    （utf-8、iso-8859-*、windows-125*、shift_jis…）都保持 ASCII 字符的单字节写法，
    用默认那组即可。
    """
    if encoding == "utf-16-le":
        return b"<\x00", b"<\x00/\x00", b">\x00"
    if encoding == "utf-16-be":
        return b"\x00<", b"\x00<\x00/", b"\x00>"
    return b"<", b"</", b">"


def _xml_root_span(data: bytes, encoding: str) -> Tuple[int, int]:
    """量出根元素标记在原文里的字节区间 `(start, end)`，`end` 是根元素最后一个字节之后。

    要「原位搬运」根元素之外的内容就得知道根元素从哪到哪，而 `ET` 的事件里没有位置信息：
    这里用 expat 的 `CurrentByteIndex`（§17 风险表点名的「基于 expat 偏移」路线）。命名空间
    处理必须**关掉**——expat 文档写明开启后该偏移不保证对应原始字节。代价是 XML 侧多一遍
    只装两个 handler 的 expat 扫描（不建树），换到的是「不删内容」；审阅提交在机型写锁内
    一次一份，量级可接受。

    区间两端的字节必须核对得上才敢搬运：核对不上就明确拒绝（宁可不写，也不交出少内容的
    正文），而不是退回静默删除。真出问题这一层也还有第 6 步的再解析兜着。
    """
    open_marker, slash_marker, close_marker = _span_markers(encoding)
    reader = expat.ParserCreate()
    found: Dict[str, int] = {}
    depth = [0]

    def on_start(_name: str, _attrs: Dict[str, str]) -> None:
        depth[0] += 1
        if depth[0] == 1:
            found["start"] = reader.CurrentByteIndex

    def on_end(_name: str) -> None:
        if depth[0] == 1:
            found["end"] = reader.CurrentByteIndex
        depth[0] -= 1

    reader.StartElementHandler = on_start
    reader.EndElementHandler = on_end
    try:
        reader.Parse(data, True)
    except expat.ExpatError as exc:
        raise AppError(REVIEW_UNSAFE, "原文无法按 XML 安全重写：%s" % exc)

    start = found.get("start", -1)
    closed_at = found.get("end", -1)
    # 根元素有两种收尾：`</名称>`（偏移落在它的 `<` 上）与自闭合 `<名称/>`（偏移已在 `/>`
    # 之后）。用「偏移处是不是 `</`」区分，不能用「往后找 `>`」——自闭合根那么找会一头
    # 扎进 epilog，把根后注释切掉半截。
    if data[closed_at:closed_at + len(slash_marker)] == slash_marker:
        end = data.index(close_marker, closed_at) + len(close_marker)
    else:
        end = closed_at
    if not (0 <= start < end <= len(data)):
        raise AppError(REVIEW_UNSAFE, "无法在原文中量出根元素区间，为保住根外内容已拒绝写回")
    if (
        data[start:start + len(open_marker)] != open_marker
        or data[end - len(close_marker):end] != close_marker
    ):
        raise AppError(REVIEW_UNSAFE, "根元素区间与原文对不上，已拒绝写回")
    return start, end


def _effective_text(elem: ET.Element) -> str:
    """元素「第一个子元素之前」的全部字符数据，口径与显示链路一致（§6.2、B7）。

    显示时注释/PI 不进树（§6.1 第 1 步 `insert_comments=False`），标准库把它们前后的文本
    **拼进** `.text`；写入侧的树留着这些节点，同一段文本被切成 `.text` + 各注释/PI 兄弟的
    `.tail`。只读 `elem.text` 就会「显示有值、写入定位不到」。
    """
    pieces = [elem.text or ""]
    for child in elem:
        if isinstance(child.tag, str):
            break  # 子元素之后的文本是 tail，不属于本节点的值（§6.2）
        pieces.append(child.tail or "")
    return "".join(pieces)


def _write_effective_text(elem: ET.Element, value: str) -> None:
    """把 `_effective_text` 那段整段换成 `value`，中间的注释/PI 节点原地保留。

    值写在段首（`elem.text`），段内那些注释/PI 兄弟的尾随文本清空：被替换的就是这一整段
    字符数据（显示时它们本来就被拼成同一个值），节点本身一个都不动。段首原有的空白会随
    建议值被取代——与没有注释的元素上 `elem.text = value` 的既有行为一致（显示时 strip）。
    """
    elem.text = value
    for child in elem:
        if isinstance(child.tag, str):
            break
        child.tail = None


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

    值行文本按 `_effective_text` 取，不用裸 `elem.text`：否则元素内含注释/PI 时显示有值、
    这里定位不到，`/review/submit` 会永久 409「无法唯一定位」（§6.7、B7）。

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
        text = _effective_text(elem).strip()

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


def _xml_output_encoding(src: bytes) -> str:
    """正文要写回的目标编码：必须与按字节拼回的那段 prolog 里的声明一致。

    UTF-16 认 BOM，也认 expat 自己的嗅探（`<\\x00` / `\\x00<`）——NUL 在 XML 里是非法字符，
    所以这两种开头只可能来自 UTF-16。认出后用**不带 BOM** 的 `utf-16-le/be` 写正文：文档
    开头那一个 BOM 已经躺在 prolog 里，正文再带一个就变成两份。其余编码取声明里的名字，
    没声明或认不出时按 expat 的默认 utf-8。编码名在这里不用再验一次：原文已经过
    `to_compact`，expat 认的编码名必然也是 Python codec 认的（不认时它抛 `LookupError`，
    在 `apply_edits` 里就变成 `PARSE_FAILED`）。
    """
    if src.startswith(codecs.BOM_UTF16_LE) or src[:2] == b"<\x00":
        return "utf-16-le"
    if src.startswith(codecs.BOM_UTF16_BE) or src[:2] == b"\x00<":
        return "utf-16-be"
    return _xml_declared_encoding(src)


def _xml_declared_encoding(src: bytes) -> str:
    """声明里写的编码名（§6.7 第 5 步：`tostring` 的 `encoding` 参数照源来）。"""
    head = src[:512]
    if head.startswith(codecs.BOM_UTF8):
        head = head[len(codecs.BOM_UTF8):]
    match = _XML_DECL_RE.match(head.lstrip())
    if not match:
        return "utf-8"
    encoding = _XML_ENCODING_RE.search(match.group(1))
    return encoding.group(1).decode("ascii") if encoding else "utf-8"


def _keep_bom(src: bytes, out: bytes) -> bytes:
    """`json.loads` 会吃掉 UTF-8 BOM，写回时放回去：BOM 属于原文，不是选中节点。

    XML 侧不走这里——根外两端整体拼回时 BOM 已经在 prolog 里，再加一遍就是两份。
    """
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
    """建议值 → `(要写进 JSON 的值, 它在 compact 里应有的字符串)`（§6.2、§6.7 第 4/5 步）。

    类型口径不在这里再写一份：判定来自 `value_type_hint`，与提交建议（`services/review.py`）
    是同一个函数。过不了就 `INVALID_INPUT`，不把数字写成字符串——那样节点的 `t` 会变质，
    第 6 步只能事后拒绝。`NoneType` 的转换按 §6.7 第 5 步保留，但 null 是只读值
    （§6.7 第 4 步注），真提交「改成 null」会被第 6 步当成无变更拒绝。
    """
    hint = value_type_hint(kind, new_value)
    if hint:
        raise AppError(INVALID_INPUT, "建议值无法写成%s：%s" % (hint, new_value))
    if kind == "int":
        number = int(new_value)
        return number, str(number)
    if kind == "float":
        number = float(new_value)
        return number, str(number)
    if kind == "bool":
        return new_value == "true", new_value
    if kind == "NoneType":
        return None, "null"
    return new_value, new_value
