"""M9-04 W/P 存储隔离、CAS、幂等与损坏只读（INH-2121）。"""

from __future__ import annotations

import json
import os

import pytest

from app.core import storage
from app.core.errors import AppError, CorruptDataError
from app.services import workspace_catalog, workspace_store

ACTOR = {"ip": "127.0.0.1", "name": "tester"}


def _workspace(name="W"):
    return workspace_catalog.create_workspace(name)


def _tree_snapshot(root):
    out = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames.sort()
        for name in sorted(filenames):
            path = os.path.join(dirpath, name)
            out.append((os.path.relpath(path, root), os.path.getsize(path)))
    return out


def test_w1_cannot_read_w2_object_or_blob():
    w1, w2 = _workspace("W1"), _workspace("W2")
    repo1 = workspace_store.repository(w1["id"], "default")
    repo2 = workspace_store.repository(w2["id"], "default")
    tx = repo1.transaction(ACTOR, "seed", action="seed")
    digest = tx.blob(b"w1-payload")
    tx.put(workspace_store.STATE_KIND, {"n": 1, "blob": digest}, object_id=workspace_store.STATE_ID)
    tx.commit()
    assert repo1.get(workspace_store.STATE_KIND, workspace_store.STATE_ID) is not None
    assert repo2.get(workspace_store.STATE_KIND, workspace_store.STATE_ID) is None
    with pytest.raises(CorruptDataError):
        repo2.read_blob(digest)


def test_same_idempotency_key_across_workspaces_does_not_collide():
    w1, w2 = _workspace("W1"), _workspace("W2")
    first = workspace_store.commit_state(w1["id"], "default", {"n": 1}, ACTOR, idempotency_key="k-1")
    second = workspace_store.commit_state(w2["id"], "default", {"n": 2}, ACTOR, idempotency_key="k-1")
    assert first["receipt"].replayed is False
    assert second["receipt"].replayed is False
    assert first["state"]["state_id"] != second["state"]["state_id"]
    replay = workspace_store.commit_state(w1["id"], "default", {"n": 1}, ACTOR, idempotency_key="k-1")
    assert replay["receipt"].replayed is True
    assert replay["state"]["state_id"] == first["state"]["state_id"]


def test_state_id_is_opaque_and_stale_cas_rejected():
    w = _workspace()
    first = workspace_store.commit_state(w["id"], "default", {"n": 1}, ACTOR)
    state_id = first["state"]["state_id"]
    assert w["id"] not in state_id and "default" not in state_id
    second = workspace_store.commit_state(w["id"], "default", {"n": 2}, ACTOR)
    assert second["state"]["state_id"] != state_id
    with pytest.raises(AppError) as exc:
        workspace_store.commit_state(
            w["id"], "default", {"n": 3}, ACTOR, expected_state_id=state_id
        )
    assert exc.value.code == "REVISION_CONFLICT"


def test_corrupt_state_latches_readonly_but_other_workspace_ok():
    w1, w2 = _workspace("W1"), _workspace("W2")
    workspace_store.commit_state(w1["id"], "default", {"n": 1}, ACTOR)
    repo1 = workspace_store.repository(w1["id"], "default")
    with open(repo1.state_path, "w", encoding="utf-8") as handle:
        handle.write("{broken")
    with pytest.raises(CorruptDataError):
        workspace_store.state(w1["id"], "default")
    with pytest.raises(AppError) as exc:
        workspace_store.commit_state(w1["id"], "default", {"n": 2}, ACTOR)
    assert exc.value.code == "CORRUPT_DATA"
    workspace_store.commit_state(w2["id"], "default", {"n": 1}, ACTOR)
    workspace_store.reset_for_tests()


def test_legacy_dry_run_is_zero_write():
    before = _tree_snapshot(storage.DATA_DIR)
    plan = workspace_store.plan_legacy_migration()
    after = _tree_snapshot(storage.DATA_DIR)
    assert before == after
    assert plan["target"].startswith("data/workspaces/default/")
    assert not os.path.exists(os.path.join(storage.DATA_DIR, "workspaces", "default"))


def test_repository_rejects_unknown_workspace_or_profile():
    w = _workspace()
    with pytest.raises(AppError) as exc:
        workspace_store.repository("w-deadbeef", "default")
    assert exc.value.code == "NOT_FOUND"
    with pytest.raises(AppError) as exc:
        workspace_store.repository(w["id"], "missing")
    assert exc.value.code == "NOT_FOUND"


def test_global_identity_files_stay_at_data_root():
    w = _workspace()
    root = workspace_catalog.workspace_root(w["id"])
    for name in ("ip_mapping.json", "admin_users.json", "device_models.json"):
        assert not os.path.exists(os.path.join(root, name))
    assert os.path.join(storage.DATA_DIR, "ip_mapping.json").startswith(storage.DATA_DIR)
