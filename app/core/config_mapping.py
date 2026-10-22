"""参数定位与模板变体契约（M5-06 / INH-1940）。

两件事：

1. **稳定参数 ID ↔ 目标文件里的一个可写位置**。XML 用「元素路径 + 同名序号 (+ 属性)」，
   JSON 用 RFC 6901 Pointer。同一定位被两个参数占用就是 `MAPPING_AMBIGUOUS`——
   这是配置写回最危险的错误，必须在校验阶段拦住，而不是等到编译 XML 时才发现。
2. **模板变体兼容判定**：定义集合与变体必须精确绑定（同一软件版本可有多个结构变体）。

命名只是**候选证据**：`testParamX` 与 `TESTPARAMX` 形态相似不构成同一参数的证明，
本模块不提供任何"按名字自动合并"的入口。
"""

from __future__ import annotations

import hashlib
import xml.etree.ElementTree as ET
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from .errors import (
    ARTIFACT_TEMPLATE_INCOMPATIBLE,
    INVALID_INPUT,
    MAPPING_AMBIGUOUS,
    AppError,
)


class XmlLoc:
    """XML 定位：`Axis[2]/Velocity` 这类路径 + 可选属性名。

    `steps` 是 `(元素名, 同名序号)`，序号从 1 起；元素名可带 `{uri}` 命名空间。
    """

    __slots__ = ("steps", "attribute")

    def __init__(self, steps: Sequence[Tuple[str, int]], attribute: Optional[str] = None) -> None:
        if not steps:
            raise AppError(INVALID_INPUT, "XML 定位不能为空")
        normalized: List[Tuple[str, int]] = []
        for name, occurrence in steps:
            text = str(name or "").strip()
            if not text:
                raise AppError(INVALID_INPUT, "XML 定位含空元素名")
            number = int(occurrence)
            if number < 1:
                raise AppError(INVALID_INPUT, "同名序号从 1 起")
            normalized.append((text, number))
        self.steps = tuple(normalized)
        self.attribute = attribute or None

    # -- 序列化 -------------------------------------------------------------

    def __str__(self) -> str:
        body = "/".join(
            name if occurrence == 1 else "%s[%d]" % (name, occurrence)
            for name, occurrence in self.steps
        )
        # 属性定位必须带上路径：只写 `@unit` 会让 `Axis/Velocity@unit` 与
        # `Axis/Speed@unit` 撞成同一个字符串，查重时被误判成"两个参数抢同一个节点"。
        return "%s@%s" % (body, self.attribute) if self.attribute else body

    def __repr__(self) -> str:  # pragma: no cover - 诊断用
        return "XmlLoc(%r)" % str(self)

    def to_json(self) -> Dict[str, Any]:
        return {
            "path": [{"name": name, "occurrence": occurrence} for name, occurrence in self.steps],
            "attribute": self.attribute,
        }

    @classmethod
    def from_json(cls, payload: Any) -> "XmlLoc":
        if not isinstance(payload, dict):
            raise AppError(INVALID_INPUT, "XML 定位必须是对象")
        raw_path = payload.get("path")
        if not isinstance(raw_path, list) or not raw_path:
            raise AppError(INVALID_INPUT, "XML 定位缺 path")
        steps = []
        for item in raw_path:
            if not isinstance(item, dict) or "name" not in item:
                raise AppError(INVALID_INPUT, "XML 定位段必须是 {name, occurrence}")
            steps.append((item["name"], int(item.get("occurrence", 1))))
        return cls(steps, payload.get("attribute"))

    @classmethod
    def parse(cls, text: str, attribute: Optional[str] = None) -> "XmlLoc":
        """解析 `Axis[2]/Velocity`；`@attr` 写法表示属性。"""
        raw = str(text or "").strip()
        if not raw:
            raise AppError(INVALID_INPUT, "XML 定位不能为空")
        if "@" in raw:
            raw, _, attr = raw.partition("@")
            attribute = attribute or attr
        steps = []
        for chunk in raw.split("/"):
            segment = chunk.strip()
            if not segment:
                raise AppError(INVALID_INPUT, "XML 定位含空段：%s" % text)
            name, occurrence = segment, 1
            if segment.endswith("]") and "[" in segment:
                name, _, rest = segment[:-1].partition("[")
                try:
                    occurrence = int(rest)
                except ValueError:
                    raise AppError(INVALID_INPUT, "序号不是整数：%s" % segment)
            steps.append((name.strip(), occurrence))
        return cls(steps, attribute)

    def canonical(self) -> str:
        """唯一化后的字符串（序号补全、去空白），用于查重。"""
        return str(self)

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, XmlLoc):
            return NotImplemented
        return self.canonical() == other.canonical()

    def __hash__(self) -> int:
        return hash(self.canonical())


