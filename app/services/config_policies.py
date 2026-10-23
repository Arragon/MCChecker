"""追踪策略与旧入口守卫（M6-05 / M6-06）。

两件事：

1. **追踪策略**（M6-05）：`TrackingPolicyRevision` 分两级——配置类别级与机型级，
   机型级可再按类别收窄。服务端统一算"有效策略"并回**继承来源**，前端不做二次推断。
2. **旧入口守卫**（M6-06）：`archive_only` 完全保持旧行为；`managed` 下旧入口
   （上传 / URL 导入 / 定时下载 / 旧审阅 / 删除）仍然可写，但**只能写进各自的
   受控通道**——现场 JSON → observation，XML 出口 → candidate，旧节点审阅 →
   change_source，改名删除 → reassociate。任何一条通道都**不可能**直接发布基线，
   这是 `may_publish_baseline()` 恒为 False 的结构性保证，不靠调用方自觉。

开关本身不改变历史：关掉 `managed` 只是停止"新入口的收口"，已产生的 observation /
基线 / 审计一条都不会被隐藏或删除（卡面验收第 1 条）。

门禁仍由 `config_gate` 决定：特性关闭时 `guard_legacy_write` 一律放行，旧功能零影响。
"""

from __future__ import annotations

import hashlib
import os
from typing import Any, Dict, List, Optional, Tuple

from ..core import device_models, storage
from ..core.config_repository import ConfigRepository, now_iso
from ..core.errors import (
    INVALID_INPUT,
    LIFECYCLE_BLOCKED,
    NOT_FOUND,
    POLICY_BLOCKED,
    REVISION_CONFLICT,
    AppError,
)
from . import config_catalog, config_gate

MODE_ARCHIVE_ONLY = "archive_only"
MODE_MANAGED = "managed"
MODES = (MODE_ARCHIVE_ONLY, MODE_MANAGED)

#: 没有显式策略时的默认档：与旧行为完全一致，升级不需要任何配置动作。
DEFAULT_MODE = MODE_ARCHIVE_ONLY

SCOPE_FAMILY = "family"
SCOPE_MODEL = "model"
SCOPES = (SCOPE_FAMILY, SCOPE_MODEL)

#: 旧入口的动作名（与卡面"五条旁路"一一对应）。
ACTION_UPLOAD = "upload"
ACTION_IMPORT = "import"
ACTION_SCHEDULE_DOWNLOAD = "schedule_download"
ACTION_REVIEW_SUBMIT = "review_submit"
ACTION_DELETE = "delete"
ACTION_RENAME = "rename"
LEGACY_ACTIONS = (
    ACTION_UPLOAD,
    ACTION_IMPORT,
    ACTION_SCHEDULE_DOWNLOAD,
    ACTION_REVIEW_SUBMIT,
    ACTION_DELETE,
    ACTION_RENAME,
)

#: 受控通道。旧入口永远不会产出 `baseline`。
ROUTE_LEGACY = "legacy"
ROUTE_OBSERVATION = "observation"
ROUTE_CANDIDATE = "candidate"
ROUTE_CHANGE_SOURCE = "change_source"
ROUTE_REASSOCIATE = "reassociate"
ROUTE_BLOCKED = "blocked"

#: 只有这些通道是"可以直接改动已批准基线"的——旧入口一个都不在其中。
BASELINE_PUBLISHING_ROUTES = frozenset(("baseline",))


def repository() -> ConfigRepository:
    """旧服务层没有 `Settings`，用同一数据根构造仓储（与 `config_store` 指向一致）。"""
    return ConfigRepository(storage.DATA_DIR)


def may_publish_baseline(route: str) -> bool:
    """旧入口给出的通道是否允许直接发布基线——**结构性恒假**。"""
    return str(route) in BASELINE_PUBLISHING_ROUTES


# -- 策略读写 ---------------------------------------------------------------


