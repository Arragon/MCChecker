"""变更单状态机与批准发布（M6-11 / M6-12 / INH-1956、INH-1957）。

一张变更单就是"我要把这些参数从 A 改成 B"的**可审阅承诺**。它必须做到四件事：

1. **草稿可以反复改，提交之后就冻结**。提交那一刻把计划、逐项改动、理由一起
   固化进这一修订；之后再改只能重开一版（`previous_revision_id` 指回来）。
2. **状态机只有五条边**（draft→submitted→approved/rejected/withdrawn，
   外加 draft→withdrawn 与终态→新草稿）。非法回跳一律 `CHANGE_STATE_INVALID`，
   不靠前端自觉。
3. **`stale` 是计算标记，不是状态**。目标基线修订变了、定义集合换了，这张单
   依然在、历史依然可查，只是"不能直接批准了"——重开一版即可。
4. **批准即发布，没有中间态**。`approve` 在**同一个事务**里写新基线修订 +
   变更单决议，任何一步失败都不会留下"已批准但未发布"的悬空对象。

发布前重新校验六项：权限（在 server 层）、状态、目标 head、定义集合、模板哈希、
参数范围、未决冲突。这些校验全部在**批准时**再跑一遍——创建时的校验会过期，
批准这一下才是真正生效的那次。
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Dict, List, Optional

from ..core import config_values
from ..core.config_repository import ConfigRepository, now_iso
from ..core.errors import (
    CHANGE_STATE_INVALID,
    INVALID_INPUT,
    NOT_FOUND,
    PLAN_STALE,
    REVISION_CONFLICT,
    STORAGE_FAILURE,
    VALUE_OUT_OF_RANGE,
    AppError,
)
from . import config_baselines, config_catalog

# --- 变更单状态 ---------------------------------------------------------------

STATE_DRAFT = "draft"
STATE_SUBMITTED = "submitted"
STATE_APPROVED = "approved"
STATE_REJECTED = "rejected"
STATE_WITHDRAWN = "withdrawn"

STATES = (
    STATE_DRAFT,
    STATE_SUBMITTED,
    STATE_APPROVED,
    STATE_REJECTED,
    STATE_WITHDRAWN,
)

#: 只能从这些状态走出的边。`stale` 不是状态，所以不在这里。
ALLOWED_TRANSITIONS = {
    STATE_DRAFT: (STATE_SUBMITTED, STATE_WITHDRAWN),
    STATE_SUBMITTED: (STATE_APPROVED, STATE_REJECTED, STATE_WITHDRAWN),
    STATE_REJECTED: (STATE_DRAFT,),
    STATE_WITHDRAWN: (STATE_DRAFT,),
    STATE_APPROVED: (),
}

#: 终态：必须重开一版才能再动。
TERMINAL_STATES = frozenset((STATE_APPROVED, STATE_REJECTED, STATE_WITHDRAWN))

# --- 逐项决议 ----------------------------------------------------------------

DECISION_USE_NEW = "use_new"
DECISION_INHERIT_OLD = "inherit_old"
DECISION_MANUAL = "manual"
DECISION_PIN = "pin"
DECISION_CLEAR_PIN = "clear_pin"

DECISIONS = (
    DECISION_USE_NEW,
    DECISION_INHERIT_OLD,
    DECISION_MANUAL,
    DECISION_PIN,
    DECISION_CLEAR_PIN,
)

#: 只有 `use_new` / `manual` / `pin` 会真的写入新值；其余两种是"不动"。
_WRITES_VALUE = frozenset((DECISION_USE_NEW, DECISION_MANUAL, DECISION_PIN))

# --- 计划算法 ----------------------------------------------------------------

#: `three_way` / `model_upgrade` / `device_rebase` 随 M7 落地；
#: `reconcile` 把现场采集与批准基线的差异当计划输入；`promote`（M7-05）把机型层
#: 的本地决定当成计划输入。两者都只产出候选值，真正发布仍靠变更单批准。
ALGORITHM_RECONCILE = "reconcile"
ALGORITHMS = (
    ALGORITHM_RECONCILE,
    "three_way",
    "model_upgrade",
    "device_rebase",
    "promote",
)
SUPPORTED_ALGORITHMS = frozenset((ALGORITHM_RECONCILE, "promote"))


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


def _digest(payload: Any) -> str:
    """计划指纹：`sort_keys` + 紧凑分隔符，同输入必然同哈希。"""
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


# --- 读取 --------------------------------------------------------------------


def get_change_set(
    repo: ConfigRepository, change_id: str, revision: Optional[str] = None
) -> Dict[str, Any]:
    object_id = _text(change_id, "change_id")
    payload = repo.get("change_set", object_id, revision)
    if payload is None:
        raise AppError(NOT_FOUND, "变更单不存在", change_id=object_id)
    return payload


def list_change_sets(
    repo: ConfigRepository,
    *,
    profile_id: Optional[str] = None,
    baseline_id: Optional[str] = None,
    state: Optional[str] = None,
    include_common: bool = False,
) -> List[Dict[str, Any]]:
    """列变更单（按创建时间倒序，最新的在前面）。

    `include_common=True` 时把**通用层**变更（`profile_id` 为空）也算进来：
    通用基线不属于任何机型，但它一改就影响所有继承它的机型，所以在任一机型的
    变更列表里都该看得见。默认不含，是为了让服务层的按机型过滤保持字面语义。
    """
    wanted_state = _optional_text(state) or None
    if wanted_state and wanted_state not in STATES:
        raise AppError(
            INVALID_INPUT, "状态非法", field="state", allowed=list(STATES)
        )
    pid = _optional_text(profile_id) or None
    bid = _optional_text(baseline_id) or None
    items = []
    for payload in repo.list_objects("change_set"):
        if not isinstance(payload, dict):
            continue
        owner = str(payload.get("profile_id") or "")
        if pid and owner != pid and not (include_common and owner == ""):
            continue
        if bid and str(payload.get("baseline_id")) != bid:
            continue
        if wanted_state and payload.get("state") != wanted_state:
            continue
        items.append(payload)
    items.sort(key=lambda item: (str(item.get("created_at") or ""), str(item.get("id") or "")))
    items.reverse()
    return items


def staleness(repo: ConfigRepository, change_set: Dict[str, Any]) -> Dict[str, Any]:
    """`stale` 是**算出来的**：目标基线 head / 定义集合 / 计划指纹任一变化即为过期。

    只对**还能往下走**的单有意义。已批准/已驳回/已撤回的单不再"过期"——它已经
    结束，冻结的父修订号与当前 head 不同是必然的，那不是过期而是历史。
    """
    state = _optional_text(change_set.get("state")) or STATE_DRAFT
    base_id = _optional_text(change_set.get("baseline_id"))
    frozen_revision = _optional_text(change_set.get("baseline_revision"))
    head = repo.head_revision("baseline", base_id) if base_id else None

    if state in TERMINAL_STATES:
        return {
            "stale": False,
            "applicable": False,
            "reasons": [],
            "baseline_head": head,
            "baseline_revision": frozen_revision or None,
            "definition_set_id": _optional_text(change_set.get("definition_set_id")) or None,
        }

    reasons: List[str] = []
    if not base_id or head is None:
        reasons.append("baseline_missing")
    elif frozen_revision and head != frozen_revision:
        reasons.append("baseline_revision_changed")

    baseline = None
    if base_id and head:
        baseline = repo.get("baseline", base_id)
    frozen_set = _optional_text(change_set.get("definition_set_id"))
    if baseline is not None:
        current_set = _optional_text(baseline.get("definition_set_id"))
        if frozen_set != current_set:
            reasons.append("definition_set_changed")
        if baseline.get("state") == config_baselines.STATE_SUPERSEDED:
            reasons.append("baseline_superseded")

    return {
        "stale": bool(reasons),
        "applicable": True,
        "reasons": reasons,
        "baseline_head": head,
        "baseline_revision": frozen_revision or None,
        "definition_set_id": frozen_set or None,
    }


# --- 逐项改动规整 -------------------------------------------------------------


def _baseline_view(repo: ConfigRepository, baseline_id: str) -> Dict[str, Any]:
    payload = repo.get("baseline", baseline_id)
    if payload is None:
        raise AppError(NOT_FOUND, "基线不存在", baseline_id=baseline_id)
    return payload


def _normalize_changes(
    repo: ConfigRepository,
    raw: Any,
    *,
    baseline: Dict[str, Any],
) -> List[Dict[str, Any]]:
    """把请求里的 `changes` 规整成固定形状。

    `from` 一律以**服务端基线现值**为准，不信客户端传的 before——否则"改了
    什么"这件事就由提交方自己说了算，审阅的人看不到真差异。
    """
    if not isinstance(raw, list) or not raw:
        raise AppError(INVALID_INPUT, "changes 必须是非空数组", field="changes")
    values = dict(baseline.get("values") or {})
    definition_set_id = _optional_text(baseline.get("definition_set_id")) or None
    definitions = config_baselines.definitions_by_parameter(repo, definition_set_id)

    out: List[Dict[str, Any]] = []
    seen: Dict[str, int] = {}
    for index, item in enumerate(raw):
        if not isinstance(item, dict):
            raise AppError(
                INVALID_INPUT, "changes 的每一项必须是对象", field="changes", index=index
            )
        parameter_id = _text(item.get("parameter_id"), "parameter_id")
        if parameter_id in seen:
            raise AppError(
                INVALID_INPUT,
                "同一参数在变更单里出现了两次",
                field="changes",
                parameter_id=parameter_id,
                first_index=seen[parameter_id],
                index=index,
            )
        seen[parameter_id] = index
        config_catalog.get_parameter(repo, parameter_id)

        decision = _optional_text(item.get("decision")) or DECISION_USE_NEW
        if decision not in DECISIONS:
            raise AppError(
                INVALID_INPUT,
                "决议非法",
                field="decision",
                parameter_id=parameter_id,
                allowed=list(DECISIONS),
            )

        current = values.get(parameter_id) or {}
        before_value = current.get("value")
        before_intent = _optional_text(current.get("intent")) or None

        if decision == DECISION_INHERIT_OLD:
            # "保持上游/现值"：不写值，但必须说清保持的是哪个值
            if before_value is None:
                raise AppError(
                    INVALID_INPUT,
                    "选择「保持现值」但基线里本来就没有这个参数",
                    field="decision",
                    parameter_id=parameter_id,
                )
            after_value = before_value
            after_intent = before_intent
        elif decision == DECISION_CLEAR_PIN:
            if before_intent != config_baselines.INTENT_PIN:
                raise AppError(
                    INVALID_INPUT,
                    "只能取消已经 pin 的参数",
                    field="decision",
                    parameter_id=parameter_id,
                    intent=before_intent,
                )
            after_value = before_value
            after_intent = config_baselines.INTENT_OVERRIDE
        else:
            if "to" not in item:
                raise AppError(
                    INVALID_INPUT, "缺少 to", field="to", parameter_id=parameter_id
                )
            normalized = config_baselines.normalize_values(
                repo,
                {parameter_id: {"value": item.get("to"), "intent": config_baselines.INTENT_OVERRIDE}},
                definition_set_id=definition_set_id,
            )[parameter_id]
            after_value = normalized["value"]
            after_intent = (
                config_baselines.INTENT_PIN
                if decision == DECISION_PIN
                else config_baselines.INTENT_OVERRIDE
            )

        out.append(
            {
                "parameter_id": parameter_id,
                "from": before_value,
                "from_intent": before_intent,
                "to": after_value,
                "to_intent": after_intent,
                "decision": decision,
                "reason": _optional_text(item.get("reason")),
                "writes_value": decision in _WRITES_VALUE,
            }
        )
    return out


def _changed_rows(changes: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """真正产生差异的行：值变了或意图变了。决策不写值的不算。"""
    rows = []
    for item in changes:
        if not item.get("writes_value"):
            continue
        before, after = item.get("from"), item.get("to")
        same_value = _same_value(before, after)
        same_intent = item.get("from_intent") == item.get("to_intent")
        if same_value and same_intent:
            continue
        rows.append(item)
    return rows


def _same_value(left: Any, right: Any) -> bool:
    if left is None and right is None:
        return True
    if left is None or right is None:
        return False
    try:
        return (
            config_values.compare(
                config_values.TypedValue.from_json(left),
                config_values.TypedValue.from_json(right),
            )
            == "equal"
        )
    except AppError:
        return False


def _validate_ranges(
    repo: ConfigRepository, baseline: Dict[str, Any], changes: List[Dict[str, Any]]
) -> None:
    """按定义集合里的上下界/枚举逐项复核——**批准时**才是真正生效的那次校验。"""
    definition_set_id = _optional_text(baseline.get("definition_set_id")) or None
    definitions = config_baselines.definitions_by_parameter(repo, definition_set_id)
    for item in changes:
        if not item.get("writes_value"):
            continue
        definition = definitions.get(item["parameter_id"])
        if definition is None:
            continue
        constraints = definition.get("constraints") or {}
        if not constraints:
            continue
        try:
            value = config_values.TypedValue.from_json(item["to"])
            value.validate(constraints)
        except AppError as err:
            if err.code == VALUE_OUT_OF_RANGE:
                detail = dict(err.detail or {})
                detail.setdefault("parameter_id", item["parameter_id"])
                raise AppError(err.code, err.message, **detail)
            raise


def _plan_ref(
    *,
    algorithm: str,
    inputs: Dict[str, Any],
    changes: List[Dict[str, Any]],
) -> Dict[str, Any]:
    plan = {
        "algorithm": algorithm,
        "inputs": dict(inputs),
        "changes": [
            {
                "parameter_id": item["parameter_id"],
                "from": item.get("from"),
                "to": item.get("to"),
                "decision": item.get("decision"),
            }
            for item in changes
        ],
    }
    return {"algorithm": algorithm, "inputs": dict(inputs), "output_sha256": _digest(plan)}


def _require_transition(payload: Dict[str, Any], target: str) -> str:
    current = _optional_text(payload.get("state")) or STATE_DRAFT
    allowed = ALLOWED_TRANSITIONS.get(current, ())
    if target not in allowed:
        raise AppError(
            CHANGE_STATE_INVALID,
            "变更单当前是「%s」，不能变成「%s」" % (current, target),
            field="state",
            state=current,
            target=target,
            allowed=list(allowed),
        )
    return current


def _check_expected(repo: ConfigRepository, change_id: str, expected_revision: Any) -> str:
    head = repo.head_revision("change_set", change_id)
    wanted = _optional_text(expected_revision)
    if wanted and wanted != head:
        raise AppError(
            REVISION_CONFLICT,
            "变更单已被改动，请刷新后重试",
            expected_revision=wanted,
            actual_revision=head,
        )
    return str(head)


# --- 创建 / 修改 ---------------------------------------------------------------


def create_change_set(
    repo: ConfigRepository,
    actor: Any,
    *,
    baseline_id: Any,
    changes: Any,
    title: Any = "",
    reason: Any = "",
    logical_file_id: Any = None,
    observation_id: Any = None,
    algorithm: Any = ALGORITHM_RECONCILE,
    expected_baseline_revision: Any = None,
    idempotency_key: Optional[str] = None,
) -> Dict[str, Any]:
    """建草稿。草稿不冻结任何东西，可以反复改到满意为止。"""
    bid = _text(baseline_id, "baseline_id")
    baseline = _baseline_view(repo, bid)

    chosen = _optional_text(algorithm) or ALGORITHM_RECONCILE
    if chosen not in ALGORITHMS:
        raise AppError(
            INVALID_INPUT, "计划算法非法", field="algorithm", allowed=list(ALGORITHMS)
        )
    if chosen not in SUPPORTED_ALGORITHMS:
        # 明确拒绝而不是"先按 reconcile 算着"：合并算法的输入不同，糊过去等于骗人
        raise AppError(
            INVALID_INPUT,
            "该计划算法尚未实现（三方合并/升级/上提随 M7 落地）",
            field="algorithm",
            algorithm=chosen,
            supported=sorted(SUPPORTED_ALGORITHMS),
        )

    head = repo.head_revision("baseline", bid)
    wanted = _optional_text(expected_baseline_revision)
    if wanted and wanted != head:
        raise AppError(
            PLAN_STALE,
            "基线已发生变化，请重新对账后再建变更单",
            expected_baseline_revision=wanted,
            actual_revision=head,
        )

    normalized = _normalize_changes(repo, changes, baseline=baseline)
    _validate_ranges(repo, baseline, normalized)
    changed = _changed_rows(normalized)
    if not changed:
        raise AppError(
            INVALID_INPUT,
            "没有任何实际改动（值或意图都没变）",
            field="changes",
            changed_rows=0,
        )

    inputs = {
        "baseline_id": bid,
        "baseline_revision": head,
        "definition_set_id": _optional_text(baseline.get("definition_set_id")) or None,
        "logical_file_id": _optional_text(logical_file_id) or None,
        "observation_id": _optional_text(observation_id) or None,
    }
    plan = _plan_ref(algorithm=chosen, inputs=inputs, changes=normalized)
    stamp = now_iso()
    payload = {
        "profile_id": _optional_text(baseline.get("profile_id")) or None,
        "baseline_id": bid,
        "baseline_revision": head,
        "baseline_scope": baseline.get("scope"),
        "definition_set_id": inputs["definition_set_id"],
        "logical_file_id": inputs["logical_file_id"],
        "observation_id": inputs["observation_id"],
        "title": _optional_text(title),
        "reason": _optional_text(reason),
        "plan_ref": plan,
        "changes": normalized,
        "state": STATE_DRAFT,
        "frozen_at": None,
        "submitted_at": None,
        "submitted_by": None,
        "decided_at": None,
        "decided_by": None,
        "decide_note": None,
        "decided_state": None,
        "published_baseline_revision": None,
        "previous_revision_id": None,
        "created_by": _label(actor),
        "created_at": stamp,
        "updated_at": stamp,
        "updated_by": _label(actor),
    }
    tx = repo.transaction(
        actor,
        _optional_text(reason) or "新建变更单：%s" % (payload["title"] or bid),
        action="change.create",
        idempotency_key=idempotency_key,
    )
    ref = tx.put("change_set", payload)
    tx.commit()
    return get_change_set(repo, str(ref.id))


def update_change_set(
    repo: ConfigRepository,
    actor: Any,
    *,
    change_id: Any,
    changes: Any = None,
    title: Any = None,
    reason: Any = None,
    expected_revision: Any = None,
    idempotency_key: Optional[str] = None,
) -> Dict[str, Any]:
    """改草稿：追加修订，`previous_revision_id` 指向上一版草稿。"""
    cid = _text(change_id, "change_id")
    current = get_change_set(repo, cid)
    if current.get("state") != STATE_DRAFT:
        raise AppError(
            CHANGE_STATE_INVALID,
            "只有草稿能改（当前「%s」），要改请先重开一版" % current.get("state"),
            field="state",
            state=current.get("state"),
            allowed=list(ALLOWED_TRANSITIONS.get(current.get("state"), ())),
        )
    head = _check_expected(repo, cid, expected_revision)
    baseline = _baseline_view(repo, _text(current.get("baseline_id"), "baseline_id"))

    if changes is None:
        normalized = list(current.get("changes") or [])
    else:
        normalized = _normalize_changes(repo, changes, baseline=baseline)
        _validate_ranges(repo, baseline, normalized)
        if not _changed_rows(normalized):
            raise AppError(
                INVALID_INPUT, "没有任何实际改动（值或意图都没变）", field="changes"
            )

    inputs = dict((current.get("plan_ref") or {}).get("inputs") or {})
    plan = _plan_ref(
        algorithm=str((current.get("plan_ref") or {}).get("algorithm") or ALGORITHM_RECONCILE),
        inputs=inputs,
        changes=normalized,
    )
    stamp = now_iso()
    payload = dict(current)
    payload.pop("id", None)
    payload.pop("revision", None)
    payload.update(
        {
            "title": _optional_text(title) if title is not None else current.get("title") or "",
            "reason": _optional_text(reason) if reason is not None else current.get("reason") or "",
            "changes": normalized,
            "plan_ref": plan,
            "previous_revision_id": head,
            "updated_at": stamp,
            "updated_by": _label(actor),
        }
    )
    tx = repo.transaction(
        actor,
        "修改变更单草稿 %s" % cid,
        action="change.update",
        expected={"change_set:%s" % cid: head},
        idempotency_key=idempotency_key,
    )
    ref = tx.put("change_set", payload, object_id=cid)
    tx.commit()
    return get_change_set(repo, cid, ref.revision)


# --- 状态流转 ------------------------------------------------------------------


def submit_change_set(
    repo: ConfigRepository,
    actor: Any,
    *,
    change_id: Any,
    reason: Any = "",
    expected_revision: Any = None,
    idempotency_key: Optional[str] = None,
) -> Dict[str, Any]:
    """提交：把计划、逐项改动、理由一起**冻结**进这一修订。"""
    cid = _text(change_id, "change_id")
    current = get_change_set(repo, cid)
    _require_transition(current, STATE_SUBMITTED)
    head = _check_expected(repo, cid, expected_revision)

    story = _optional_text(reason) or _optional_text(current.get("reason"))
    if not story:
        raise AppError(INVALID_INPUT, "提交必须写明理由", field="reason")

    staleness_info = staleness(repo, current)
    if staleness_info["stale"]:
        raise AppError(
            PLAN_STALE,
            "目标基线已变化，这份计划已经过期，请重开一版",
            reasons=staleness_info["reasons"],
            baseline_head=staleness_info["baseline_head"],
            baseline_revision=staleness_info["baseline_revision"],
        )

    baseline = _baseline_view(repo, _text(current.get("baseline_id"), "baseline_id"))
    normalized = list(current.get("changes") or [])
    _validate_ranges(repo, baseline, normalized)
    if not _changed_rows(normalized):
        raise AppError(INVALID_INPUT, "没有任何实际改动，不能提交", field="changes")

    stamp = now_iso()
    payload = dict(current)
    payload.pop("id", None)
    payload.pop("revision", None)
    payload.update(
        {
            "state": STATE_SUBMITTED,
            "reason": story,
            "frozen_at": stamp,
            "submitted_at": stamp,
            "submitted_by": _label(actor),
            "previous_revision_id": head,
            "updated_at": stamp,
            "updated_by": _label(actor),
        }
    )
    tx = repo.transaction(
        actor,
        "提交变更单 %s" % cid,
        action="change.submit",
        expected={"change_set:%s" % cid: head},
        idempotency_key=idempotency_key,
    )
    ref = tx.put("change_set", payload, object_id=cid)
    tx.commit()
    return get_change_set(repo, cid, ref.revision)


def withdraw_change_set(
    repo: ConfigRepository,
    actor: Any,
    *,
    change_id: Any,
    reason: Any,
    expected_revision: Any = None,
    idempotency_key: Optional[str] = None,
) -> Dict[str, Any]:
    """撤回：终态。撤回不是删除，历史与理由都留着。"""
    cid = _text(change_id, "change_id")
    current = get_change_set(repo, cid)
    _require_transition(current, STATE_WITHDRAWN)
    head = _check_expected(repo, cid, expected_revision)
    note = _text(reason, "reason")
    stamp = now_iso()
    payload = dict(current)
    payload.pop("id", None)
    payload.pop("revision", None)
    payload.update(
        {
            "state": STATE_WITHDRAWN,
            "decided_state": STATE_WITHDRAWN,
            "decided_at": stamp,
            "decided_by": _label(actor),
            "decide_note": note,
            "previous_revision_id": head,
            "updated_at": stamp,
            "updated_by": _label(actor),
        }
    )
    tx = repo.transaction(
        actor,
        "撤回变更单 %s" % cid,
        action="change.withdraw",
        expected={"change_set:%s" % cid: head},
        idempotency_key=idempotency_key,
    )
    ref = tx.put("change_set", payload, object_id=cid)
    tx.commit()
    return get_change_set(repo, cid, ref.revision)


def reject_change_set(
    repo: ConfigRepository,
    actor: Any,
    *,
    change_id: Any,
    note: Any,
    expected_revision: Any = None,
    idempotency_key: Optional[str] = None,
) -> Dict[str, Any]:
    """驳回：要求写明意见——没有意见的驳回等于把问题丢回给提交人。"""
    cid = _text(change_id, "change_id")
    current = get_change_set(repo, cid)
    _require_transition(current, STATE_REJECTED)
    head = _check_expected(repo, cid, expected_revision)
    story = _text(note, "note")
    stamp = now_iso()
    payload = dict(current)
    payload.pop("id", None)
    payload.pop("revision", None)
    payload.update(
        {
            "state": STATE_REJECTED,
            "decided_state": STATE_REJECTED,
            "decided_at": stamp,
            "decided_by": _label(actor),
            "decide_note": story,
            "previous_revision_id": head,
            "updated_at": stamp,
            "updated_by": _label(actor),
        }
    )
    tx = repo.transaction(
        actor,
        "驳回变更单 %s" % cid,
        action="change.reject",
        expected={"change_set:%s" % cid: head},
        idempotency_key=idempotency_key,
    )
    ref = tx.put("change_set", payload, object_id=cid)
    tx.commit()
    return get_change_set(repo, cid, ref.revision)


def reopen_change_set(
    repo: ConfigRepository,
    actor: Any,
    *,
    change_id: Any,
    reason: Any = "",
    expected_revision: Any = None,
    idempotency_key: Optional[str] = None,
) -> Dict[str, Any]:
    """重开一版草稿：终态不改，**新建一个对象**。

    驳回/撤回/批准后要接着改，正确的做法是从那一版复制出一张新单，而不是把
    终态对象改回 draft——否则"谁在什么时候批准过什么"就再也说不清了。
    """
    cid = _text(change_id, "change_id")
    current = get_change_set(repo, cid)
    _require_transition(current, STATE_DRAFT)
    head = _check_expected(repo, cid, expected_revision)
    baseline = _baseline_view(repo, _text(current.get("baseline_id"), "baseline_id"))
    baseline_head = repo.head_revision("baseline", baseline["id"])

    normalized = list(current.get("changes") or [])
    inputs = dict((current.get("plan_ref") or {}).get("inputs") or {})
    inputs["baseline_revision"] = baseline_head
    inputs["definition_set_id"] = _optional_text(baseline.get("definition_set_id")) or None
    plan = _plan_ref(
        algorithm=str((current.get("plan_ref") or {}).get("algorithm") or ALGORITHM_RECONCILE),
        inputs=inputs,
        changes=normalized,
    )
    stamp = now_iso()
    payload = {
        "profile_id": current.get("profile_id"),
        "baseline_id": baseline["id"],
        "baseline_revision": baseline_head,
        "baseline_scope": baseline.get("scope"),
        "definition_set_id": inputs["definition_set_id"],
        "logical_file_id": current.get("logical_file_id"),
        "observation_id": current.get("observation_id"),
        "title": _optional_text(current.get("title")),
        "reason": _optional_text(reason) or _optional_text(current.get("reason")),
        "plan_ref": plan,
        "changes": normalized,
        "state": STATE_DRAFT,
        "frozen_at": None,
        "submitted_at": None,
        "submitted_by": None,
        "decided_at": None,
        "decided_by": None,
        "decide_note": None,
        "decided_state": None,
        "published_baseline_revision": None,
        "previous_revision_id": head,
        "reopened_from": cid,
        "created_by": _label(actor),
        "created_at": stamp,
        "updated_at": stamp,
        "updated_by": _label(actor),
    }
    tx = repo.transaction(
        actor,
        "重开变更单（源自 %s）" % cid,
        action="change.reopen",
        expected={"change_set:%s" % cid: head},
        idempotency_key=idempotency_key,
    )
    ref = tx.put("change_set", payload)
    tx.commit()
    return get_change_set(repo, str(ref.id))


# --- 批准并发布（M6-12） --------------------------------------------------------


def _merge_values(
    baseline: Dict[str, Any], changes: List[Dict[str, Any]], change_id: str
) -> Dict[str, Dict[str, Any]]:
    """把决议落到值上。非写入型决议（保持现值 / 取消 pin）只动意图，不动数值。"""
    merged = dict(baseline.get("values") or {})
    for item in changes:
        parameter_id = item["parameter_id"]
        previous = dict(merged.get(parameter_id) or {})
        if item.get("writes_value"):
            previous["value"] = item.get("to")
            previous["intent"] = item.get("to_intent")
            previous["source_ref"] = {"kind": "change_set", "id": change_id}
            previous["reason"] = item.get("reason") or ""
        elif item.get("decision") == DECISION_INHERIT_OLD:
            continue
        elif item.get("decision") == DECISION_CLEAR_PIN:
            previous["intent"] = config_baselines.INTENT_OVERRIDE
        merged[parameter_id] = previous
    return merged


def approve_change_set(
    repo: ConfigRepository,
    actor: Any,
    *,
    change_id: Any,
    note: Any = "",
    expected_revision: Any = None,
    expected_baseline_revision: Any = None,
    idempotency_key: Optional[str] = None,
) -> Dict[str, Any]:
    """批准 → **原子**发布新基线修订（同事务），没有 approved-but-unpublished 中间态。"""
    cid = _text(change_id, "change_id")
    current = get_change_set(repo, cid)
    _require_transition(current, STATE_APPROVED)
    head = _check_expected(repo, cid, expected_revision)

    base_id = _text(current.get("baseline_id"), "baseline_id")
    baseline = _baseline_view(repo, base_id)
    baseline_head = repo.head_revision("baseline", base_id)

    # 1) 目标 head：调用方看到的与现在的是不是同一条修订
    wanted = _optional_text(expected_baseline_revision) or _optional_text(
        current.get("baseline_revision")
    )
    if wanted and wanted != baseline_head:
        raise AppError(
            PLAN_STALE,
            "基线在提交之后又变了，这份变更单不能直接批准",
            expected_baseline_revision=wanted,
            actual_revision=baseline_head,
        )

    # 2) 定义集合 / 已作废基线
    info = staleness(repo, current)
    if info["stale"]:
        raise AppError(
            PLAN_STALE,
            "计划已过期，请重开一版后重新提交",
            reasons=info["reasons"],
            baseline_head=info["baseline_head"],
            baseline_revision=info["baseline_revision"],
        )

    # 3) 权限：唯一有 `config_change_approve` 的角色是管理员（见 authz 表），
    #    在 server 层强制。这里刻意**不**加"提交人不能批准自己"的硬拦——
    #    单管理员部署下那会把唯一可用路径堵死；两人的名字都记进载荷与审计，
    #    要追责时看得见，比装作拦住了更有用。
    approver = _label(actor)

    # 4) 参数范围/枚举复核 + 5) 未决冲突
    changes = list(current.get("changes") or [])
    if not changes:
        raise AppError(INVALID_INPUT, "变更单里没有改动，不能批准", field="changes")
    _validate_ranges(repo, baseline, changes)
    unresolved = [item["parameter_id"] for item in changes if item.get("unresolved")]
    if unresolved:
        raise AppError(
            INVALID_INPUT, "还有未决冲突，不能批准", field="changes", parameters=unresolved
        )

    merged = _merge_values(baseline, changes, cid)
    stamp = now_iso()
    note_text = _optional_text(note)

    # 6) 发布：新基线修订 + 变更单决议，**同一个事务**
    baseline_payload = dict(baseline)
    baseline_payload.pop("id", None)
    baseline_payload.pop("revision", None)
    baseline_payload.update(
        {
            "values": merged,
            "state": config_baselines.STATE_APPROVED,
            "previous_revision_id": baseline_head,
            "decided_at": stamp,
            "decided_by": approver,
            "decide_note": note_text or "由变更单 %s 批准发布" % cid,
            "updated_at": stamp,
            "updated_by": approver,
        }
    )

    change_payload = dict(current)
    change_payload.pop("id", None)
    change_payload.pop("revision", None)
    change_payload.update(
        {
            "state": STATE_APPROVED,
            "decided_state": STATE_APPROVED,
            "decided_at": stamp,
            "decided_by": approver,
            "decide_note": note_text,
            "published_baseline_id": base_id,
            "published_baseline_from_revision": baseline_head,
            "previous_revision_id": head,
            "updated_at": stamp,
            "updated_by": approver,
        }
    )
    for item in change_payload.get("changes") or []:
        item["change_set_id"] = cid

    tx = repo.transaction(
        actor,
        note_text or "批准变更单 %s 并发布基线 %s" % (cid, base_id),
        action="change.approve",
        expected={
            "change_set:%s" % cid: head,
            "baseline:%s" % base_id: baseline_head,
        },
        idempotency_key=idempotency_key,
    )
    # `Ref.revision` 要到 commit 时才回填，而载荷在 commit 时就已写盘——所以这里
    # 按仓储的编号规则**预先算出**新修订号。这不是猜测：`expected` 已经把基线 head
    # CAS 死在 `baseline_head` 上，下一个修订号只可能是 `n+1`。
    next_revision = "r%d" % (int(str(baseline_head).lstrip("r")) + 1)
    change_payload["published_baseline_revision"] = next_revision
    tx.put("baseline", baseline_payload, object_id=base_id)
    tx.put("change_set", change_payload, object_id=cid)
    tx.commit()

    published = repo.get("baseline", base_id)
    if published is None or published.get("revision") != next_revision:
        # 理论上到不了这里；真到了说明仓储的编号规则变了，必须让人看见
        raise AppError(
            STORAGE_FAILURE,
            "批准后基线修订号与预期不一致",
            expected_revision=next_revision,
            actual_revision=(published or {}).get("revision"),
        )

    return {
        "change_set": get_change_set(repo, cid),
        "baseline": published,
    }


# --- 详情 --------------------------------------------------------------------


def detail(repo: ConfigRepository, change_id: str) -> Dict[str, Any]:
    """一张单 + 逐项改动展开 + 过期判定 + 修订链（前端一次拿全）。"""
    change_set = get_change_set(repo, change_id)
    parameters = {item["id"]: item for item in config_catalog.list_parameters(repo)}
    rows = []
    for item in change_set.get("changes") or []:
        parameter = parameters.get(item["parameter_id"]) or {}
        rows.append(
            {
                "parameter_id": item["parameter_id"],
                "name": parameter.get("name") or "",
                "category": parameter.get("category") or "",
                "from": item.get("from"),
                "to": item.get("to"),
                "from_intent": item.get("from_intent"),
                "to_intent": item.get("to_intent"),
                "decision": item.get("decision"),
                "reason": item.get("reason"),
                "writes_value": bool(item.get("writes_value")),
                "known": bool(parameter),
            }
        )
    return {
        "item": change_set,
        "rows": rows,
        "changed_rows": len(_changed_rows(change_set.get("changes") or [])),
        "staleness": staleness(repo, change_set),
        "allowed": list(ALLOWED_TRANSITIONS.get(change_set.get("state"), ())),
        "revisions": repo.history("change_set", change_id),
    }
