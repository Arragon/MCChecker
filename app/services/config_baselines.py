"""基线的初始化与继承意图（M6-09 / INH-1954）。

三件事：

1. **通用基线**：从某个软件版本的**默认出口批次**（出厂文件）与冻结定义集合建草稿，
   每个值都记住它从哪个快照来（`source_ref`）。
2. **机型/设备基线**：从**精确指定**的通用基线派生。这里刻意不接受"最新通用基线"
   这种写法——上游必须是一个具体的基线 ID，否则"我到底基于哪版改的"就说不清，
   而这句话是后面所有三方合并的前提。
3. **继承意图**：`inherit` / `override` / `pin` 三种。`pin` 即使数值恰好等于默认值
   也要保留意图——那是"我明确锁死了它，别再跟着上游动"，不是"碰巧一样"。

两条硬规则：

* 已发布的基线**不会漂移**：新通用基线出现后，旧机型基线里的值一个字都不变
  （它存的是快照值，不是对上游的活引用）。
* 草稿可以残缺（缺定义、缺模板、缺上游都是常态），但 `readiness` 会把这些缺口逐条
  列出来并判 `publishable=false`——**能看，不能发**。
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any, Dict, Iterable, List, Optional, Tuple

from ..core import config_mapping, config_values
from ..core.config_repository import ConfigRepository, now_iso
from ..core.errors import (
    INVALID_INPUT,
    NOT_FOUND,
    REVISION_CONFLICT,
    AppError,
)
from . import config_catalog, config_ingest

SCOPE_COMMON = "common"
SCOPE_MODEL = "model"
SCOPE_DEVICE = "device"
SCOPES = (SCOPE_COMMON, SCOPE_MODEL, SCOPE_DEVICE)

STATE_DRAFT = "draft"
STATE_SUBMITTED = "submitted"
STATE_APPROVED = "approved"
STATE_REJECTED = "rejected"
STATE_WITHDRAWN = "withdrawn"
STATE_SUPERSEDED = "superseded"
STATES = (
    STATE_DRAFT,
    STATE_SUBMITTED,
    STATE_APPROVED,
    STATE_REJECTED,
    STATE_WITHDRAWN,
    STATE_SUPERSEDED,
)

INTENT_INHERIT = "inherit"
INTENT_OVERRIDE = "override"
INTENT_PIN = "pin"
INTENTS = (INTENT_INHERIT, INTENT_OVERRIDE, INTENT_PIN)

#: 草稿可编辑的状态。提交之后只能由批准/驳回改变。
EDITABLE_STATES = frozenset((STATE_DRAFT,))

#: 登记侧叫 `text`、值模型侧叫 `string`（与 `config_ingest` 同一处翻译）。
_TYPE_ALIASES = {"text": "string"}


# -- 小工具 -----------------------------------------------------------------


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


def _definition_set_id(baseline: Dict[str, Any]) -> Optional[str]:
    value = _optional_text(baseline.get("definition_set_id"))
    return value or None


# -- 读取 -------------------------------------------------------------------


def get_baseline(
    repo: ConfigRepository, baseline_id: str, revision: Optional[str] = None
) -> Dict[str, Any]:
    payload = repo.get("baseline", _optional_text(baseline_id), revision)
    if payload is None:
        raise AppError(NOT_FOUND, "基线不存在", baseline_id=baseline_id)
    return payload


def list_baselines(
    repo: ConfigRepository,
    *,
    scope: Optional[str] = None,
    profile_id: Optional[str] = None,
    device_id: Optional[str] = None,
    state: Optional[str] = None,
    release_id: Optional[str] = None,
) -> List[Dict[str, Any]]:
    items = repo.list_objects("baseline")
    if scope is not None:
        items = [i for i in items if i.get("scope") == scope]
    if profile_id is not None:
        items = [i for i in items if str(i.get("profile_id") or "") == str(profile_id)]
    if device_id is not None:
        items = [i for i in items if str(i.get("device_id") or "") == str(device_id)]
    if state is not None:
        items = [i for i in items if i.get("state") == state]
    if release_id is not None:
        items = [i for i in items if str(i.get("release_id") or "") == str(release_id)]
    return sorted(
        items,
        key=lambda i: (
            str(i.get("scope") or ""),
            str(i.get("profile_id") or ""),
            str(i.get("device_id") or ""),
            str(i.get("created_at") or ""),
            str(i.get("id") or ""),
        ),
    )


def lineage(repo: ConfigRepository, baseline: Dict[str, Any]) -> List[str]:
    """沿 `previous_revision_id` 往上走，返回从旧到新的 ID 链。"""
    chain: List[str] = []
    seen = set()
    current: Optional[Dict[str, Any]] = baseline
    while current is not None:
        object_id = str(current.get("id"))
        if object_id in seen:
            break
        seen.add(object_id)
        chain.append(object_id)
        previous = _optional_text(current.get("previous_revision_id"))
        current = repo.get("baseline", previous) if previous else None
    return list(reversed(chain))


def _head_of_scope(
    repo: ConfigRepository,
    scope: str,
    profile_id: Optional[str],
    device_id: Optional[str],
) -> Optional[Dict[str, Any]]:
    """同一作用域里最新的一条（用于串 `previous_revision_id`）。"""
    items = [
        item
        for item in list_baselines(repo, scope=scope, profile_id=profile_id)
        if str(item.get("device_id") or "") == str(device_id or "")
        and item.get("state") != STATE_SUPERSEDED
    ]
    return items[-1] if items else None


# -- 值规范化 ---------------------------------------------------------------


def _coerce(
    value: Any, definition: Optional[Dict[str, Any]], field: str
) -> Dict[str, Any]:
    """把入参值转成 `TypedValue` 的传输形态。

    接受两种写法：已经带 `type` 的对象，或裸文本/数字（此时按定义的类型解释）。
    裸浮点一律拒绝——精度问题不能等到比较的时候才发现。
    """
    if isinstance(value, dict):
        if "type" not in value:
            raise AppError(
                INVALID_INPUT, "%s 缺少 type" % field, field=field, value=value
            )
        kind = str(value.get("type") or "")
        if kind not in config_values.KINDS:
            raise AppError(
                INVALID_INPUT,
                "%s 的值类型非法" % field,
                field=field,
                allowed=list(config_values.KINDS),
            )
        return {
            "type": kind,
            "text": str(value.get("text") if value.get("text") is not None else ""),
            "unit": _optional_text(value.get("unit")) or None,
        }
    if isinstance(value, float):
        raise AppError(
            INVALID_INPUT, "%s 不接受浮点数，请按文本传" % field, field=field
        )
    if isinstance(value, bool):
        return {"type": "bool", "text": "true" if value else "false", "unit": None}
    if isinstance(value, int):
        return {"type": "int", "text": str(value), "unit": None}
    if isinstance(value, Decimal):
        return {"type": "decimal", "text": format(value, "f"), "unit": None}
    if value is None:
        return {"type": "null", "text": "", "unit": None}
    if isinstance(value, str):
        kind = str((definition or {}).get("type") or "text")
        kind = _TYPE_ALIASES.get(kind, kind)
        parsed = config_values.parse_with_unit(
            value, kind if kind in config_values.KINDS else "string",
            (definition or {}).get("unit") or None,
        )
        return parsed.to_json()
    raise AppError(INVALID_INPUT, "%s 的值类型不支持" % field, field=field)


def _normalize_values(
    repo: ConfigRepository,
    raw: Any,
    *,
    definition_set_id: Optional[str],
) -> Dict[str, Dict[str, Any]]:
    """把请求里的 `values` 规整成 `{parameter_id: {value, intent, source_ref}}`。"""
    if not raw:
        return {}
    if not isinstance(raw, dict):
        raise AppError(INVALID_INPUT, "values 必须是对象", field="values")
    definitions = _definitions_by_parameter(repo, definition_set_id)
    out: Dict[str, Dict[str, Any]] = {}
    for parameter_id, item in raw.items():
        pid = _text(parameter_id, "values 的键")
        config_catalog.get_parameter(repo, pid)
        if not isinstance(item, dict):
            raise AppError(
                INVALID_INPUT, "values 的每一项必须是对象", field="values", parameter_id=pid
            )
        intent = _optional_text(item.get("intent")) or INTENT_OVERRIDE
        if intent not in INTENTS:
            raise AppError(
                INVALID_INPUT,
                "继承意图非法",
                field="intent",
                parameter_id=pid,
                allowed=list(INTENTS),
            )
        if "value" not in item:
            raise AppError(
                INVALID_INPUT, "缺少 value", field="value", parameter_id=pid
            )
        out[pid] = {
            "value": _coerce(item.get("value"), definitions.get(pid), "value"),
            "intent": intent,
            "source_ref": item.get("source_ref") or {"kind": "manual", "id": None},
            "reason": _optional_text(item.get("reason")),
        }
    return out


def _definitions_by_parameter(
    repo: ConfigRepository, definition_set_id: Optional[str]
) -> Dict[str, Dict[str, Any]]:
    """定义集合 → `{parameter_id: definition}`；没有集合就退到当前生效定义。"""
    if definition_set_id:
        definition_set = config_catalog.get_definition_set(repo, definition_set_id)
        return {
            str(config_catalog.get_definition(repo, item)["parameter_id"]):
                config_catalog.get_definition(repo, item)
            for item in definition_set.get("definition_ids") or []
        }
    return {
        str(definition["parameter_id"]): definition
        for definition in (
            config_catalog.current_definition(repo, parameter["id"])
            for parameter in config_catalog.list_parameters(repo, include_retired=False)
        )
        if definition is not None
    }


# -- 播种：从默认出口批次 ---------------------------------------------------


def _seed_from_default_batch(
    repo: ConfigRepository,
    release_id: str,
    definition_set_id: Optional[str],
) -> Tuple[Dict[str, Dict[str, Any]], List[str]]:
    """按出厂文件播种通用基线；返回 `(values, warnings)`。"""
    batch = config_catalog.default_batch(repo, release_id)
    if batch is None:
        return {}, ["该软件版本还没有默认出口批次，通用基线只能手工填值"]
    warnings: List[str] = []
    values: Dict[str, Dict[str, Any]] = {}
    definitions = _definitions_by_parameter(repo, definition_set_id)
    for snapshot in config_catalog.batch_snapshots(repo, batch):
        logical_file = config_catalog.get_logical_file(
            repo, str(snapshot.get("logical_file_id"))
        )
        raw = repo.read_blob(str(snapshot.get("blob_sha256")))
        try:
            parsed = config_ingest.parse_document(
                raw, str(snapshot.get("original_filename") or "")
            )
        except AppError as exc:
            warnings.append(
                "%s 解析失败：%s" % (logical_file.get("display_name"), exc.message)
            )
            continue
        scoped = [
            definitions[pid]
            for pid in definitions
            if str(
                config_catalog.get_parameter(repo, pid).get("family_id")
            )
            == str(logical_file.get("family_id"))
        ]
        mapping = config_ingest.build_mapping(repo, logical_file, scoped, parsed)
        for entry in mapping["entries"]:
            parameter_id = entry.get("parameter_id")
            if not parameter_id or entry.get("status") != config_ingest.ENTRY_MAPPED:
                continue
            if parameter_id in values:
                # 同一个参数在两个出口文件里出现：先到先得，但要提示
                warnings.append(
                    "参数 %s 在多个出口文件里出现，已取第一个" % parameter_id
                )
                continue
            values[parameter_id] = {
                "value": entry["value_ref"],
                "intent": INTENT_INHERIT,
                "source_ref": {
                    "kind": "snapshot",
                    "id": snapshot.get("id"),
                    "sha256": snapshot.get("blob_sha256"),
                },
                "reason": "",
            }
    return values, warnings


def _seed_from_upstream(
    repo: ConfigRepository, upstream: Dict[str, Any]
) -> Dict[str, Dict[str, Any]]:
    """机型/设备基线默认继承上游全部值：意图 `inherit`，来源指向上游基线。"""
    values: Dict[str, Dict[str, Any]] = {}
    for parameter_id, item in (upstream.get("values") or {}).items():
        values[str(parameter_id)] = {
            "value": dict(item.get("value") or {}),
            "intent": INTENT_INHERIT,
            "source_ref": {"kind": "baseline", "id": upstream.get("id")},
            "reason": "",
        }
    return values


# -- 就绪度 -----------------------------------------------------------------


def readiness(repo: ConfigRepository, baseline: Dict[str, Any]) -> Dict[str, Any]:
    """能不能进入"可发布"：逐条给缺口，而不是一个布尔。"""
    blockers: List[Dict[str, str]] = []
    warnings: List[str] = []

    definition_set_id = _definition_set_id(baseline)
    definition_set: Optional[Dict[str, Any]] = None
    if not definition_set_id:
        blockers.append(
            {
                "code": "definition_set_missing",
                "message": "还没有选定定义集合，无法确定这批值对应哪些参数",
            }
        )
    else:
        try:
            definition_set = config_catalog.get_definition_set(repo, definition_set_id)
        except AppError:
            blockers.append(
                {
                    "code": "definition_set_missing",
                    "message": "定义集合不存在：%s" % definition_set_id,
                }
            )

    if definition_set is not None:
        if str(definition_set.get("release_id")) != str(baseline.get("release_id")):
            blockers.append(
                {
                    "code": "definition_set_mismatch",
                    "message": "定义集合属于其他软件版本",
                }
            )
        if _optional_text(definition_set.get("variant_id")) != _optional_text(
            baseline.get("variant_id")
        ):
            blockers.append(
                {"code": "variant_mismatch", "message": "定义集合属于其他模板变体"}
            )
        # 模板缺失：变体存在就必须有至少一个模板快照可回溯
        variant_id = _optional_text(baseline.get("variant_id"))
        if variant_id:
            variant = repo.get("template_variant", variant_id)
            if variant is None:
                blockers.append(
                    {"code": "template_missing", "message": "模板变体不存在"}
                )
            elif not _optional_text(variant.get("template_snapshot_id")):
                blockers.append(
                    {
                        "code": "template_missing",
                        "message": "该变体还没有登记模板快照",
                    }
                )

    values = baseline.get("values") or {}
    if not values:
        blockers.append({"code": "values_empty", "message": "基线还没有任何值"})

    known = _definitions_by_parameter(repo, definition_set_id)
    for parameter_id in sorted(values):
        if parameter_id not in known:
            blockers.append(
                {
                    "code": "definition_missing",
                    "message": "参数缺少定义修订：%s" % parameter_id,
                    "parameter_id": parameter_id,
                }
            )

    upstream_id = _optional_text(baseline.get("upstream_ref"))
    if baseline.get("scope") != SCOPE_COMMON:
        if not upstream_id:
            blockers.append(
                {"code": "upstream_missing", "message": "机型/设备基线必须指定上游通用基线"}
            )
        else:
            upstream = repo.get("baseline", upstream_id)
            if upstream is None:
                blockers.append(
                    {
                        "code": "upstream_missing",
                        "message": "引用的上游基线不存在：%s" % upstream_id,
                    }
                )
            elif upstream.get("state") != STATE_APPROVED:
                blockers.append(
                    {
                        "code": "upstream_not_approved",
                        "message": "上游基线尚未批准（当前 %s）" % upstream.get("state"),
                    }
                )
            elif upstream.get("scope") != SCOPE_COMMON:
                warnings.append("上游基线的 scope 不是 common，请确认这是有意为之")

    for parameter_id, item in values.items():
        if item.get("intent") == INTENT_PIN and not (item.get("value") or {}).get("type"):
            blockers.append(
                {
                    "code": "pin_without_value",
                    "message": "锁定意图必须带一个具体值：%s" % parameter_id,
                    "parameter_id": parameter_id,
                }
            )

    return {
        "publishable": not blockers,
        "blockers": blockers,
        "warnings": warnings,
    }


# -- 写入 -------------------------------------------------------------------


def create_baseline(
    repo: ConfigRepository,
    actor: Any,
    *,
    scope: Any,
    release_id: Any,
    profile_id: Any = None,
    device_id: Any = None,
    definition_set_id: Any = None,
    variant_id: Any = None,
    upstream_baseline_id: Any = None,
    values: Any = None,
    reason: Any = "",
    idempotency_key: Optional[str] = None,
) -> Dict[str, Any]:
    """建一条基线草稿。"""
    target_scope = _text(scope, "scope")
    if target_scope not in SCOPES:
        raise AppError(
            INVALID_INPUT, "scope 非法", field="scope", allowed=list(SCOPES)
        )
    release = config_catalog.get_release(repo, _text(release_id, "release_id"))
    pid = _optional_text(profile_id) or None
    did = _optional_text(device_id) or None
    if target_scope == SCOPE_COMMON:
        if pid or did:
            raise AppError(
                INVALID_INPUT, "通用基线不属于任何机型/设备", field="profile_id"
            )
    else:
        if not pid:
            raise AppError(
                INVALID_INPUT, "机型/设备基线必须指定 profile_id", field="profile_id"
            )
        if target_scope == SCOPE_DEVICE and not did:
            raise AppError(
                INVALID_INPUT, "设备基线必须指定 device_id", field="device_id"
            )
        if target_scope == SCOPE_MODEL and did:
            raise AppError(
                INVALID_INPUT, "机型基线不挂设备", field="device_id"
            )

    variant = _optional_text(variant_id) or None
    set_id = _optional_text(definition_set_id) or None
    if set_id is None:
        latest = config_catalog.latest_definition_set(repo, release["id"], variant)
        set_id = latest["id"] if latest is not None else None
    if set_id is not None:
        # 变体以**定义集合**为准：调用方不传变体时，集合决定了这份值属于哪个模板，
        # 否则会出现"值来自 A 模板、基线却说没指定变体"的半吊子状态。
        chosen = config_catalog.get_definition_set(repo, set_id)
        chosen_variant = _optional_text(chosen.get("variant_id")) or None
        if variant and chosen_variant and variant != chosen_variant:
            raise AppError(
                INVALID_INPUT,
                "定义集合属于其他模板变体",
                field="variant_id",
                expected=variant,
                actual=chosen_variant,
            )
        variant = chosen_variant

    upstream: Optional[Dict[str, Any]] = None
    if target_scope == SCOPE_COMMON:
        if _optional_text(upstream_baseline_id):
            raise AppError(
                INVALID_INPUT, "通用基线没有上游", field="upstream_baseline_id"
            )
    else:
        # 刻意不接受"最新通用基线"：父修订必须点名，否则合并时说不清基于哪版
        upstream_id = _optional_text(upstream_baseline_id)
        if not upstream_id:
            raise AppError(
                INVALID_INPUT,
                "必须显式指定上游基线 ID（不接受“用最新的那个”）",
                field="upstream_baseline_id",
            )
        upstream = get_baseline(repo, upstream_id)

    warnings: List[str] = []
    seeded: Dict[str, Dict[str, Any]] = {}
    if upstream is not None:
        seeded = _seed_from_upstream(repo, upstream)
    elif target_scope == SCOPE_COMMON:
        seeded, warnings = _seed_from_default_batch(repo, release["id"], set_id)

    explicit = _normalize_values(repo, values, definition_set_id=set_id)
    for parameter_id, item in explicit.items():
        merged = dict(seeded.get(parameter_id) or {})
        merged.update(item)
        # 显式给的值至少要标成 override；调用方自己写 pin 则保留 pin
        if merged.get("intent") == INTENT_INHERIT:
            merged["intent"] = INTENT_OVERRIDE
        seeded[parameter_id] = merged

    if not seeded:
        warnings.append("草稿里还没有任何值")

    previous = _head_of_scope(repo, target_scope, pid, did)
    stamp = now_iso()
    tx = repo.transaction(
        actor,
        _optional_text(reason) or "初始化%s基线 %s" % (target_scope, release.get("version_label")),
        action="baseline.create",
        idempotency_key=idempotency_key,
    )
    ref = tx.put(
        "baseline",
        {
            "scope": target_scope,
            "profile_id": pid,
            "device_id": did,
            "release_id": release["id"],
            "definition_set_id": set_id,
            "variant_id": variant,
            "values": seeded,
            "upstream_ref": upstream["id"] if upstream is not None else None,
            "previous_revision_id": previous["id"] if previous is not None else None,
            "state": STATE_DRAFT,
            "reason": _optional_text(reason),
            "created_by": _label(actor),
            "created_at": stamp,
            "decided_at": None,
            "decided_by": None,
            "decide_note": None,
            "seed_warnings": warnings,
        },
    )
    tx.commit()
    return get_baseline(repo, str(ref.id))


def update_values(
    repo: ConfigRepository,
    actor: Any,
    *,
    baseline_id: Any,
    values: Any,
    expected_revision: Any = None,
    reason: Any = "",
    idempotency_key: Optional[str] = None,
) -> Dict[str, Any]:
    """改草稿里的值：**追加修订**，已提交/已批准的基线一个字都不能动。"""
    object_id = _text(baseline_id, "baseline_id")
    current = get_baseline(repo, object_id)
    if current.get("state") not in EDITABLE_STATES:
        raise AppError(
            INVALID_INPUT,
            "只有草稿可以改值（当前 %s）" % current.get("state"),
            field="state",
            state=current.get("state"),
        )
    head = repo.head_revision("baseline", object_id)
    wanted = _optional_text(expected_revision)
    if wanted and wanted != head:
        raise AppError(
            REVISION_CONFLICT,
            "基线已被改动，请刷新后重试",
            expected_revision=wanted,
            actual_revision=head,
        )
    definition_set_id = _definition_set_id(current)
    patch = _normalize_values(repo, values, definition_set_id=definition_set_id)
    if not patch:
        raise AppError(INVALID_INPUT, "values 不能为空", field="values")

    merged = dict(current.get("values") or {})
    for parameter_id, item in patch.items():
        merged[parameter_id] = item
    payload = dict(current)
    payload.pop("id", None)
    payload.update(
        {
            "values": merged,
            "reason": _optional_text(reason) or current.get("reason") or "",
            "updated_at": now_iso(),
            "updated_by": _label(actor),
        }
    )
    tx = repo.transaction(
        actor,
        _optional_text(reason) or "修改基线草稿 %s" % object_id,
        action="baseline.update",
        expected={"baseline:%s" % object_id: head},
        idempotency_key=idempotency_key,
    )
    ref = tx.put("baseline", payload, object_id=object_id)
    tx.commit()
    return get_baseline(repo, str(ref.id), ref.revision)


def set_intent(
    repo: ConfigRepository,
    actor: Any,
    *,
    baseline_id: Any,
    parameter_id: Any,
    intent: Any,
    expected_revision: Any = None,
) -> Dict[str, Any]:
    """只改意图（不改数值）：`pin` 与"值恰好一样"是两件事，必须能单独表达。"""
    object_id = _text(baseline_id, "baseline_id")
    pid = _text(parameter_id, "parameter_id")
    wanted_intent = _text(intent, "intent")
    if wanted_intent not in INTENTS:
        raise AppError(
            INVALID_INPUT, "继承意图非法", field="intent", allowed=list(INTENTS)
        )
    current = get_baseline(repo, object_id)
    values = dict(current.get("values") or {})
    if pid not in values:
        raise AppError(NOT_FOUND, "基线里没有这个参数", parameter_id=pid)
    item = dict(values[pid])
    item["intent"] = wanted_intent
    values[pid] = item
    return update_values(
        repo,
        actor,
        baseline_id=object_id,
        values={pid: {"value": item["value"], "intent": wanted_intent,
                      "source_ref": item.get("source_ref")}},
        expected_revision=expected_revision or repo.head_revision("baseline", object_id),
        reason="设置继承意图为 %s" % wanted_intent,
    )


def detail(repo: ConfigRepository, baseline_id: str) -> Dict[str, Any]:
    """基线 + 展开后的参数信息 + 就绪度（前端一次拿全）。"""
    baseline = get_baseline(repo, baseline_id)
    parameters = {item["id"]: item for item in config_catalog.list_parameters(repo)}
    rows = []
    for parameter_id, item in (baseline.get("values") or {}).items():
        parameter = parameters.get(parameter_id) or {}
        rows.append(
            {
                "parameter_id": parameter_id,
                "name": parameter.get("name") or "",
                "category": parameter.get("category") or "",
                "unit": (item.get("value") or {}).get("unit") or "",
                "value": item.get("value"),
                "intent": item.get("intent"),
                "source_ref": item.get("source_ref"),
                "known": bool(parameter),
            }
        )
    rows.sort(key=lambda row: (row["category"], row["name"], row["parameter_id"]))
    return {
        "item": baseline,
        "rows": rows,
        "readiness": readiness(repo, baseline),
        "lineage": lineage(repo, baseline),
        "revisions": repo.history("baseline", baseline_id),
    }