def list_policies(
    repo: ConfigRepository,
    *,
    scope: Optional[str] = None,
    family_id: Optional[str] = None,
    profile_id: Optional[str] = None,
) -> List[Dict[str, Any]]:
    items = repo.list_objects("tracking_policy")
    if scope is not None:
        items = [item for item in items if item.get("scope") == scope]
    if family_id is not None:
        items = [item for item in items if str(item.get("family_id") or "") == str(family_id)]
    if profile_id is not None:
        items = [item for item in items if str(item.get("profile_id") or "") == str(profile_id)]
    return sorted(
        items,
        key=lambda item: (
            item.get("scope") or "",
            str(item.get("family_id") or ""),
            str(item.get("profile_id") or ""),
        ),
    )


def _find_policy(
    repo: ConfigRepository,
    scope: str,
    family_id: Optional[str],
    profile_id: Optional[str],
) -> Optional[Dict[str, Any]]:
    for item in repo.list_objects("tracking_policy"):
        if item.get("scope") != scope:
            continue
        if str(item.get("family_id") or "") != str(family_id or ""):
            continue
        if str(item.get("profile_id") or "") != str(profile_id or ""):
            continue
        return item
    return None


def _sha256_file(path: str) -> Optional[str]:
    if not os.path.isfile(path):
        return None
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _drift_profiles(scope: str, profile_id: Optional[str]) -> List[str]:
    if scope == SCOPE_MODEL:
        return [str(profile_id)] if profile_id else []
    return [str(model.get("id")) for model in device_models.load_models()]


def _snapshot_scope(
    repo: ConfigRepository,
    *,
    scope: str,
    family_id: Optional[str],
    profile_id: Optional[str],
) -> Dict[str, Optional[str]]:
    """记录停用瞬间各机型当前配置文件的 sha256；缺失也记 None。"""
    files = config_catalog.list_logical_files(
        repo, family_id, include_retired=False
    )
    names = [str(item.get("display_name") or "") for item in files if item.get("display_name")]
    snapshot: Dict[str, Optional[str]] = {}
    for pid in _drift_profiles(scope, profile_id):
        with storage.use_profile(pid):
            for name in names:
                snapshot["%s|%s" % (pid, name)] = _sha256_file(storage.get_config_path(name))
    return snapshot


def _drift_changes(
    before: Dict[str, Optional[str]], after: Dict[str, Optional[str]]
) -> List[Dict[str, Any]]:
    changes = []
    for key in sorted(set(before) | set(after)):
        old = before.get(key)
        new = after.get(key)
        if old == new:
            continue
        pid, _, filename = key.partition("|")
        if old is None:
            state = "added"
        elif new is None:
            state = "missing"
        else:
            state = "changed"
        changes.append(
            {
                "profile_id": pid,
                "filename": filename,
                "before_sha256": old,
                "after_sha256": new,
                "state": state,
            }
        )
    return changes


