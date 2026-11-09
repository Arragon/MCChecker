"""M16-24 trusted commit tests."""

import hashlib
import os

from app.core import storage
from app.services import sync_commit, workspace_catalog
from app.services import config_v3_catalog, file_versions, sync_status, sync_candidates, sync_sources
from app.services.sync_http_adapter import HttpFetchItem, HttpFetchResult

INITIAL = b"<R><x>1</x></R>"


def sha(data):
    return hashlib.sha256(data).hexdigest()


def make_fixture():
    wid = workspace_catalog.create_workspace("W-commit")["id"]
    with storage.use_workspace(wid), storage.use_profile("default"):
        storage.save_config_file("A.xml", INITIAL)
        file_id = file_versions.current_identity("default", "A.xml")["file_id"]
    source = {
        "name": "A.xml",
        "profile_id": "default",
        "kind": "http_file",
        "target": {"kind": "file_archive", "file_id": file_id},
        "record_source": None,
    }
    return wid, source, sha(INITIAL)


def fetch_result(content, kind="http_file", basename="A.xml"):
    item = HttpFetchItem(
        basename=basename,
        content=content,
        size=len(content),
        sha256=sha(content),
    )
    return HttpFetchResult(
        kind=kind,
        status="succeeded",
        source_id="src-1",
        profile_id="default",
        message="ok",
        items=(item,),
    )


def read_current(wid):
    with storage.use_workspace(wid), storage.use_profile("default"):
        return storage.load_config_file("A.xml")


def test_commit_new_content_archive_and_unchanged():
    wid, source, base = make_fixture()
    new = b"<R><x>2</x></R>"
    result = sync_commit.commit_fetch_result(wid, "default", source, fetch_result(new), base_sha=base)
    assert result["status"] == "succeeded"
    assert result["items"][0]["status"] == "success"
    assert read_current(wid) == new
    assert result["items"][0]["archived"]

    same = sync_commit.commit_fetch_result(
        wid, "default", source, fetch_result(new), base_sha=sha(new)
    )
    assert same["status"] == "succeeded"
    assert same["items"][0]["status"] == "unchanged"
    assert read_current(wid) == new


def test_default_target_creates_traceable_candidate_without_changing_d0():
    wid, source, base = make_fixture()
    with storage.use_workspace(wid), storage.use_profile("default"):
        original = file_versions.current_identity("default", "A.xml")
        config = config_v3_catalog.create_config("default", {"code": "C", "name": "Configuration"})
        release = config_v3_catalog.create_release("default", config["config_id"], {"version": "1"})
    source.update(source_id="src-1", revision=1, target={"kind": "default_candidate", "configuration_id": config["config_id"], "release_id": release["release_id"]})
    sync_status.record_commit(wid, "default", "src-1", original["blob_sha256"], original["version_id"], receipt={"binding_fingerprint": sync_sources.binding_fingerprint(source)})
    result = sync_commit.commit_fetch_result(wid, "default", source, fetch_result(b"<R><x>2</x></R>"), base_sha=base)
    assert result["status"] == "succeeded", result
    candidate = sync_candidates.get_candidate(wid, result["candidate_id"])
    assert candidate["base_file_version_id"] == original["version_id"]
    assert candidate["file_version_id"] != original["version_id"]
    with storage.use_workspace(wid):
        latest = config_v3_catalog.list_releases("default", config["config_id"])[0]
    assert latest["d0"] == release["d0"]


def test_display_name_does_not_change_bound_file_identity():
    wid, source, base = make_fixture()
    source["name"] = "Display name.xml"
    new = b"<R><x>2</x></R>"
    result = sync_commit.commit_fetch_result(wid, "default", source,
        fetch_result(new, basename="Display name.xml"), base_sha=base)
    assert result["status"] == "succeeded", result
    assert read_current(wid) == new
    with storage.use_workspace(wid), storage.use_profile("default"):
        assert storage.load_config_file("Display name.xml") is None