# ---------------------------------------------------------------------------
# JSON Pointer（RFC 6901）
# ---------------------------------------------------------------------------


def encode_pointer(parts: Sequence[str]) -> str:
    """把键序列编码成 Pointer：`~` → `~0`、`/` → `~1`。"""
    encoded = []
    for part in parts:
        text = str(part)
        encoded.append(text.replace("~", "~0").replace("/", "~1"))
    return "/" + "/".join(encoded) if encoded else ""


def parse_pointer(pointer: Any) -> List[str]:
    """解析 Pointer；空串表示根，非法转义即拒绝。"""
    if not isinstance(pointer, str):
        raise AppError(INVALID_INPUT, "JSON Pointer 必须是字符串")
    if pointer == "":
        return []
    if not pointer.startswith("/"):
        raise AppError(INVALID_INPUT, "JSON Pointer 必须以 / 开头")
    parts = []
    for raw in pointer[1:].split("/"):
        if "~" in raw:
            if "~2" in raw or raw.endswith("~") or "~0" not in raw and "~1" not in raw:
                raise AppError(INVALID_INPUT, "JSON Pointer 含非法转义：%s" % raw)
        parts.append(raw.replace("~1", "/").replace("~0", "~"))
    return parts


def resolve_pointer(document: Any, pointer: Any) -> Any:
    """按 Pointer 取值；不存在返回 `None`（由调用方区分缺失与空值）。"""
    node = document
    for part in parse_pointer(pointer):
        if isinstance(node, dict):
            if part not in node:
                return None
            node = node[part]
        elif isinstance(node, list):
            try:
                node = node[int(part)]
            except (ValueError, IndexError):
                return None
        else:
            return None
    return node


# ---------------------------------------------------------------------------
# XML 解析与定位
# ---------------------------------------------------------------------------


def _qualify(name: str, namespace: Optional[str]) -> str:
    if name.startswith("{") or not namespace:
        return name
    return "{%s}%s" % (namespace, name)


def default_namespace(root: ET.Element) -> Optional[str]:
    """从根元素 tag 里取默认命名空间（`{urn:x}root` → `urn:x`）。"""
    tag = root.tag or ""
    if tag.startswith("{"):
        return tag[1:].split("}", 1)[0]
    return None


def resolve_xml(root: ET.Element, loc: XmlLoc, namespace: Optional[str] = None) -> Optional[ET.Element]:
    """按 `XmlLoc` 找元素；找不到返回 None。属性定位时返回宿主元素。"""
    ns = default_namespace(root) if namespace is None else namespace
    node = root
    for name, occurrence in loc.steps:
        wanted = _qualify(name, ns)
        matches = [child for child in list(node) if child.tag == wanted]
        if len(matches) < occurrence:
            return None
        node = matches[occurrence - 1]
    return node


def read_xml_value(root: ET.Element, loc: XmlLoc, namespace: Optional[str] = None) -> Optional[str]:
    """读定位处的文本或属性；元素不存在返回 None（调用方据此判 `missing`）。"""
    node = resolve_xml(root, loc, namespace)
    if node is None:
        return None
    if loc.attribute:
        return node.get(loc.attribute)
    return (node.text or "").strip()


def structure_signature(xml_bytes: bytes) -> str:
    """结构指纹：所有元素的「路径 + 同名序号 + 命名空间」，与文本内容无关。

    用于判定两个出口文件是不是同一个模板变体：改一个值不改变签名，加/删/移动
    节点会改变签名。
    """
    root = ET.fromstring(xml_bytes)
    lines: List[str] = []

    def walk(node: ET.Element, path: str, counters: Dict[str, int]) -> None:
        for child in list(node):
            counters[child.tag] = counters.get(child.tag, 0) + 1
            occurrence = counters[child.tag]
            tag = child.tag or ""
            if tag.startswith("{"):
                uri, _, local = tag[1:].partition("}")
            else:
                uri, local = "", tag
            here = "%s/%s" % (path, local) if path else local
            lines.append("%s|%d|%s|%s" % (here, occurrence, uri, "attr" if child.attrib else ""))
            walk(child, here, {})

    root_tag = root.tag or ""
    uri, _, local = root_tag[1:].partition("}") if root_tag.startswith("{") else ("", "", root_tag)
    lines.append("%s|1|%s|" % (local, uri))
    walk(root, local, {})
    digest = hashlib.sha256("\n".join(lines).encode("utf-8")).hexdigest()
    return digest


# ---------------------------------------------------------------------------
# 映射表与变体兼容
# ---------------------------------------------------------------------------


