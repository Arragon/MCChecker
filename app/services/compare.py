"""结构化对比（§7.5、§6.6；T-13 / M2-08）。

两侧都是版本引用 `{"file", "version"}`（`current` / `archive:<文件名>` /
`record:<文件名>`），或上传的临时文件 `{"temp": <token>}`。旧版的四种对比模式
（当前 vs 历史、历史 vs 历史、上传 vs 当前、上传 vs 历史）都能用这对引用表达，
再加上记录 vs 记录的任意组合。

本模块负责把引用解析成原文与 compact 节点，并把绑定表**快照**交给 `core.differ`
（§6.6：`differ` 不再 import `storage`，比较时用的是请求当下的绑定）。
"""

from __future__ import annotations

import os
from typing import Any, Dict, List, Optional

from ..core import differ, parser, storage
from ..core.errors import INVALID_INPUT, AppError
from . import documents

#: 临时文件（上传后未保存的原文）属 M2-10，本卡先明确拒绝而不是静默返回空对比。
TEMP_MESSAGE = "临时文件对比将在 M2-10 接入"


def compare(
    profile_id: str,
    base_ref: Any,
    target_ref: Any,
    text_limit: int = differ.DEFAULT_TEXT_LIMIT,
) -> Dict[str, Any]:
    """`POST /p/{pid}/compare`（§7.5）。只读，不动修订号。"""
    with storage.use_profile(profile_id):
        base = _side(profile_id, base_ref, "base")
        target = _side(profile_id, target_ref, "target")
        bindings = storage.load_bindings()
        return {
            "has_changes": base["text"] != target["text"],
            "base": {"label": base["label"], "name": base["name"]},
            "target": {"label": target["label"], "name": target["name"]},
            "structural": _structural(base, target, bindings),
            "unified": differ.unified_diff_text(
                base["text"],
                target["text"],
                base["name"],
                target["name"],
                _limit(text_limit),
            ),
        }


def _limit(text_limit: int) -> int:
    try:
        value = int(text_limit)
    except (TypeError, ValueError):
        return differ.DEFAULT_TEXT_LIMIT
    return max(0, value)


def _side(profile_id: str, raw: Any, field: str) -> Dict[str, Any]:
    """把一个引用解析成 `{name, label, logical, text, nodes}`。"""
    if not isinstance(raw, dict):
        raise AppError(INVALID_INPUT, "对比的一侧必须是对象", field=field)
    if str(raw.get("temp") or "").strip():
        raise AppError(INVALID_INPUT, TEMP_MESSAGE, field=field)

    name = str(raw.get("file") or "").strip()
    if not name:
        raise AppError(INVALID_INPUT, "缺少 file", field="%s.file" % field)
    version = str(raw.get("version") or "current").strip() or "current"

    ref = documents.resolve_ref(profile_id, name, version)
    data = _read(ref.path)
    display = os.path.basename(ref.path) if ref.kind != "current" else ref.name
    return {
        "name": display,
        "label": _label(ref.kind, display),
        "logical": ref.name,
        "text": _decode(data),
        "nodes": _nodes(ref.name, data),
    }


def _label(kind: str, display: str) -> str:
    """§7.5 的展示名：与模板 mock 的 `版本引用 → label` 一致。"""
    prefix = {"archive": "历史版本 ", "record": "修改记录 "}.get(kind)
    return prefix + display if prefix else "当前版本"


def _structural(
    base: Dict[str, Any], target: Dict[str, Any], bindings: List[Dict[str, Any]]
) -> Dict[str, Any]:
    if base["nodes"] is None or target["nodes"] is None:
        return {
            "type": "unsupported",
            "summary": {"added": 0, "removed": 0, "modified": 0, "bound": 0},
            "items": [],
        }
    return differ.compare_documents(
        base["nodes"],
        target["nodes"],
        base["logical"],
        target["logical"],
        bindings,
    )


def _nodes(name: str, data: bytes) -> Optional[Dict[str, List[Any]]]:
    try:
        return parser.to_compact(data, name)["nodes"]
    except Exception:
        return None


def _read(path: str) -> bytes:
    with open(path, "rb") as fh:
        return fh.read()


def _decode(data: bytes) -> str:
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError:
        return data.decode("latin-1")