def test_missing_file_target_blocks_without_writing_a_new_file():
    wid, source, _base = make_fixture()
    source["target"]["file_id"] = "missing-file"
    result = sync_commit.commit_fetch_result(wid, "default", source, fetch_result(b"<R><x>2</x></R>"))
    assert result["status"] == "blocked"
    assert read_current(wid) == INITIAL


def test_candidate_save_failure_retains_committed_file_and_original_base_for_retry(monkeypatch):
    wid, source, base = make_fixture()
    with storage.use_workspace(wid), storage.use_profile("default"):
        original = file_versions.current_identity("default", "A.xml")
        config = config_v3_catalog.create_config("default", {"code": "C", "name": "Configuration"})
        release = config_v3_catalog.create_release("default", config["config_id"], {"version": "1"})
    source.update(source_id="src-1", revision=1, target={"kind": "default_candidate", "configuration_id": config["config_id"], "release_id": release["release_id"]})
    sync_status.record_commit(wid, "default", "src-1", original["blob_sha256"], original["version_id"], receipt={"binding_fingerprint": sync_sources.binding_fingerprint(source)})
    create_candidate = sync_candidates.create_candidate

    def unavailable(*args, **kwargs):
        raise OSError("fixture unavailable")

    monkeypatch.setattr(sync_candidates, "create_candidate", unavailable)
    content = b"<R><x>2</x></R>"
    result = sync_commit.commit_fetch_result(wid, "default", source, fetch_result(content), base_sha=base)
    assert result["status"] == "partial" and result["items"][0]["status"] == "success"
    assert read_current(wid) == content
    assert sync_status.get_status(wid, "default", "src-1")["last_good_version_id"] == original["version_id"]
    monkeypatch.setattr(sync_candidates, "create_candidate", create_candidate)
    retried = sync_commit.commit_fetch_result(wid, "default", source, fetch_result(content), base_sha=sha(content))
    candidate = sync_candidates.get_candidate(wid, retried["candidate_id"])
    assert candidate["base_file_version_id"] == original["version_id"]


def test_invalid_content_rejected_and_current_preserved():
    wid, source, base = make_fixture()
    html = b"<html>login page</html>"
    result = sync_commit.commit_fetch_result(wid, "default", source, fetch_result(html), base_sha=base)
    assert result["status"] == "failed"
    assert result["items"][0]["status"] == "rejected"
    assert read_current(wid) == INITIAL


def test_base_sha_race_rejected():
    wid, source, base = make_fixture()
    with storage.use_workspace(wid), storage.use_profile("default"):
        storage.save_config_file("A.xml", b"<R><x>other</x></R>")
    result = sync_commit.commit_fetch_result(
        wid,
        "default",
        source,
        fetch_result(b"<R><x>new</x></R>"),
        base_sha=base,
    )
    assert result["status"] == "failed"
    assert result["items"][0]["status"] == "rejected"
    assert read_current(wid) == b"<R><x>other</x></R>"


def test_record_commit_and_unchanged():
    wid, source, _base = make_fixture()
    record_source = dict(source, record_source="https://up.example/record.txt", target=None)
    text = b"record line\n"
    first = sync_commit.commit_fetch_result(
        wid, "default", record_source, fetch_result(text, kind="http_record", basename="record.txt")
    )
    assert first["status"] == "succeeded"
    assert first["items"][0]["status"] == "success"
    with storage.use_workspace(wid), storage.use_profile("default"):
        names = os.listdir(storage.get_record_dir("A.xml"))
    assert len(names) == 1

    second = sync_commit.commit_fetch_result(
        wid, "default", record_source, fetch_result(text, kind="http_record", basename="record.txt")
    )
    assert second["status"] == "succeeded"
    assert second["items"][0]["status"] == "unchanged"


def test_non_file_target_is_blocked():
    wid, source, _base = make_fixture()
    candidate = dict(source, target={"kind": "default_candidate", "configuration_id": "c", "release_id": "r"})
    result = sync_commit.commit_fetch_result(wid, "default", candidate, fetch_result(b"<R/>"))
    assert result["status"] == "blocked"
