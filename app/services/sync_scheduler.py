"""M16-09 按源到期计算、错过周期合并与 BUSY 重排边界。

本模块只做两件事：

1. `compute_due_sources()`：纯函数，按 workspace/source 策略与锚点算出**每个来源
   最多一条**到期投递；不读盘、不写盘、不碰网络；
2. `load_workspace_inputs()` / `active_workspace_ids()`：给现有 `Scheduler` 的只读
   薄钩子用的读取器，只读 `sync/policy.json`、`sync/sources.json` 与 run manifest。

为什么不是第二个调度器：现有 `Scheduler` 的线程模型、BUSY 重排、停止排空都已经被
`tests/test_scheduler.py` 钉住；M16-09 只扩展现有的到期扫描。调度线程本身仍然
**不写任何权威数据、不下载、不授权**；真正的投递必须由调用方经 `TaskRunner.submit`
进入工作线程。本模块不持有 runner，也不启动线程。

时间口径：Sync 的 `policy.anchor`、attempt 与 run 时间都是 UTC；`compute_due_sources`
的 `now` 若带时区会先转 UTC，naive 时间按 UTC 解释。`Scheduler` 的旧钟是本地时间，
所以薄钩子会先把 `now_fn()` 的结果转成 UTC 再交给纯函数。

到期规则（每条都对应卡面验收）：

- `policy.enabled is not True` → 整个 workspace 不自动投递；
- source `enabled is False`、tombstone、`schedule_mode == "manual"`、profile override
  `enabled is False` → 不进自动列；
- 间隔取 source `custom` 的 `interval_hours`，否则取 profile override / policy
  `default_interval_hours`（缺省 6）；规划时只接受 1–2160 整数小时；
- 锚点优先级：显式 `anchors[source]` → policy `anchor` → **自动** last attempt；
- 手动 attempt（`origin == "manual"`）不参与锚点，所以手动连续更新不会移动定时锚点；
- 错过多个周期只返回一条 `catch_up`，不会按周期数放大成任务风暴；
- 时钟回拨（自动 attempt 明显晚于 now，且没有显式/policy 锚点）只返回一条
  `clock_skew`，下一次由调用方用新锚点收口；
- 没有任何锚点/自动 attempt 时不立即投递（避免重启风暴），由 `Scheduler` 在内存里
  种下首次锚点；下一次扫描到间隔后才到期。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from . import sync_policy, sync_run_store, sync_sources, workspace_catalog

logger = logging.getLogger(__name__)

#: M16 的默认自动间隔（M16-08：新工作区 auto off + 6 小时）。
DEFAULT_INTERVAL_HOURS = 6
MIN_INTERVAL_HOURS = 1
MAX_INTERVAL_HOURS = 2160
#: 与 `scheduler.BUSY_RETRY_SECONDS` 同值：BUSY 时由调用方 60 秒后重排，仍然走 runner。
BUSY_RETRY_SECONDS = 60.0
#: 判定「时钟回拨」的容差；小于它的前后抖动按正常到期处理。
CLOCK_SKEW_TOLERANCE_SECONDS = 60.0

REASON_DUE = "due"
REASON_CATCH_UP = "catch_up"
REASON_CLOCK_SKEW = "clock_skew"
DUE_REASONS = frozenset({REASON_DUE, REASON_CATCH_UP, REASON_CLOCK_SKEW})

ACTIVE_RUN_STATES = frozenset({"queued", "running"})
_TIME_FORMAT = "%Y-%m-%dT%H:%M:%SZ"


@dataclass(frozen=True)
class DueSource:
    """一条待投递的自动更新；`source_id` 在结果里唯一。

    `due_at` 是应投递的时刻（catch_up/clock_skew 为 now）；`anchor_at` 是本次判定
    使用的锚点；`next_anchor_at` 是交付成功后应写回内存/持久状态的「下一次基准」，
    下一到期 = `next_anchor_at + interval_hours`。
    """

    workspace_id: str
    profile_id: str
    source_id: str
    name: str
    interval_hours: int
    reason: str
    due_at: str
    anchor_at: str
    next_anchor_at: str
    due_cycles: int = 1

    def to_dict(self) -> Dict[str, Any]:
        return {
            "workspace_id": self.workspace_id,
            "profile_id": self.profile_id,
            "source_id": self.source_id,
            "name": self.name,
            "interval_hours": self.interval_hours,
            "reason": self.reason,
            "due_at": self.due_at,
            "anchor_at": self.anchor_at,
            "next_anchor_at": self.next_anchor_at,
            "due_cycles": self.due_cycles,
        }


# ---------------------------------------------------------------------------
# 时间与策略读取（纯函数 / 只读）
# ---------------------------------------------------------------------------


def parse_timestamp(value: Any) -> Optional[datetime]:
    """把 ISO-8601 / datetime 转成 naive UTC；读不懂返回 None（规划不抛异常）。"""
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        moment = value
    else:
        if not isinstance(value, str):
            return None
        text = value.strip()
        if not text:
            return None
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        try:
            moment = datetime.fromisoformat(text)
        except ValueError:
            return None
    if moment.tzinfo is not None:
        moment = moment.astimezone(timezone.utc).replace(tzinfo=None)
    return moment


def format_timestamp(moment: datetime) -> str:
    return moment.strftime(_TIME_FORMAT)


def normalize_now(now: Any) -> datetime:
    """纯函数的 `now` 归一：带时区转 UTC，naive 按 UTC 解释。"""
    parsed = parse_timestamp(now)
    if parsed is None:
        raise ValueError("now 必须是 datetime 或 ISO-8601 时间")
    return parsed


def local_to_utc(moment: datetime) -> datetime:
    """把旧 `Scheduler.now_fn()` 的本地钟转成 Sync 使用的 naive UTC。"""
    return moment.astimezone(timezone.utc).replace(tzinfo=None)


def _coerce_interval(value: Any) -> Optional[int]:
    """规划用的间隔收纳：只接受 1–2160 的整数，越界/布尔/非整数一律 None。"""
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    if value < MIN_INTERVAL_HOURS or value > MAX_INTERVAL_HOURS:
        return None
    return value


def _source_attr(source: Any, name: str, default: Any = None) -> Any:
    if isinstance(source, Mapping):
        return source.get(name, default)
    return getattr(source, name, default)


def _source_id(source: Any) -> str:
    return str(_source_attr(source, "source_id", "") or "")


def source_id_of(source: Any) -> str:
    """公开的只读访问器，供 `Scheduler` 的薄钩子种首次锚点。"""
    return _source_id(source)


def _source_enabled(source: Any) -> bool:
    return bool(_source_attr(source, "enabled", True))


def _source_deleted(source: Any) -> bool:
    return bool(_source_attr(source, "deleted", False))


def _source_mode(source: Any) -> str:
    return str(_source_attr(source, "schedule_mode", "inherit") or "inherit")


def _source_interval(source: Any) -> Optional[int]:
    return _coerce_interval(_source_attr(source, "interval_hours", None))


def _policy_override(policy: Mapping[str, Any], profile_id: str) -> Dict[str, Any]:
    overrides = policy.get("profile_overrides")
    if not isinstance(overrides, Mapping):
        return {}
    value = overrides.get(profile_id)
    return value if isinstance(value, Mapping) else {}


def _policy_for(policies: Any, workspace_id: str) -> Any:
    """支持 `{wid: policy}` 与单份 policy（扁平来源列表时使用）两种入参。"""
    if not isinstance(policies, Mapping):
        return None
    value = policies.get(workspace_id)
    if isinstance(value, Mapping):
        return value
    if any(
        key in policies
        for key in ("enabled", "default_interval_hours", "anchor", "profile_overrides")
    ):
        return policies
    return None


def effective_interval(policy: Any, source: Any) -> Optional[int]:
    """来源在自动计划下的有效间隔；None 表示不自动投递。

    纯函数，不读盘。规则与 `sync_policy.resolve_interval` 对齐，并补上 M16-09 需要的
    profile override 与「规划不抛异常」：
    - 全局 `enabled is not True`、source disabled/tombstone、manual → None；
    - profile override `enabled is False` → None；
    - `custom` 用 source interval；`inherit` 用 override / policy 默认 / 6 小时。
    """
    if not isinstance(policy, Mapping) or policy.get("enabled") is not True:
        return None
    if not _source_enabled(source) or _source_deleted(source):
        return None
    mode = _source_mode(source)
    if mode == "manual":
        return None

    profile_id = str(_source_attr(source, "profile_id", "") or "")
    override = _policy_override(policy, profile_id)
    if override.get("enabled") is False:
        return None
    override_hours = _coerce_interval(override.get("interval_hours"))

    if mode == "custom":
        return _source_interval(source)
    if mode == "inherit":
        if override_hours is not None:
            return override_hours
        return _coerce_interval(policy.get("default_interval_hours")) or DEFAULT_INTERVAL_HOURS
    return None


def is_auto_source(policy: Any, source: Any) -> bool:
    return effective_interval(policy, source) is not None


def _lookup(mapping: Optional[Mapping[Any, Any]], workspace_id: str, source_id: str) -> Any:
    if not isinstance(mapping, Mapping):
        return None
    key: Tuple[str, str] = (workspace_id, source_id)
    if key in mapping:
        return mapping[key]
    if source_id in mapping:
        return mapping[source_id]
    nested = mapping.get(workspace_id)
    if isinstance(nested, Mapping):
        return nested.get(source_id)
    return None


def _attempt_time(value: Any) -> Optional[datetime]:
    """last attempt 的时间；manual 来源不参与自动锚点。"""
    if isinstance(value, Mapping):
        origin = str(value.get("origin") or value.get("current_origin") or "").strip().lower()
        if origin == "manual":
            return None
        value = value.get("at") or value.get("last_attempt_at")
    return parse_timestamp(value)


def last_attempt_time(
    last_attempts: Optional[Mapping[Any, Any]], workspace_id: str, source_id: str
) -> Optional[datetime]:
    return _attempt_time(_lookup(last_attempts, workspace_id, source_id))


def _source_groups(sources: Any) -> Iterable[Tuple[str, Sequence[Any]]]:
    if isinstance(sources, Mapping) and "source_id" in sources:
        workspace_id = str(_source_attr(sources, "workspace_id", "") or "")
        yield workspace_id, [sources]
        return
    if isinstance(sources, Mapping):
        for workspace_id, items in sources.items():
            yield str(workspace_id), _as_items(items)
        return
    grouped: Dict[str, List[Any]] = {}
    for item in sources or []:
        workspace_id = str(_source_attr(item, "workspace_id", "") or "")
        grouped.setdefault(workspace_id, []).append(item)
    for workspace_id, items in grouped.items():
        yield workspace_id, items


def _as_items(items: Any) -> List[Any]:
    if isinstance(items, Mapping):
        if "source_id" in items:
            return [items]
        if isinstance(items.get("items"), list):
            return list(items["items"])
        return list(items.values())
    if isinstance(items, (list, tuple, set)):
        return list(items)
    return [items]


def _due(
    workspace_id: str,
    source: Any,
    interval: int,
    reason: str,
    due_at: datetime,
    anchor_at: datetime,
    next_anchor_at: datetime,
    due_cycles: int,
) -> DueSource:
    return DueSource(
        workspace_id=workspace_id,
        profile_id=str(_source_attr(source, "profile_id", "") or ""),
        source_id=_source_id(source),
        name=str(_source_attr(source, "name", "") or ""),
        interval_hours=interval,
        reason=reason,
        due_at=format_timestamp(due_at),
        anchor_at=format_timestamp(anchor_at),
        next_anchor_at=format_timestamp(next_anchor_at),
        due_cycles=due_cycles,
    )


# ---------------------------------------------------------------------------
# 纯函数：按源到期
# ---------------------------------------------------------------------------


def compute_due_sources(
    now: Any,
    policies: Mapping[str, Any],
    sources: Mapping[str, Iterable[Any]],
    last_attempts: Optional[Mapping[Any, Any]] = None,
    anchors: Optional[Mapping[Any, Any]] = None,
) -> List[DueSource]:
    """返回每个来源最多一条到期投递；不读盘、不写盘、不碰网络。

    `policies` 是 `{workspace_id: policy}`（也接受单份 policy + 扁平来源列表）；
    `sources` 是 `{workspace_id: [SyncSource | dict]}`、扁平列表或单个来源。
    `last_attempts` / `anchors` 的键可以是 `source_id`、`(workspace_id, source_id)`
    或 `{workspace_id: {source_id: ...}}`；`anchors` 还接受 `{workspace_id: anchor}`。
    """
    moment = normalize_now(now)
    attempts = last_attempts or {}
    overrides = anchors or {}
    result: List[DueSource] = []

    for workspace_id, items in _source_groups(sources):
        policy = _policy_for(policies, workspace_id)
        if not isinstance(policy, Mapping) or policy.get("enabled") is not True:
            continue
        policy_anchor = parse_timestamp(policy.get("anchor"))
        for source in items:
            source_id = _source_id(source)
            if not source_id:
                continue
            interval = effective_interval(policy, source)
            if interval is None:
                continue

            attempt = last_attempt_time(attempts, workspace_id, source_id)
            explicit_anchor = parse_timestamp(_lookup(overrides, workspace_id, source_id))
            if explicit_anchor is None and isinstance(overrides, Mapping):
                # 也接受 `{workspace_id: anchor}` 形式的工作区级显式锚点。
                explicit_anchor = parse_timestamp(overrides.get(workspace_id))

            # 时钟回拨：自动 attempt 明显晚于 now。只有在没有显式/policy 锚点时才算，
            # 且只补一条；调用方用 next_anchor_at 收口，不会按 tick 放大。
            if (
                explicit_anchor is None
                and policy_anchor is None
                and attempt is not None
                and attempt > moment + timedelta(seconds=CLOCK_SKEW_TOLERANCE_SECONDS)
            ):
                result.append(
                    _due(
                        workspace_id,
                        source,
                        interval,
                        REASON_CLOCK_SKEW,
                        moment,
                        attempt,
                        moment + timedelta(hours=interval),
                        0,
                    )
                )
                continue

            if explicit_anchor is not None:
                anchor = explicit_anchor
            elif policy_anchor is not None:
                anchor = policy_anchor
            else:
                anchor = attempt
            if anchor is None or anchor > moment:
                # 没有锚点的首次行为交给 Scheduler 在内存里种锚；未来锚点不提前投递。
                continue

            interval_delta = timedelta(hours=interval)
            elapsed = moment - anchor
            if elapsed < interval_delta:
                continue
            cycles = int(elapsed.total_seconds() // interval_delta.total_seconds())
            cycles = max(1, cycles)
            if cycles >= 2:
                result.append(
                    _due(
                        workspace_id,
                        source,
                        interval,
                        REASON_CATCH_UP,
                        moment,
                        anchor,
                        moment + interval_delta,
                        cycles,
                    )
                )
            else:
                result.append(
                    _due(
                        workspace_id,
                        source,
                        interval,
                        REASON_DUE,
                        anchor + interval_delta,
                        anchor,
                        # 下一次计算继续用「刚交付的边界」当锚点：下一到期 = 该锚点 + 间隔。
                        anchor + interval_delta,
                        1,
                    )
                )

    result.sort(key=lambda item: (item.workspace_id, item.profile_id, item.source_id))
    return result


# ---------------------------------------------------------------------------
# 只读读取器：给 Scheduler.due_sync_sources() 用
# ---------------------------------------------------------------------------


def _list_all_sources(workspace_id: str) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    cursor = "0"
    seen: set = set()
    while True:
        page = sync_sources.list_sources(
            workspace_id, status="all", limit=200, cursor=cursor
        )
        rows.extend(page.get("items") or [])
        next_cursor = page.get("next_cursor")
        if not next_cursor or next_cursor in seen:
            return rows
        seen.add(next_cursor)
        cursor = str(next_cursor)


def load_workspace_inputs() -> Tuple[Dict[str, Any], Dict[str, List[Dict[str, Any]]]]:
    """只读加载所有 workspace 的 policy 与 sources；单个 workspace 损坏不拖垮扫描。"""
    policies: Dict[str, Any] = {}
    sources: Dict[str, List[Dict[str, Any]]] = {}
    try:
        workspaces = workspace_catalog.list_workspaces()
    except Exception as exc:
        logger.warning("读取工作区目录失败，同步到期扫描本轮跳过：%s", exc)
        return policies, sources

    for row in workspaces:
        workspace_id = str(row.get("id") or "")
        if not workspace_id:
            continue
        try:
            policy = sync_policy.get_policy(workspace_id)
        except Exception as exc:
            logger.warning("读取工作区 %s 的同步计划失败，本轮跳过：%s", workspace_id, exc)
            continue
        if not isinstance(policy, dict) or policy.get("enabled") is not True:
            # 全局暂停：不需要读 sources，更不投递。
            policies[workspace_id] = policy if isinstance(policy, dict) else {}
            sources[workspace_id] = []
            continue
        try:
            items = _list_all_sources(workspace_id)
        except Exception as exc:
            logger.warning("读取工作区 %s 的更新源失败，本轮按空列表：%s", workspace_id, exc)
            items = []
        policies[workspace_id] = policy
        sources[workspace_id] = items
    return policies, sources


def active_workspace_ids(workspace_ids: Optional[Iterable[str]] = None) -> set:
    """有 queued/running SyncRun 的 workspace；调度器看到后不叠加新投递。"""
    if workspace_ids is None:
        try:
            workspace_ids = [str(row.get("id") or "") for row in workspace_catalog.list_workspaces()]
        except Exception as exc:
            logger.warning("读取工作区目录失败，无法判断在途同步：%s", exc)
            return set()
    active: set = set()
    for workspace_id in workspace_ids:
        workspace_id = str(workspace_id or "")
        if not workspace_id:
            continue
        for state in ACTIVE_RUN_STATES:
            try:
                page = sync_run_store.list_runs(workspace_id, state=state, limit=1)
            except Exception as exc:
                logger.warning("读取工作区 %s 的同步 run 失败：%s", workspace_id, exc)
                continue
            if int(page.get("total") or 0) > 0:
                active.add(workspace_id)
                break
    return active
