"""M16-09 / INH-2174：按源到期、错过周期合并与调度线程零权威写。

全部用例使用假时钟与临时数据根；没有一条为「等一小时」真睡，也没有一条让
`mcsched` 线程写 `data/`。纯函数用例直接喂 dict，集成用例才落到 sync store。
"""

from __future__ import annotations

import hashlib
import os
from datetime import datetime, timedelta, timezone

from app.core.sync_models import SyncRun
from app.services import scheduler as scheduler_module
from app.services import (
    sync_policy,
    sync_run_store,
    sync_scheduler,
    sync_sources,
    workspace_catalog,
)

NOW = datetime(2026, 11, 2, 12, 0, 0, tzinfo=timezone.utc)
SIX_HOURS = timedelta(hours=6)


# ---------------------------------------------------------------------------
# 纯函数输入构造
# ---------------------------------------------------------------------------


def iso(moment: datetime) -> str:
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


def src(
    source_id: str,
    mode: str = "inherit",
    interval=None,
    enabled: bool = True,
    profile_id: str = "default",
    deleted: bool = False,
) -> dict:
    return {
        "source_id": source_id,
        "workspace_id": "w1",
        "profile_id": profile_id,
        "revision": 1,
        "name": source_id + ".xml",
        "kind": "http_file",
        "target": {"kind": "file_archive", "file_id": "file-1"},
        "enabled": enabled,
        "schedule_mode": mode,
        "interval_hours": interval,
        "connection_id": None,
        "resource_ref": None,
        "display_path": "/synthetic/" + source_id,
        "record_source": None,
        "keyword": "",
        "created_at": iso(NOW),
        "updated_at": iso(NOW),
        "deleted": deleted,
    }


def policy(enabled: bool = True, hours=None, anchor=None, overrides=None) -> dict:
    value = {
        "revision": 1,
        "enabled": enabled,
        "anchor": anchor,
        "profile_overrides": overrides or {},
    }
    if hours is not None:
        value["default_interval_hours"] = hours
    return value


def due_ids(result):
    return [item.source_id for item in result]


# ---------------------------------------------------------------------------
# 1. inherit / custom / profile override / 排除项
# ---------------------------------------------------------------------------


def test_inherit_custom_override_interval_and_exclusions():
    rows = [
        src("a-inherit", profile_id="p6"),
        src("b-override", profile_id="p12"),
        src("c-custom-24", mode="custom", interval=24),
        src("d-custom-48", mode="custom", interval=48),
        src("m-manual", mode="manual"),
        src("x-disabled", enabled=False),
        src("z-deleted", deleted=True),
    ]
    pol = policy(
        hours=6,
        overrides={"p12": {"interval_hours": 12}},
    )
    anchors = {
        "a-inherit": iso(NOW - timedelta(hours=7)),
        "b-override": iso(NOW - timedelta(hours=13)),
        "c-custom-24": iso(NOW - timedelta(hours=25)),
        "d-custom-48": iso(NOW - timedelta(hours=97)),
        "m-manual": iso(NOW - timedelta(days=10)),
        "x-disabled": iso(NOW - timedelta(days=10)),
        "z-deleted": iso(NOW - timedelta(days=10)),
    }

    result = sync_scheduler.compute_due_sources(
        NOW, {"w1": pol}, {"w1": rows}, anchors=anchors
    )

    assert set(due_ids(result)) == {
        "a-inherit",
        "b-override",
        "c-custom-24",
        "d-custom-48",
    }
    by_id = {item.source_id: item for item in result}
    assert by_id["a-inherit"].interval_hours == 6
    assert by_id["a-inherit"].reason == sync_scheduler.REASON_DUE
    assert by_id["b-override"].interval_hours == 12
    assert by_id["c-custom-24"].interval_hours == 24
    assert by_id["d-custom-48"].interval_hours == 48
    assert by_id["d-custom-48"].reason == sync_scheduler.REASON_CATCH_UP