def set_policy(
    repo: ConfigRepository,
    actor: Any,
    *,
    scope: str,
    mode: str,
    reason: Any,
    family_id: Optional[str] = None,
    profile_id: Optional[str] = None,
    notes: Any = "",
    expected_revision: Optional[str] = None,
    idempotency_key: Optional[str] = None,
    acknowledge_drift: bool = False,
) -> Dict[str, Any]:
    """设置（或改写）一条追踪策略。**同一目标追加修订，不新建对象**，因此历史可查。"""
    if scope not in SCOPES:
        raise AppError(INVALID_INPUT, "策略层级非法", field="scope", allowed=list(SCOPES))
    if mode not in MODES:
        raise AppError(INVALID_INPUT, "追踪档位非法", field="mode", allowed=list(MODES))
    reason_text = str(reason if reason is not None else "").strip()
    if not reason_text:
        raise AppError(INVALID_INPUT, "开关追踪策略必须填写理由", field="reason")

    family = None
    model_id = None
    if scope == SCOPE_FAMILY:
        if not str(family_id or "").strip():
            raise AppError(INVALID_INPUT, "类别级策略必须指定配置类别", field="family_id")
        family = config_catalog.get_family(repo, str(family_id))
    else:
        if not str(profile_id or "").strip():
            raise AppError(INVALID_INPUT, "机型级策略必须指定机型", field="profile_id")
        model_id = str(profile_id).strip()
        if device_models.is_retired(model_id):
            raise AppError(
                LIFECYCLE_BLOCKED,
                "机型已退役，不能修改追踪策略",
                profile_id=model_id,
                hint="先恢复机型，再调整策略",
            )
        if str(family_id or "").strip():
            family = config_catalog.get_family(repo, str(family_id))

    existing = _find_policy(
        repo, scope, family["id"] if family else None, model_id
    )
    if existing is not None and existing.get("mode") == mode:
        # 同值再提交：不产生新修订（避免审计里出现"改了但其实没改"）
        return existing

    stamp = now_iso()
    drift: List[Dict[str, Any]] = []
    disabled_snapshot = existing.get("disabled_snapshot") if existing else None
    if existing is not None and existing.get("mode") == MODE_MANAGED and mode == MODE_ARCHIVE_ONLY:
        # 停用瞬间留一份指纹；重开时用它判断"停用期间有没有人改过文件"。
        disabled_snapshot = _snapshot_scope(
            repo, scope=scope, family_id=family["id"] if family else None,
            profile_id=model_id,
        )
    elif existing is not None and existing.get("mode") == MODE_ARCHIVE_ONLY and mode == MODE_MANAGED:
        current = _snapshot_scope(
            repo, scope=scope, family_id=family["id"] if family else None,
            profile_id=model_id,
        )
        drift = _drift_changes(disabled_snapshot or {}, current)
        if drift and not acknowledge_drift:
            raise AppError(
                LIFECYCLE_BLOCKED,
                "停用期间配置文件发生变化，重新开启前必须确认漂移",
                field="acknowledge_drift",
                drift=drift,
                hint="逐项对账后再以 acknowledge_drift=true 重试",
            )
    payload = {
        "scope": scope,
        "family_id": family["id"] if family else None,
        "profile_id": model_id,
        "mode": mode,
        "notes": str(notes if notes is not None else "").strip(),
        "reason": reason_text,
        "created_by": _label(actor),
        "updated_at": stamp,
        "disabled_snapshot": disabled_snapshot,
        "disabled_at": existing.get("disabled_at") if existing else None,
        "reopened_at": stamp if drift or (
            existing is not None and existing.get("mode") == MODE_ARCHIVE_ONLY and mode == MODE_MANAGED
        ) else None,
        "drift": drift,
        "drift_acknowledged_at": stamp if drift and acknowledge_drift else None,
    }
    if existing is not None and existing.get("mode") == MODE_MANAGED and mode == MODE_ARCHIVE_ONLY:
        payload["disabled_at"] = stamp
        payload["reopened_at"] = None
    expected: Dict[str, Any] = {}
    if existing is not None:
        expected["tracking_policy:%s" % existing["id"]] = (
            str(expected_revision or "").strip() or existing.get("revision")
        )
        if expected_revision and existing.get("revision") != str(expected_revision).strip():
            raise AppError(
                REVISION_CONFLICT,
                "策略已被修改，请刷新后重试",
                expected_revision=expected_revision,
                actual_revision=existing.get("revision"),
                policy_id=existing["id"],
            )
    tx = repo.transaction(
        actor,
        reason_text,
        action="tracking_policy.set",
        idempotency_key=idempotency_key,
        expected=expected or None,
    )
    ref = tx.put(
        "tracking_policy",
        payload,
        object_id=existing["id"] if existing is not None else None,
    )
    tx.commit()
    return repo.get("tracking_policy", str(ref.id)) or {}


def _label(actor: Any) -> str:
    if actor is None:
        return ""
    if isinstance(actor, dict):
        return str(actor.get("name") or actor.get("ip") or "")
    return str(getattr(actor, "name", "") or getattr(actor, "ip", "") or "")


