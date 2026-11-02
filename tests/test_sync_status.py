"""M16-25 last-good/freshness tests."""

import pytest

from app.core.errors import AppError
from app.services import sync_status, workspace_catalog

SHA_A = "a" * 64
SHA_B = "b" * 64


def make_ws(name="W-status"):
    return workspace_catalog.create_workspace(name)["id"]


def test_attempt_and_verified_do_not_advance_last_good():
    wid = make_ws()
    status = sync_status.get_status(wid, "default", "src-1")
    assert status["available"] is False
    assert status["last_good_sha"] is None

    attempt = sync_status.record_attempt(
        wid, "default", "src-1", at="2026-11-02T00:00:00Z", reason_code="auth_failed"
    )
    assert attempt["last_attempt_at"] == "2026-11-02T00:00:00Z"
    assert attempt["last_verified_at"] is None
    assert attempt["last_good_sha"] is None
    assert attempt["available"] is False

    verified = sync_status.record_verified(
        wid, "default", "src-1", at="2026-11-02T00:01:00Z", sha256=SHA_A
    )
    assert verified["last_verified_at"] == "2026-11-02T00:01:00Z"
    assert verified["last_good_sha"] is None
    assert verified["available"] is False


def test_commit_advances_last_good_and_same_content_keeps_it():
    wid = make_ws()
    committed = sync_status.record_commit(
        wid,
        "default",
        "src-1",
        sha256=SHA_A,
        version_id="v-1",
        at="2026-11-02T00:02:00Z",
        source_version="691",
        receipt={"sha256": SHA_A, "version_id": "v-1"},
    )
    assert committed["last_good_sha"] == SHA_A
    assert committed["last_good_version_id"] == "v-1"
    assert committed["available"] is True
    assert committed["current_origin"] == "remote"

    verified = sync_status.record_verified(
        wid, "default", "src-1", at="2026-11-02T00:03:00Z", sha256=SHA_A
    )
    assert verified["last_verified_at"] == "2026-11-02T00:03:00Z"
    assert verified["last_good_sha"] == SHA_A

    manual = sync_status.record_manual(wid, "default", "src-1", current_sha256=SHA_B)
    assert manual["current_origin"] == "manual"
    assert manual["reason_code"] == "source_changed"
    assert manual["last_good_sha"] == SHA_A


def test_reconcile_current_and_receipt_recovery():
    wid = make_ws()
    sync_status.record_commit(wid, "default", "src-1", sha256=SHA_A, version_id="v-1")

    same = sync_status.reconcile_current(wid, "default", "src-1", SHA_A)
    assert same["current_origin"] == "manual"
    assert same["last_good_sha"] == SHA_A

    different = sync_status.reconcile_current(wid, "default", "src-1", SHA_B)
    assert different["reason_code"] == "source_changed"
    assert different["last_good_sha"] == SHA_A

    recovered = sync_status.recover_from_receipt(
        wid, "default", "src-1", {"sha256": SHA_A, "version_id": "v-1"}, SHA_A
    )
    assert recovered["last_good_sha"] == SHA_A
    assert recovered["current_origin"] == "remote"

    unknown = sync_status.recover_from_receipt(
        wid, "default", "src-1", {"sha256": SHA_A, "version_id": "v-1"}, SHA_B
    )
    assert unknown["current_origin"] == "unknown"
    assert unknown["reason_code"] == "unknown"
    assert unknown["last_good_sha"] == SHA_A


def test_workspace_isolation_and_secret_rejection():
    w1 = make_ws("W1")
    w2 = make_ws("W2")
    sync_status.record_commit(w1, "default", "src-1", sha256=SHA_A, version_id="v-1")
    assert sync_status.get_status(w1, "default", "src-1")["last_good_sha"] == SHA_A
    assert sync_status.get_status(w2, "default", "src-1")["last_good_sha"] is None
    assert len(sync_status.list_status(w1)) == 1

    with pytest.raises(AppError):
        sync_status.record_commit(
            w1,
            "default",
            "src-1",
            sha256=SHA_A,
            version_id="v-2",
            receipt={"password": "x"},
        )