def test_default_is_six_hours_and_policy_disabled_delivers_nothing():
    row = src("a")
    enabled = sync_scheduler.compute_due_sources(
        NOW,
        {"w1": policy(hours=None, anchor=iso(NOW - timedelta(hours=7)))},
        {"w1": [row]},
    )
    assert len(enabled) == 1
    assert enabled[0].interval_hours == 6

    paused = sync_scheduler.compute_due_sources(
        NOW,
        {"w1": policy(enabled=False, anchor=iso(NOW - timedelta(days=10)))},
        {"w1": [row]},
    )
    assert paused == []


def test_interval_bounds_are_1_to_2160_and_invalid_sources_are_excluded():
    one = src("one", mode="custom", interval=1)
    long = src("long", mode="custom", interval=2160)
    zero = src("zero", mode="custom", interval=0)
    too_long = src("too-long", mode="custom", interval=2161)
    result = sync_scheduler.compute_due_sources(
        NOW,
        {"w1": policy()},
        {"w1": [one, long, zero, too_long]},
        anchors={
            "one": iso(NOW - timedelta(hours=2)),
            "long": iso(NOW - timedelta(hours=2161)),
            "zero": iso(NOW - timedelta(days=10)),
            "too-long": iso(NOW - timedelta(days=1000)),
        },
    )
    by_id = {item.source_id: item for item in result}
    assert set(by_id) == {"one", "long"}
    assert by_id["one"].interval_hours == 1
    assert by_id["long"].interval_hours == 2160


# ---------------------------------------------------------------------------
# 2. 错过周期合并 / 时钟回拨 / 手动锚点
# ---------------------------------------------------------------------------


def test_five_missed_cycles_merge_into_one_catch_up():
    row = src("a")
    pol = policy(anchor=iso(NOW - timedelta(hours=31)))

    result = sync_scheduler.compute_due_sources(NOW, {"w1": pol}, {"w1": [row]})

    assert len(result) == 1
    item = result[0]
    assert item.reason == sync_scheduler.REASON_CATCH_UP
    assert item.due_cycles == 5
    assert item.interval_hours == 6
    assert item.due_at == iso(NOW)
    assert item.next_anchor_at == iso(NOW + SIX_HOURS)

    # 纯函数每次调用也只给一条，不会按 5 个周期放大。
    again = sync_scheduler.compute_due_sources(NOW, {"w1": pol}, {"w1": [row]})
    assert len(again) == 1


def test_clock_rollback_yields_one_clock_skew_not_a_storm():
    row = src("a")
    future_attempt = {"a": {"at": iso(NOW + timedelta(hours=2)), "origin": "scheduled"}}
    pol = policy(anchor=None)

    result = sync_scheduler.compute_due_sources(
        NOW, {"w1": pol}, {"w1": [row]}, last_attempts=future_attempt
    )

    assert len(result) == 1
    assert result[0].reason == sync_scheduler.REASON_CLOCK_SKEW
    assert result[0].next_anchor_at == iso(NOW + SIX_HOURS)


def test_manual_attempt_does_not_move_the_timer_anchor():
    row = src("a")
    pol = policy(anchor=iso(NOW))
    now = NOW + SIX_HOURS + timedelta(minutes=30)
    manual_attempt = {
        "a": {"at": iso(now + timedelta(hours=10)), "origin": "manual"}
    }

    result = sync_scheduler.compute_due_sources(
        now, {"w1": pol}, {"w1": [row]}, last_attempts=manual_attempt
    )

    assert len(result) == 1
    item = result[0]
    assert item.reason == sync_scheduler.REASON_DUE
    assert item.anchor_at == iso(NOW)
    assert item.next_anchor_at == iso(NOW + SIX_HOURS)


def test_missing_anchor_and_attempt_is_not_due_on_first_pure_call():
    row = src("a")
    assert (
        sync_scheduler.compute_due_sources(
            NOW, {"w1": policy(anchor=None)}, {"w1": [row]}
        )
        == []
    )