def effective_policy(
    repo: ConfigRepository,
    *,
    family_id: Optional[str] = None,
    profile_id: Optional[str] = None,
) -> Dict[str, Any]:
    """有效策略 + 继承链。优先级：机型-类别 > 机型 > 类别 > 默认。

    返回 `{mode, source, policy_id, chain: [{scope, target, mode, policy_id}], candidates}`，
    `chain` 按优先级从高到低列出**实际存在**的策略，前端据此显示"为什么是这个档"。
    """
    candidates: List[Dict[str, Any]] = []
    family_key = str(family_id or "")
    model_key = str(profile_id or "")
    if model_key and family_key:
        found = _find_policy(repo, SCOPE_MODEL, family_key, model_key)
        if found is not None:
            candidates.append(("model+family", found))
    if model_key:
        found = _find_policy(repo, SCOPE_MODEL, None, model_key)
        if found is not None:
            candidates.append(("model", found))
    if family_key:
        found = _find_policy(repo, SCOPE_FAMILY, family_key, None)
        if found is not None:
            candidates.append(("family", found))

    chain = [
        {
            "source": source,
            "scope": item.get("scope"),
            "family_id": item.get("family_id"),
            "profile_id": item.get("profile_id"),
            "mode": item.get("mode"),
            "policy_id": item.get("id"),
        }
        for source, item in candidates
    ]
    if candidates:
        source, chosen = candidates[0]
        return {
            "mode": chosen.get("mode"),
            "source": source,
            "policy_id": chosen.get("id"),
            "reason": chosen.get("reason") or "",
            "chain": chain,
        }
    return {
        "mode": DEFAULT_MODE,
        "source": "default",
        "policy_id": None,
        "reason": "未设置策略，沿用与旧版本一致的归档行为",
        "chain": chain,
    }


# -- 旧入口守卫 -------------------------------------------------------------


def classify_file(
    repo: ConfigRepository, filename: Any, *, role: Optional[str] = None
) -> Optional[Dict[str, Any]]:
    """按文件名在全域里找稳定 `LogicalFile`（跨类别，先精确后历史别名）。

    旧入口只知道"机型 + 文件名"，不知道配置类别；因此这里按名字回查，
    找到即带出它所属的类别——这就是"按稳定 LogicalFile/配置族判定"的落点。
    """
    target = str(filename if filename is not None else "").strip()
    if not target:
        return None
    families = {item["id"]: item for item in config_catalog.list_families(repo)}
    for family_id, family in sorted(families.items()):
        for candidate_role in ([role] if role else list(config_catalog.ROLES)):
            logical = config_catalog.resolve_logical_file(
                repo, family_id, candidate_role, target
            )
            if logical is not None:
                return {"family": family, "logical_file": logical, "role": logical.get("role")}
    return None


def _route_for(action: str, role: Optional[str]) -> str:
    if action in (ACTION_DELETE, ACTION_RENAME):
        return ROUTE_REASSOCIATE
    if action == ACTION_REVIEW_SUBMIT:
        return ROUTE_CHANGE_SOURCE
    if role == config_catalog.ROLE_FIELD_JSON:
        return ROUTE_OBSERVATION
    return ROUTE_CANDIDATE


def guard_legacy_write(
    repo: ConfigRepository,
    action: str,
    *,
    filename: Any,
    profile_id: Optional[str] = None,
    role: Optional[str] = None,
) -> Dict[str, Any]:
    """判定一次旧入口写入该怎么走；**从不**返回可发布基线的通道。

    * 特性关闭 → 一律 `legacy`（旧行为）；
    * `archive_only` → 一律 `legacy`（回归不变）；
    * `managed` → 按稳定逻辑文件的角色分流；未登记的文件直接 `blocked`，
      因为"不知道它是什么"就无从判断能不能落盘。
    """
    if not config_gate.enabled():
        return {
            "action": action,
            "mode": None,
            "allowed": True,
            "route": ROUTE_LEGACY,
            "reason": "配置管理特性未开启",
            "publish_baseline": False,
            "family_id": None,
            "logical_file_id": None,
            "policy": {"mode": None, "source": "disabled"},
        }

    classified = classify_file(repo, filename, role=role)
    family_id = classified["family"]["id"] if classified else None
    if classified is not None:
        policy = effective_policy(repo, family_id=family_id, profile_id=profile_id)
    else:
        # 未登记的文件不知道属于哪个类别，只能按"机型全域策略"判定：
        # 只有该机型被整体设为 managed 时才拦——否则一台机器装了受管类别，
        # 会把所有无关文件的上传一起挡掉，那不是本卡要的效果。
        policy = effective_policy(repo, family_id=None, profile_id=profile_id)
    if policy["mode"] == MODE_ARCHIVE_ONLY:
        return {
            "action": action,
            "mode": MODE_ARCHIVE_ONLY,
            "allowed": True,
            "route": ROUTE_LEGACY,
            "reason": "类别处于 archive_only，保持旧行为",
            "publish_baseline": False,
            "family_id": family_id,
            "logical_file_id": classified["logical_file"]["id"] if classified else None,
            "policy": policy,
        }

    if classified is None:
        return {
            "action": action,
            "mode": MODE_MANAGED,
            "allowed": False,
            "route": ROUTE_BLOCKED,
            "reason": "该机型处于受管模式，未登记的文件不能直接落盘",
            "hint": "先在参数登记里登记该文件的逻辑文件，或把机型/类别保持在 archive_only",
            "publish_baseline": False,
            "family_id": None,
            "logical_file_id": None,
            "policy": policy,
        }

    route = _route_for(action, classified["role"])
    return {
        "action": action,
        "mode": MODE_MANAGED,
        "allowed": True,
        "route": route,
        "reason": "受管模式：写入进入 %s 通道，需变更单批准后才会成为基线" % route,
        "publish_baseline": may_publish_baseline(route),
        "family_id": family_id,
        "logical_file_id": classified["logical_file"]["id"],
        "role": classified["role"],
        "policy": policy,
    }


