"""M16-29 机台观测引用/last-good 顺序测试。"""

from __future__ import annotations

import uuid

import pytest

from app.core import storage
from app.core.errors import INVALID_INPUT, REVISION_CONFLICT, AppError
from app.services import (
    sync_connections,
    sync_observations,
    sync_sources,
    workspace_catalog,
)


def make_source(target=None):
    wid = workspace_catalog.create_workspace("W-obs-" + uuid.uuid4().hex[:8])["id"]
    connection = sync_connections.create_connection(wid, "SDA", "https://sda.invalid")
    payload = {
        "profile_id": "default",
        "name": "M.xml",
        "kind": "sda_file",
        "connection_id": connection["connection_id"],
        "resource_ref": "file:1",
        "target": target or {"kind": "machine_observation", "device_id": "dev-1"},
    }
    return wid, sync_sources.create_source(wid, payload)["source_id"]


def capture(observation_id="obs-1", captured_at="2026-11-02T00:00:00Z", status="parsed", reason=None):
    return lambda: {
        "observation_id": observation_id,
        "captured_at": captured_at,
        "status": status,
        "reason_code": reason,
    }


def test_machine_target_and_device_are_required():
    wid, sid = make_source()
    with pytest.raises(AppError) as exc:
        sync_observations.record_observation(wid, sid, "dev-2", capture())
    assert exc.value.code == INVALID_INPUT

    with pytest.raises(AppError) as exc:
        sync_observations.record_observation(wid, sid, "", capture())
    assert exc.value.code == INVALID_INPUT

    other_wid, other_sid = make_source(target={"kind": "file_archive", "file_id": "file-1"})
    with pytest.raises(AppError) as exc:
        sync_observations.record_observation(other_wid, other_sid, "dev-1", capture())
    assert exc.value.code == INVALID_INPUT


def test_good_observation_sets_last_good_without_touching_baseline():
    wid, sid = make_source()
    with storage.use_workspace(wid), storage.use_profile("default"):
        storage.save_config_file("M.xml", b"<R><machine>1</machine></R>")
        baseline = storage.load_config_file("M.xml")

    state = sync_observations.record_observation(wid, sid, "dev-1", capture())
    assert state["last_good_observation_id"] == "obs-1"
    assert state["last_good_captured_at"] == "2026-11-02T00:00:00Z"
    assert state["last_status"] == "parsed"
    assert state["history"][0]["observation_id"] == "obs-1"
    assert state["history"][0]["stale"] is False

    with storage.use_workspace(wid), storage.use_profile("default"):
        assert storage.load_config_file("M.xml") == baseline


def test_parse_failure_is_audited_but_never_moves_last_good():
    wid, sid = make_source()
    sync_observations.record_observation(wid, sid, "dev-1", capture())
    state = sync_observations.record_observation(
        wid,
        sid,
        "dev-1",
        capture("obs-2", "2026-11-02T00:01:00Z", status="parse_failed", reason="parse_failed"),
    )
    assert state["last_good_observation_id"] == "obs-1"
    assert state["last_status"] == "parse_failed"
    assert state["last_reason"] == "parse_failed"
    assert [row["observation_id"] for row in state["history"]] == ["obs-1", "obs-2"]
    assert state["history"][1]["stale"] is True


def test_out_of_order_observation_does_not_regress_last_good():
    wid, sid = make_source()
    sync_observations.record_observation(wid, sid, "dev-1", capture("obs-2", "2026-11-02T00:02:00Z"))
    stale = sync_observations.record_observation(
        wid, sid, "dev-1", capture("obs-1", "2026-11-02T00:01:00Z")
    )
    assert stale["last_good_observation_id"] == "obs-2"
    assert stale["history"][-1]["stale"] is True

    newer = sync_observations.record_observation(
        wid, sid, "dev-1", capture("obs-3", "2026-11-02T00:03:00Z")
    )
    assert newer["last_good_observation_id"] == "obs-3"
    assert newer["history"][-1]["stale"] is False


def test_capture_failure_and_cas_conflict_are_explicit():
    wid, sid = make_source()
    calls = []

    def broken():
        calls.append(1)
        raise RuntimeError("device offline")

    state = sync_observations.record_observation(wid, sid, "dev-1", broken)
    assert state["last_status"] == "failed"
    assert state["last_reason"] == "network_error"
    assert state["last_good_observation_id"] is None
    assert state["history"][-1]["observation_id"] is None

    revision = state["revision"]
    before = len(calls)
    with pytest.raises(AppError) as exc:
        sync_observations.record_observation(wid, sid, "dev-1", capture(), expected_revision=0)
    assert exc.value.code == REVISION_CONFLICT
    assert len(calls) == before

    good = sync_observations.record_observation(
        wid, sid, "dev-1", capture(), expected_revision=revision
    )
    assert good["last_good_observation_id"] == "obs-1"
    assert sync_observations.get_state(wid, sid, "dev-1")["last_good_observation_id"] == "obs-1"
    assert sync_observations.list_states(wid) == [sync_observations.get_state(wid, sid, "dev-1")]
