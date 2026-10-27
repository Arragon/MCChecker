"""DL 计算参数的保存与修改历史。

任何人（含访客）都可以保存当前计算项的全部参数：多项式系数、上下限、绑定、
PP 能量的换算系数，以及 RP 脉宽的两套参数和固定和。每次真正改动都记一条历史：
IP、已登记姓名、事件、备注和整份设置。一键应用会把该条历史整份写回，
但不再追加记录；和已有记录完全相同的「应用历史」不出现在列表里。
管理员可以删除一条历史，当前正在使用的参数不变。

历史文件是机型目录里的 `dl_coeff_history.json`。读取时不创建文件。
与当前设置完全相同的最新一条可见记录标成当前。
"""

from __future__ import annotations

import logging
import math
import os
from copy import deepcopy
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from ..core import dltool, fsutil, storage
from ..core.errors import INVALID_INPUT, NOT_FOUND, REVISION_CONFLICT, AppError, CorruptDataError
from .auth import Actor

logger = logging.getLogger(__name__)

HISTORY_FILE = "dl_coeff_history.json"
NOTE_LIMIT = 200
KEEP_PER_SERIES = 200
EVENTS = ("save", "apply", "config")
AOM_MODES = ("delay", "width")

_COEFF_FIELDS = dltool.KEYS + ("b",)
_RANGE_FIELDS = ("x_min", "x_max", "y_min", "y_max")
_IF_MATCH = "If-Match"


def history_path() -> str:
    """调用方必须已在 `storage.use_profile` 里。"""
    return os.path.join(storage.get_profile_data_dir(storage.get_active_profile()), HISTORY_FILE)


def list_history(pid: str, item: str, aom: Any) -> Dict[str, Any]:
    """当前计算项的全部参数历史，新的在前。`aom` 只保留兼容，不再拆开两种脉宽模式。"""
    item_id = _require_item(item)
    with storage.use_profile(pid):
        with storage.profile_lock(pid):
            cfg = dltool.load_config()
            live = _item_snapshot(item_id, _item_target(cfg, item_id))
            entries = _visible_entries(_load_book(), item_id)
    current_id = _current_id(entries, live, item_id)
    return {
        "aom": "",
        "current_id": current_id,
        "entries": [_public(entry, current_id) for entry in reversed(entries)],
    }


def save_coefficients(
    pid: str,
    item: str,
    payload: Optional[Dict[str, Any]],
    if_match: Optional[str],
    actor: Optional[Actor],
) -> Dict[str, Any]:
    """写入当前计算项的全部参数，并在确实变化时追加记录。"""
    item_id = _require_item(item)
    header = _require_match(if_match)
    if not isinstance(payload, dict):
        raise AppError(INVALID_INPUT, "请求体必须是对象")
    incoming = _validated_snapshot(item_id, payload)
    note = _validated_note(payload.get("note", ""))

    with storage.use_profile(pid), storage.profile_write(pid):
        _reject_stale(header)
        cfg = dltool.load_config()
        target = _item_target(cfg, item_id)
        current = _item_snapshot(item_id, target)
        if _same_snapshot(current, incoming):
            return {"rev": _rev(), "snapshot": current, "coeffs": current["coeffs"], "unchanged": True, "entry": None}
        _load_book()
        _write_snapshot(item_id, target, incoming)
        dltool.save_config(cfg)
        entry = _append_locked(item_id, "", "save", note, incoming, actor, "")
        rev = _rev()

    from . import revision
    from .dl import PART

    revision.bump(pid, PART)
    logger.info("DL 参数已保存：机型=%s 计算项=%s 记录=%s", pid, item_id, entry["id"])
    return {
        "rev": rev,
        "snapshot": incoming,
        "coeffs": incoming["coeffs"],
        "unchanged": False,
        "entry": _public(entry, entry["id"]),
    }


