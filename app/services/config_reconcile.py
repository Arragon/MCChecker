"""文件内现场对账（M6-10 / INH-1955 的服务端支撑）。

一张表三栏并列：**现场原值** / **批准基线** / **拟值**。服务端只负责把三栏摆好、
把差异算清楚，并把"这次用的是哪条观测、哪条基线"写进响应——前端不做二次推断，
也不允许把整份文件塞进 localStorage 当权威。

四条差异状态：

* `same` —— 现场与基线一致（含"都是 missing"）；
* `changed` —— 现场有值、基线有值、但不同；
* `missing_observed` —— 基线有、现场没采到（`missing` 不等于"要删掉"）；
* `missing_baseline` —— 现场有、基线没有（新参数或早期基线没覆盖）；
* `unknown` —— 现场那条是 `unmapped`/`ambiguous`/`invalid` 或整份没解析成功。

**拟值**只是服务端的建议（"疑似要改成现场这个值"），不是提交内容：真正提交要走
变更单（M6-11），那里才有决定、理由与批准。把建议当成决定是这类工具最常见的错。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from ..core import config_values
from ..core.config_repository import ConfigRepository
from ..core.errors import INVALID_INPUT, NOT_FOUND, AppError
from . import config_baselines, config_catalog, config_ingest, config_policies

DIFF_SAME = "same"
DIFF_CHANGED = "changed"
DIFF_MISSING_OBSERVED = "missing_observed"
DIFF_MISSING_BASELINE = "missing_baseline"
DIFF_MISSING_BOTH = "missing_both"
DIFF_UNKNOWN = "unknown"

#: 观测侧不是"确定的值"的状态：这些情况下不给拟值建议。
_UNCERTAIN = frozenset(
    (
        config_ingest.ENTRY_UNMAPPED,
        config_ingest.ENTRY_AMBIGUOUS,
        config_ingest.ENTRY_INVALID,
    )
)

PROPOSED_FROM_OBSERVED = "observed"
PROPOSED_FROM_BASELINE = "baseline"
PROPOSED_NONE = "none"


def _optional_text(value: Any) -> str:
    return str(value if value is not None else "").strip()


def pick_observation(
    repo: ConfigRepository,
    profile_id: str,
    logical_file_id: str,
    observation_id: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """选一条观测：显式给的优先，否则用该逻辑文件的"当前观测"。"""
    if observation_id:
        payload = config_ingest.get_observation(repo, observation_id)
        if str(payload.get("profile_id")) != str(profile_id):
            raise AppError(
                INVALID_INPUT, "观测不属于该机型", field="observation_id"
            )
        return payload
    return config_ingest.current_observation(repo, profile_id, logical_file_id)


def pick_baseline(
    repo: ConfigRepository,
    profile_id: str,
    baseline_id: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """选一条基线：显式给的优先，否则机型级 approved 优先，其次通用 approved。"""
    if baseline_id:
        payload = config_baselines.get_baseline(repo, baseline_id)
        if payload.get("state") != config_baselines.STATE_APPROVED:
            raise AppError(
                INVALID_INPUT,
                "只有已批准的基线可以作为对账基准",
                field="baseline_id",
                state=payload.get("state"),
            )
        return payload
    models = [
        item
        for item in config_baselines.list_baselines(
            repo,
            scope=config_baselines.SCOPE_MODEL,
            profile_id=profile_id,
            state=config_baselines.STATE_APPROVED,
        )
    ]
    if models:
        return models[-1]
    commons = config_baselines.list_baselines(
        repo,
        scope=config_baselines.SCOPE_COMMON,
        state=config_baselines.STATE_APPROVED,
    )
    return commons[-1] if commons else None


def _resolve_logical_file(
    repo: ConfigRepository,
    logical_file_id: Optional[str],
    filename: Optional[str],
) -> Dict[str, Any]:
    if _optional_text(logical_file_id):
        return config_catalog.get_logical_file(repo, _optional_text(logical_file_id))
    classified = config_policies.classify_file(repo, filename)
    if classified is None:
        raise AppError(
            NOT_FOUND,
            "该文件还没有登记为逻辑文件，无法对账",
            filename=_optional_text(filename),
            hint="先在配置类别里登记这个文件的逻辑文件（含 role）",
        )
    return classified["logical_file"]


def _entry_index(observation: Optional[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    """观测的 entries 按参数索引（同名多参数取最后一条，前面已被标 ambiguous）。"""
    index: Dict[str, Dict[str, Any]] = {}
    if not observation:
        return index
    for entry in (observation.get("mapping") or {}).get("entries") or []:
        parameter_id = _optional_text(entry.get("parameter_id"))
        if parameter_id:
            index[parameter_id] = entry
    return index


def _diff(observed: Optional[Dict[str, Any]], baseline_value: Optional[Dict[str, Any]]) -> str:
    if observed is None or observed.get("status") == config_ingest.ENTRY_MISSING:
        # 两边都没有值时不叫"一致"：那是"谁都没说"，界面上要能看出这个区别
        return DIFF_MISSING_OBSERVED if baseline_value else DIFF_MISSING_BOTH
    if observed.get("status") in _UNCERTAIN:
        return DIFF_UNKNOWN
    if not baseline_value:
        return DIFF_MISSING_BASELINE
    same = (
        config_values.compare(
            config_values.TypedValue.from_json(observed["value_ref"]),
            config_values.TypedValue.from_json(baseline_value),
        )
        == "equal"
    )
    return DIFF_SAME if same else DIFF_CHANGED


def reconcile(
    repo: ConfigRepository,
    *,
    profile_id: str,
    logical_file_id: Optional[str] = None,
    filename: Optional[str] = None,
    baseline_id: Optional[str] = None,
    observation_id: Optional[str] = None,
) -> Dict[str, Any]:
    """把三栏摆好，返回行 + 选择依据 + 过期提醒。"""
    pid = _optional_text(profile_id)
    if not pid:
        raise AppError(INVALID_INPUT, "缺少 profile_id", field="profile_id")
    logical_file = _resolve_logical_file(repo, logical_file_id, filename)
    observation = pick_observation(repo, pid, logical_file["id"], observation_id)
    baseline = pick_baseline(repo, pid, baseline_id)

    observed_index = _entry_index(observation)
    baseline_values = dict((baseline or {}).get("values") or {})

    # 参数集合取三者并集：基线里有的（可能要标 missing_observed）、观测里有的
    # （新参数）、以及该类别下当前登记的（没采到也没基线，但仍要显示出来）。
    parameters = {
        item["id"]: item
        for item in config_catalog.list_parameters(
            repo, str(logical_file.get("family_id"))
        )
    }
    parameter_ids = set(parameters) | set(baseline_values) | set(observed_index)

    rows: List[Dict[str, Any]] = []
    counts: Dict[str, int] = {
        key: 0
        for key in (
            DIFF_SAME,
            DIFF_CHANGED,
            DIFF_MISSING_OBSERVED,
            DIFF_MISSING_BASELINE,
            DIFF_MISSING_BOTH,
            DIFF_UNKNOWN,
        )
    }
    for parameter_id in sorted(parameter_ids):
        parameter = parameters.get(parameter_id) or {}
        observed = observed_index.get(parameter_id)
        baseline_value = (baseline_values.get(parameter_id) or {}).get("value")
        status = _diff(observed, baseline_value)
        counts[status] = counts.get(status, 0) + 1

        if status == DIFF_CHANGED:
            proposed, source = observed.get("value_ref"), PROPOSED_FROM_OBSERVED
        elif status in (DIFF_SAME, DIFF_MISSING_OBSERVED):
            proposed, source = baseline_value, PROPOSED_FROM_BASELINE
        elif status == DIFF_MISSING_BASELINE:
            proposed, source = observed.get("value_ref"), PROPOSED_FROM_OBSERVED
        else:
            proposed, source = None, PROPOSED_NONE

        rows.append(
            {
                "parameter_id": parameter_id,
                "name": parameter.get("name") or "",
                "category": parameter.get("category") or "",
                "unit": (
                    (observed or {}).get("value_ref") or baseline_value or {}
                ).get("unit")
                or "",
                "known": bool(parameter),
                "observed": (
                    {
                        "status": observed.get("status"),
                        "value": observed.get("value_ref"),
                        "raw_key": observed.get("raw_key"),
                        "reason": observed.get("reason"),
                        "observation_id": (observation or {}).get("id"),
                        "captured_at": (observation or {}).get("captured_at"),
                    }
                    if observed is not None
                    else None
                ),
                "baseline": {
                    "value": baseline_value,
                    "baseline_id": (baseline or {}).get("id"),
                    "state": (baseline or {}).get("state"),
                    "intent": (baseline_values.get(parameter_id) or {}).get("intent"),
                },
                "proposed": {"value": proposed, "source": source},
                "diff": status,
            }
        )

    # "过期提醒"：当前观测不是你在看的这条 → 提示，而不是静默换掉。
    # 判据用**对象身份**而不是时间戳比较：同一秒内的两次采集时间戳会打平，
    # 但"当前那条"确实换人了，界面必须知道。
    current = config_ingest.current_observation(repo, pid, logical_file["id"])
    stale = bool(
        observation is not None
        and current is not None
        and str(current.get("id")) != str(observation.get("id"))
    )

    return {
        "profile_id": pid,
        "logical_file": {
            "id": logical_file["id"],
            "display_name": logical_file.get("display_name"),
            "role": logical_file.get("role"),
            "family_id": logical_file.get("family_id"),
        },
        "observation": (
            {
                "id": observation.get("id"),
                "captured_at": observation.get("captured_at"),
                "status": observation.get("status"),
                "completeness": observation.get("completeness"),
                "release_ref": observation.get("release_ref"),
                "unresolved_keys": (observation.get("mapping") or {}).get("unresolved_keys") or [],
                "revision": observation.get("revision"),
                "mapping_revision": observation.get("mapping_revision"),
            }
            if observation is not None
            else None
        ),
        "baseline": (
            {
                "id": baseline.get("id"),
                "scope": baseline.get("scope"),
                "state": baseline.get("state"),
                "release_id": baseline.get("release_id"),
                "revision": baseline.get("revision"),
            }
            if baseline is not None
            else None
        ),
        "rows": rows,
        "counts": counts,
        "stale_observation": stale,
        "newer_observation_id": current.get("id") if stale and current else None,
        "has_baseline": baseline is not None,
        "has_observation": observation is not None,
    }
