"""交付记录：四态分离（M7-09 / INH-1968）。

"下发了"和"打进去了"是两件事。这个模块只做**人工登记**——不接自动控制器、不从
下载动作推断、不因为文件被下载过就把状态改成"已导入"。

四种状态（`result`）：

| 值 | 含义 | 谁能得出 |
|---|---|---|
| `pending` | 还没人登记过 | **推导**：产物没有记录时即此值，不落库 |
| `declared_ok` | 现场声明导入成功 | 人工登记，必须带证据 |
| `failed` | 导入失败 | 人工登记，必须写失败原因 |
| `insufficient_evidence` | 说不清 | 人工登记，必须写缺什么证据 |

三条硬规矩：

1. **下载不自动标"已导入"**：读路径一行写操作都没有（`test_download_does_not_claim`）。
2. **失败/重试逐次留记录**：一条记录一个对象，`attempt` 递增，`previous_delivery_id`
   指向上一次。撤回不是删除，是新增一条（历史必须能回答"试过几次、每次怎么挂的"）。
3. **缺设备身份不得伪造"所有设备已完成"**：设备层关闭（M7-07/08 延期）时
   `device_id` 直接拒收，`summary.can_claim_all_devices` 恒 `False`，
   措辞固定为"只能就登记过的对象给结论"。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from ..core.config_repository import ConfigRepository, now_iso
from ..core.errors import INVALID_INPUT, NOT_FOUND, AppError
from ..core.locks import management_lock
from . import config_artifacts, config_device_layer

RESULT_PENDING = "pending"
RESULT_DECLARED_OK = "declared_ok"
RESULT_FAILED = "failed"
RESULT_INSUFFICIENT = "insufficient_evidence"

#: 能落库的状态。`pending` 不在此列——它不是"登记出来的"，是"还没登记"。
STORED_RESULTS = (RESULT_DECLARED_OK, RESULT_FAILED, RESULT_INSUFFICIENT)

#: 四态的中文说法。前端直接用，不各自造词。
RESULT_LABELS = {
    RESULT_PENDING: "待导入",
    RESULT_DECLARED_OK: "已声明成功",
    RESULT_FAILED: "失败",
    RESULT_INSUFFICIENT: "证据不足",
}

#: 登记"成功"却拿不出证据时给的话。不要含糊成"证据可选"。
_EVIDENCE_HINT = "没有证据就登记为「证据不足」，不要登记成功"

#: 设备层关闭时的固定措辞（`summary` 与接口错误共用一份）。
_NO_DEVICE_CLAIM = (
    "设备层未启用（M7-07/08 按计划延期）：只能就登记过的机型给出结论，"
    "不能断言「所有设备已完成」"
)


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


def _evidence(raw: Any) -> Dict[str, Any]:
    """证据三件套：说明、引用（照片/日志/截图）、哈希。空字符串一律归 None。"""
    if raw is None:
        return {"note": "", "ref": None, "photo_ref": None, "sha256": None}
    if not isinstance(raw, dict):
        raise AppError(INVALID_INPUT, "evidence 必须是对象", field="evidence")
    ref = _optional_text(raw.get("ref")) or _optional_text(raw.get("photo_ref")) or None
    return {
        "note": _optional_text(raw.get("note")),
        "ref": ref,
        # 兼容契约里的旧字段名：样例载荷用 `photo_ref`
        "photo_ref": ref,
        "sha256": _optional_text(raw.get("sha256")) or None,
    }


def _has_evidence(evidence: Dict[str, Any]) -> bool:
    return bool(evidence.get("ref") or evidence.get("sha256") or evidence.get("note"))


def get_delivery(repo: ConfigRepository, delivery_id: Any) -> Dict[str, Any]:
    did = _text(delivery_id, "delivery_id")
    payload = repo.get("delivery", did)
    if payload is None:
        raise AppError(NOT_FOUND, "交付记录不存在", delivery_id=did)
    return payload


def list_deliveries(
    repo: ConfigRepository,
    *,
    artifact_id: Optional[str] = None,
    profile_id: Optional[str] = None,
    result: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """按产物/机型/状态过滤，新的在前（`created_at` 相同则按 attempt 降序）。"""
    wanted_artifact = _optional_text(artifact_id)
    wanted_profile = _optional_text(profile_id)
    wanted_result = _optional_text(result)
    items = []
    for item in repo.list_objects("delivery"):
        if wanted_artifact and str(item.get("artifact_id") or "") != wanted_artifact:
            continue
        target = item.get("target") or {}
        if wanted_profile and str(target.get("profile_id") or "") != wanted_profile:
            continue
        if wanted_result and str(item.get("result") or "") != wanted_result:
            continue
        items.append(item)
    items.sort(
        key=lambda row: (
            str(row.get("created_at") or ""),
            int(row.get("attempt") or 0),
            str(row.get("id") or ""),
        ),
        reverse=True,
    )
    return items


def attempt_of(repo: ConfigRepository, *, artifact_id: str, profile_id: str, device_id: str = "") -> int:
    """同产物 + 同目标的下一次尝试序号（从 1 起）。"""
    same = [
        item
        for item in list_deliveries(repo, artifact_id=artifact_id, profile_id=profile_id)
        if _optional_text((item.get("target") or {}).get("device_id")) == _optional_text(device_id)
    ]
    return len(same) + 1


def register(
    repo: ConfigRepository,
    actor: Any,
    *,
    artifact_id: Any,
    result: Any,
    profile_id: Any = None,
    device_id: Any = None,
    executed_at: Any = None,
    executed_by: Any = "",
    evidence: Any = None,
    note: Any = "",
    failure_reason: Any = "",
    missing_evidence: Any = None,
    idempotency_key: Optional[str] = None,
) -> Dict[str, Any]:
    """登记一次导入结果。**新增对象**，不修改任何已有记录。"""
    aid = _text(artifact_id, "artifact_id")
    artifact = config_artifacts.get_artifact(repo, aid)
    chosen = _optional_text(result)
    if chosen not in STORED_RESULTS:
        raise AppError(
            INVALID_INPUT,
            "交付状态非法（`pending` 是推导出来的，不能登记）",
            field="result",
            allowed=list(STORED_RESULTS),
        )

    did = _optional_text(device_id)
    if did:
        # 设备层关闭时，任何 device_id 都不认：认了就等于凭空造设备身份
        config_device_layer.require_enabled(field="device_id", device_id=did)

    owner = _optional_text(artifact.get("profile_id"))
    pid = _optional_text(profile_id) or owner
    if owner and pid and owner != pid:
        raise AppError(NOT_FOUND, "产物不属于该机型", artifact_id=aid, profile_id=pid)
    if not pid:
        raise AppError(INVALID_INPUT, "profile_id 不能为空", field="profile_id")

    stamp = _optional_text(executed_at) if result else ""
    if chosen in (RESULT_DECLARED_OK, RESULT_FAILED) and not stamp:
        raise AppError(
            INVALID_INPUT,
            "已声明成功/失败必须给出实际执行时间",
            field="executed_at",
        )
    stamp = stamp or now_iso()

    proof = _evidence(evidence)
    if chosen == RESULT_DECLARED_OK and not _has_evidence(proof):
        raise AppError(
            INVALID_INPUT, "登记成功但没有任何证据", field="evidence", hint=_EVIDENCE_HINT
        )
    artifact_sha = _optional_text(artifact.get("blob_sha256"))
    if proof.get("sha256") and artifact_sha and proof["sha256"] != artifact_sha:
        raise AppError(
            INVALID_INPUT,
            "证据哈希与产物哈希不一致",
            field="evidence.sha256",
            artifact_sha256=artifact_sha,
            evidence_sha256=proof["sha256"],
        )
    reason = _optional_text(failure_reason)
    if chosen == RESULT_FAILED and not reason:
        raise AppError(INVALID_INPUT, "登记失败必须写失败原因", field="failure_reason")
    missing = missing_evidence
    if isinstance(missing, str):
        missing = [missing] if missing.strip() else []
    if chosen == RESULT_INSUFFICIENT and not missing:
        raise AppError(
            INVALID_INPUT, "登记「证据不足」必须写清缺什么", field="missing_evidence"
        )

    # 序号与"上一次"是同一条链上的派生值：必须在同一把管理锁内读取并提交，
    # 否则两个并发登记会拿到同一个 attempt。
    with management_lock:
        attempt = attempt_of(repo, artifact_id=aid, profile_id=pid, device_id=did)
        previous = [
            item
            for item in list_deliveries(repo, artifact_id=aid, profile_id=pid)
            if _optional_text((item.get("target") or {}).get("device_id")) == did
        ]

        # 重试时 attempt 会变，不能写进事务理由，否则同一幂等键会算出不同请求指纹。
        tx = repo.transaction(
            actor,
            "登记交付结果 %s（%s）" % (aid, RESULT_LABELS[chosen]),
            action="delivery.register",
            idempotency_key=idempotency_key,
        )
        ref = tx.put(
            "delivery",
            {
                "artifact_id": aid,
                "artifact_sha256": artifact_sha,
                "baseline_id": _optional_text(artifact.get("baseline_id")),
                "baseline_revision": _optional_text(artifact.get("baseline_revision")),
                "target": {"profile_id": pid, "device_id": did or None},
                "result": chosen,
                "result_label": RESULT_LABELS[chosen],
                "registered_by": _label(actor),
                "registered_at": now_iso(),
                "executed_at": stamp,
                "executed_by": _optional_text(executed_by),
                "evidence": proof,
                "note": _optional_text(note),
                "failure_reason": reason or None,
                "missing_evidence": list(missing or []),
                "attempt": attempt,
                "previous_delivery_id": previous[0]["id"] if previous else None,
            },
        )
        tx.commit()
    return get_delivery(repo, str(ref.id))


def effective_result(repo: ConfigRepository, artifact_id: Any) -> Dict[str, Any]:
    """产物当前的四态：没有记录 → `pending`；有记录 → 最近一次的结果。"""
    aid = _text(artifact_id, "artifact_id")
    config_artifacts.get_artifact(repo, aid)
    items = list_deliveries(repo, artifact_id=aid)
    if not items:
        return {
            "artifact_id": aid,
            "result": RESULT_PENDING,
            "label": RESULT_LABELS[RESULT_PENDING],
            "attempts": 0,
            "delivery_id": None,
            "executed_at": None,
        }
    latest = items[0]
    return {
        "artifact_id": aid,
        "result": latest.get("result"),
        "label": RESULT_LABELS.get(str(latest.get("result")), ""),
        "attempts": len(items),
        "delivery_id": latest.get("id"),
        "executed_at": latest.get("executed_at"),
    }


def summary(
    repo: ConfigRepository, *, artifact_id: Any, profile_id: Optional[str] = None
) -> Dict[str, Any]:
    """逐态计数 + 一句"能声称什么、不能声称什么"。

    `profile_id` 给定时只统计该机型的登记：通用产物本身是全局对象，但"打在谁身上"
    是机型级事实，不能拿 A 机型的成功记录替 B 机型背书。

    `can_claim_all_devices` 恒为 `False`：没有设备目录就没有"所有设备"这个集合，
    哪怕登记了一百条成功记录，也只能说"登记过的这些机型/设备成功了"。
    """
    aid = _text(artifact_id, "artifact_id")
    config_artifacts.get_artifact(repo, aid)
    items = list_deliveries(repo, artifact_id=aid, profile_id=profile_id)
    counts = {key: 0 for key in STORED_RESULTS}
    for item in items:
        result = str(item.get("result") or "")
        if result in counts:
            counts[result] += 1
    latest = items[0] if items else None
    return {
        "artifact_id": aid,
        "result": latest.get("result") if latest else RESULT_PENDING,
        "label": RESULT_LABELS[latest.get("result") if latest else RESULT_PENDING],
        "counts": counts,
        "total": len(items),
        "profiles": sorted({str((item.get("target") or {}).get("profile_id") or "") for item in items}),
        "devices_known": False,
        "can_claim_all_devices": False,
        "note": _NO_DEVICE_CLAIM,
    }


def history(repo: ConfigRepository, *, artifact_id: Any) -> List[Dict[str, Any]]:
    """同产物的全部登记（含失败与重试），新的在前。撤回/重试都在这条链上。"""
    return [
        {
            "delivery_id": item.get("id"),
            "result": item.get("result"),
            "label": RESULT_LABELS.get(str(item.get("result")), ""),
            "attempt": item.get("attempt"),
            "executed_at": item.get("executed_at"),
            "registered_by": item.get("registered_by"),
            "failure_reason": item.get("failure_reason"),
            "previous_delivery_id": item.get("previous_delivery_id"),
        }
        for item in list_deliveries(repo, artifact_id=_text(artifact_id, "artifact_id"))
    ]