class MappingEntry:
    """一个参数的定位集合：XML 与 JSON 各自可选。"""

    __slots__ = ("parameter_id", "xml", "json_pointer", "json_key", "unit", "value_type")

    def __init__(
        self,
        parameter_id: str,
        xml: Optional[XmlLoc] = None,
        json_pointer: Optional[str] = None,
        json_key: Optional[str] = None,
        unit: Optional[str] = None,
        value_type: Optional[str] = None,
    ) -> None:
        if not parameter_id:
            raise AppError(INVALID_INPUT, "参数 ID 不能为空")
        if xml is None and json_pointer is None:
            raise AppError(INVALID_INPUT, "参数至少要有一个定位（XML 或 JSON）")
        self.parameter_id = parameter_id
        self.xml = xml
        self.json_pointer = json_pointer
        self.json_key = json_key
        self.unit = unit
        self.value_type = value_type

    def to_json(self) -> Dict[str, Any]:
        return {
            "parameter_id": self.parameter_id,
            "xml": self.xml.to_json() if self.xml else None,
            "json": (
                {"pointer": self.json_pointer, "key": self.json_key}
                if self.json_pointer is not None
                else None
            ),
            "unit": self.unit,
            "type": self.value_type,
        }


class MappingTable:
    """一批参数的定位集合；`assert_unique()` 是发布前必过的闸门。"""

    def __init__(self, entries: Optional[Iterable[MappingEntry]] = None) -> None:
        self._entries: Dict[str, MappingEntry] = {}
        for entry in entries or ():
            self.add(entry)

    def add(self, entry: MappingEntry) -> None:
        if entry.parameter_id in self._entries:
            raise AppError(MAPPING_AMBIGUOUS, "参数重复登记：%s" % entry.parameter_id)
        self._entries[entry.parameter_id] = entry

    def get(self, parameter_id: str) -> Optional[MappingEntry]:
        return self._entries.get(parameter_id)

    def __len__(self) -> int:
        return len(self._entries)

    def parameter_ids(self) -> List[str]:
        return sorted(self._entries)

    def duplicates(self) -> Dict[str, List[str]]:
        """返回「同一目标被多个参数占用」的清单：`目标 → 参数 ID 列表`。"""
        seen: Dict[str, List[str]] = {}
        for parameter_id in sorted(self._entries):
            entry = self._entries[parameter_id]
            if entry.xml is not None:
                seen.setdefault("xml:" + entry.xml.canonical(), []).append(parameter_id)
            if entry.json_pointer is not None:
                seen.setdefault("json:" + entry.json_pointer, []).append(parameter_id)
        return {target: owners for target, owners in seen.items() if len(owners) > 1}

    def assert_unique(self) -> None:
        duplicates = self.duplicates()
        if duplicates:
            target, owners = sorted(duplicates.items())[0]
            raise AppError(
                MAPPING_AMBIGUOUS,
                "同一目标被多个参数占用",
                target=target,
                parameters=owners,
            )

    def resolve_xml(self, parameter_id: str, root: ET.Element, namespace: Optional[str] = None) -> Optional[str]:
        entry = self._entries.get(parameter_id)
        if entry is None or entry.xml is None:
            return None
        return read_xml_value(root, entry.xml, namespace)

    def resolve_json(self, parameter_id: str, document: Any) -> Any:
        entry = self._entries.get(parameter_id)
        if entry is None or entry.json_pointer is None:
            return None
        return resolve_pointer(document, entry.json_pointer)


def to_xml_loc(payload: Any) -> Optional[XmlLoc]:
    """兼容两种写法：字符串 `Axis[2]/Velocity` 或契约里的 `{path, attribute}`。"""
    if payload is None:
        return None
    if isinstance(payload, XmlLoc):
        return payload
    if isinstance(payload, str):
        return XmlLoc.parse(payload)
    return XmlLoc.from_json(payload)


def check_variant_compatibility(
    *,
    variant_id: Optional[str],
    definition_set: Dict[str, Any],
    release_id: str,
    template_sha256: str,
    expected_template_sha256: Optional[str] = None,
) -> None:
    """定义集合 / 变体 / 软件版本 / 模板哈希必须四者一致，否则拒绝生成产物。"""
    problems = []
    if not variant_id:
        problems.append("未指定模板变体")
    elif definition_set.get("variant_id") != variant_id:
        problems.append("定义集合属于其他变体")
    if definition_set.get("release_id") != release_id:
        problems.append("定义集合属于其他软件版本")
    if not template_sha256:
        problems.append("模板哈希为空")
    if expected_template_sha256 and template_sha256 != expected_template_sha256:
        problems.append("模板内容与登记哈希不符")
    if problems:
        raise AppError(
            ARTIFACT_TEMPLATE_INCOMPATIBLE,
            "；".join(problems),
            variant_id=variant_id,
            release_id=release_id,
        )