def apply_history(
    pid: str,
    item: str,
    entry_id: str,
    if_match: Optional[str],
    actor: Optional[Actor],
) -> Dict[str, Any]:
    """把一条历史里的整份设置写回。包括固定和与 RP 脉宽的两套参数。不新增历史。"""
    item_id = _require_item(item)
    header = _require_match(if_match)
    wanted = str(entry_id or "").strip()

    with storage.use_profile(pid), storage.profile_write(pid):
        _reject_stale(header)
        book = _load_book()
        source = _find(book, wanted)
        if source is None or source.get("item") != item_id:
            raise AppError(NOT_FOUND, "系数历史不存在", id=wanted)
        cfg = dltool.load_config()
        target = _item_target(cfg, item_id)
        incoming = _item_snapshot(item_id, source)
        current = _item_snapshot(item_id, target)
        changed = not _same_snapshot(current, incoming)
        if changed:
            _write_snapshot(item_id, target, incoming)
            dltool.save_config(cfg)
        removed = _prune_redundant_apply(book, item_id)
        if removed:
            fsutil.atomic_write_json(history_path(), book)
        entries = _visible_entries(book, item_id)
        current_id = _current_id(entries, incoming, item_id)
        shown = _find(book, current_id) if current_id else None
        rev = _rev()

    if changed:
        from . import revision
        from .dl import PART

        revision.bump(pid, PART)
    who = actor.ip if actor is not None else ""
    logger.info(
        "DL 参数已应用历史：机型=%s 计算项=%s 来源=%s 操作者=%s 未新增记录",
        pid,
        item_id,
        wanted,
        who,
    )
    return {
        "rev": rev,
        "snapshot": incoming,
        "coeffs": incoming["coeffs"],
        "unchanged": not changed,
        "entry": _public(shown, current_id) if shown else None,
    }


def delete_history(pid: str, item: str, entry_id: str) -> Dict[str, Any]:
    """删掉一条历史。不改当前正在使用的参数。调用方必须已确认是管理员。"""
    item_id = _require_item(item)
    wanted = str(entry_id or "").strip()
    with storage.use_profile(pid), storage.profile_write(pid):
        book = _load_book()
        source = _find(book, wanted)
        if source is None or source.get("item") != item_id:
            raise AppError(NOT_FOUND, "系数历史不存在", id=wanted)
        book["entries"] = [row for row in book["entries"] if row.get("id") != wanted]
        fsutil.atomic_write_json(history_path(), book)
        cfg = dltool.load_config()
        live = _item_snapshot(item_id, _item_target(cfg, item_id))
        current_id = _current_id(_visible_entries(book, item_id), live, item_id)
    logger.info("DL 参数历史已删除：机型=%s 计算项=%s 记录=%s", pid, item_id, wanted)
    return {"deleted": wanted, "current_id": current_id}


def ensure_history_readable() -> None:
    """历史文件损坏时尽早失败，避免先改了系数再发现记不下来。"""
    _load_book()


def record_config_save(item: str, before: Dict[str, Any], after: Dict[str, Any], actor: Optional[Actor]) -> None:
    """完整配置保存若改了该计算项的任何参数，补一条「保存配置」记录。"""
    before_snap = _item_snapshot(item, before)
    after_snap = _item_snapshot(item, after)
    if _same_snapshot(before_snap, after_snap):
        return
    book = _load_book()
    _append_locked(item, "", "config", "", after_snap, actor, "", book=book)
    logger.info("DL 完整保存改了参数：计算项=%s", item)


def _snapshot_of(item: str, source: Any) -> Dict[str, Any]:
    raw = source if isinstance(source, dict) else {}
    snap = {
        "coeffs": _as_map(_canon(raw.get("coeffs"))),
        "x_min": _optional_number(raw.get("x_min")),
        "x_max": _optional_number(raw.get("x_max")),
        "y_min": _optional_number(raw.get("y_min")),
        "y_max": _optional_number(raw.get("y_max")),
        "binding": _binding_view(raw.get("binding")),
    }
    if item == "pp_energy":
        snap["trans_coeff"] = _finite_stored(raw.get("trans_coeff"), 1.0)
        snap["power_conv_coeff"] = _finite_stored(raw.get("power_conv_coeff"), 1.0)
    return snap


