"""回读核验：覆盖率与新鲜度（M7-10 / INH-1969）。

把"新采集到的观测"和"发出去的产物/基线"逐个参数比一遍，给出**逐参数结论**和一个
聚合结论。四条不许：

1. **缺项不当 0**：观测量不到的参数算 `missing`，不是"等于 0"，也不进分母蒙混。
2. **不宣传全量通过**：文件里有、但没登记成参数的字段（`unmapped`）算
   `unobservable`；存在 `unobservable`/`missing` 时结论只能是 `incomplete`。
3. **采集早于导入 → 不算 verified**：`capture_time < imported_after`（缺省取产物生成
   时间）时，这份观测证明不了导入后的状态，结论 `incomplete` 并给
   `capture_before_import`。
4. **版本不同 → `version_mismatch`**：观测归属的软件版本与产物所在版本不一致时，
   逐参数比较没有意义，直接给版本结论。

每次核验都**新增**一个 `verification` 对象：同一份产物允许多次核验，旧结论照旧可读
（"上次核验通过、这次不通过"必须能同时看到）。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from ..core import config_values
from ..core.config_repository import ConfigRepository, now_iso
from ..core.errors import INVALID_INPUT, NOT_FOUND, AppError
from . import config_artifacts, config_baselines as baselines, config_catalog
from . import config_ingest

CONCLUSION_VERIFIED = "verified"
CONCLUSION_MISMATCH = "mismatch"
CONCLUSION_INCOMPLETE = "incomplete"
CONCLUSION_VERSION_MISMATCH = "version_mismatch"

CONCLUSION_LABELS = {
    CONCLUSION_VERIFIED: "核验通过",
    CONCLUSION_MISMATCH: "值不一致",
    CONCLUSION_INCOMPLETE: "证据不完整",
    CONCLUSION_VERSION_MISMATCH: "版本不一致",
}

ROW_MATCHED = "matched"
ROW_MISSING = "missing"
ROW_MISMATCHED = "mismatched"
ROW_UNOBSERVABLE = "unobservable"

ROW_LABELS = {
    ROW_MATCHED: "一致",
    ROW_MISSING: "观测里没有",
    ROW_MISMATCHED: "不一致",
    ROW_UNOBSERVABLE: "无法核验",
}

#: `missing` 的理由：区分"观测里根本没这个点"与"有这个点但读不出来"。
_MISSING_REASONS = {
    config_ingest.ENTRY_MISSING: "观测文件里没有这个节点",
    config_ingest.ENTRY_INVALID: "观测里的值不符合定义（类型/单位）",
    config_ingest.ENTRY_AMBIGUOUS: "同一个节点被多个参数占用，读不出唯一值",
}


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


def get_verification(repo: ConfigRepository, verification_id: Any) -> Dict[str, Any]:
    vid = _text(verification_id, "verification_id")
    payload = repo.get("verification", vid)
    if payload is None:
        raise AppError(NOT_FOUND, "核验报告不存在", verification_id=vid)
    return payload


def list_verifications(
    repo: ConfigRepository, *, artifact_id: Optional[str] = None, profile_id: Optional[str] = None
) -> List[Dict[str, Any]]:
    wanted_artifact = _optional_text(artifact_id)
    wanted_profile = _optional_text(profile_id)
    items = []
    for item in repo.list_objects("verification"):
        if wanted_artifact and str(item.get("artifact_id") or "") != wanted_artifact:
            continue
        if wanted_profile and str(item.get("profile_id") or "") != wanted_profile:
            continue
        items.append(item)
    items.sort(key=lambda row: (str(row.get("created_at") or ""), str(row.get("id") or "")), reverse=True)
    return items


def _latest_observation(repo: ConfigRepository, profile_id: str, family_id: str):
    candidates = [
        item
        for item in config_ingest.list_observations(repo, profile_id=profile_id)
        if str(item.get("family_id") or "") == family_id
        and str(item.get("status") or "") == config_ingest.STATUS_PARSED
    ]
    return candidates[-1] if candidates else None


def _compare(left: Any, right: Any) -> Optional[bool]:
    """`True` 相等 / `False` 不等 / `None` 比不了（量纲或单位问题）。"""
    try:
        left_value = config_values.TypedValue.from_json(left)
        right_value = config_values.TypedValue.from_json(right)
    except AppError:
        return None
    try:
        return config_values.compare(left_value, right_value) == "equal"
    except AppError:
        return None


def _rows(
    repo: ConfigRepository,
    expected: Dict[str, Any],
    observation: Dict[str, Any],
) -> List[Dict[str, Any]]:
    """逐参数：期望（基线值） vs 实测（观测映射值）。"""
    entries = {
        str(entry.get("parameter_id")): entry
        for entry in (observation.get("mapping") or {}).get("entries") or []
        if entry.get("parameter_id")
    }
    names = {}
    for parameter_id in sorted(expected):
        try:
            names[parameter_id] = str(
                config_catalog.get_parameter(repo, parameter_id).get("name") or parameter_id
            )
        except AppError:
            names[parameter_id] = parameter_id

    rows: List[Dict[str, Any]] = []
    for parameter_id in sorted(expected):
        want = (expected.get(parameter_id) or {}).get("value")
        entry = entries.get(parameter_id)
        row = {
            "parameter_id": parameter_id,
            "name": names.get(parameter_id, parameter_id),
            "expected": want,
            "observed": None,
            "status": ROW_MISSING,
            "reason": "",
        }
        if entry is None:
            row["reason"] = "观测里没有这个参数"
            rows.append(row)
            continue
        entry_status = str(entry.get("status") or "")
        if entry_status != config_ingest.ENTRY_MAPPED or entry.get("value_ref") is None:
            row["status"] = ROW_MISSING
            row["reason"] = _MISSING_REASONS.get(entry_status, "观测里的这个参数读不出来")
            rows.append(row)
            continue
        row["observed"] = entry.get("value_ref")
        same = _compare(want, entry.get("value_ref"))
        if same is None:
            # 比不了就不许说"一致"，也不算"不一致"——单列一态，避免两头骗
            row["status"] = ROW_UNOBSERVABLE
            row["reason"] = "单位/量纲不同，无法比较"
        elif same:
            row["status"] = ROW_MATCHED
        else:
            row["status"] = ROW_MISMATCHED
            row["reason"] = "实测值与基线值不同"
        rows.append(row)

    # 观测里有、期望里没有的参数：不能算通过（可能只是"我们没登记"）
    for parameter_id, entry in entries.items():
        if parameter_id in expected:
            continue
        if entry.get("status") != config_ingest.ENTRY_MAPPED:
            continue
        rows.append(
            {
                "parameter_id": parameter_id,
                "name": names.get(parameter_id, parameter_id),
                "expected": None,
                "observed": entry.get("value_ref"),
                "status": ROW_UNOBSERVABLE,
                "reason": "基线里没有这个参数，无法判断该不该是观测值",
            }
        )
    return rows


def _conclusion(rows: List[Dict[str, Any]], *, version_ok: bool, fresh: bool) -> str:
    if not version_ok:
        return CONCLUSION_VERSION_MISMATCH
    if any(row["status"] == ROW_MISMATCHED for row in rows):
        return CONCLUSION_MISMATCH
    if not fresh:
        return CONCLUSION_INCOMPLETE
    if any(row["status"] in (ROW_MISSING, ROW_UNOBSERVABLE) for row in rows):
        return CONCLUSION_INCOMPLETE
    if not rows:
        return CONCLUSION_INCOMPLETE
    return CONCLUSION_VERIFIED


def verify(
    repo: ConfigRepository,
    actor: Any,
    *,
    artifact_id: Any,
    observation_id: Any = None,
    profile_id: Any = None,
    imported_after: Any = None,
    note: Any = "",
    idempotency_key: Optional[str] = None,
) -> Dict[str, Any]:
    """核验一份产物在某个观测下的表现，**落档**一条报告。

    `profile_id` 是"在哪台机器上读回来的"。通用产物没有机型归属，所以它必须由
    调用方点名（或由观测本身带出）——否则"这次核验属于谁"无从回答。
    """
    aid = _text(artifact_id, "artifact_id")
    artifact = config_artifacts.get_artifact(repo, aid)
    baseline = baselines.get_baseline(
        repo, _text(artifact.get("baseline_id"), "baseline_id"),
        _optional_text(artifact.get("baseline_revision")) or None,
    )
    owner = _optional_text(artifact.get("profile_id")) or _optional_text(baseline.get("profile_id"))
    explicit = _optional_text(profile_id)

    wanted = _optional_text(observation_id)
    observation: Optional[Dict[str, Any]] = (
        config_ingest.get_observation(repo, wanted) if wanted else None
    )

    pid = explicit or _optional_text((observation or {}).get("profile_id")) or owner
    if not pid:
        # 通用产物的核验必须点名机型：否则"在哪台机器上读回来的"无从回答
        raise AppError(
            INVALID_INPUT,
            "通用产物核验必须点名 profile_id",
            field="profile_id",
            artifact_id=aid,
        )
    if owner and pid != owner:
        raise AppError(NOT_FOUND, "产物不属于该机型", artifact_id=aid, profile_id=pid)

    if observation is None:
        variant = config_catalog.get_variant(repo, _text(artifact.get("variant_id"), "variant_id"))
        observation = _latest_observation(repo, pid, str(variant.get("family_id")))
        if observation is None:
            raise AppError(
                NOT_FOUND,
                "该机型还没有这个文件族的观测，无法回读核验",
                field="observation_id",
                profile_id=pid,
                family_id=variant.get("family_id"),
            )
    elif str(observation.get("profile_id") or "") != pid:
        raise AppError(NOT_FOUND, "观测不属于该机型", observation_id=observation.get("id"))

    obs_release = _optional_text((observation.get("release_ref") or {}).get("release_id"))
    base_release = _optional_text(baseline.get("release_id"))
    version_ok = bool(obs_release) and obs_release == base_release

    capture = _optional_text(observation.get("captured_at"))
    threshold = _optional_text(imported_after) or _optional_text(artifact.get("created_at"))
    # 采集早于（或等于）导入时刻：这份观测看的是"导入之前"的机器状态
    fresh = True
    if threshold and capture and capture <= threshold:
        fresh = False

    expected = dict(baseline.get("values") or {})
    rows = _rows(repo, expected, observation)
    counts = {key: 0 for key in (ROW_MATCHED, ROW_MISSING, ROW_MISMATCHED, ROW_UNOBSERVABLE)}
    for row in rows:
        counts[row["status"]] += 1

    # 观测文件里"有节点、但没有登记成参数"的部分：我们根本没能力核验它们，
    # 所以既不算通过也不算失败，而是把范围说清楚——不能宣传"全量一致"。
    unregistered = [
        str(entry.get("raw_key") or "")
        for entry in (observation.get("mapping") or {}).get("entries") or []
        if not entry.get("parameter_id")
    ]
    scope_complete = not unregistered
    conclusion = _conclusion(rows, version_ok=version_ok, fresh=fresh)
    reasons: List[str] = []
    if not version_ok:
        reasons.append("version_mismatch")
    if not fresh:
        reasons.append("capture_before_import")
    if counts[ROW_MISSING]:
        reasons.append("missing")
    if counts[ROW_UNOBSERVABLE]:
        reasons.append("unobservable")
    if counts[ROW_MISMATCHED]:
        reasons.append("mismatched")

    warnings: List[str] = []
    if not scope_complete:
        warnings.append(
            "观测里有 %d 个节点没有登记成参数，未纳入本次核验（结论只覆盖已登记参数）"
            % len(unregistered)
        )
    if conclusion == CONCLUSION_VERIFIED and not scope_complete:
        claim = "只能断言：已登记参数与观测量一致；其余节点未纳入核验"
    elif conclusion == CONCLUSION_VERIFIED:
        claim = "可断言：该产物在该观测下的全部已登记参数一致"
    else:
        claim = "不可断言一致（见 reasons 与逐参数结论）"

    tx = repo.transaction(
        actor,
        "回读核验 %s（%s）" % (aid, CONCLUSION_LABELS[conclusion]),
        action="verification.run",
        idempotency_key=idempotency_key,
    )
    ref = tx.put(
        "verification",
        {
            "artifact_id": aid,
            "artifact_sha256": _optional_text(artifact.get("blob_sha256")),
            "baseline_id": _optional_text(baseline.get("id")),
            "baseline_revision": _optional_text(baseline.get("revision")),
            "observation_id": _optional_text(observation.get("id")),
            "profile_id": pid,
            "device_id": _optional_text(observation.get("device_id")) or None,
            "conclusion": conclusion,
            "conclusion_label": CONCLUSION_LABELS[conclusion],
            "reasons": reasons,
            "warnings": warnings,
            "claim": claim,
            "scope_complete": scope_complete,
            "unregistered_keys": unregistered[:50],
            "counts": counts,
            "aggregate": {
                "matched": counts[ROW_MATCHED],
                "missing": counts[ROW_MISSING],
                "mismatched": counts[ROW_MISMATCHED],
                "unobservable": counts[ROW_UNOBSERVABLE],
                "total": len(rows),
            },
            "rows": rows,
            "verification_scope": {
                "profile_id": pid,
                "release_id": base_release,
                "observation_release_id": obs_release or None,
                "definition_set_id": _optional_text(artifact.get("definition_set_id")) or None,
                "variant_id": _optional_text(artifact.get("variant_id")) or None,
                "parameters_in_baseline": len(expected),
                "parameters_observed": sum(
                    1 for row in rows if row["status"] != ROW_MISSING
                ),
            },
            "capture_time": capture,
            "imported_after": threshold or None,
            "fresh": fresh,
            "source_fingerprint": _optional_text(observation.get("blob_sha256")),
            "note": _optional_text(note),
            "created_at": now_iso(),
            "created_by": _label(actor),
        },
    )
    tx.commit()
    return get_verification(repo, str(ref.id))
