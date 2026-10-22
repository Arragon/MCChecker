"""跨版本升级（M7-02 / M7-03）。

升级不是"把新版本的默认值抄一遍"：它要在三份数据之间做一次受控合并。

| 代号 | 含义 | 通用升级 | 机型升级 |
|---|---|---|---|
| O | 旧共同基准 | 旧版本默认出口批次解析出的值 | 该机型**实际引用**的那条通用基线 |
| L | 旧有效本地值 | 旧版本已批准通用基线 | 旧机型基线 |
| N | 新上游 | 新版本默认出口批次 | 新版本已批准通用基线 |

三条硬约束：

1. **不随机选最新**：新版本有多个模板变体时必须点名 `variant_id`，否则 400 并列出候选。
   跨版本靠 `family_id` 认"同一个模板"，不靠"最新那个变体"。
2. **父修订要钉死**：机型基线的共同基准取它当初引用的 `upstream_ref` +
   `upstream_revision`，不是当前 head。少了这一条，通用层一发 r5，旧机型的"共同基准"
   就悄悄变成 r5，合并结果会退化成"什么都没变"——这正是"机型头漂移"的成因。
3. **不自动传播**：通用层升版不碰任何机型基线。机型要显式调这里才产生新对象。

升级产物一律是**草稿**：本模块只出计划、建草稿，发布仍走变更单（M6-11/12）。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from ..core import config_merge
from ..core.config_repository import ConfigRepository
from ..core.errors import INVALID_INPUT, NOT_FOUND, PLAN_STALE, AppError
from . import config_baselines as baselines
from . import config_catalog

MODE_COMMON = "common_release_upgrade"
MODE_MODEL = "model_release_upgrade"
MODE_REBASE = "model_rebase"

#: 决议里允许"我确认丢掉这个参数"——唯一能越过阻断项的办法，且必须显式写。
DECISION_DROP = "drop"

_RESOLVED = ("use_new", "inherit_old", "pin", "clear_pin", DECISION_DROP)


# ---------------------------------------------------------------------------
# 小工具
# ---------------------------------------------------------------------------


def _text(value: Any, field: str) -> str:
    text = str(value if value is not None else "").strip()
    if not text:
        raise AppError(INVALID_INPUT, "%s 不能为空" % field, field=field)
    return text


def _optional_text(value: Any) -> str:
    return str(value if value is not None else "").strip()


def _project(definition: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """定义修订 → 合并算法要的形状（把 catalog 的内部结构挡在 core 之外）。"""
    if not definition:
        return None
    xml = definition.get("xml") or {}
    return {
        "type": definition.get("type"),
        "unit": definition.get("unit") or None,
        "constraints": definition.get("constraints") or {},
        "xml_loc": xml.get("loc"),
        "retired": str(definition.get("status") or "") == "retired",
    }


def _definitions(repo: ConfigRepository, definition_set_id: Optional[str]) -> Dict[str, Dict[str, Any]]:
    raw = baselines.definitions_by_parameter(repo, definition_set_id)
    return {str(pid): _project(item) for pid, item in raw.items() if item}


def _names(repo: ConfigRepository, parameter_ids: List[str]) -> Dict[str, str]:
    names: Dict[str, str] = {}
    for parameter_id in parameter_ids:
        try:
            names[parameter_id] = str(config_catalog.get_parameter(repo, parameter_id).get("name") or parameter_id)
        except AppError:
            names[parameter_id] = parameter_id
    return names


def _snapshot_hashes(repo: ConfigRepository, batch: Optional[Dict[str, Any]]) -> List[Dict[str, str]]:
    if not batch:
        return []
    return [
        {
            "snapshot_id": str(snapshot.get("id")),
            "logical_file_id": _optional_text(snapshot.get("logical_file_id")),
            "sha256": _optional_text(snapshot.get("blob_sha256")),
        }
        for snapshot in config_catalog.batch_snapshots(repo, batch)
    ]


def _resolve_variant(repo: ConfigRepository, release: Dict[str, Any], variant_id: Any) -> Dict[str, Any]:
    """新版本的目标变体。多个候选时**必须点名**，绝不"取最新的那个"。"""
    variants = config_catalog.list_variants(repo, release_id=str(release["id"]))
    if not variants:
        raise AppError(
            INVALID_INPUT,
            "该版本还没有模板变体，无法确定升级目标",
            field="release_id",
            release_id=release.get("id"),
        )
    wanted = _optional_text(variant_id) or None
    if wanted:
        chosen = config_catalog.get_variant(repo, wanted)
        if str(chosen.get("release_id")) != str(release["id"]):
            raise AppError(
                INVALID_INPUT,
                "模板变体不属于目标版本",
                field="variant_id",
                variant_id=wanted,
                release_id=release.get("id"),
            )
        return chosen
    if len(variants) == 1:
        return variants[0]
    raise AppError(
        INVALID_INPUT,
        "该版本有 %d 个模板变体，必须点名 variant_id" % len(variants),
        field="variant_id",
        candidates=[
            {
                "variant_id": str(item.get("id")),
                "family_id": _optional_text(item.get("family_id")),
                "template_snapshot_id": _optional_text(item.get("template_snapshot_id")),
            }
            for item in variants
        ],
    )


def _variant_of_family(
    repo: ConfigRepository, release_id: str, family_id: str
) -> Optional[Dict[str, Any]]:
    """同一模板（`family_id`）在另一个版本里的变体。没有就返回 None。"""
    if not family_id:
        return None
    matches = [
        item
        for item in config_catalog.list_variants(repo, release_id=release_id)
        if str(item.get("family_id")) == str(family_id)
    ]
    if len(matches) > 1:
        raise AppError(
            INVALID_INPUT,
            "同一版本里出现了多个同族模板变体，无法自动确定父版本",
            family_id=family_id,
            release_id=release_id,
            variants=[str(item.get("id")) for item in matches],
        )
    return matches[0] if matches else None


def _approved_common(
    repo: ConfigRepository, release_id: str, family_id: str
) -> Optional[Dict[str, Any]]:
    """某版本某族的**已批准**通用基线（取最新一条）。"""
    variant = _variant_of_family(repo, release_id, family_id)
    if variant is None:
        return None
    items = [
        item
        for item in baselines.list_baselines(
            repo, scope=baselines.SCOPE_COMMON, release_id=release_id, state=baselines.STATE_APPROVED
        )
        if str(item.get("variant_id") or "") == str(variant["id"])
    ]
    return items[-1] if items else None


def _previous_release(repo: ConfigRepository, release: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """按版本号找上一个版本（跳过已退役）。"""
    current = config_catalog.version_key(str(release.get("version_label") or ""))
    candidates = []
    for item in config_catalog.list_releases(repo):
        if str(item.get("id")) == str(release.get("id")):
            continue
        if str(item.get("status") or "") == "retired":
            continue
        key = config_catalog.version_key(str(item.get("version_label") or ""))
        if key < current:
            candidates.append((key, str(item.get("id") or ""), item))
    if not candidates:
        return None
    candidates.sort(key=lambda row: (row[0], row[1]))
    return candidates[-1][2]


def _current_model_baseline(repo: ConfigRepository, profile_id: str) -> Optional[Dict[str, Any]]:
    items = [
        item
        for item in baselines.list_baselines(repo, scope=baselines.SCOPE_MODEL, profile_id=profile_id)
        if item.get("state") != baselines.STATE_SUPERSEDED
    ]
    return items[-1] if items else None


def _values_of(baseline: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    return dict((baseline or {}).get("values") or {})


def _fresh_items(
    theirs: Dict[str, Any], names: Dict[str, str], reason: str
) -> List[Dict[str, Any]]:
    """没有共同基准时的逐项列表：全部是"取新值"，但**不许**说是三方继承。"""
    items = []
    for parameter_id in sorted(theirs):
        item = theirs[parameter_id]
        items.append(
            {
                "parameter_id": parameter_id,
                "name": names.get(parameter_id, parameter_id),
                "action": "from_new",
                "decision": "use_new",
                "allowed": (),
                "candidate": (item.get("value") or None),
                "base": None,
                "ours": None,
                "theirs": {"value": item.get("value"), "intent": item.get("intent")},
                "reasons": [reason],
                "blockers": [],
                "evidence": [],
                "needs_decision": False,
            }
        )
    return items


# ---------------------------------------------------------------------------
# 计划 → 决议
# ---------------------------------------------------------------------------


def _resolve_decisions(
    items: List[Dict[str, Any]], decisions: Optional[Dict[str, Any]]
) -> Tuple[Dict[str, Dict[str, Any]], List[str], List[str]]:
    """把计划 + 人的决议翻成"要写进新基线的显式值"。

    返回 `(explicit_values, pending, dropped)`：

    * `use_new` / `clear_pin` 不需要显式值——新基线本来就按新出口播种；
    * `inherit_old` / `pin` 要把本地值显式写进去（否则会被播种覆盖）；
    * `manual` 项必须给 `drop` 才放行，否则留在 `pending` 里。
    """
    picked = decisions or {}
    explicit: Dict[str, Dict[str, Any]] = {}
    pending: List[str] = []
    dropped: List[str] = []

    for item in items:
        parameter_id = item["parameter_id"]
        decision = _optional_text(picked.get(parameter_id)) or item.get("decision")
        if decision == DECISION_DROP:
            dropped.append(parameter_id)
            continue
        if item["action"] == "manual":
            pending.append(parameter_id)
            continue
        if item["action"] == "conflict" and not decision:
            pending.append(parameter_id)
            continue
        if decision not in _RESOLVED:
            if item["action"] == "conflict":
                pending.append(parameter_id)
            continue
        if decision == "inherit_old" or decision == "pin":
            ours = (item.get("ours") or {}).get("value")
            if not ours:
                pending.append(parameter_id)
                continue
            explicit[parameter_id] = {
                "value": ours,
                "intent": "pin" if decision == "pin" else "override",
                "reason": _optional_text(picked.get(parameter_id + ":reason")) or "跨版本升级保留本地值",
            }
    return explicit, pending, dropped


def _assert_fresh(plan: Dict[str, Any], expected: Optional[Dict[str, Any]]) -> None:
    """计划 → 落地之间的并发守卫。

    计划是给人看几分钟的；这几分钟里如果有人又建了一条机型基线、或者通用层又出了
    新修订，拿着旧计划落库就是"按我看到的那个世界写"。与其悄悄写错，不如 409。
    """
    wanted = {key: _optional_text(value) for key, value in (expected or {}).items() if _optional_text(value)}
    if not wanted:
        return
    previous = plan.get("previous") or {}
    actual = {
        "model_revision": _optional_text(previous.get("model_revision")),
        "model_baseline_id": _optional_text(previous.get("model_baseline_id")),
        "parent_revision": _optional_text(previous.get("upstream_revision")),
        "previous_baseline_revision": _optional_text(previous.get("baseline_revision")),
        "previous_release_id": _optional_text(previous.get("release_id")),
    }
    drift = {
        key: {"expected": value, "actual": actual.get(key)}
        for key, value in wanted.items()
        if actual.get(key) != value
    }
    if drift:
        raise AppError(
            PLAN_STALE,
            "升级计划已过期：相关对象在你确认之后发生了变化",
            field="expected",
            reasons=sorted(drift),
            drift=drift,
        )


def _apply(
    repo: ConfigRepository,
    actor: Any,
    *,
    plan: Dict[str, Any],
    scope: str,
    release_id: str,
    profile_id: Optional[str],
    upstream_baseline_id: Optional[str],
    decisions: Optional[Dict[str, Any]],
    reason: Any,
    idempotency_key: Optional[str],
    expected: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    _assert_fresh(plan, expected)
    items = plan["items"]
    explicit, pending, dropped = _resolve_decisions(items, decisions)
    unresolved = [blocker for blocker in plan["blockers"] if blocker["parameter_id"] not in dropped]
    if pending:
        raise AppError(
            INVALID_INPUT,
            "还有 %d 项没有决议，不能升级" % len(pending),
            field="decisions",
            pending=pending,
        )
    if unresolved:
        raise AppError(
            INVALID_INPUT,
            "升级计划有 %d 个阻断项，需要人工处理（或显式 drop）" % len(unresolved),
            field="decisions",
            blockers=unresolved,
        )

    target = plan["target"]
    provenance = {
        "mode": plan["mode"],
        "three_way": plan["three_way"],
        "from_release_id": (plan.get("previous") or {}).get("release_id"),
        "to_release_id": target["release_id"],
        "base": plan["sources"]["base"],
        "ours": plan["sources"]["ours"],
        "theirs": plan["sources"]["theirs"],
        "decisions": {
            item["parameter_id"]: (_optional_text((decisions or {}).get(item["parameter_id"])) or item.get("decision") or "")
            for item in items
        },
        "dropped": dropped,
        "evidence": (plan.get("merge") or {}).get("evidence") or [],
    }
    created = baselines.create_baseline(
        repo,
        actor,
        scope=scope,
        release_id=release_id,
        profile_id=profile_id,
        definition_set_id=target.get("definition_set_id"),
        variant_id=target.get("variant_id"),
        upstream_baseline_id=upstream_baseline_id,
        values=explicit,
        reason=_optional_text(reason) or "跨版本升级",
        idempotency_key=idempotency_key,
        provenance=provenance,
    )
    return {
        "item": created,
        "plan": plan,
        "applied": {
            "explicit": sorted(explicit),
            "dropped": dropped,
            "three_way": plan["three_way"],
        },
    }


# ---------------------------------------------------------------------------
# M7-02 通用层升版
# ---------------------------------------------------------------------------


def plan_common_upgrade(
    repo: ConfigRepository, *, release_id: Any, variant_id: Any = None, previous_release_id: Any = None
) -> Dict[str, Any]:
    """通用层升版计划：旧出口默认 A / 旧已批准通用 B / 新出口默认 C。"""
    release = config_catalog.get_release(repo, _text(release_id, "release_id"))
    variant = _resolve_variant(repo, release, variant_id)
    family_id = _optional_text(variant.get("family_id"))

    new_batch = config_catalog.default_batch(repo, str(release["id"]))
    if new_batch is None:
        raise AppError(
            INVALID_INPUT,
            "目标版本还没有默认出口批次，无法确定新基线要哪些参数",
            field="release_id",
            release_id=release.get("id"),
        )
    new_set = config_catalog.latest_definition_set(repo, str(release["id"]), str(variant["id"]))
    if new_set is None:
        raise AppError(
            INVALID_INPUT,
            "目标版本还没有定义集合，无法确定参数类型与单位",
            field="definition_set_id",
            release_id=release.get("id"),
            variant_id=variant.get("id"),
        )

    warnings: List[str] = []
    previous_release: Optional[Dict[str, Any]] = None
    wanted_previous = _optional_text(previous_release_id)
    if wanted_previous:
        previous_release = config_catalog.get_release(repo, wanted_previous)
        if config_catalog.version_key(
            str(previous_release.get("version_label") or "")
        ) >= config_catalog.version_key(str(release.get("version_label") or "")):
            raise AppError(
                INVALID_INPUT,
                "上一版本必须早于目标版本",
                field="previous_release_id",
                previous=previous_release.get("version_label"),
                target=release.get("version_label"),
            )
    else:
        previous_release = _previous_release(repo, release)

    old_batch: Optional[Dict[str, Any]] = None
    old_variant: Optional[Dict[str, Any]] = None
    old_baseline: Optional[Dict[str, Any]] = None
    base_values: Dict[str, Any] = {}
    ours_values: Dict[str, Any] = {}
    if previous_release is not None:
        old_batch = config_catalog.default_batch(repo, str(previous_release["id"]))
        old_variant = _variant_of_family(repo, str(previous_release["id"]), family_id)
        old_baseline = _approved_common(repo, str(previous_release["id"]), family_id)
        if old_batch is None:
            warnings.append("上一版本没有默认出口批次，无法取旧出口默认值")
        if old_variant is None:
            warnings.append("上一版本没有同族模板变体（family 不同），无法做三方继承")
        else:
            old_set = config_catalog.latest_definition_set(
                repo, str(previous_release["id"]), str(old_variant["id"])
            )
            if old_baseline is not None and old_baseline.get("definition_set_id"):
                old_set = config_catalog.get_definition_set(
                    repo, str(old_baseline["definition_set_id"])
                )
            if old_set is not None and old_batch is not None:
                base_values, seed_warnings = baselines.seed_from_default_batch(
                    repo, str(previous_release["id"]), str(old_set["id"])
                )
                warnings.extend(seed_warnings)
        if old_baseline is None:
            warnings.append("上一版本没有已批准的通用基线，只能新建（不是三方继承）")
        else:
            ours_values = _values_of(old_baseline)
    else:
        warnings.append("没有更早的版本，无法做三方继承；本次只按新出口默认新建")

    theirs_values, seed_warnings = baselines.seed_from_default_batch(
        repo, str(release["id"]), str(new_set["id"])
    )
    warnings.extend(seed_warnings)

    three_way = bool(base_values) and bool(ours_values)
    sources = {
        "base": {
            "kind": "release_default",
            "present": bool(base_values),
            "release_id": (previous_release or {}).get("id"),
            "batch_id": (old_batch or {}).get("id"),
            "snapshots": _snapshot_hashes(repo, old_batch),
        },
        "ours": {
            "kind": "baseline",
            "present": bool(ours_values),
            "baseline_id": (old_baseline or {}).get("id"),
            "revision": (old_baseline or {}).get("revision"),
            "definition_set_id": (old_baseline or {}).get("definition_set_id"),
        },
        "theirs": {
            "kind": "release_default",
            "present": bool(theirs_values),
            "release_id": release.get("id"),
            "batch_id": new_batch.get("id"),
            "snapshots": _snapshot_hashes(repo, new_batch),
        },
    }

    all_ids = sorted(set(base_values) | set(ours_values) | set(theirs_values))
    names = _names(repo, all_ids)

    if three_way:
        merged = config_merge.merge(
            base=base_values,
            ours=ours_values,
            theirs=theirs_values,
            base_definitions=_definitions(repo, (old_baseline or {}).get("definition_set_id")),
            ours_definitions=_definitions(repo, (old_baseline or {}).get("definition_set_id")),
            theirs_definitions=_definitions(repo, str(new_set["id"])),
            names=names,
        )
        items = merged["items"]
        blockers = merged["blockers"]
    else:
        merged = None
        items = _fresh_items(theirs_values, names, "no_previous_source")
        blockers = []

    return {
        "mode": MODE_COMMON,
        "target": {
            "release_id": release.get("id"),
            "release_label": release.get("version_label"),
            "variant_id": variant.get("id"),
            "family_id": family_id,
            "definition_set_id": new_set.get("id"),
            "batch_id": new_batch.get("id"),
        },
        "previous": None
        if previous_release is None
        else {
            "release_id": previous_release.get("id"),
            "release_label": previous_release.get("version_label"),
            "variant_id": (old_variant or {}).get("id"),
            "batch_id": (old_batch or {}).get("id"),
            "baseline_id": (old_baseline or {}).get("id"),
            "baseline_revision": (old_baseline or {}).get("revision"),
        },
        "sources": sources,
        "three_way": three_way,
        "merge": merged,
        "items": items,
        "blockers": blockers,
        "warnings": warnings,
        "needs_decision": [item["parameter_id"] for item in items if item["action"] == "conflict"],
        "manual": [item["parameter_id"] for item in items if item["action"] == "manual"],
        "can_apply": not blockers and not [item for item in items if item["action"] == "conflict"],
        "publishable": False,
    }


def create_common_upgrade_baseline(
    repo: ConfigRepository,
    actor: Any,
    *,
    release_id: Any,
    variant_id: Any = None,
    previous_release_id: Any = None,
    decisions: Optional[Dict[str, Any]] = None,
    reason: Any = "",
    idempotency_key: Optional[str] = None,
    expected: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    plan = plan_common_upgrade(
        repo, release_id=release_id, variant_id=variant_id, previous_release_id=previous_release_id
    )
    return _apply(
        repo,
        actor,
        plan=plan,
        scope=baselines.SCOPE_COMMON,
        release_id=str(plan["target"]["release_id"]),
        profile_id=None,
        upstream_baseline_id=None,
        decisions=decisions,
        reason=reason,
        idempotency_key=idempotency_key,
        expected=expected,
    )


# ---------------------------------------------------------------------------
# M7-03 机型升版 / 同版本刷新
# ---------------------------------------------------------------------------


def _plan_model(
    repo: ConfigRepository,
    *,
    profile_id: Any,
    target_release: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    pid = _text(profile_id, "profile_id")
    model = _current_model_baseline(repo, pid)
    if model is None:
        raise AppError(
            INVALID_INPUT,
            "该机型还没有机型基线，无法升级（先用通用基线建一条）",
            field="profile_id",
            profile_id=pid,
        )
    family_id = ""
    model_variant_id = _optional_text(model.get("variant_id"))
    if model_variant_id:
        family_id = _optional_text(config_catalog.get_variant(repo, model_variant_id).get("family_id"))
    if not family_id:
        raise AppError(
            INVALID_INPUT,
            "机型基线没有模板变体，无法确定同族模板",
            field="variant_id",
            baseline_id=model.get("id"),
        )

    parent_id = _optional_text(model.get("upstream_ref"))
    if not parent_id:
        raise AppError(
            INVALID_INPUT,
            "机型基线没有记录上游通用基线，无法确定共同基准",
            field="upstream_ref",
            baseline_id=model.get("id"),
        )
    # **按记录的修订**取父，不取 head：否则通用层一发 r5，共同基准就漂到 r5
    parent_revision = _optional_text(model.get("upstream_revision")) or None
    warnings: List[str] = []
    if not parent_revision:
        warnings.append("机型基线没有记录上游修订号，按当前 head 作共同基准（结论可能偏乐观）")
    base = baselines.get_baseline(repo, parent_id, parent_revision)

    if target_release is not None:
        theirs = _approved_common(repo, str(target_release["id"]), family_id)
        if theirs is None:
            raise AppError(
                INVALID_INPUT,
                "目标版本的通用基线还没有批准，先完成通用层升版与批准",
                field="release_id",
                release_id=target_release.get("id"),
                family_id=family_id,
            )
        mode = MODE_MODEL
    else:
        theirs = baselines.get_baseline(repo, parent_id)
        if theirs.get("state") != baselines.STATE_APPROVED:
            raise AppError(
                INVALID_INPUT,
                "上游通用基线当前不是已批准状态，不能作为刷新目标",
                field="upstream_baseline_id",
                baseline_id=parent_id,
                state=theirs.get("state"),
            )
        mode = MODE_REBASE

    theirs_values = _values_of(theirs)
    ours_values = _values_of(model)
    base_values = _values_of(base)
    names = _names(repo, sorted(set(base_values) | set(ours_values) | set(theirs_values)))
    merged = config_merge.merge(
        base=base_values,
        ours=ours_values,
        theirs=theirs_values,
        base_definitions=_definitions(repo, base.get("definition_set_id")),
        ours_definitions=_definitions(repo, model.get("definition_set_id")),
        theirs_definitions=_definitions(repo, theirs.get("definition_set_id")),
        names=names,
    )

    noop = (
        base.get("revision") == theirs.get("revision")
        and not merged["needs_decision"]
        and not merged["blockers"]
        and all(item["action"] in ("keep",) for item in merged["items"])
    )
    if noop and mode == MODE_REBASE:
        warnings.append("通用层没有新修订，刷新不会有任何变化")

    target_release_id = str((target_release or {}).get("id") or theirs.get("release_id"))
    return {
        "mode": mode,
        "profile_id": pid,
        "noop": noop,
        "target": {
            "release_id": target_release_id,
            "release_label": (target_release or {}).get("version_label"),
            "variant_id": theirs.get("variant_id"),
            "family_id": family_id,
            "definition_set_id": theirs.get("definition_set_id"),
            "upstream_baseline_id": theirs.get("id"),
            "upstream_revision": theirs.get("revision"),
        },
        "previous": {
            "release_id": model.get("release_id"),
            "model_baseline_id": model.get("id"),
            "model_revision": model.get("revision"),
            "upstream_baseline_id": base.get("id"),
            "upstream_revision": base.get("revision"),
        },
        "sources": {
            "base": {
                "kind": "baseline",
                "present": bool(base_values),
                "baseline_id": base.get("id"),
                "revision": base.get("revision"),
                "definition_set_id": base.get("definition_set_id"),
            },
            "ours": {
                "kind": "baseline",
                "present": bool(ours_values),
                "baseline_id": model.get("id"),
                "revision": model.get("revision"),
                "definition_set_id": model.get("definition_set_id"),
            },
            "theirs": {
                "kind": "baseline",
                "present": bool(theirs_values),
                "baseline_id": theirs.get("id"),
                "revision": theirs.get("revision"),
                "definition_set_id": theirs.get("definition_set_id"),
            },
        },
        "three_way": True,
        "merge": merged,
        "items": merged["items"],
        "blockers": merged["blockers"],
        "warnings": warnings,
        "needs_decision": merged["needs_decision"],
        "manual": merged["manual"],
        "can_apply": not merged["blockers"] and not merged["needs_decision"],
        "publishable": False,
    }


def plan_model_upgrade(repo: ConfigRepository, *, profile_id: Any, release_id: Any) -> Dict[str, Any]:
    """机型跨版本升级：共同基准取该机型**实际引用**的旧通用基线。"""
    release = config_catalog.get_release(repo, _text(release_id, "release_id"))
    return _plan_model(repo, profile_id=profile_id, target_release=release)


def plan_model_rebase(repo: ConfigRepository, *, profile_id: Any) -> Dict[str, Any]:
    """同版本刷新：上游同一条通用基线出了新修订，机型显式跟进。"""
    return _plan_model(repo, profile_id=profile_id, target_release=None)


def create_model_upgrade_baseline(
    repo: ConfigRepository,
    actor: Any,
    *,
    profile_id: Any,
    release_id: Any,
    decisions: Optional[Dict[str, Any]] = None,
    reason: Any = "",
    idempotency_key: Optional[str] = None,
    expected: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    plan = plan_model_upgrade(repo, profile_id=profile_id, release_id=release_id)
    return _apply(
        repo,
        actor,
        plan=plan,
        scope=baselines.SCOPE_MODEL,
        release_id=str(plan["target"]["release_id"]),
        profile_id=str(plan["profile_id"]),
        upstream_baseline_id=str(plan["target"]["upstream_baseline_id"]),
        decisions=decisions,
        reason=reason,
        idempotency_key=idempotency_key,
        expected=expected,
    )


def create_model_rebase_baseline(
    repo: ConfigRepository,
    actor: Any,
    *,
    profile_id: Any,
    decisions: Optional[Dict[str, Any]] = None,
    reason: Any = "",
    idempotency_key: Optional[str] = None,
    expected: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    plan = plan_model_rebase(repo, profile_id=profile_id)
    return _apply(
        repo,
        actor,
        plan=plan,
        scope=baselines.SCOPE_MODEL,
        release_id=str(plan["target"]["release_id"]),
        profile_id=str(plan["profile_id"]),
        upstream_baseline_id=str(plan["target"]["upstream_baseline_id"]),
        decisions=decisions,
        reason=reason,
        idempotency_key=idempotency_key,
        expected=expected,
    )