def guard_or_raise(
    repo: ConfigRepository,
    action: str,
    *,
    filename: Any,
    profile_id: Optional[str] = None,
    role: Optional[str] = None,
) -> Dict[str, Any]:
    """守卫 + 抛错版本：被拦就抛 `POLICY_BLOCKED`（403），detail 里带完整判定。"""
    if action not in LEGACY_ACTIONS:
        raise ValueError("未登记的旧入口动作：%s" % action)
    report = guard_legacy_write(
        repo, action, filename=filename, profile_id=profile_id, role=role
    )
    if not report["allowed"]:
        raise AppError(
            POLICY_BLOCKED,
            report["reason"],
            action=action,
            mode=report["mode"],
            route=report["route"],
            filename=str(filename or ""),
            hint=report.get("hint"),
        )
    return report


def annotate(payload: Dict[str, Any], report: Dict[str, Any]) -> Dict[str, Any]:
    """把通道结论写进旧接口的响应体。

    特性未开启时**不加字段**——旧响应逐字节不变，前端与第三方脚本都零影响。
    只有真正处于 `archive_only` / `managed` 时才多出 `tracking`，调用方据此知道
    这次写入去了哪条通道。
    """
    mode = report.get("mode")
    if mode:
        payload["tracking"] = {"route": report.get("route"), "mode": mode}
    return payload


def tracking_overview(repo: ConfigRepository, profile_ids: Optional[List[str]] = None) -> Dict[str, Any]:
    """策略总览：所有显式策略 + 每个机型（可选列表）的有效档与来源。"""
    families = config_catalog.list_families(repo)
    policies = list_policies(repo)
    effective = []
    for family in families:
        effective.append(
            {
                "scope": "family",
                "family_id": family["id"],
                "family_name": family["name"],
                **effective_policy(repo, family_id=family["id"]),
            }
        )
    for pid in profile_ids or []:
        for family in families:
            effective.append(
                {
                    "scope": "model",
                    "profile_id": pid,
                    "family_id": family["id"],
                    "family_name": family["name"],
                    **effective_policy(repo, family_id=family["id"], profile_id=pid),
                }
            )
    counts: Dict[str, int] = {}
    for item in policies:
        counts[item.get("mode") or ""] = counts.get(item.get("mode") or "", 0) + 1
    return {
        "policies": policies,
        "effective": effective,
        "counts": counts,
        "default_mode": DEFAULT_MODE,
        "modes": list(MODES),
    }


def policy_history(repo: ConfigRepository, policy_id: str) -> List[str]:
    """某条策略的修订历史（`r1, r2…`）：关开关不会抹掉历史。"""
    payload = repo.get("tracking_policy", str(policy_id or ""))
    if payload is None:
        raise AppError(NOT_FOUND, "追踪策略不存在", policy_id=policy_id)
    return repo.history("tracking_policy", str(policy_id))
