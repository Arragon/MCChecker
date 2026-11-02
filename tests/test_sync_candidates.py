"""M16-26 candidate dedupe/version binding tests."""

import hashlib

import pytest

from app.core import storage
from app.core.errors import NOT_FOUND, AppError
from app.services import file_versions, sync_candidates, workspace_catalog

HASH_A = "a" * 64
HASH_B = "b" * 64


def make_workspace(name="W-cand"):
    wid = workspace_catalog.create_workspace(name)["id"]
    with storage.use_workspace(wid), storage.use_profile("default"):
        storage.save_config_file("A.xml", b"<R><x>1</x></R>")
        rows = file_versions.list_resources("default")
    version_id = next(row["version_id"] for row in rows if row["name"] == "A.xml")
    return wid, version_id


def create(wid, version_id, content_hash=HASH_A, scope="default:cfg:rel"):
    return sync_candidates.create_candidate(
        wid,
        "default",
        "src-1",
        1,
        content_hash,
        scope,
        file_version_id=version_id,
        created_at="2026-11-02T00:00:00Z",
    )


def test_same_hash_dedupes_and_counts_seen():
    wid, version_id = make_workspace()
    first = create(wid, version_id)
    second = create(wid, version_id)
    assert first["candidate_id"] == second["candidate_id"]
    assert second["seen_count"] == 2
    assert sync_candidates.list_candidates(wid)["total"] == 1
    assert first["file_version_id"] == version_id
    assert first["state"] == "pending"


def test_new_hash_supersedes_old_pending():
    wid, version_id = make_workspace()
    old = create(wid, version_id)
    new = create(wid, version_id, content_hash=HASH_B)
    assert new["candidate_id"] != old["candidate_id"]
    old_now = sync_candidates.get_candidate(wid, old["candidate_id"])
    assert old_now["state"] == "superseded"
    assert old_now["superseded_by"] == new["candidate_id"]
    assert sync_candidates.list_candidates(wid, state="superseded")["total"] == 1
    assert sync_candidates.list_candidates(wid, state="pending")["total"] == 1


def test_cross_workspace_and_missing_version_rejected():
    w1, version_id = make_workspace("W1")
    w2 = workspace_catalog.create_workspace("W2")["id"]
    with pytest.raises(AppError) as exc:
        create(w2, version_id)
    assert exc.value.code == NOT_FOUND

    with pytest.raises(AppError) as exc:
        create(w1, "v-missing")
    assert exc.value.code == NOT_FOUND


def test_candidate_does_not_touch_config_or_publish():
    wid, version_id = make_workspace()
    with storage.use_workspace(wid), storage.use_profile("default"):
        before = storage.load_config_file("A.xml")
    create(wid, version_id, content_hash=hashlib.sha256(b"candidate").hexdigest())
    with storage.use_workspace(wid), storage.use_profile("default"):
        after = storage.load_config_file("A.xml")
    assert before == after
