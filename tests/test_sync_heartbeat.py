"""M16-20 保活/续期证据测试。

合成 probe 只证明「现在还能取数」；heartbeat_validated 必须由显式真实 idle
证据才能置真。真实 E3 证据未取得，仍为 BLOCKED-EXTERNAL。
"""

from __future__ import annotations

import threading

import pytest

from app.core.errors import INVALID_INPUT, REVISION_CONFLICT, SOURCE_CHANGED, AppError
from app.services import (
    sync_connections,
    sync_heartbeat,
    sync_status,
    workspace_catalog,
)


def make_connection():
    wid = workspace_catalog.create_workspace("W-heartbeat")["id"]
    connection = sync_connections.create_connection(wid, "SDA", "https://sda.invalid")
    return wid, connection["connection_id"]


def configure(wid, cid, interval=10, enabled=True, revision=0):
    return sync_heartbeat.configure(wid, cid, interval, enabled, revision)


def test_successful_heartbeat_is_not_real_idle_validation_and_keeps_last_verified():
    wid, cid = make_connection()
    assert sync_heartbeat.get_state(wid, cid)["enabled"] is False
    configured = configure(wid, cid, interval=10)
    assert configured["enabled"] is True
    assert configured["heartbeat_validated"] is False

    before = sync_status.record_verified(
        wid, "default", "src-a", at="2026-11-01T00:00:00Z", sha256="a" * 64
    )
    calls = []

    def probe():
        calls.append("probe")
        return {"status": "succeeded", "session_valid": True}

    result = sync_heartbeat.run_heartbeat(wid, cid, probe, now="2026-11-02T00:00:00Z")
    assert result["status"] == "succeeded"
    assert calls == ["probe"]
    state = result["state"]
    assert state["heartbeat_validated"] is False
    assert state["last_result"] == "success"
    assert state["last_heartbeat_at"] == "2026-11-02T00:00:00Z"
    assert state["next_due_at"] == "2026-11-02T00:10:00Z"
    assert state["validated_at"] is None
    assert sync_status.record_verified(
        wid, "default", "src-a", at="2026-11-01T00:00:00Z", sha256="a" * 64
    )["last_verified_at"] == before["last_verified_at"]

    assert sync_heartbeat.is_due(state, "2026-11-02T00:09:00Z") is False
    assert sync_heartbeat.is_due(state, "2026-11-02T00:11:00Z") is True
    with pytest.raises(AppError) as exc:
        sync_heartbeat.assert_evidence(state)
    assert exc.value.code == SOURCE_CHANGED


def test_failed_heartbeat_stays_unvalidated_and_records_reason():
    wid, cid = make_connection()
    configure(wid, cid)
    result = sync_heartbeat.run_heartbeat(
        wid, cid, lambda: {"status": "failed", "reason_code": "auth_failed", "session_valid": False}
    )
    assert result["status"] == "failed"
    assert result["reason_code"] == "auth_failed"
    assert result["state"]["heartbeat_validated"] is False
    assert result["state"]["last_result"] == "failed"
    assert result["state"]["reason_code"] == "auth_failed"


def test_one_heartbeat_per_connection_at_a_time():
    wid, cid = make_connection()
    configure(wid, cid)
    entered = threading.Event()
    release = threading.Event()
    calls = []

    def slow_probe():
        calls.append(1)
        entered.set()
        assert release.wait(5.0)
        return {"status": "succeeded", "session_valid": True}

    output = {}
    thread = threading.Thread(
        target=lambda: output.setdefault("first", sync_heartbeat.run_heartbeat(wid, cid, slow_probe))
    )
    thread.start()
    assert entered.wait(5.0)
    second = sync_heartbeat.run_heartbeat(wid, cid, lambda: {"status": "succeeded", "session_valid": True})
    assert second["status"] == "busy"
    assert second["reason_code"] == "heartbeat_pending"
    assert calls == [1]
    release.set()
    thread.join(timeout=5)
    assert not thread.is_alive()
    assert output["first"]["status"] == "succeeded"


def test_disabled_heartbeat_is_skipped_without_probe():
    wid, cid = make_connection()
    configure(wid, cid, enabled=False)
    calls = []
    result = sync_heartbeat.run_heartbeat(wid, cid, lambda: calls.append(1) or {"status": "succeeded"})
    assert result["status"] == "skipped"
    assert result["reason_code"] == "disabled"
    assert calls == []


def test_explicit_real_idle_evidence_is_required_for_validation():
    wid, cid = make_connection()
    configured = configure(wid, cid, interval=10)

    with pytest.raises(AppError) as exc:
        sync_heartbeat.record_idle_evidence(
            wid, cid, {"real_idle": False, "renewal_observed": True}, configured["revision"]
        )
    assert exc.value.code == INVALID_INPUT

    with pytest.raises(AppError) as exc:
        sync_heartbeat.record_idle_evidence(
            wid,
            cid,
            {
                "real_idle": True,
                "renewal_observed": True,
                "idle_window_minutes": 5,
                "observed_at": "2026-11-02T00:00:00Z",
                "operator": "tester",
                "authorization_ref": "auth-1",
            },
            configured["revision"],
        )
    assert exc.value.code == INVALID_INPUT

    # 单元测试只验证结构校验；真实 E3 授权证据仍未取得。
    state = sync_heartbeat.record_idle_evidence(
        wid,
        cid,
        {
            "real_idle": True,
            "renewal_observed": True,
            "idle_window_minutes": 10,
            "observed_at": "2026-11-02T00:00:00Z",
            "operator": "tester",
            "authorization_ref": "auth-1",
        },
        configured["revision"],
    )
    assert state["heartbeat_validated"] is True
    assert state["validated_at"] == "2026-11-02T00:00:00Z"
    assert state["evidence"]["idle_window_minutes"] == 10
    assert sync_heartbeat.assert_evidence(state)["connection_id"] == cid


def test_configure_interval_bounds_and_cas():
    wid, cid = make_connection()
    for interval in (0, 61, True, "10"):
        with pytest.raises(AppError):
            sync_heartbeat.configure(wid, cid, interval, True, 0)
    configured = configure(wid, cid, 60)
    assert configured["revision"] == 1
    with pytest.raises(AppError) as exc:
        sync_heartbeat.configure(wid, cid, 10, True, 0)
    assert exc.value.code == REVISION_CONFLICT
