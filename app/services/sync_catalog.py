"""M16-32 SDA catalog 受控读取与来源选择校验。

collector 只允许返回已注册的 `table/file` 条目；主应用不接触任意 URL/JS。
真实 catalog 由部署端通过 `register_reader` 注入；未接入时 HTTP 返回
`DOWNLOAD_FAILED + reason_code=collector_offline`，不伪造目录内容。
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from app.core.errors import DOWNLOAD_FAILED, INVALID_INPUT, AppError

from . import sync_connections

_READER = None
_RESOURCE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,159}$")
CATALOG_KINDS = ("table", "file")


def register_reader(reader: Optional[Any]) -> None:
    global _READER
    _READER = reader


def has_reader() -> bool:
    return _READER is not None


def _text(value: Any, field: str, limit: int) -> str:
    text = str(value or "").strip()
    if not text or len(text) > limit or "\x00" in text or "\\" in text:
        raise AppError(INVALID_INPUT, "%s 非法" % field, field=field)
    return text


def _safe_item(raw: Any) -> Dict[str, str]:
    if not isinstance(raw, dict):
        raise AppError(INVALID_INPUT, "catalog 条目必须是对象", field="items")
    name = _text(raw.get("name"), "name", 120)
    kind = str(raw.get("kind") or "").strip().lower()
    if kind not in CATALOG_KINDS:
        raise AppError(INVALID_INPUT, "catalog kind 非法", field="kind")
    resource_ref = str(raw.get("resource_ref") or "").strip()
    if not _RESOURCE_RE.match(resource_ref):
        raise AppError(INVALID_INPUT, "catalog resource_ref 非法", field="resource_ref")
    display_path = _text(raw.get("display_path"), "display_path", 240)
    if not display_path.startswith("/") or ".." in display_path.split("/"):
        raise AppError(INVALID_INPUT, "catalog display_path 非法", field="display_path")
    return {
        "name": name,
        "kind": kind,
        "resource_ref": resource_ref,
        "display_path": display_path,
    }


def read_catalog(workspace_id: str, connection_id: str) -> List[Dict[str, str]]:
    sync_connections.get_connection(workspace_id, connection_id)
    if _READER is None:
        raise AppError(
            DOWNLOAD_FAILED,
            "collector 未接入，无法读取 SDA 目录",
            reason_code="collector_offline",
        )
    try:
        raw_items = _READER(workspace_id, connection_id)
    except AppError:
        raise
    except Exception:
        raise AppError(
            DOWNLOAD_FAILED,
            "SDA 目录读取失败",
            reason_code="collector_offline",
        )
    if not isinstance(raw_items, (list, tuple)):
        raise AppError(INVALID_INPUT, "catalog 结果必须是数组", field="items")
    out: List[Dict[str, str]] = []
    seen = set()
    for raw in raw_items:
        item = _safe_item(raw)
        key = (item["kind"], item["resource_ref"], item["display_path"])
        if key in seen:
            continue
        seen.add(key)
        out.append(item)
    return out


def validate_selection(
    workspace_id: str,
    connection_id: str,
    kind: str,
    resource_ref: Any,
    display_path: Any,
) -> None:
    """reader 已接入时必须命中同一 kind/resource_ref/display_path；未接入则放行。"""
    if _READER is None:
        return
    wanted_kind = kind[4:] if str(kind).startswith("sda_") else str(kind)
    wanted_ref = str(resource_ref or "").strip()
    wanted_path = str(display_path or "").strip()
    for item in read_catalog(workspace_id, connection_id):
        if (
            item["kind"] == wanted_kind
            and item["resource_ref"] == wanted_ref
            and item["display_path"] == wanted_path
        ):
            return
    raise AppError(
        INVALID_INPUT,
        "SDA catalog 资源 ID 与展示路径不一致或不在当前目录",
        field="resource_ref",
    )
