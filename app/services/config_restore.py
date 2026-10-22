"""历史回滚、跨变体迁移与出口补发（M7-06 / INH-1965）。

三件事，一个共同底线：**历史对象与 head 指针都不许改**。

* `restore_baseline`：把某个历史修订的内容变成**一条新的已批准基线**。
  旧修订照旧可读、旧产照样旧可下载；head 指针指向新对象，而不是被"倒回去"。
* `plan_variant_migration`：跨变体迁移只出计划——参数按 `parameter_id` 对齐，
  目标定义集合里没有的参数、换算不了的单位，一律阻断，不猜。
* `plan_reissue_impact`：同版出口补发后，报告"已发布基线用的是哪个批次/模板"。
  补发本身不改任何基线的批次引用；要不要跟进是**另一个显式动作**。

为什么回滚要新建对象而不是"把 head 指回旧修订"：基线是审计对象，修订链一旦被
改写，"上周批准的是什么"就再也答不上来。新建一条 + `restore_provenance` 记录的
做法，把"谁在什么时候把哪一版内容恢复成了新版"留在链上。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from ..core import config_values
from ..core.config_repository import ConfigRepository, now_iso
from ..core.errors import INVALID_INPUT, NOT_FOUND, PLAN_STALE, AppError
from . import config_baselines as baselines
from . import config_catalog

MODE_RESTORE = "restore"
MODE_VARIANT_MIGRATION = "variant_migration"
MODE_REISSUE = "reissue_refresh"


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


def _diff_values(
    before: Dict[str, Any], after: Dict[str, Any], names: Dict[str, str]
) -> List[Dict[str, Any]]:
    """逐参数比较两组值（含意图），只列真正变了的。"""
    rows: List[Dict[str, Any]] = []
    for parameter_id in sorted(set(before) | set(after)):
        left = before.get(parameter_id) or {}
        right = after.get(parameter_id) or {}
        left_value = left.get("value")
        right_value = right.get("value")
        same = True
        if left_value is not None and right_value is not None:
            try:
                same = (
                    config_values.compare(
                        config_values.TypedValue.from_json(left_value),
                        config_values.TypedValue.from_json(right_value),
                    )
                    == "equal"
                )
            except AppError:
                same = False
        else:
            same = left_value == right_value
        if same and _optional_text(left.get("intent")) == _optional_text(right.get("intent")):
            continue
        rows.append(
            {
                "parameter_id": parameter_id,
                "name": names.get(parameter_id, parameter_id),
                "from": left_value,
                "to": right_value,
                "from_intent": left.get("intent"),
                "to_intent": right.get("intent"),
                "value_changed": not same,
            }
        )
    return rows


# ---------------------------------------------------------------------------
# 回滚
# ---------------------------------------------------------------------------


def plan_restore(
    repo: ConfigRepository, *, baseline_id: Any, revision: Any
) -> Dict[str, Any]:
    """把某个历史修订与当前 head 摆在一起，看回滚会改什么。只读。"""
    object_id = _text(baseline_id, "baseline_id")
    wanted = _text(revision, "revision")
    current = baselines.get_baseline(repo, object_id)
    if wanted == _optional_text(current.get("revision")):
        raise AppError(
            INVALID_INPUT,
            "目标修订就是当前 head，回滚没有意义",
            field="revision",
            revision=wanted,
        )
    try:
        source = baselines.get_baseline(repo, object_id, wanted)
    except AppError:
        raise AppError(
            NOT_FOUND,
            "基线没有这个修订",
            baseline_id=object_id,
            revision=wanted,
            revisions=repo.history("baseline", object_id),
        )

    names = _names(repo, sorted(set(source.get("values") or {}) | set(current.get("values") or {})))
    rows = _diff_values(
        dict(current.get("values") or {}), dict(source.get("values") or {}), names
    )
    warnings: List[str] = []
    if wanted not in repo.history("baseline", object_id):
        warnings.append("目标修订不在当前修订链上（可能已归档），回滚后的谱系会跳过它")

    return {
        "mode": MODE_RESTORE,
        "baseline": {
            "baseline_id": object_id,
            "head_revision": current.get("revision"),
            "state": current.get("state"),
            "scope": current.get("scope"),
            "profile_id": current.get("profile_id"),
            "release_id": current.get("release_id"),
            "definition_set_id": current.get("definition_set_id"),
        },
        "source": {
            "revision": source.get("revision"),
            "state": source.get("state"),
            "decided_by": source.get("decided_by"),
            "decided_at": source.get("decided_at"),
            "created_at": source.get("created_at"),
        },
        "items": rows,
        "changed": len(rows),
        "warnings": warnings,
        "publishable": False,
    }


def restore_baseline(
    repo: ConfigRepository,
    actor: Any,
    *,
    baseline_id: Any,
    revision: Any,
    reason: Any = "",
    expected_head: Any = None,
    idempotency_key: Optional[str] = None,
) -> Dict[str, Any]:
    """回滚 = 用历史内容建一条**新的已批准基线**。旧对象一个字不动。"""
    plan = plan_restore(repo, baseline_id=baseline_id, revision=revision)
    object_id = plan["baseline"]["baseline_id"]
    head_revision = plan["baseline"]["head_revision"]
    wanted_head = _optional_text(expected_head)
    if wanted_head and wanted_head != head_revision:
        raise AppError(
            PLAN_STALE,
            "基线的 head 已经变了，按旧计划回滚会覆盖别人的改动",
            reasons=["head_revision"],
            drift={
                "head_revision": {"expected": wanted_head, "actual": head_revision}
            },
        )

    current = baselines.get_baseline(repo, object_id)
    source = baselines.get_baseline(repo, object_id, revision)
    stamp = now_iso()
    payload = dict(source)
    payload.pop("id", None)
    payload.pop("revision", None)
    payload.update(
        {
            "state": baselines.STATE_APPROVED,
            "reason": _optional_text(reason)
            or "回滚到 %s 的 %s" % (object_id, source.get("revision")),
            "previous_revision_id": current.get("id"),
            "decided_by": _label(actor),
            "decided_at": stamp,
            "decide_note": "回滚自 %s %s" % (object_id, source.get("revision")),
            "restore_provenance": {
                "mode": MODE_RESTORE,
                "from_baseline_id": object_id,
                "from_revision": source.get("revision"),
                "head_revision_at_restore": head_revision,
                "restored_by": _label(actor),
                "restored_at": stamp,
                "changed": plan["changed"],
            },
        }
    )
    tx = repo.transaction(
        actor,
        _optional_text(reason) or "回滚基线 %s" % object_id,
        action="baseline.restore",
        idempotency_key=idempotency_key,
    )
    ref = tx.put("baseline", payload)
    tx.commit()
    restored = baselines.get_baseline(repo, str(ref.id))
    return {
        "item": restored,
        "plan": plan,
        # 旧修订与旧对象仍然可读——这是"不改写历史"的可验证形式
        "source": {
            "baseline_id": object_id,
            "revision": source.get("revision"),
            "still_readable": True,
        },
        "previous_head": {"baseline_id": object_id, "revision": head_revision},
    }


# ---------------------------------------------------------------------------
# 跨变体迁移
# ---------------------------------------------------------------------------


def plan_variant_migration(
    repo: ConfigRepository, *, baseline_id: Any, target_variant_id: Any
) -> Dict[str, Any]:
    """把一条基线的值迁到另一个模板变体上：只出计划，不发布。"""
    source = baselines.get_baseline(repo, _text(baseline_id, "baseline_id"))
    variant = config_catalog.get_variant(repo, _text(target_variant_id, "target_variant_id"))
    release_id = _optional_text(variant.get("release_id"))
    definition_set = config_catalog.latest_definition_set(
        repo, release_id, str(variant["id"])
    )
    if definition_set is None:
        raise AppError(
            INVALID_INPUT,
            "目标变体还没有定义集合，无法确定参数类型与单位",
            field="target_variant_id",
            target_variant_id=variant.get("id"),
            release_id=release_id,
        )
    definitions = baselines.definitions_by_parameter(repo, str(definition_set["id"]))
    source_variant_id = _optional_text(source.get("variant_id"))
    source_family = ""
    if source_variant_id:
        source_family = _optional_text(
            config_catalog.get_variant(repo, source_variant_id).get("family_id")
        )
    target_family = _optional_text(variant.get("family_id"))

    values = dict(source.get("values") or {})
    names = _names(repo, sorted(values))
    rows: List[Dict[str, Any]] = []
    blockers: List[Dict[str, Any]] = []
    for parameter_id in sorted(values):
        entry = values[parameter_id]
        definition = definitions.get(parameter_id)
        if definition is None:
            blockers.append(
                {
                    "parameter_id": parameter_id,
                    "code": "unknown_parameter",
                    "message": "目标变体的定义集合里没有这个参数，无法迁移",
                }
            )
            continue
        value = entry.get("value")
        candidate = value
        target_unit = _optional_text(definition.get("unit")) or None
        if value is not None and target_unit and _optional_text(value.get("unit")) not in ("", target_unit):
            try:
                converted = config_values.convert(
                    config_values.TypedValue.from_json(value), target_unit
                )
                candidate = converted.to_json()
            except AppError:
                blockers.append(
                    {
                        "parameter_id": parameter_id,
                        "code": "incomparable",
                        "message": "单位无法换算到目标变体的定义，不能迁移",
                        "from_unit": value.get("unit"),
                        "to_unit": target_unit,
                    }
                )
                continue
        rows.append(
            {
                "parameter_id": parameter_id,
                "name": names.get(parameter_id, parameter_id),
                "from": value,
                "to": candidate,
                "intent": entry.get("intent"),
                "converted": candidate != value,
            }
        )

    warnings: List[str] = []
    if source_family and target_family and source_family != target_family:
        warnings.append(
            "跨配置族迁移：源族 %s → 目标族 %s，落点语义由目标族的定义决定"
            % (source_family, target_family)
        )

    return {
        "mode": MODE_VARIANT_MIGRATION,
        "source": {
            "baseline_id": source.get("id"),
            "revision": source.get("revision"),
            "release_id": source.get("release_id"),
            "variant_id": source.get("variant_id"),
            "family_id": source_family,
            "definition_set_id": source.get("definition_set_id"),
        },
        "target": {
            "release_id": release_id,
            "variant_id": variant.get("id"),
            "family_id": target_family,
            "definition_set_id": definition_set.get("id"),
            "template_snapshot_id": variant.get("template_snapshot_id"),
        },
        "items": rows,
        "blockers": blockers,
        "warnings": warnings,
        "can_apply": not blockers and bool(rows),
        "publishable": False,
    }


def create_variant_migration(
    repo: ConfigRepository,
    actor: Any,
    *,
    baseline_id: Any,
    target_variant_id: Any,
    reason: Any = "",
    idempotency_key: Optional[str] = None,
) -> Dict[str, Any]:
    """按迁移计划建一条目标变体的**草稿**（不发布，不覆盖源基线）。"""
    plan = plan_variant_migration(
        repo, baseline_id=baseline_id, target_variant_id=target_variant_id
    )
    if plan["blockers"]:
        raise AppError(
            INVALID_INPUT,
            "迁移计划有 %d 个阻断项" % len(plan["blockers"]),
            field="target_variant_id",
            blockers=plan["blockers"],
        )
    if not plan["items"]:
        raise AppError(INVALID_INPUT, "没有可迁移的参数", field="baseline_id")

    source = baselines.get_baseline(repo, _text(baseline_id, "baseline_id"))
    scope = _optional_text(source.get("scope"))
    profile_id = _optional_text(source.get("profile_id")) or None
    values = {
        row["parameter_id"]: {
            "value": row["to"],
            "intent": row["intent"] or baselines.INTENT_OVERRIDE,
            "reason": _optional_text(reason) or "跨变体迁移",
        }
        for row in plan["items"]
    }
    created = baselines.create_baseline(
        repo,
        actor,
        scope=scope,
        release_id=plan["target"]["release_id"],
        profile_id=profile_id,
        definition_set_id=plan["target"]["definition_set_id"],
        variant_id=plan["target"]["variant_id"],
        upstream_baseline_id=(source.get("upstream_ref") if scope != baselines.SCOPE_COMMON else None),
        values=values,
        reason=_optional_text(reason) or "跨变体迁移",
        idempotency_key=idempotency_key,
        provenance={
            "mode": MODE_VARIANT_MIGRATION,
            "from_baseline_id": source.get("id"),
            "from_revision": source.get("revision"),
            "from_variant_id": source.get("variant_id"),
            "to_variant_id": plan["target"]["variant_id"],
        },
    )
    return {"item": created, "plan": plan}


# ---------------------------------------------------------------------------
# 出口补发
# ---------------------------------------------------------------------------


def plan_reissue_impact(repo: ConfigRepository, *, baseline_id: Any) -> Dict[str, Any]:
    """同版出口补发之后：这条基线用的还是哪个模板？补发改变了什么？

    补发只新增批次，**不改**已发布基线引用的变体与模板快照。这个计划就是把
    "确实没变"证明出来，而不是嘴上保证。
    """
    baseline = baselines.get_baseline(repo, _text(baseline_id, "baseline_id"))
    release_id = _optional_text(baseline.get("release_id"))
    variant_id = _optional_text(baseline.get("variant_id"))
    release = config_catalog.get_release(repo, release_id)
    variant = config_catalog.get_variant(repo, variant_id) if variant_id else None
    snapshot_id = _optional_text((variant or {}).get("template_snapshot_id"))
    template_sha = ""
    if snapshot_id:
        template_sha = _optional_text(
            config_catalog.get_snapshot(repo, snapshot_id).get("blob_sha256")
        )
    batch = config_catalog.default_batch(repo, release_id)
    snapshots = config_catalog.batch_snapshots(repo, batch) if batch else []
    shas = [_optional_text(item.get("blob_sha256")) for item in snapshots]

    rows: List[Dict[str, Any]] = []
    if batch is not None:
        seeded, _warnings = baselines.seed_from_default_batch(
            repo, release_id, _optional_text(baseline.get("definition_set_id")) or None
        )
        names = _names(repo, sorted(seeded))
        rows = _diff_values(dict(baseline.get("values") or {}), seeded, names)

    return {
        "mode": MODE_REISSUE,
        "baseline": {
            "baseline_id": baseline.get("id"),
            "revision": baseline.get("revision"),
            "state": baseline.get("state"),
            "release_id": release_id,
            "release_label": release.get("version_label"),
            "variant_id": variant_id,
            "definition_set_id": baseline.get("definition_set_id"),
        },
        "template": {
            "snapshot_id": snapshot_id or None,
            "sha256": template_sha,
            "in_current_batch": bool(template_sha) and template_sha in shas,
        },
        "current_batch": None
        if batch is None
        else {
            "batch_id": batch.get("id"),
            "snapshot_ids": list(batch.get("snapshot_ids") or []),
            "sha256": shas,
        },
        "items": rows,
        "changed": len(rows),
        "warnings": []
        if batch is not None
        else ["该版本当前没有默认出口批次，无法比较补发影响"],
        # 明确写死：补发本身不改变已发布基线的批次/模板引用
        "baseline_reference_unchanged": True,
        "publishable": False,
    }


def create_reissue_refresh(
    repo: ConfigRepository,
    actor: Any,
    *,
    baseline_id: Any,
    reason: Any = "",
    idempotency_key: Optional[str] = None,
) -> Dict[str, Any]:
    """按补发后的出口默认，建一条**新草稿**（旧基线保持原批次引用不变）。"""
    plan = plan_reissue_impact(repo, baseline_id=baseline_id)
    baseline = baselines.get_baseline(repo, plan["baseline"]["baseline_id"])
    release_id = plan["baseline"]["release_id"]
    if plan["current_batch"] is None:
        raise AppError(
            INVALID_INPUT,
            "该版本还没有默认出口批次，无法按补发刷新",
            field="baseline_id",
            baseline_id=baseline.get("id"),
        )
    values = {
        row["parameter_id"]: {"value": row["to"], "intent": "inherit", "reason": "补发后跟随出口默认"}
        for row in plan["items"]
        if row["to"] is not None
    }
    if not values:
        raise AppError(
            INVALID_INPUT,
            "补发后的出口默认与现有基线没有差异，无需刷新",
            field="baseline_id",
            baseline_id=baseline.get("id"),
        )
    scope = _optional_text(baseline.get("scope"))
    created = baselines.create_baseline(
        repo,
        actor,
        scope=scope,
        release_id=release_id,
        profile_id=_optional_text(baseline.get("profile_id")) or None,
        definition_set_id=baseline.get("definition_set_id"),
        variant_id=baseline.get("variant_id"),
        upstream_baseline_id=(
            _optional_text(baseline.get("upstream_ref"))
            if scope != baselines.SCOPE_COMMON
            else None
        ),
        values=values,
        reason=_optional_text(reason) or "出口补发后刷新",
        idempotency_key=idempotency_key,
        provenance={
            "mode": MODE_REISSUE,
            "from_baseline_id": baseline.get("id"),
            "from_revision": baseline.get("revision"),
            "batch_id": plan["current_batch"]["batch_id"],
            "changed": plan["changed"],
        },
    )
    return {"item": created, "plan": plan}
