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

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from ..core import config_values
from ..core.config_repository import ConfigRepository, now_iso
from ..core.errors import INVALID_INPUT, NOT_FOUND, AppError
from . import config_artifacts, config_baselines as baselines, config_catalog
from . import config_delivery, config_ingest

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
        and not item.get("stale_capture")
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
        verdict = config_values.compare(left_value, right_value)
    except AppError:
        return None
    if verdict == "equal":
        return True
    if verdict == "different":
        return False
    # `incomparable` / `missing` 都不是"不一致"，单列 unobservable 才不骗人
    return None


def _parse_time(value: Any, field: str) -> Optional[datetime]:
    """ISO-8601 → aware UTC。带 `+08:00` 的时间不能按字符串直接比大小。"""
    text = _optional_text(value)
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        raise AppError(INVALID_INPUT, "时间必须是 ISO-8601", field=field)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _import_threshold(
    repo: ConfigRepository,
    artifact: Dict[str, Any],
    pid: str,
    imported_after: Any,
) -> tuple:
    """核验的新鲜度基准：显式值 > 最近一次“已声明成功”的实际执行时间 > 产物生成时间。"""
    explicit = _parse_time(imported_after, "imported_after") if imported_after else None
    if explicit is not None:
        return explicit, "request"
    declared = config_delivery.list_deliveries(
        repo,
        artifact_id=str(artifact.get("id") or ""),
        profile_id=pid,
        result=config_delivery.RESULT_DECLARED_OK,
    )
    candidates = []
    for item in declared:
        stamp = _parse_time(item.get("executed_at"), "executed_at")
        if stamp is not None:
            candidates.append((stamp, item))
    if candidates:
        stamp, item = max(candidates, key=lambda pair: pair[0])
        return stamp, "delivery:%s" % item.get("id")
    return _parse_time(artifact.get("created_at"), "created_at"), "artifact.created_at"


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


def _conclusion(
    rows: List[Dict[str, Any]],
    *,
    version_ok: bool,
    fresh: bool,
    stale: bool,
    scope_ok: bool,
) -> str:
    if not version_ok:
        return CONCLUSION_VERSION_MISMATCH
    if any(row["status"] == ROW_MISMATCHED for row in rows):
        return CONCLUSION_MISMATCH
    if not scope_ok or stale or not fresh:
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

    obs_def = _optional_text(observation.get("definition_set_id"))
    art_def = _optional_text(artifact.get("definition_set_id"))
    definition_ok = not (obs_def and art_def and obs_def != art_def)
    obs_variant = _optional_text(observation.get("variant_id"))
    art_variant = _optional_text(artifact.get("variant_id"))
    variant_ok = not (obs_variant and art_variant and obs_variant != art_variant)
    scope_ok = definition_ok and variant_ok

    capture = _optional_text(observation.get("captured_at"))
    threshold_dt, threshold_source = _import_threshold(repo, artifact, pid, imported_after)
    capture_dt = _parse_time(capture, "captured_at")
    # 采集早于（或等于）导入时刻：这份观测看的是"导入之前"的机器状态
    fresh = True
    if capture_dt is None:
        fresh = False
    elif threshold_dt is not None and capture_dt <= threshold_dt:
        fresh = False
    stale = bool(observation.get("stale_capture"))

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
    obs_format = _optional_text(observation.get("format"))
    obs_role = _optional_text(observation.get("role"))
    cross_format = (
        _optional_text(artifact.get("format")) == "xml"
        and (obs_format == "json" or obs_role == config_catalog.ROLE_FIELD_JSON)
    )
    scope_complete = not unregistered and not cross_format
    conclusion = _conclusion(
        rows, version_ok=version_ok, fresh=fresh, stale=stale, scope_ok=scope_ok
    )
    reasons: List[str] = []
    if not version_ok:
        reasons.append("version_mismatch")
    if not scope_ok:
        reasons.append("scope_mismatch")
    if stale:
        reasons.append("stale_source")
    if capture_dt is None:
        reasons.append("missing_capture_time")
    elif not fresh:
        reasons.append("capture_before_import")
    if counts[ROW_MISSING]:
        reasons.append("missing")
    if counts[ROW_UNOBSERVABLE]:
        reasons.append("unobservable")
    if counts[ROW_MISMATCHED]:
        reasons.append("mismatched")

    warnings: List[str] = []
    if unregistered:
        warnings.append(
            "观测里有 %d 个节点没有登记成参数，未纳入本次核验（结论只覆盖已登记参数）"
            % len(unregistered)
        )
    if cross_format:
        warnings.append(
            "产物是 XML 模板，但观测是 JSON：XML 独有节点没有对应观测，"
            "本次不能宣称全量一致"
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
                "definition_set_id": art_def or None,
                "observation_definition_set_id": obs_def or None,
                "variant_id": art_variant or None,
                "observation_variant_id": obs_variant or None,
                "parameters_in_baseline": len(expected),
                "parameters_observed": sum(
                    1 for row in rows if row["status"] != ROW_MISSING
                ),
            },
            "capture_time": capture,
            "imported_after": (
                threshold_dt.isoformat().replace("+00:00", "Z")
                if threshold_dt is not None
                else None
            ),
            "imported_after_source": threshold_source,
            "fresh": fresh,
            "stale_capture": stale,
            "scope_cross_format": cross_format,
            "definition_set_ok": definition_ok,
            "variant_ok": variant_ok,
            "source_fingerprint": _optional_text(observation.get("blob_sha256")),
            "note": _optional_text(note),
            "created_at": now_iso(),
            "created_by": _label(actor),
        },
    )
    tx.commit()
    return get_verification(repo, str(ref.id))
