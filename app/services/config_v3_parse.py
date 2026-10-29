"""V3 精确 XML/JSON 定位（M11-03 / INH-2132）。

XML 用 ADR-007 的双表示：服务端 locator 保存
`path: [{namespace_uri, local_name, occurrence}]`，显示路径派生为原型风格
`/R[1]/params[1]/item[2]`；属性追加 `/@name`，混合内容的文本追加 `/#text`。
不同前缀只要 URI 相同，locator 就相同。

JSON 用 RFC6901 pointer（`~`→`~0`、`/`→`~1`）；重复键、NaN/Infinity、
超深嵌套、DTD/ENTITY 一律 `PARSE_FAILED/TOO_LARGE`，解析前不写任何文件。
"""

from __future__ import annotations

import io
import json
import re
import xml.etree.ElementTree as ET
from typing import Any, Dict, Iterable, Iterator, List, Optional, Tuple

from ..core import config_mapping
from ..core.errors import PARSE_FAILED, TOO_LARGE, AppError

try:  # parser 的公开上限与这里共用，避免两套数字
    from ..core.parser import MAX_DEPTH, MAX_NODES
except Exception:  # pragma: no cover - 仅防御导入环
    MAX_DEPTH = 512
    MAX_NODES = 2_000_000

_DTD_RE = re.compile(br"<!(DOCTYPE|ENTITY)", re.IGNORECASE)


def _split_tag(tag: Any) -> Tuple[str, str]:
    text = str(tag or "")
    if text.startswith("{"):
        uri, _, local = text[1:].partition("}")
        return uri, local
    return "", text


def _display_path(parts: List[Dict[str, Any]], suffix: str = "") -> str:
    if not parts:
        return suffix or "/"
    out = "/" + "/".join(
        "%s[%d]" % (part["local_name"], part["occurrence"]) for part in parts
    )
    return out + suffix


def xml_locators(content: bytes) -> List[Dict[str, Any]]:
    """解析 XML，返回全部元素值/属性/混合文本的精确 locator（只读）。"""
    if _DTD_RE.search(content or b""):
        raise AppError(PARSE_FAILED, "文件包含 DTD/实体声明，出于安全原因不予解析")
    rows: List[Dict[str, Any]] = []
    stack: List[Dict[str, Any]] = []
    counters: List[Dict[Tuple[str, str], int]] = [{}]
    try:
        for event, elem in ET.iterparse(io.BytesIO(content), events=("start", "end")):
            if event == "start":
                uri, local = _split_tag(elem.tag)
                depth = len(stack) + 1
                if depth > MAX_DEPTH:
                    raise AppError(TOO_LARGE, "文档嵌套超过上限（%d 层）" % MAX_DEPTH)
                counter = counters[-1]
                occurrence = counter.get((uri, local), 0) + 1
                counter[(uri, local)] = occurrence
                part = {
                    "namespace_uri": uri,
                    "local_name": local,
                    "occurrence": occurrence,
                }
                frame = {
                    "path": (stack[-1]["path"] if stack else []) + [part],
                }
                stack.append(frame)
                counters.append({})
                for name, value in elem.attrib.items():
                    _append_row(rows, frame["path"], "attribute", value,
                                attribute_name=name)
                if len(rows) > MAX_NODES:
                    raise AppError(TOO_LARGE, "节点数超过上限（%d）" % MAX_NODES)
            else:
                if not stack:
                    continue
                frame = stack.pop()
                counters.pop()
                text = (elem.text or "").strip()
                if len(elem):
                    if text:
                        _append_row(rows, frame["path"], "element_text", text)
                else:
                    _append_row(rows, frame["path"], "element", text or None)
                elem.clear()
    except ET.ParseError as exc:
        raise AppError(PARSE_FAILED, "XML 解析失败：%s" % exc) from exc
    return rows


def _append_row(
    rows: List[Dict[str, Any]],
    path: List[Dict[str, Any]],
    kind: str,
    value: Any,
    attribute_name: str = "",
) -> None:
    suffix = "/@" + attribute_name if kind == "attribute" else ("/#text" if kind == "element_text" else "")
    locator = {
        "path": [dict(part) for part in path],
        "kind": kind,
    }
    if attribute_name:
        locator["attribute_name"] = attribute_name
    rows.append(
        {
            "locator": locator,
            "display_path": _display_path(path, suffix),
            "value": value,
        }
    )


def _reject_constant(value: str) -> Any:
    raise AppError(PARSE_FAILED, "JSON 不允许 %s" % value)


def _pairs(pairs: List[Tuple[str, Any]]) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for key, value in pairs:
        if key in out:
            raise AppError(PARSE_FAILED, "JSON 存在重复键：%s" % key)
        out[key] = value
    return out


def _json_pointer(parent: str, key: str) -> str:
    return parent + "/" + key.replace("~", "~0").replace("/", "~1")


def json_locators(content: bytes) -> List[Dict[str, Any]]:
    """解析 JSON，返回 RFC6901 pointer 定位的全部标量值（只读）。"""
    try:
        text = content.decode("utf-8-sig")
        data = json.loads(text, object_pairs_hook=_pairs, parse_constant=_reject_constant)
    except UnicodeDecodeError as exc:
        raise AppError(PARSE_FAILED, "JSON 不是 UTF-8") from exc
    except json.JSONDecodeError as exc:
        raise AppError(PARSE_FAILED, "JSON 解析失败：%s" % exc) from exc
    except RecursionError as exc:
        raise AppError(TOO_LARGE, "文档嵌套超过上限（%d 层）" % MAX_DEPTH) from exc
    rows: List[Dict[str, Any]] = []
    _walk_json(data, "", 0, rows, "")
    if not rows and not isinstance(data, (dict, list)):
        rows.append({"locator": {"pointer": "", "path_kinds": ""}, "display_path": "", "value": data})
    return rows


def _walk_json(
    value: Any, pointer: str, depth: int, rows: List[Dict[str, Any]], kinds: str
) -> None:
    if depth > MAX_DEPTH:
        raise AppError(TOO_LARGE, "文档嵌套超过上限（%d 层）" % MAX_DEPTH)
    if isinstance(value, dict):
        for key, child in value.items():
            _walk_json(child, _json_pointer(pointer, str(key)), depth + 1, rows, kinds + "o")
        return
    if isinstance(value, list):
        for index, child in enumerate(value):
            _walk_json(child, pointer + "/" + str(index), depth + 1, rows, kinds + "a")
        return
    rows.append(
        {
            "locator": {"pointer": pointer, "path_kinds": kinds},
            "display_path": pointer or "/",
            "value": value,
        }
    )
    if len(rows) > MAX_NODES:
        raise AppError(TOO_LARGE, "节点数超过上限（%d）" % MAX_NODES)


def page(items: Iterable[Dict[str, Any]], offset: int = 0, limit: int = 200) -> List[Dict[str, Any]]:
    """按需分页：大文件只消费当前页，不做第二份索引。"""
    start = max(0, int(offset))
    size = max(1, int(limit))
    out: List[Dict[str, Any]] = []
    for index, item in enumerate(items):
        if index < start:
            continue
        if len(out) >= size:
            break
        out.append(item)
    return out


def locator_key(row: Dict[str, Any]) -> str:
    """把 locator 规范化成可比较的稳定键（测试/去重用）。"""
    return config_mapping.canonical_locator(row.get("locator") or {})