def _item_snapshot(item: str, source: Any) -> Dict[str, Any]:
    snap = _snapshot_of(item, source)
    if item != "rp_width":
        return snap
    raw = source if isinstance(source, dict) else {}
    width = raw.get("aom_width")
    snap["aom_sum"] = _optional_number(raw.get("aom_sum"))
    snap["aom_width"] = _snapshot_of("rp_width", width if isinstance(width, dict) else {})
    return snap


def _item_target(cfg: Dict[str, Any], item: str) -> Dict[str, Any]:
    items = cfg.get("items")
    if not isinstance(items, dict) or not isinstance(items.get(item), dict):
        raise AppError(NOT_FOUND, "计算项不存在")
    host = items[item]
    if item == "rp_width" and not isinstance(host.get("aom_width"), dict):
        host["aom_width"] = deepcopy(dltool.DEFAULT_ITEM)
    if not isinstance(host.get("coeffs"), dict):
        host["coeffs"] = {}
    return host


def _item_entries(book: Dict[str, Any], item: str) -> List[Dict[str, Any]]:
    return [row for row in book["entries"] if row.get("item") == item]


def _redundant_apply_ids(entries: List[Dict[str, Any]], item: str) -> set:
    """和更早一条完全相同的「应用历史」。这种记录只是把已有设置又写了一遍。"""
    seen: List[Dict[str, Any]] = []
    drop = set()
    for row in entries:
        if row.get("item") != item:
            continue
        snap = _item_snapshot(item, row)
        if row.get("event") == "apply" and any(_same_snapshot(snap, prev) for prev in seen):
            ident = row.get("id")
            if ident:
                drop.add(ident)
            continue
        seen.append(snap)
    return drop


def _visible_entries(book: Dict[str, Any], item: str) -> List[Dict[str, Any]]:
    drop = _redundant_apply_ids(book["entries"], item)
    return [row for row in _item_entries(book, item) if row.get("id") not in drop]


def _prune_redundant_apply(book: Dict[str, Any], item: str) -> int:
    drop = _redundant_apply_ids(book["entries"], item)
    if not drop:
        return 0
    book["entries"] = [row for row in book["entries"] if row.get("id") not in drop]
    return len(drop)


def _write_snapshot(item: str, target: Dict[str, Any], snap: Dict[str, Any]) -> None:
    target["coeffs"] = dict(snap["coeffs"])
    for key in _RANGE_FIELDS:
        target[key] = snap[key]
    binding = target.get("binding")
    if not isinstance(binding, dict):
        binding = {}
        target["binding"] = binding
    binding["source_file"] = snap["binding"]["source_file"]
    binding["variable_path"] = snap["binding"]["variable_path"]
    binding["field_map"] = dict(snap["binding"]["field_map"])
    if item == "pp_energy":
        target["trans_coeff"] = snap["trans_coeff"]
        target["power_conv_coeff"] = snap["power_conv_coeff"]
    if item == "rp_width":
        target["aom_sum"] = snap.get("aom_sum")
        width = target.get("aom_width")
        if not isinstance(width, dict):
            width = deepcopy(dltool.DEFAULT_ITEM)
            target["aom_width"] = width
        if isinstance(snap.get("aom_width"), dict):
            _write_snapshot("rp_energy", width, snap["aom_width"])


def _same_snapshot(left: Dict[str, Any], right: Dict[str, Any]) -> bool:
    if _canon(left.get("coeffs")) != _canon(right.get("coeffs")):
        return False
    for key in _RANGE_FIELDS:
        if _optional_number(left.get(key)) != _optional_number(right.get(key)):
            return False
    if _binding_key(left.get("binding")) != _binding_key(right.get("binding")):
        return False
    if "trans_coeff" in left or "trans_coeff" in right:
        if _finite_stored(left.get("trans_coeff"), 1.0) != _finite_stored(right.get("trans_coeff"), 1.0):
            return False
        if _finite_stored(left.get("power_conv_coeff"), 1.0) != _finite_stored(right.get("power_conv_coeff"), 1.0):
            return False
    if "aom_sum" in left or "aom_sum" in right:
        if _optional_number(left.get("aom_sum")) != _optional_number(right.get("aom_sum")):
            return False
    if "aom_width" in left or "aom_width" in right:
        if not _same_snapshot(left.get("aom_width") or {}, right.get("aom_width") or {}):
            return False
    return True


