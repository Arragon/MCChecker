"""M16-11 persistent SyncRun/event log tests."""

import json
import os

import pytest

from app.core.errors import INVALID_INPUT, NOT_FOUND, REVISION_CONFLICT, AppError
from app.core.sync_models import SyncRun
from app.services import sync_paths, sync_run_store, workspace_catalog


def make_workspace():
    return workspace_catalog.create_workspace("W-run")["id"]


def make_run(wid, rid="run-1", state="running", created_at="2026-11-02T00:00:00Z", trigger="manual"):
    terminal = state in ("partial", "succeeded", "failed", "canceled", "interrupted")
    return SyncRun(
        run_id=rid,
        workspace_id=wid,
        trigger=trigger,
        state=state,
        counts={"total": 1, "running": 0 if terminal else 1},
        created_at=created_at,
        started_at=created_at,
        finished_at=created_at if terminal else None,
        parent_run_id=None,
        plan_id="plan-1",
        request_id="req-1",
    )


def test_create_get_list_and_no_write_on_read():
    wid = make_workspace()
    assert not os.path.exists(sync_paths.runs_root(wid))
    assert sync_run_store.list_runs(wid)["total"] == 0
    assert not os.path.exists(sync_paths.runs_root(wid))

    created = sync_run_store.create_run(
        wid,
        make_run(wid),
        frozen={"preview_id": "preview-1"},
        receipt={"idempotency_key": "idem-1"},
    )
    assert created["manifest_revision"] == 1
    assert created["run"]["run_id"] == "run-1"
    assert created["frozen"]["preview_id"] == "preview-1"
    assert created["receipt"]["idempotency_key"] == "idem-1"

    with pytest.raises(AppError):
        sync_run_store.create_run(wid, make_run(wid))
    assert sync_run_store.get_run(wid, "run-1")["run"]["state"] == "running"
    assert sync_run_store.list_runs(wid)["total"] == 1


def test_update_cas_and_finish():
    wid = make_workspace()
    sync_run_store.create_run(wid, make_run(wid))

    updated = sync_run_store.update_run(
        wid,
        "run-1",
        patch={"state": "partial", "counts": {"total": 2, "succeeded": 1, "failed": 1}},
        expected_manifest_revision=1,
    )
    assert updated["manifest_revision"] == 2
    assert updated["run"]["state"] == "partial"

    with pytest.raises(AppError) as exc:
        sync_run_store.update_run(
            wid,
            "run-1",
            patch={"state": "failed"},
            expected_manifest_revision=1,
        )
    assert exc.value.code == REVISION_CONFLICT

    finished = sync_run_store.finish_run(
        wid,
        "run-1",
        "failed",
        counts={"total": 2, "failed": 2},
        finished_at="2026-11-02T00:10:00Z",
        expected_manifest_revision=2,
    )
    assert finished["manifest_revision"] == 3
    assert finished["run"]["state"] == "failed"
    assert finished["run"]["finished_at"] == "2026-11-02T00:10:00Z"

    with pytest.raises(AppError):
        sync_run_store.finish_run(wid, "run-1", "running")
    with pytest.raises(AppError):
        sync_run_store.update_run(wid, "missing", patch={"state": "failed"})


def test_event_log_order_secret_rejection_and_truncated_tail_recovery():
    wid = make_workspace()
    sync_run_store.create_run(wid, make_run(wid))

    first = sync_run_store.append_event(
        wid, "run-1", {"level": "info", "stage": "collecting", "message": "start"}
    )
    second = sync_run_store.append_event(
        wid, "run-1", {"level": "info", "stage": "validating", "sha256": "b" * 64}
    )
    third = sync_run_store.append_event(
        wid, "run-1", {"level": "error", "reason_code": "invalid_payload"}
    )
    assert [first["seq"], second["seq"], third["seq"]] == [1, 2, 3]
    assert [item["seq"] for item in sync_run_store.read_events(wid, "run-1", after_seq=1)] == [2, 3]

    for bad in (
        {"level": "info", "password": "x"},
        {"level": "info", "message": "Bearer abc"},
        {"level": "info", "reason_code": "not_a_reason"},
    ):
        with pytest.raises(AppError):
            sync_run_store.append_event(wid, "run-1", bad)

    path = os.path.join(sync_paths.run_dir(wid, "run-1"), "events.jsonl")
    with open(path, "r", encoding="utf-8") as handle:
        lines = handle.readlines()
    with open(path, "w", encoding="utf-8") as handle:
        handle.write("".join(lines[:2]) + '{"seq":3,"level":"info"')
    recovered = sync_run_store.append_event(
        wid, "run-1", {"level": "info", "stage": "committing", "message": "after recovery"}
    )
    assert recovered["seq"] == 3
    events = sync_run_store.read_events(wid, "run-1")
    assert [item["seq"] for item in events] == [1, 2, 3]
    assert events[-1]["message"] == "after recovery"


def test_list_filters_and_pagination():
    wid = make_workspace()
    for index, state in enumerate(("succeeded", "failed", "succeeded")):
        sync_run_store.create_run(
            wid,
            make_run(wid, "run-%d" % index, state=state, created_at="2026-11-0%dT00:00:00Z" % (index + 1)),
        )
    result = sync_run_store.list_runs(wid, state="succeeded")
    assert result["total"] == 2
    page = sync_run_store.list_runs(wid, limit=1)
    assert len(page["items"]) == 1
    assert page["next_cursor"] == "1"
    rest = sync_run_store.list_runs(wid, limit=1, cursor=page["next_cursor"])
    assert len(rest["items"]) == 1


def test_prune_keeps_unfinished_and_bounds_history():
    wid = make_workspace()
    sync_run_store.create_run(
        wid, make_run(wid, "old-finished", state="succeeded", created_at="2020-01-01T00:00:00Z")
    )
    sync_run_store.create_run(
        wid, make_run(wid, "old-running", state="running", created_at="2020-01-01T00:00:00Z")
    )
    for index in range(3):
        sync_run_store.create_run(
            wid,
            make_run(
                wid,
                "recent-%d" % index,
                state="succeeded",
                created_at="2026-11-0%dT00:00:00Z" % (index + 1),
            ),
        )

    removed = sync_run_store.prune_runs(
        wid,
        now="2026-11-02T00:00:00Z",
        retain_days=30,
        max_runs=2,
    )
    assert removed == 2
    with pytest.raises(AppError) as exc:
        sync_run_store.get_run(wid, "old-finished")
    assert exc.value.code == NOT_FOUND
    assert sync_run_store.get_run(wid, "old-running")["run"]["state"] == "running"
    assert sync_run_store.get_run(wid, "recent-2")["run"]["state"] == "succeeded"


def test_manifest_rejects_secret_details():
    wid = make_workspace()
    with pytest.raises(AppError):
        sync_run_store.create_run(wid, make_run(wid), frozen={"password": "x"})
    with pytest.raises(AppError):
        sync_run_store.create_run(wid, make_run(wid), receipt={"Cookie": "x"})
