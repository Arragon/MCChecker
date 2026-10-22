"""现场经验上提（M7-05 / INH-1964）。

把机型层验证过的值，经**审批**提到通用层；把设备层（单机标定）的经验提到机型层。

四条规矩：

1. **不自动**：一台机型的补偿值出现多少次都不会自己变成通用默认。上提永远是一次
   显式动作，产出的是**变更单**，由管理员批准后才发布新通用修订。
2. **不猜来源**：只上提"有本地决策证据"的值（`manual` / `change_set` / `promote`）。
   现场采集（`observation`）、单机标定（`device` / `calibration`）、出口默认
   （`snapshot`）一律排除——它们不是"经验"，是"某一次观测"或"厂商默认"。
   来源标不通（缺 `source_ref`，或标着 `baseline` 却是 override）算未知来源，也排除。
3. **不改写下级**：批准后只新增一条通用修订；已经引用旧上级的机型基线一个字不动，
   要跟进必须显式 rebase（M7-03）。
4. **证据撤销即过期**：来源对象被删、或源基线与目标不在同一版本，计划直接阻断。

设备层（M7-07/08）当前**功能关闭**：`promote_to_model` 会明确报
`DEVICE_LAYER_DISABLED`，而不是假装成功。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from ..core.config_repository import ConfigRepository
from ..core.errors import INVALID_INPUT, NOT_FOUND, PLAN_STALE, AppError
from . import config_baselines as baselines
from . import config_catalog, config_changes, config_device_layer

MODE_TO_COMMON = "promote_to_common"
MODE_TO_MODEL = "promote_to_model"

#: 能证明"这是本地做出的决定"的来源。只有这些可以上提。
LOCAL_SOURCE_KINDS = ("manual", "change_set", "promote")

#: 明确排除的来源 → 理由码。
EXCLUDED_SOURCE_KINDS = {
    "observation": "observation_sourced",
    "device": "device_calibration",
    "calibration": "device_calibration",
    "snapshot": "factory_default",
}

EXCLUDE_REASONS = {
    "not_local": "本地没有改过（意图是 inherit），本来就跟着上游走",
    "observation_sourced": "值来自一次现场采集，属于单次观测而不是经验",
    "device_calibration": "值来自单台设备的标定，不能进机型/通用层",
    "factory_default": "值就是出口默认，没有上提的意义",
    "unknown_source": "来源无法确认（缺 source_ref 或标注与意图矛盾）",
    "already_same": "与目标基线当前值相同，无需上提",
}

#: 设备层关闭时的错误码。取值只在 `config_device_layer` 一处定，这里只是别名，
#: 免得调用方为了一个字符串再 import 一层。
DEVICE_LAYER_DISABLED = config_device_layer.DEVICE_LAYER_DISABLED


def _text(value: Any, field: str) -> str:
    text = str(value if value is not None else "").strip()
    if not text:
        raise AppError(INVALID_INPUT, "%s 不能为空" % field, field=field)
    return text


def _optional_text(value: Any) -> str:
    return str(value if value is not None else "").strip()


def _label(actor: Any) -> str:
    if actor is None:
        return ""
    if isinstance(actor, dict):
        return str(actor.get("name") or actor.get("ip") or "")
    return str(getattr(actor, "name", "") or getattr(actor, "ip", "") or "")


def _names(repo: ConfigRepository, parameter_ids: List[str]) -> Dict[str, str]:
    names: Dict[str, str] = {}
    for parameter_id in parameter_ids:
        try:
            names[parameter_id] = str(
                config_catalog.get_parameter(repo, parameter_id).get("name") or parameter_id
            )
        except AppError:
            names[parameter_id] = parameter_id
    return names


def _source_alive(repo: ConfigRepository, source_ref: Dict[str, Any]) -> bool:
    """来源对象还在不在。`manual` 没有对象，永远算在。"""
    kind = _optional_text(source_ref.get("kind"))
    object_id = _optional_text(source_ref.get("id"))
    if not object_id:
        return True
    mapping = {
        "change_set": "change_set",
        "observation": "observation",
        "baseline": "baseline",
        "snapshot": "file_snapshot",
        "device": "device",
    }
    store_kind = mapping.get(kind)
    if not store_kind:
        # 不认识的来源种类：不能证明它还在，按"撤销"处理
        return False
    return repo.get(store_kind, object_id) is not None


def _classify(entry: Dict[str, Any], target_entry: Optional[Dict[str, Any]]) -> Tuple[bool, str]:
    """一条值能不能上提：`(eligible, reason)`。"""
    intent = _optional_text(entry.get("intent"))
    source_ref = entry.get("source_ref") or {}
    kind = _optional_text(source_ref.get("kind"))

    if intent == baselines.INTENT_INHERIT:
        return False, "not_local"
    if not kind:
        return False, "unknown_source"
    if kind in EXCLUDED_SOURCE_KINDS:
        return False, EXCLUDED_SOURCE_KINDS[kind]
    if kind not in LOCAL_SOURCE_KINDS:
        # `baseline` 却标成 override：标注与意图矛盾，不能当作本地经验
        return False, "unknown_source"

    current = (target_entry or {}).get("value")
    mine = entry.get("value")
    if current is not None and mine is not None:
        if _same(current, mine):
            return False, "already_same"
    return True, ""


def _same(left: Dict[str, Any], right: Dict[str, Any]) -> bool:
    from ..core import config_values

    try:
        a = config_values.TypedValue.from_json(left)
        b = config_values.TypedValue.from_json(right)
    except AppError:
        return False
    return config_values.compare(a, b) == "equal"


def _device_baselines(repo: ConfigRepository) -> List[Dict[str, Any]]:
    return [
        item
        for item in repo.list_objects("baseline")
        if str(item.get("scope")) == baselines.SCOPE_DEVICE
    ]


def plan_promote_to_common(
    repo: ConfigRepository,
    *,
    profile_id: Any,
    parameter_ids: Any = None,
    source_baseline_id: Any = None,
) -> Dict[str, Any]:
    """机型 → 通用：把机型基线上的本地决定，做成一条待批准的变更单计划。"""
    pid = _text(profile_id, "profile_id")
    model: Optional[Dict[str, Any]] = None
    wanted = _optional_text(source_baseline_id)
    if wanted:
        model = baselines.get_baseline(repo, wanted)
    else:
        items = [
            item
            for item in baselines.list_baselines(
                repo, scope=baselines.SCOPE_MODEL, profile_id=pid
            )
            if item.get("state") != baselines.STATE_SUPERSEDED
        ]
        model = items[-1] if items else None
    if model is None:
        raise AppError(
            INVALID_INPUT,
            "该机型还没有机型基线，没有可上提的本地经验",
            field="profile_id",
            profile_id=pid,
        )
    if str(model.get("profile_id") or "") != pid:
        raise AppError(NOT_FOUND, "机型基线不属于该机型", baseline_id=model.get("id"))
    if model.get("state") != baselines.STATE_APPROVED:
        raise AppError(
            INVALID_INPUT,
            "只有已批准的机型基线才能上提（当前 %s）" % model.get("state"),
            field="baseline_id",
            state=model.get("state"),
        )

    parent_id = _optional_text(model.get("upstream_ref"))
    if not parent_id:
        raise AppError(
            INVALID_INPUT,
            "机型基线没有记录上游通用基线，无法确定上提目标",
            field="upstream_ref",
            baseline_id=model.get("id"),
        )
    target = baselines.get_baseline(repo, parent_id)
    if target.get("state") != baselines.STATE_APPROVED:
        raise AppError(
            INVALID_INPUT,
            "上游通用基线不是已批准状态，不能作为上提目标",
            field="baseline_id",
            baseline_id=parent_id,
            state=target.get("state"),
        )

    blockers: List[Dict[str, Any]] = []
    if _optional_text(model.get("release_id")) != _optional_text(target.get("release_id")):
        blockers.append(
            {
                "code": "release_mismatch",
                "message": "机型基线与上游通用基线不在同一版本，先完成机型升版再上提",
                "model_release_id": model.get("release_id"),
                "common_release_id": target.get("release_id"),
            }
        )

    wanted_ids = None
    if parameter_ids is not None:
        if not isinstance(parameter_ids, (list, tuple)) or not parameter_ids:
            raise AppError(
                INVALID_INPUT, "parameter_ids 必须是非空数组", field="parameter_ids"
            )
        wanted_ids = [str(item) for item in parameter_ids]

    model_values = dict(model.get("values") or {})
    target_values = dict(target.get("values") or {})
    candidates = wanted_ids if wanted_ids is not None else sorted(model_values)
    names = _names(repo, candidates)

    rows: List[Dict[str, Any]] = []
    eligible: List[str] = []
    excluded: List[Dict[str, str]] = []
    for parameter_id in candidates:
        entry = model_values.get(parameter_id)
        if entry is None:
            excluded.append({"parameter_id": parameter_id, "reason": "unknown_source"})
            continue
        source_ref = entry.get("source_ref") or {}
        if not _source_alive(repo, source_ref):
            blockers.append(
                {
                    "code": "evidence_revoked",
                    "message": "这条值的来源对象已经不存在，证据无法复核",
                    "parameter_id": parameter_id,
                    "source_ref": source_ref,
                }
            )
        ok, reason = _classify(entry, target_values.get(parameter_id))
        row = {
            "parameter_id": parameter_id,
            "name": names.get(parameter_id, parameter_id),
            "eligible": ok,
            "excluded_reason": reason,
            "excluded_message": EXCLUDE_REASONS.get(reason, ""),
            "from": (target_values.get(parameter_id) or {}).get("value"),
            "to": entry.get("value"),
            "intent": entry.get("intent"),
            "source_ref": source_ref or None,
            "decision": "use_new" if ok else None,
        }
        rows.append(row)
        if ok:
            eligible.append(parameter_id)
        else:
            excluded.append({"parameter_id": parameter_id, "reason": reason})

    if not eligible and not blockers and not excluded:
        blockers.append(
            {"code": "nothing_to_promote", "message": "上没有可上提的参数"}
        )

    return {
        "mode": MODE_TO_COMMON,
        "profile_id": pid,
        "source": {
            "baseline_id": model.get("id"),
            "revision": model.get("revision"),
            "scope": model.get("scope"),
            "release_id": model.get("release_id"),
        },
        "target": {
            "baseline_id": target.get("id"),
            "revision": target.get("revision"),
            "scope": target.get("scope"),
            "release_id": target.get("release_id"),
            "definition_set_id": target.get("definition_set_id"),
        },
        "items": rows,
        "eligible": eligible,
        "excluded": excluded,
        "blockers": blockers,
        "warnings": []
        if eligible
        else ["没有可上提的参数：先确认机型层是否真的改过值（意图为 override/pin）"],
        "can_apply": not blockers and bool(eligible),
        "publishable": False,
        "algorithm": "promote",
    }


def create_promote_to_common(
    repo: ConfigRepository,
    actor: Any,
    *,
    profile_id: Any,
    parameter_ids: Any = None,
    source_baseline_id: Any = None,
    title: Any = "",
    reason: Any = "",
    submit: bool = True,
    expected: Optional[Dict[str, Any]] = None,
    idempotency_key: Optional[str] = None,
) -> Dict[str, Any]:
    """把上提计划冻成一条变更单（默认同时提交，等管理员批准）。"""
    plan = plan_promote_to_common(
        repo,
        profile_id=profile_id,
        parameter_ids=parameter_ids,
        source_baseline_id=source_baseline_id,
    )
    _assert_fresh(plan, expected)
    if plan["blockers"]:
        raise AppError(
            PLAN_STALE,
            "上提计划有阻断项，不能建变更单",
            reasons=sorted({str(item["code"]) for item in plan["blockers"]}),
            blockers=plan["blockers"],
        )
    if not plan["eligible"]:
        raise AppError(
            INVALID_INPUT,
            "没有可上提的参数",
            field="parameter_ids",
            excluded=plan["excluded"],
        )

    changes = []
    for row in plan["items"]:
        if not row["eligible"]:
            continue
        changes.append(
            {
                "parameter_id": row["parameter_id"],
                "decision": "use_new",
                "to": row["to"],
                "reason": _optional_text(reason) or "现场经验上提到通用层",
            }
        )
    change_set = config_changes.create_change_set(
        repo,
        actor,
        baseline_id=plan["target"]["baseline_id"],
        changes=changes,
        title=_optional_text(title) or "机型 %s 经验上提" % plan["profile_id"],
        reason=_optional_text(reason) or "把机型层验证过的值提到通用层",
        algorithm="promote",
        expected_baseline_revision=plan["target"]["revision"],
        idempotency_key=idempotency_key,
    )
    if submit and change_set.get("state") == config_changes.STATE_DRAFT:
        # 幂等重放时拿回来的是同一条已提交变更单，不能再"提交"一次
        change_set = config_changes.submit_change_set(
            repo, actor, change_id=change_set["id"], reason="上提待批准"
        )
    return {
        "item": change_set,
        "plan": plan,
        "submitted": change_set.get("state") == config_changes.STATE_SUBMITTED,
    }


def plan_promote_to_model(
    repo: ConfigRepository, *, profile_id: Any, device_id: Any = None
) -> Dict[str, Any]:
    """设备 → 机型。**设备层当前功能关闭**（M7-07/08 延期）。"""
    _text(profile_id, "profile_id")
    devices = _device_baselines(repo)
    # 开关只在 `config_device_layer` 一处：接口、状态页、这里读的是同一个值，
    # 免得"某个入口开了、另一个还关着"这种自相矛盾的状态。
    config_device_layer.require_enabled(
        field="device_id",
        device_id=_optional_text(device_id) or None,
        device_baselines=len(devices),
    )
    raise AppError(
        INVALID_INPUT,
        "设备层已启用但设备上提尚未接入本版本",
        field="device_id",
        device_id=_optional_text(device_id) or None,
    )


def _assert_fresh(plan: Dict[str, Any], expected: Optional[Dict[str, Any]]) -> None:
    wanted = {
        key: _optional_text(value)
        for key, value in (expected or {}).items()
        if _optional_text(value)
    }
    if not wanted:
        return
    actual = {
        "source_revision": _optional_text(plan["source"].get("revision")),
        "target_revision": _optional_text(plan["target"].get("revision")),
        "source_baseline_id": _optional_text(plan["source"].get("baseline_id")),
        "target_baseline_id": _optional_text(plan["target"].get("baseline_id")),
    }
    drift = {
        key: {"expected": value, "actual": actual.get(key)}
        for key, value in wanted.items()
        if actual.get(key) != value
    }
    if drift:
        raise AppError(
            PLAN_STALE,
            "上提计划已过期：源或目标基线发生了变化",
            reasons=sorted(drift),
            drift=drift,
        )
