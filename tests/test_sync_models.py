"""M16-03 Sync domain model tests (pure, no I/O)."""

import json

import pytest

from app.core import sync_models as m
from app.core.errors import INVALID_INPUT, AppError

HASH = "a" * 64


def target_archive():
    return m.SyncTarget(kind="file_archive", file_id="file-1")


def target_candidate():
    return m.SyncTarget(
        kind="default_candidate",
        file_id="file-1",
        configuration_id="cfg-1",
        release_id="rel-1",
    )


def source(**overrides):
    data = {
        "source_id": "src-1",
        "workspace_id": "w-1",
        "profile_id": "p-1",
        "revision": 1,
        "name": "MachineConstant.xml",
        "kind": "sda_file",
        "target": target_candidate(),
        "enabled": True,
        "schedule_mode": "inherit",
        "interval_hours": None,
        "connection_id": "conn-1",
        "resource_ref": "res-1",
        "display_path": "/synthetic/file.xml",
        "record_source": None,
        "keyword": "",
        "created_at": "2026-11-02T00:00:00Z",
        "updated_at": "2026-11-02T00:00:00Z",
    }
    data.update(overrides)
    return m.SyncSource(**data)


def run(**overrides):
    data = {
        "run_id": "run-1",
        "workspace_id": "w-1",
        "trigger": "manual",
        "state": "partial",
        "counts": {"total": 2, "succeeded": 1, "failed": 1},
        "created_at": "2026-11-02T00:00:00Z",
        "started_at": "2026-11-02T00:00:01Z",
        "finished_at": "2026-11-02T00:00:02Z",
        "parent_run_id": None,
        "plan_id": "plan-1",
        "request_id": "req-1",
    }
    data.update(overrides)
    return m.SyncRun(**data)


def item(**overrides):
    data = {
        "item_id": "item-1",
        "run_id": "run-1",
        "source_id": "src-1",
        "workspace_id": "w-1",
        "profile_id": "p-1",
        "state": "succeeded",
        "stage": "finished",
        "reason_code": "validated",
        "using_last_good": False,
        "last_verified_at": "2026-11-02T00:00:02Z",
        "candidate_id": "cand-1",
        "observation_id": None,
        "file_version_id": "v-1",
    }
    data.update(overrides)
    return m.SyncItem(**data)


def candidate(**overrides):
    data = {
        "candidate_id": "cand-1",
        "workspace_id": "w-1",
        "profile_id": "p-1",
        "source_id": "src-1",
        "binding_revision": 2,
        "content_hash": HASH,
        "target_scope": "default:cfg-1:rel-1",
        "state": "pending",
        "created_at": "2026-11-02T00:00:00Z",
        "superseded_by": None,
        "file_version_id": "v-1",
        "plan_id": "plan-1",
    }
    data.update(overrides)
    return m.SyncCandidate(**data)


def freshness(**overrides):
    data = {
        "workspace_id": "w-1",
        "profile_id": "p-1",
        "source_id": "src-1",
        "last_attempt_at": "2026-11-02T00:00:00Z",
        "last_verified_at": "2026-11-02T00:00:01Z",
        "last_commit_at": "2026-11-02T00:00:02Z",
        "last_good_sha": HASH,
        "last_good_version_id": "v-1",
        "source_version": "691",
        "current_origin": "remote",
        "reason_code": "validated",
    }
    data.update(overrides)
    return m.SourceFreshness(**data)


def test_roundtrip_each_model():
    connection = m.SdaConnection(
        connection_id="conn-1",
        workspace_id="w-1",
        name="SDA main",
        origin="https://sda.example.invalid",
        protocol="sda_v1",
        policy_revision=3,
        enabled=True,
        created_at="2026-11-02T00:00:00Z",
        updated_at="2026-11-02T00:00:00Z",
    )
    event = m.ConnectionEvent(
        event_id="evt-1",
        workspace_id="w-1",
        connection_id="conn-1",
        event_type="keepalive_renewed",
        at="2026-11-02T00:00:00Z",
        reason_code="validated",
        details={"renewal_observed": True},
    )

    for original, cls in (
        (connection, m.SdaConnection),
        (target_archive(), m.SyncTarget),
        (target_candidate(), m.SyncTarget),
        (source(), m.SyncSource),
        (run(), m.SyncRun),
        (item(), m.SyncItem),
        (candidate(), m.SyncCandidate),
        (freshness(), m.SourceFreshness),
        (event, m.ConnectionEvent),
    ):
        restored = cls.from_dict(original.to_dict())
        assert restored == original
        assert json.loads(m.to_json(original)) == original.to_dict()


