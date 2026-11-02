"""M16-24 trusted commit tests."""

import hashlib
import os

from app.core import storage
from app.services import sync_commit, workspace_catalog
from app.services.sync_http_adapter import HttpFetchItem, HttpFetchResult

INITIAL = b"<R><x>1</x></R>"


def sha(data):
    return hashlib.sha256(data).hexdigest()


def make_fixture():
    wid = workspace_catalog.create_workspace("W-commit")["id"]
    with storage.use_workspace(wid), storage.use_profile("default"):
        storage.save_config_file("A.xml", INITIAL)
    source = {
        "name": "A.xml",
        "profile_id": "default",
        "kind": "http_file",
        "target": {"kind": "file_archive", "file_id": "file-1"},
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