# ---------------------------------------------------------------------------
# 集成：真实 policy/source/run store + Scheduler 薄钩子
# ---------------------------------------------------------------------------


class FakeClock:
    def __init__(self, start: datetime = NOW) -> None:
        self.current = start

    def now(self) -> datetime:
        return self.current

    def advance(self, delta: timedelta) -> None:
        self.current = self.current + delta


def make_workspace(name: str = "W-sync-sched") -> str:
    return workspace_catalog.create_workspace(name)["id"]


def make_source(
    workspace_id: str,
    name: str,
    mode: str = "inherit",
    interval=None,
    enabled: bool = True,
    profile_id: str = "default",
):
    return sync_sources.create_source(
        workspace_id,
        {
            "profile_id": profile_id,
            "name": name,
            "kind": "http_file",
            "target": {"kind": "file_archive", "file_id": "file-1"},
            "enabled": enabled,
            "schedule_mode": mode,
            "interval_hours": interval,
            "connection_id": None,
            "resource_ref": None,
            "display_path": "/synthetic/" + name,
            "record_source": None,
            "keyword": "",
        },
    )


def set_policy(
    workspace_id: str,
    enabled: bool = True,
    hours: int = 6,
    anchor=None,
    overrides=None,
) -> None:
    current = sync_policy.get_policy(workspace_id)
    sync_policy.save_policy(
        workspace_id,
        current["revision"],
        {
            "enabled": enabled,
            "default_interval_hours": hours,
            "anchor": anchor,
            "profile_overrides": overrides or {},
        },
    )


def make_scheduler(clock: FakeClock, runner=None):
    return scheduler_module.Scheduler(
        runner=runner if runner is not None else object(),
        now_fn=clock.now,
        sleep_fn=lambda _seconds: None,
    )


def snapshot_tree(root: str):
    entries = {}
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames.sort()
        for dirname in dirnames:
            path = os.path.join(dirpath, dirname)
            entries[os.path.relpath(path, root) + "/"] = "dir"
        for filename in sorted(filenames):
            path = os.path.join(dirpath, filename)
            with open(path, "rb") as handle:
                entries[os.path.relpath(path, root)] = hashlib.sha256(
                    handle.read()
                ).hexdigest()
    return entries


def test_scheduler_scan_excludes_manual_and_disabled_sources():
    wid = make_workspace()
    make_source(wid, "auto.xml")
    make_source(wid, "manual.xml", mode="manual")
    make_source(wid, "disabled.xml", enabled=False)
    set_policy(wid, anchor=iso(NOW - timedelta(hours=7)))
    scheduler = make_scheduler(FakeClock())

    result = scheduler.due_sync_sources(NOW)

    assert [item.name for item in result] == ["auto.xml"]
    assert scheduler.pending_sync_sources == result


def test_scheduler_restart_without_attempt_waits_one_interval_then_delivers_once():
    wid = make_workspace()
    make_source(wid, "auto.xml")
    set_policy(wid, anchor=None)
    clock = FakeClock()
    scheduler = make_scheduler(clock)

    assert scheduler.due_sync_sources(NOW) == []
    clock.advance(timedelta(hours=5))
    assert scheduler.due_sync_sources(clock.now()) == []

    clock.advance(timedelta(hours=1))
    first = scheduler.due_sync_sources(clock.now())
    assert len(first) == 1
    assert first[0].reason == sync_scheduler.REASON_DUE
    assert first[0].next_anchor_at == iso(NOW + SIX_HOURS)

    scheduler.mark_sync_delivered(first[0])
    assert scheduler.due_sync_sources(clock.now()) == []

    clock.advance(timedelta(hours=5))
    assert scheduler.due_sync_sources(clock.now()) == []
    clock.advance(timedelta(hours=1))
    second = scheduler.due_sync_sources(clock.now())
    assert len(second) == 1
    assert second[0].due_at == iso(NOW + timedelta(hours=12))