def test_invalid_enum_scope_and_id_are_rejected():
    with pytest.raises(AppError) as exc:
        source(kind="bogus")
    assert exc.value.code == INVALID_INPUT

    with pytest.raises(AppError):
        source(workspace_id="")

    with pytest.raises(AppError):
        source(source_id="../escape")

    with pytest.raises(AppError):
        source(profile_id="p/1")

    with pytest.raises(AppError):
        source(connection_id=None)

    with pytest.raises(AppError):
        source(resource_ref=None)

    with pytest.raises(AppError):
        m.SyncTarget(kind="machine_observation")

    with pytest.raises(AppError):
        m.SyncTarget(kind="file_archive")


def test_schedule_invariants():
    with pytest.raises(AppError):
        source(schedule_mode="custom", interval_hours=None)
    with pytest.raises(AppError):
        source(schedule_mode="inherit", interval_hours=6)
    with pytest.raises(AppError):
        source(schedule_mode="manual", interval_hours=6)

    custom = source(schedule_mode="custom", interval_hours=48)
    assert custom.interval_hours == 48


def test_candidate_and_freshness_invariants():
    with pytest.raises(AppError):
        candidate(content_hash="not-a-sha")
    with pytest.raises(AppError):
        candidate(target_scope="")
    with pytest.raises(AppError):
        freshness(current_origin="local")
    with pytest.raises(AppError):
        item(reason_code="not_a_reason")


def test_aggregate_run_state_cases():
    assert m.aggregate_run_state([]) == "queued"
    assert m.aggregate_run_state(["queued"]) == "running"
    assert m.aggregate_run_state(["running", "succeeded"]) == "running"
    assert m.aggregate_run_state(["succeeded", "unchanged"]) == "succeeded"
    assert m.aggregate_run_state(["succeeded", "skipped"]) == "succeeded"
    assert m.aggregate_run_state(["skipped", "skipped"]) == "succeeded"
    assert m.aggregate_run_state(["succeeded", "failed"]) == "partial"
    assert m.aggregate_run_state(["succeeded", "blocked"]) == "partial"
    assert m.aggregate_run_state(["failed", "blocked", "interrupted"]) == "failed"
    assert m.aggregate_run_state(["canceled", "canceled"]) == "canceled"
    assert m.aggregate_run_state(["canceled", "skipped"]) == "canceled"
    assert m.aggregate_run_state(["failed", "canceled"]) == "partial"


def test_aggregate_counts_and_terminal_helpers():
    counts = m.aggregate_counts(["succeeded", "failed", "skipped"])
    assert counts["total"] == 3
    assert counts["succeeded"] == 1
    assert counts["failed"] == 1
    assert counts["skipped"] == 1

    assert m.is_terminal_run_state("partial") is True
    assert m.is_terminal_run_state("running") is False
    assert m.can_cancel_run("queued") is True
    assert m.can_cancel_run("running") is True
    assert m.can_cancel_run("partial") is False
    with pytest.raises(AppError):
        m.aggregate_run_state(["bogus"])


def test_stable_identity_is_deterministic_and_scoped():
    assert m.make_source_id("w-1", "p-1", "Same") == m.make_source_id("w-1", "p-1", "Same")
    assert m.make_source_id("w-1", "p-1", "Same") != m.make_source_id("w-2", "p-1", "Same")
    assert m.make_connection_id("w-1", "SDA") == m.make_connection_id("w-1", "SDA")
    assert m.make_candidate_id("src-1", 2, HASH, "default:cfg-1:rel-1") == m.make_candidate_id(
        "src-1", 2, HASH, "default:cfg-1:rel-1"
    )
    assert m.make_item_id("run-1", "src-1", "p-1") == m.make_item_id("run-1", "src-1", "p-1")
    assert m.make_run_id("2026-11-02T00:00:00Z", "abcd") == "run-20261102T000000Z-abcd"


def test_safe_details_reject_secret_shapes():
    m.ConnectionEvent(
        event_id="evt-1",
        workspace_id="w-1",
        connection_id="conn-1",
        event_type="login_succeeded",
        at="2026-11-02T00:00:00Z",
        details={"renewal_observed": True, "stage": "probe"},
    )

    for details in (
        {"password": "x"},
        {"Cookie": "x"},
        {"note": "Bearer abc"},
        {"nested": {"authorization": "x"}},
    ):
        with pytest.raises(AppError):
            m.ConnectionEvent(
                event_id="evt-1",
                workspace_id="w-1",
                connection_id="conn-1",
                event_type="login_succeeded",
                at="2026-11-02T00:00:00Z",
                details=details,
            )


def test_no_secret_keys_in_model_dicts():
    forbidden = {"password", "passwd", "cookie", "csrf", "authorization", "token", "secret", "har"}
    for value in (
        m.SdaConnection(
            "conn-1", "w-1", "SDA", "https://sda.example.invalid"
        ),
        source(),
        run(),
        item(),
        candidate(),
        freshness(),
        m.ConnectionEvent(
            "evt-1", "w-1", "conn-1", "configured", "2026-11-02T00:00:00Z"
        ),
    ):
        payload = value.to_dict()
        assert not (set(payload) & forbidden)
        assert "password" not in repr(value).lower()
        assert "cookie" not in repr(value).lower()