def _validated_snapshot(item: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    from .dl import TRANS_ZERO_MESSAGE, _as_optional_number, _validated_binding

    missing = [field for field in _RANGE_FIELDS if field not in payload]
    if missing:
        raise AppError(
            INVALID_INPUT,
            "保存时必须提供完整的范围字段（缺少：%s）；不需要限制时请显式传 null" % "、".join(missing),
            field=missing[0],
        )
    if "binding" not in payload:
        raise AppError(INVALID_INPUT, "binding 必须是对象", field="binding")
    coeffs = _validated_coeffs(payload.get("coeffs"))
    ranges = {field: _as_optional_number(payload.get(field), field=field) for field in _RANGE_FIELDS}
    if ranges["x_min"] is not None and ranges["x_max"] is not None and ranges["x_min"] >= ranges["x_max"]:
        raise AppError(INVALID_INPUT, "x 范围下限必须小于上限", field="x_min")
    if ranges["y_min"] is not None and ranges["y_max"] is not None and ranges["y_min"] >= ranges["y_max"]:
        raise AppError(INVALID_INPUT, "y 范围下限必须小于上限", field="y_min")
    binding = _binding_view(_validated_binding(payload))
    snap = {"coeffs": _as_map(_canon(coeffs)), "binding": binding}
    snap.update(ranges)
    if item == "pp_energy":
        trans = _finite(payload.get("trans_coeff"), "trans_coeff", "传输系数与功率转换系数必须是有限数值")
        power = _finite(payload.get("power_conv_coeff"), "power_conv_coeff", "传输系数与功率转换系数必须是有限数值")
        if power == 0.0:
            raise AppError(INVALID_INPUT, "功率转换系数不能为 0", field="power_conv_coeff")
        if trans == 0.0:
            raise AppError(INVALID_INPUT, TRANS_ZERO_MESSAGE, field="trans_coeff")
        snap["trans_coeff"] = trans
        snap["power_conv_coeff"] = power
    if item == "rp_width":
        if "aom_sum" not in payload:
            raise AppError(INVALID_INPUT, "保存时必须提供固定和；不需要时请显式传 null", field="aom_sum")
        raw_sum = payload.get("aom_sum")
        if raw_sum is None or raw_sum == "":
            snap["aom_sum"] = None
        else:
            snap["aom_sum"] = _finite(raw_sum, "aom_sum", "AOM2 delay 与 width 的和必须是有限数值")
        width = payload.get("aom_width")
        if not isinstance(width, dict):
            raise AppError(INVALID_INPUT, "aom_width 必须是对象", field="aom_width")
        snap["aom_width"] = _validated_snapshot("rp_energy", width)
    return snap


def _mode_set(item: str, host: Any, mode: str) -> Dict[str, Any]:
    if not isinstance(host, dict):
        return {}
    if item == "rp_width" and mode == "width":
        width = host.get("aom_width")
        return width if isinstance(width, dict) else {}
    return host


def _binding_view(raw: Any) -> Dict[str, Any]:
    source = raw if isinstance(raw, dict) else {}
    field_map = source.get("field_map")
    pairs: Dict[str, str] = {}
    if isinstance(field_map, dict):
        for field, coeff in field_map.items():
            if isinstance(field, str) and isinstance(coeff, str):
                pairs[field] = coeff
    return {
        "source_file": source.get("source_file") if isinstance(source.get("source_file"), str) else "",
        "variable_path": source.get("variable_path") if isinstance(source.get("variable_path"), str) else "",
        "field_map": pairs,
    }


def _binding_key(raw: Any) -> Tuple[str, str, Tuple[Tuple[str, str], ...]]:
    view = _binding_view(raw)
    return (view["source_file"], view["variable_path"], tuple(sorted(view["field_map"].items())))


def _optional_number(value: Any) -> Optional[float]:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number):
        return None
    return number


def _finite_stored(value: Any, default: float) -> float:
    number = _optional_number(value)
    return default if number is None else number