def test_scheduler_restart_with_policy_anchor_recovers_one_catch_up():
    wid = make_workspace()
    make_source(wid, "auto.xml")
    set_policy(wid, anchor=iso(NOW - timedelta(hours=31)))
    scheduler = make_scheduler(FakeClock())

    # 重启后 last_attempt 缺失，但 policy anchor 仍在：只补一条，不补 5 条。
    result = scheduler.due_sync_sources(NOW)

    assert len(result) == 1
    assert result[0].reason == sync_scheduler.REASON_CATCH_UP
    scheduler.mark_sync_delivered(result[0])
    assert scheduler.due_sync_sources(NOW) == []


def test_scheduler_clock_rollback_ack_stops_repeated_skew_delivery():
    wid = make_workspace()
    source = make_source(wid, "auto.xml")
    set_policy(wid, anchor=None)
    attempts = {
        source["source_id"]: {
            "at": iso(NOW + timedelta(hours=2)),
            "origin": "scheduled",
        }
    }
    scheduler = make_scheduler(FakeClock())

    first = scheduler.due_sync_sources(NOW, last_attempts=attempts)
    assert len(first) == 1
    assert first[0].reason == sync_scheduler.REASON_CLOCK_SKEW

    scheduler.mark_sync_delivered(first[0])
    assert scheduler.due_sync_sources(NOW, last_attempts=attempts) == []


def test_active_sync_run_suppresses_new_delivery_until_runner_finishes():
    wid = make_workspace()
    make_source(wid, "auto.xml")
    set_policy(wid, anchor=iso(NOW - timedelta(hours=7)))
    sync_run_store.create_run(
        wid,
        SyncRun(
            run_id="run-sched-active",
            workspace_id=wid,
            trigger="schedule",
            state="queued",
            created_at=iso(NOW),
        ),
    )
    scheduler = make_scheduler(FakeClock())

    assert scheduler.due_sync_sources(NOW) == []

    sync_run_store.finish_run(wid, "run-sched-active", "succeeded")
    result = scheduler.due_sync_sources(NOW)
    assert len(result) == 1
    assert result[0].reason == sync_scheduler.REASON_DUE


def test_due_scan_and_tick_write_no_authoritative_bytes():
    wid = make_workspace()
    make_source(wid, "auto.xml")
    set_policy(wid, anchor=iso(NOW - timedelta(hours=7)))
    scheduler = make_scheduler(FakeClock())

    from app.core import storage

    before = snapshot_tree(storage.DATA_DIR)
    result = scheduler.due_sync_sources(NOW)
    scheduler.tick()
    after = snapshot_tree(storage.DATA_DIR)

    assert len(result) == 1
    assert after == before


def test_busy_retry_keeps_the_same_due_until_acknowledged():
    wid = make_workspace()
    make_source(wid, "auto.xml")
    set_policy(wid, anchor=iso(NOW - timedelta(hours=7)))
    scheduler = make_scheduler(FakeClock())

    first = scheduler.due_sync_sources(NOW)
    # 模拟 TaskRunner 返回 BUSY：没有 mark_sync_delivered，清单不能丢。
    second = scheduler.due_sync_sources(NOW)

    assert len(first) == 1
    assert [item.to_dict() for item in second] == [item.to_dict() for item in first]
    assert sync_scheduler.BUSY_RETRY_SECONDS == 60.0


def test_due_scan_never_submits_around_the_task_runner():
    class FailingRunner:
        def submit(self, *args, **kwargs):  # pragma: no cover - 被调用即失败
            raise AssertionError("sync due scan must not submit directly")

    wid = make_workspace()
    make_source(wid, "auto.xml")
    set_policy(wid, anchor=iso(NOW - timedelta(hours=7)))
    scheduler = make_scheduler(FakeClock(), runner=FailingRunner())

    result = scheduler.due_sync_sources(NOW)

    assert len(result) == 1
    assert sync_scheduler.BUSY_RETRY_SECONDS == scheduler_module.BUSY_RETRY_SECONDS
