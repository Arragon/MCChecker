"""三方合并纯算法（M7-01 / INH-1960）。

一次升级要同时满足三方：**O** 旧共同基准、**L** 旧有效机型值、**N** 新通用基线。
节点位置、字符串形状都不能用来判等——同一个参数在不同版本里可能换了 XML 名称、
换了单位、从元素搬到属性。所以：

1. **按稳定 `parameter_id` 对齐**，不看 XPath、不看 `xml_name`；
2. **比较 presence + 类型化值 + 意图**三件事，缺一不可（`650ms` 与 `0.8s` 先换算再比）；
3. 用**目标版本**的 `DefinitionSet` 做类型/单位/枚举/范围转换，转换要给得出证据；
4. **只出计划，不发布**。这里没有 I/O、没有仓储、没有事务——结果是纯数据，
   由上层（M7-02/03/05/06）决定怎么落成基线修订。

## 为什么"意图"必须参与比较

`L` 的值与 `O` 一样、但意图是 `pin`，这不是"没改"。升级时如果只比数值，
这个 pin 会被当成"未改动"而被新通用值覆盖——**用户明确钉住的东西被静默改掉**
是这类工具最不可接受的失败。所以意图不同也算变更，并要求人确认。

## 三种"人工处理"的来源

* `unknown_parameter` —— L 有、N 的定义集合里没有（拆分/合并后旧 ID 消失）；
* `retired_target` —— L 改了（override/pin）但 N 里这个参数已退役/删除；
* `incomparable` —— 单位量纲不同或不在换算表内，**不猜**，交给人。

三者都进 `blockers`，对应项 `action="manual"`，且 `candidate=None`。
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional, Tuple

from . import config_values as values
from .errors import INVALID_INPUT, AppError

#: 逐项动作。`keep` = 不需要人动手；`manual` = 算法不猜。
ACTIONS = ("keep", "from_new", "drop", "conflict", "manual")

#: 决议词表与变更单一致（M6-11），前端不用再翻译一遍。
DECISIONS = ("use_new", "inherit_old", "manual", "pin", "clear_pin")

#: 阻断码。
BLOCK_UNKNOWN = "unknown_parameter"
BLOCK_RETIRED = "retired_target"
BLOCK_MISSING_DEFINITION = "missing_definition"
BLOCK_INCOMPARABLE = "incomparable"
BLOCK_OUT_OF_RANGE = "out_of_range"
BLOCK_LOCATION_MOVED = "location_moved"

BLOCKER_MESSAGES = {
    BLOCK_UNKNOWN: "目标版本的定义集合里没有这个参数（可能被拆分或合并），无法自动对齐",
    BLOCK_RETIRED: "本地改过这个参数，但目标版本里它已退役，不能继续写入",
    BLOCK_MISSING_DEFINITION: "本地有值但旧定义集合里查不到定义，来源不明",
    BLOCK_INCOMPARABLE: "两侧单位量纲不同或不在换算表内，不能判等也不该猜",
    BLOCK_OUT_OF_RANGE: "换算后的值超出目标定义的范围/枚举",
    BLOCK_LOCATION_MOVED: "目标版本的落点变了，直接写入会写错位置",
}

#: 意图词表。`inherit` 表示"跟随上游"，是默认值。
INTENT_INHERIT = "inherit"
INTENT_OVERRIDE = "override"
INTENT_PIN = "pin"


# ---------------------------------------------------------------------------
# 输入规整
# ---------------------------------------------------------------------------


def _typed(raw: Any) -> Any:
    """把 `{type,text,unit}` 转成 `TypedValue`；`None`/缺省 → `MISSING`。"""
    if raw is None:
        return values.MISSING
    if isinstance(raw, values.TypedValue):
        return raw
    if isinstance(raw, dict) and "type" in raw:
        try:
            return values.TypedValue.from_json(raw)
        except AppError:
            raise AppError(INVALID_INPUT, "三方合并收到形状不对的类型化值", payload=raw)
    raise AppError(INVALID_INPUT, "三方合并只接受类型化值或 null", payload=raw)


def read_entry(mapping: Optional[Dict[str, Any]], parameter_id: str) -> Dict[str, Any]:
    """读一侧的条目：`{present, value, intent, raw}`，不存在的键就是 present=False。"""
    source = mapping or {}
    raw = source.get(parameter_id)
    if raw is None:
        return {"present": False, "value": values.MISSING, "intent": None, "raw": None}
    if isinstance(raw, dict) and ("value" in raw or "intent" in raw):
        entry = {
            "present": True,
            "value": _typed(raw.get("value")),
            "intent": raw.get("intent") or INTENT_OVERRIDE,
            "raw": raw,
        }
        return entry
    # 直接给类型化值的写法（测试与内部调用更顺手）
    return {"present": True, "value": _typed(raw), "intent": INTENT_OVERRIDE, "raw": raw}


def _json(value: Any) -> Optional[Dict[str, Any]]:
    if value is values.MISSING:
        return None
    return value.to_json()


def _entry_json(entry: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    if not entry["present"]:
        return None
    return {"value": _json(entry["value"]), "intent": entry["intent"]}


def _definition(definitions: Optional[Dict[str, Any]], parameter_id: str) -> Optional[Dict[str, Any]]:
    if not definitions:
        return None
    item = definitions.get(parameter_id)
    return item if isinstance(item, dict) else None


def _retired(definition: Optional[Dict[str, Any]]) -> bool:
    return bool(definition and (definition.get("retired") or definition.get("state") == "retired"))


def _loc_key(definition: Optional[Dict[str, Any]]) -> str:
    """落点指纹：只用来判断"位置变了没有"，不参与值比较。"""
    if not definition:
        return ""
    loc = definition.get("xml_loc") or definition.get("loc")
    if loc is None:
        return ""
    if isinstance(loc, str):
        return loc
    if isinstance(loc, dict):
        steps = []
        for step in loc.get("path") or []:
            if isinstance(step, dict):
                steps.append("%s[%s]" % (step.get("name"), step.get("occurrence", 1)))
            else:
                steps.append(str(step))
        text = "/".join(steps)
        attribute = loc.get("attribute")
        return text + ("@" + str(attribute) if attribute else "")
    return str(loc)


def _constraints(definition: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    if not definition:
        return {}
    raw = definition.get("constraints")
    if isinstance(raw, dict):
        return raw
    # 定义集合的投影里约束是平铺的（min/max/enum）
    flat = {}
    for key in ("min", "max", "enum"):
        if definition.get(key) is not None:
            flat[key] = definition[key]
    return flat


# ---------------------------------------------------------------------------
# 判等与换算证据
# ---------------------------------------------------------------------------


def same_value(left: Any, right: Any) -> bool:
    """两个值是否同一物理量且同一数值（跨单位先换算）。"""
    if left is values.MISSING and right is values.MISSING:
        return True
    if left is values.MISSING or right is values.MISSING:
        return False
    return values.compare(left, right) == "equal"


def same_entry(left: Dict[str, Any], right: Dict[str, Any]) -> bool:
    """presence + 值 + 意图，三者全同才算"没改"。"""
    if left["present"] != right["present"]:
        return False
    if not left["present"]:
        return True
    if (left["intent"] or None) != (right["intent"] or None):
        return False
    return same_value(left["value"], right["value"])


def incomparable(left: Any, right: Any) -> bool:
    """两侧"有值、但没法比"：量纲不同或单位不在换算表内。"""
    if left is values.MISSING or right is values.MISSING:
        return False
    return values.compare(left, right) == "incomparable"


def conversion_evidence(left: Any, right: Any) -> Optional[Dict[str, Any]]:
    """两侧数值单位不同时，给出"换算过"的证据；换不了返回 None。

    `650ms` 与 `0.8s` 这样的比较必须先换算——不记证据的话，人看到的两个数
    在纸面上根本不一样，结论就没法复核。
    """
    if left is values.MISSING or right is values.MISSING:
        return None
    if left.kind not in ("int", "decimal") or right.kind not in ("int", "decimal"):
        return None
    if not left.unit or not right.unit or left.unit == right.unit:
        return None
    try:
        converted = values.convert(left, right.unit)
    except AppError:
        return None
    return {
        "kind": "unit_conversion",
        "from": left.to_json(),
        "to": right.to_json(),
        "converted": converted.to_json(),
        "note": "%s %s = %s %s" % (left.text, left.unit, converted.text, right.unit),
    }


def _convert_into(value: Any, definition: Optional[Dict[str, Any]]) -> Tuple[Any, Optional[str]]:
    """把值转换到目标定义的单位。返回 `(值, 失败码)`。"""
    if value is values.MISSING or not definition:
        return value, None
    target_unit = definition.get("unit")
    if not target_unit or value.unit in (None, target_unit):
        return value, None
    if value.kind not in ("int", "decimal"):
        return value, None
    try:
        return values.convert(value, str(target_unit)), None
    except AppError:
        return value, BLOCK_INCOMPARABLE


def _prepare(value: Any, definition: Optional[Dict[str, Any]]) -> Tuple[Any, Optional[str]]:
    """候选值落进目标定义前的两道关：**先换算单位，再按目标约束校验**。

    升级最常见的两种"看着能过、其实过不了"：旧值单位与新定义不同（`s` → `ms`），
    以及新版收紧了范围（旧版 0–1000，新版 0–500）。两者都必须在这里暴露，
    否则错误会推迟到生成目标文件时才炸。
    """
    if value is values.MISSING or not definition:
        return value, None
    converted, failure = _convert_into(value, definition)
    if failure:
        return converted, failure
    constraints = _constraints(definition)
    if not constraints:
        return converted, None
    try:
        converted.validate(constraints)
    except AppError:
        return converted, BLOCK_OUT_OF_RANGE
    return converted, None


# ---------------------------------------------------------------------------
# 主入口
# ---------------------------------------------------------------------------


def merge(
    *,
    base: Optional[Dict[str, Any]],
    ours: Optional[Dict[str, Any]],
    theirs: Optional[Dict[str, Any]],
    base_definitions: Optional[Dict[str, Any]] = None,
    ours_definitions: Optional[Dict[str, Any]] = None,
    theirs_definitions: Optional[Dict[str, Any]] = None,
    names: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    """三方合并。**纯函数**：不读仓储、不写盘、不发布。

    `base`/`ours`/`theirs` 是 `{parameter_id: 条目}`；条目可以是
    `{"value": {...}, "intent": "..."}`，也可以直接是类型化值。
    定义集合同理，键为 `parameter_id`。
    """
    ids: List[str] = []
    for side in (base, ours, theirs):
        for parameter_id in (side or {}):
            key = str(parameter_id)
            if key not in ids:
                ids.append(key)
    ids.sort()

    items: List[Dict[str, Any]] = []
    blockers: List[Dict[str, Any]] = []
    evidence: List[Dict[str, Any]] = []
    summary = {action: 0 for action in ACTIONS}
    # "定义集合里没有这个参数"与"调用方根本没传定义集合"是两件事：
    # 前者是拆分/合并的信号，后者只是没提供信息。判据不能靠空 dict 猜。
    definitions_given = any(
        side is not None for side in (base_definitions, ours_definitions, theirs_definitions)
    )

    for parameter_id in ids:
        item = _merge_one(
            parameter_id,
            base_entry=read_entry(base, parameter_id),
            ours_entry=read_entry(ours, parameter_id),
            theirs_entry=read_entry(theirs, parameter_id),
            ours_definition=_definition(ours_definitions, parameter_id),
            theirs_definition=_definition(theirs_definitions, parameter_id),
            base_definition=_definition(base_definitions, parameter_id),
            name=(names or {}).get(parameter_id) or parameter_id,
            definitions_given=definitions_given,
        )
        items.append(item)
        summary[item["action"]] = summary.get(item["action"], 0) + 1
        for blocker in item["blockers"]:
            blockers.append(blocker)
        for note in item["evidence"]:
            evidence.append(note)

    needs_decision = [item["parameter_id"] for item in items if item["action"] == "conflict"]
    manual = [item["parameter_id"] for item in items if item["action"] == "manual"]

    return {
        "items": items,
        "summary": summary,
        "total": len(items),
        "needs_decision": needs_decision,
        "manual": manual,
        "blockers": blockers,
        "evidence": evidence,
        "allowed_actions": list(DECISIONS),
        # 这个算法**不发布**：字段写死，免得调用方误以为拿到计划就能落库。
        "publishable": False,
        "can_apply": not blockers and not needs_decision,
    }


def _merge_one(
    parameter_id: str,
    *,
    base_entry: Dict[str, Any],
    ours_entry: Dict[str, Any],
    theirs_entry: Dict[str, Any],
    base_definition: Optional[Dict[str, Any]],
    ours_definition: Optional[Dict[str, Any]],
    theirs_definition: Optional[Dict[str, Any]],
    name: str,
    definitions_given: bool = False,
) -> Dict[str, Any]:
    evidence: List[Dict[str, Any]] = []
    blockers: List[Dict[str, Any]] = []

    def item(action: str, decision: Optional[str], candidate: Any, reasons: List[str], allowed=None):
        return {
            "parameter_id": parameter_id,
            "name": name,
            "action": action,
            "decision": decision,
            "allowed": list(allowed if allowed is not None else ()),
            "candidate": _json(candidate) if candidate is not None else None,
            "base": _entry_json(base_entry),
            "ours": _entry_json(ours_entry),
            "theirs": _entry_json(theirs_entry),
            "reasons": reasons,
            "blockers": blockers,
            "evidence": evidence,
            "needs_decision": action == "conflict",
        }

    def block(code: str, **detail):
        blockers.append(
            {
                "parameter_id": parameter_id,
                "code": code,
                "message": BLOCKER_MESSAGES.get(code, code),
                "detail": detail,
            }
        )

    # 1) 目标版本新增（三方里只有 N 有）：直接取新值，没有可争的东西
    if theirs_entry["present"] and not ours_entry["present"] and not base_entry["present"]:
        candidate, failure = _prepare(theirs_entry["value"], theirs_definition)
        if failure:
            block(failure)
            return item("manual", "manual", None, ["new_value_unusable"])
        return item("from_new", "use_new", candidate, ["new_in_target"])

    # 2) 本地有、目标没有
    if ours_entry["present"] and not theirs_entry["present"]:
        customized = ours_entry["intent"] in (INTENT_OVERRIDE, INTENT_PIN) and not same_entry(
            ours_entry, base_entry
        )
        if customized and theirs_definition is None:
            # 本地改过，目标版本的定义集合里却找不到这个 ID —— 拆分/合并的典型形状。
            # 旧值没有归属，绝不静默丢弃，也绝不硬塞给某个新 ID。
            block(BLOCK_UNKNOWN, intent=ours_entry["intent"], had_definition=ours_definition is not None)
            return item("manual", "manual", None, ["split_or_merge_suspected"])
        if customized and _retired(theirs_definition):
            block(BLOCK_RETIRED, intent=ours_entry["intent"])
            return item("manual", "manual", None, ["target_retired"])
        if theirs_definition is not None and _retired(theirs_definition):
            return item("drop", None, None, ["target_retired_unlocal"])
        # 没改过，上游也不要了：跟着丢（丢的不是本地定制，不算数据丢失）
        return item("drop", None, None, ["removed_upstream"])

    # 3) 本地有值、两侧定义集合里都没有这个参数：来源不明，不猜类型。
    #    只有"调用方确实给了定义集合"时才能下这个判断——没给定义不等于没有定义。
    if (
        ours_entry["present"]
        and definitions_given
        and ours_definition is None
        and theirs_definition is None
    ):
        block(BLOCK_MISSING_DEFINITION)
        return item("manual", "manual", None, ["definition_missing"])

    # 4) 目标有、本地没有（旧版本里就没有这个参数）：取新值
    if theirs_entry["present"] and not ours_entry["present"]:
        candidate, failure = _prepare(theirs_entry["value"], theirs_definition)
        if failure:
            block(failure)
            return item("manual", "manual", None, ["new_value_unusable"])
        return item("from_new", "use_new", candidate, ["not_present_locally"])

    # 5) 落点变了：值能对，写下去会写到别的位置——先拦住
    ours_loc = _loc_key(ours_definition)
    theirs_loc = _loc_key(theirs_definition)
    moved = bool(ours_loc and theirs_loc and ours_loc != theirs_loc)

    # 6) 换算证据（两个方向都记，人一眼能复核）
    for left, right, tag in (
        (ours_entry["value"], theirs_entry["value"], "ours_vs_theirs"),
        (base_entry["value"], theirs_entry["value"], "base_vs_theirs"),
    ):
        note = conversion_evidence(left, right)
        if note:
            evidence.append(dict(note, parameter_id=parameter_id, pair=tag))

    # 7) 判等：值 + 意图
    ours_changed = not same_entry(ours_entry, base_entry)
    theirs_changed = not same_entry(theirs_entry, base_entry)
    agree = same_entry(ours_entry, theirs_entry)

    if not ours_changed and not theirs_changed:
        return item("keep", None, theirs_entry["value"], ["all_three_equal"])

    if agree:
        # 两边改到同一个地方：结论一致，不需要人裁决
        candidate, failure = _prepare(theirs_entry["value"], theirs_definition)
        if failure:
            block(failure)
            return item("manual", "manual", None, ["agreed_value_unusable"])
        return item("keep", None, candidate, ["both_sides_agree"])

    if not ours_changed:
        # 本地没动，跟随上游
        if incomparable(ours_entry["value"], theirs_entry["value"]):
            # 单位量纲变了（`mm/s` → `rpm`）：数值一样也不代表意义一样，别替人决定
            block(
                BLOCK_INCOMPARABLE,
                ours=_json(ours_entry["value"]),
                theirs=_json(theirs_entry["value"]),
            )
            return item("manual", "manual", None, ["unit_dimension_changed"])
        if moved:
            block(BLOCK_LOCATION_MOVED, from_loc=ours_loc, to_loc=theirs_loc)
            return item("manual", "manual", None, ["location_moved"])
        candidate, failure = _prepare(theirs_entry["value"], theirs_definition)
        if failure:
            block(failure, from_unit=(theirs_entry["value"].unit if theirs_entry["value"] is not values.MISSING else None))
            return item("manual", "manual", None, ["unit_incompatible"])
        return item("from_new", "use_new", candidate, ["ours_unchanged"])

    if not theirs_changed:
        # 上游没动：保留本地（含 pin/override）
        if incomparable(ours_entry["value"], theirs_entry["value"]):
            block(
                BLOCK_INCOMPARABLE,
                ours=_json(ours_entry["value"]),
                theirs=_json(theirs_entry["value"]),
            )
            return item("manual", "manual", None, ["unit_dimension_changed"])
        if moved:
            block(BLOCK_LOCATION_MOVED, from_loc=ours_loc, to_loc=theirs_loc)
            return item("manual", "manual", None, ["location_moved"])
        candidate, failure = _prepare(ours_entry["value"], theirs_definition)
        if failure:
            block(failure)
            return item("manual", "manual", None, ["unit_incompatible"])
        return item("keep", None, candidate, ["theirs_unchanged"])

    # 8) 两边都改了且不一样：冲突
    if ours_entry["intent"] == INTENT_PIN:
        # pin 是"用户明确不许改"，升级必须让人确认它还要不要继续钉
        allowed = ("pin", "clear_pin", "use_new", "manual")
        return item("conflict", None, None, ["pin_needs_confirm"], allowed)

    if moved:
        block(BLOCK_LOCATION_MOVED, from_loc=ours_loc, to_loc=theirs_loc)
        return item("manual", "manual", None, ["location_moved"])

    # 冲突项也给一个"换算后的候选"，方便人比较；但不预选决议
    candidate, failure = _prepare(ours_entry["value"], theirs_definition)
    if failure:
        block(failure)
        return item("manual", "manual", None, ["unit_incompatible"])
    return item(
        "conflict",
        None,
        candidate,
        ["both_sides_changed"],
        DECISIONS,
    )


def draft_changes(result: Dict[str, Any], decisions: Optional[Dict[str, str]] = None) -> List[Dict[str, Any]]:
    """把计划 + 人的决议折成"逐项候选"，供上层拼变更单。

    只处理 `use_new` / `inherit_old` 两种明确决议；`manual` / `pin` / `clear_pin`
    涉及额外输入（新值、钉住原因），由上层补，这里不替它编。
    """
    picked = decisions or {}
    rows: List[Dict[str, Any]] = []
    for item in result.get("items") or []:
        decision = picked.get(item["parameter_id"]) or item.get("decision")
        if item["action"] == "manual":
            continue
        if decision == "use_new" and item.get("theirs"):
            rows.append(
                {
                    "parameter_id": item["parameter_id"],
                    "decision": "use_new",
                    "value": (item.get("theirs") or {}).get("value"),
                }
            )
        elif decision == "inherit_old" and item.get("ours"):
            rows.append(
                {
                    "parameter_id": item["parameter_id"],
                    "decision": "inherit_old",
                    "value": (item.get("ours") or {}).get("value"),
                }
            )
    return rows


def blocking_ids(result: Dict[str, Any]) -> List[str]:
    """需要人工处理的参数 ID（去重保序）。"""
    seen: List[str] = []
    for blocker in result.get("blockers") or []:
        parameter_id = blocker.get("parameter_id")
        if parameter_id and parameter_id not in seen:
            seen.append(parameter_id)
    return seen