def _append_locked(
    item: str,
    mode: str,
    event: str,
    note: str,
    snapshot: Dict[str, Any],
    actor: Optional[Actor],
    source_id: str,
    book: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    if book is None:
        book = _load_book()
    _prune_redundant_apply(book, item)
    number = int(book["next"])
    entry = {
        "id": "ch_%06d" % number,
        "item": item,
        "aom": mode,
        "event": event,
        "note": note,
        "coeffs": dict(snapshot["coeffs"]),
        "x_min": snapshot["x_min"],
        "x_max": snapshot["x_max"],
        "y_min": snapshot["y_min"],
        "y_max": snapshot["y_max"],
        "binding": deepcopy(snapshot["binding"]),
        "actor_ip": actor.ip if actor is not None else "",
        "actor_name": actor.name if actor is not None and actor.registered else "",
        "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "source_id": source_id,
    }
    if item == "pp_energy":
        entry["trans_coeff"] = snapshot["trans_coeff"]
        entry["power_conv_coeff"] = snapshot["power_conv_coeff"]
    if item == "rp_width":
        entry["aom_sum"] = snapshot.get("aom_sum")
        entry["aom_width"] = deepcopy(snapshot.get("aom_width") or {})
    book["next"] = number + 1
    entries = book["entries"]
    entries.append(entry)
    series_ids = [row["id"] for row in entries if row.get("item") == item and str(row.get("aom") or "") == mode]
    overflow = len(series_ids) - KEEP_PER_SERIES
    if overflow > 0:
        drop = set(series_ids[:overflow])
        book["entries"] = [row for row in entries if row.get("id") not in drop]
    fsutil.atomic_write_json(history_path(), book)
    return entry


def _load_book() -> Dict[str, Any]:
    path = history_path()
    raw = fsutil.read_json(path, {"next": 1, "entries": []})
    if not isinstance(raw, dict) or not isinstance(raw.get("entries"), list):
        raise CorruptDataError(path)
    nxt = raw.get("next", 1)
    if isinstance(nxt, bool) or not isinstance(nxt, int) or nxt < 1:
        raise CorruptDataError(path)
    entries: List[Dict[str, Any]] = []
    for row in raw["entries"]:
        if isinstance(row, dict) and row.get("id") and row.get("item"):
            entries.append(row)
    return {"next": nxt, "entries": entries}


def _series(book: Dict[str, Any], item: str, mode: str) -> List[Dict[str, Any]]:
    return [
        row
        for row in book["entries"]
        if row.get("item") == item and str(row.get("aom") or "") == mode
    ]


def _find(book: Dict[str, Any], entry_id: Optional[str]) -> Optional[Dict[str, Any]]:
    if not entry_id:
        return None
    for row in book["entries"]:
        if row.get("id") == entry_id:
            return row
    return None


def _current_id(entries: List[Dict[str, Any]], live: Dict[str, Any], item: str) -> Optional[str]:
    """同一套参数可能出现多次，当前是时间上最新的那条。`entries` 按写入顺序。"""
    found = None
    for row in entries:
        if _same_snapshot(live, _item_snapshot(item, row)):
            found = row.get("id")
    return found


def _public(entry: Optional[Dict[str, Any]], current_id: Optional[str]) -> Optional[Dict[str, Any]]:
    if entry is None:
        return None
    view = {
        "id": entry.get("id") or "",
        "item": entry.get("item") or "",
        "aom": entry.get("aom") or "",
        "event": entry.get("event") if entry.get("event") in EVENTS else "save",
        "note": entry.get("note") or "",
        "coeffs": _as_map(_canon(entry.get("coeffs"))),
        "x_min": _optional_number(entry.get("x_min")),
        "x_max": _optional_number(entry.get("x_max")),
        "y_min": _optional_number(entry.get("y_min")),
        "y_max": _optional_number(entry.get("y_max")),
        "binding": _binding_view(entry.get("binding")),
        "actor_ip": entry.get("actor_ip") or "",
        "actor_name": entry.get("actor_name") or "",
        "created_at": entry.get("created_at") or "",
        "source_id": entry.get("source_id") or "",
        "current": entry.get("id") == current_id,
    }
    if entry.get("item") == "pp_energy":
        view["trans_coeff"] = _finite_stored(entry.get("trans_coeff"), 1.0)
        view["power_conv_coeff"] = _finite_stored(entry.get("power_conv_coeff"), 1.0)
    if entry.get("item") == "rp_width":
        view["aom_sum"] = _optional_number(entry.get("aom_sum"))
        view["aom_width"] = _snapshot_of("rp_width", entry.get("aom_width") if isinstance(entry.get("aom_width"), dict) else {})
    return view


def _live_coeffs(cfg: Dict[str, Any], item: str, mode: str) -> Dict[str, float]:
    return _as_map(_canon(_target_set(cfg, item, mode).get("coeffs")))


def _target_set(cfg: Dict[str, Any], item: str, mode: str) -> Dict[str, Any]:
    items = cfg.get("items")
    if not isinstance(items, dict) or not isinstance(items.get(item), dict):
        raise AppError(NOT_FOUND, "计算项不存在")
    host = items[item]
    if item == "rp_width" and mode == "width":
        width = host.get("aom_width")
        if not isinstance(width, dict):
            width = deepcopy(dltool.DEFAULT_ITEM)
            host["aom_width"] = width
        if not isinstance(width.get("coeffs"), dict):
            width["coeffs"] = {}
        return width
    if not isinstance(host.get("coeffs"), dict):
        host["coeffs"] = {}
    return host


def _require_item(item: Any) -> str:
    from .dl import ITEMS

    clean = str(item or "")
    if clean not in ITEMS:
        raise AppError(NOT_FOUND, "计算项不存在", item=clean)
    return clean


def _normalize_aom(item: str, raw: Any) -> str:
    if item != "rp_width":
        return ""
    if raw is None or raw == "":
        return "delay"
    mode = str(raw).strip()
    if mode not in AOM_MODES:
        raise AppError(INVALID_INPUT, "RP 脉宽模式只能是 delay 或 width", field="aom")
    return mode


def _require_match(if_match: Optional[str]) -> str:
    header = str(if_match or "").strip()
    if not header:
        raise AppError(
            REVISION_CONFLICT,
            "缺少 If-Match 头：请先 GET /dl 取得当前 rev",
            header=_IF_MATCH,
        )
    return header


def _reject_stale(header: str) -> None:
    if header != _rev():
        raise AppError(REVISION_CONFLICT, "计算参数已被他人修改，请刷新后再保存")


def _rev() -> str:
    from .dl import _current_rev_in_profile

    return _current_rev_in_profile()


def _validated_coeffs(raw: Any) -> Dict[str, float]:
    if not isinstance(raw, dict):
        raise AppError(INVALID_INPUT, "系数必须是有限数值", field="coeffs")
    coeffs: Dict[str, float] = {}
    for key in _COEFF_FIELDS:
        coeffs[key] = _finite(raw.get(key), "coeffs.%s" % key, "系数必须是有限数值")
    return coeffs


def _validated_note(raw: Any) -> str:
    if raw is None:
        return ""
    if not isinstance(raw, str):
        raise AppError(INVALID_INPUT, "备注必须是文本", field="note")
    text = raw.strip()
    if "\x00" in text:
        raise AppError(INVALID_INPUT, "备注必须是文本", field="note")
    if len(text) > NOTE_LIMIT:
        raise AppError(INVALID_INPUT, "备注不能超过 %d 字" % NOTE_LIMIT, field="note")
    return text


def _finite(value: Any, field: str, message: str) -> float:
    if isinstance(value, bool) or value is None:
        raise AppError(INVALID_INPUT, message, field=field)
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        raise AppError(INVALID_INPUT, message, field=field)
    if not math.isfinite(number):
        raise AppError(INVALID_INPUT, message, field=field)
    return number


def _canon(raw: Any) -> Tuple[float, ...]:
    src = raw if isinstance(raw, dict) else {}
    values = []
    for key in _COEFF_FIELDS:
        try:
            number = float(src.get(key, 0.0))
        except (TypeError, ValueError):
            number = 0.0
        if not math.isfinite(number):
            number = 0.0
        values.append(number)
    return tuple(values)


def _as_map(values: Tuple[float, ...]) -> Dict[str, float]:
    return {key: values[index] for index, key in enumerate(_COEFF_FIELDS)}
