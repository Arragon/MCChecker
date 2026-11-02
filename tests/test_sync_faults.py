"""M16-39 崩溃/断线/并发故障注入与 receipt 一致性（离线）。

真实 collector/进程 kill 仍属外部证据；这里用断流、BUSY、双工作区并发和
旧 base_sha 钉住「不写半文件、不覆盖新会话、不重放已提交项」的边界。
"""

from __future__ import annotations

import hashlib
import os
import threading
import time

import pytest

from app.core import storage
from app.core.errors import BUSY, SOURCE_CHANGED, AppError
from app.services import (
    file_versions,
    sync_bindings,
    sync_commit,
    sync_connections,
    sync_run_store,
    sync_sources,
    sync_status,
    sync_tasks,
    tasks,
    workspace_catalog,
)
from app.services.context import RequestContext
from app.services.sync_http_adapter import HttpFetchItem, HttpFetchResult
from collector.stream_reader import StagingSink, StreamError, read_limited, verify_received


def sha(data):
    return hashlib.sha256(data).hexdigest()


class BrokenStream:
    def __init__(self):
        self.index = 0

    def read(self, size):
        self.index += 1
        if self.index == 1:
            return b"partial-bytes"
        raise OSError("connection reset")


def test_stream_interruption_leaves_no_partial_file(tmp_path):
    sink = StagingSink(str(tmp_path / "stage"))
    path = sink.path
    with pytest.raises(StreamError) as exc:
        read_limited(BrokenStream(), sink, limit_bytes=1024)
    assert exc.value.reason_code == "network_error"
    assert not os.path.exists(path)
    with pytest.raises(StreamError):
        verify_received(b"partial-bytes", 100, "0" * 64)


def make_source(wid, name="A.xml"):
    connection = sync_connections.create_connection(wid, "SDA", "https://sda.invalid")
    return sync_sources.create_source(
        wid,
        {
            "profile_id": "default",
            "name": name,
            "kind": "sda_file",
            "target": {"kind": "file_archive", "file_id": "file-0001"},
            "connection_id": connection["connection_id"],
            "resource_ref": "file:1",
            "display_path": "/sda/" + name,
        },
    )


def ctx(wid):
    return RequestContext(ip="127.0.0.1", profile_id="default", workspace_id=wid)


def wait_run(wid, run_id, timeout=10.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        doc = sync_run_store.get_run(wid, run_id)
        if doc["run"]["state"] in ("partial", "succeeded", "failed", "canceled", "interrupted"):
            return doc
        time.sleep(0.01)
    raise AssertionError("run timeout")


def wait_task(task, timeout=10.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if task.state in tasks.FINISHED_STATES:
            return task
        time.sleep(0.01)
    raise AssertionError("task timeout")


def read_file(wid, name):
    with storage.use_workspace(wid), storage.use_profile("default"):
        return storage.load_config_file(name)


def test_two_workspaces_run_same_names_without_stealing_bytes():
    one = workspace_catalog.create_workspace("W-fault-1")["id"]
    two = workspace_catalog.create_workspace("W-fault-2")["id"]
    source_one = make_source(one, "A.xml")
    source_two = make_source(two, "A.xml")
    entered = threading.Barrier(2, timeout=5)

    def reader(source, cancel):
        entered.wait()
        body = ("<R><w>%s</w></R>" % source["workspace_id"]).encode("utf-8")
        return {
            "status": "succeeded",
            "items": [{"name": source["name"], "content": body, "sha256": sha(body)}],
        }

    first = sync_tasks.submit_sync_run(ctx(one), [source_one], sda_reader=reader)
    second = sync_tasks.submit_sync_run(ctx(two), [source_two], sda_reader=reader)
    one_doc = wait_run(one, first["run"]["run_id"])
    two_doc = wait_run(two, second["run"]["run_id"])

    assert one_doc["run"]["state"] == "succeeded"
    assert two_doc["run"]["state"] == "succeeded"
    assert read_file(one, "A.xml") == ("<R><w>%s</w></R>" % one).encode()
    assert read_file(two, "A.xml") == ("<R><w>%s</w></R>" % two).encode()


def test_second_run_same_profile_is_busy_and_marked_interrupted():
    wid = workspace_catalog.create_workspace("W-fault-busy")["id"]
    source = make_source(wid, "A.xml")
    entered = threading.Event()
    release = threading.Event()

    def slow_reader(source_dict, cancel):
        entered.set()
        assert release.wait(5.0)
        body = b"<R><slow>1</slow></R>"
        return {
            "status": "succeeded",
            "items": [{"name": source_dict["name"], "content": body, "sha256": sha(body)}],
        }

    first = sync_tasks.submit_sync_run(ctx(wid), [source], sda_reader=slow_reader)
    assert entered.wait(5.0)
    with pytest.raises(AppError) as exc:
        sync_tasks.submit_sync_run(ctx(wid), [source], sda_reader=slow_reader)
    assert exc.value.code == BUSY
    second_run = next(
        item["run"]
        for item in sync_run_store.list_runs(wid)["items"]
        if item["run"]["run_id"] != first["run"]["run_id"]
    )
    assert second_run["state"] == "interrupted"
    release.set()
    wait_run(wid, first["run"]["run_id"])
    wait_task(tasks.get_runner().get(first["task"]["id"]))


def test_old_response_with_stale_base_sha_does_not_overwrite():
    wid = workspace_catalog.create_workspace("W-fault-base")["id"]
    initial = b"<R><x>1</x></R>"
    manual = b"<R><x>manual</x></R>"
    incoming = b"<R><x>2</x></R>"
    with storage.use_workspace(wid), storage.use_profile("default"):
        storage.save_config_file("A.xml", initial)
        row = next(item for item in file_versions.list_resources("default") if item["name"] == "A.xml")
        storage.save_config_file("A.xml", manual)

    source = {
        "name": "A.xml",
        "profile_id": "default",
        "kind": "http_file",
        "target": {"kind": "file_archive", "file_id": row["file_id"]},
        "record_source": None,
    }
    item = HttpFetchItem(basename="A.xml", content=incoming, size=len(incoming), sha256=sha(incoming))
    fetch = HttpFetchResult(
        kind="http_file",
        status="succeeded",
        source_id="src-1",
        profile_id="default",
        message="ok",
        items=(item,),
    )
    result = sync_commit.commit_fetch_result(
        wid, "default", source, fetch, base_sha=row["blob_sha256"]
    )
    assert result["status"] == "failed"
    assert read_file(wid, "A.xml") == manual


def test_stale_binding_is_rejected():
    wid = workspace_catalog.create_workspace("W-fault-binding")["id"]
    with storage.use_workspace(wid), storage.use_profile("default"):
        storage.save_config_file("A.xml", b"<R><x>1</x></R>")
        row = next(item for item in file_versions.list_resources("default") if item["name"] == "A.xml")
    binding = sync_bindings.resolve_binding(
        wid, "default", {"kind": "file_archive", "file_id": row["file_id"]}
    )
    with storage.use_workspace(wid), storage.use_profile("default"):
        storage.save_config_file("A.xml", b"<R><x>2</x></R>")
    with pytest.raises(AppError) as exc:
        sync_bindings.assert_binding_current(wid, "default", binding)
    assert exc.value.code == SOURCE_CHANGED


def test_receipt_recovery_never_invents_success():
    wid = workspace_catalog.create_workspace("W-fault-receipt")["id"]
    digest = "a" * 64
    recovered = sync_status.recover_from_receipt(
        wid, "default", "src-1", {"sha256": digest, "version_id": "v-1"}, current_sha256=digest
    )
    assert recovered["last_good_sha"] == digest
    assert recovered["current_origin"] == "remote"

    mismatch = sync_status.recover_from_receipt(
        wid, "default", "src-1", {"sha256": digest, "version_id": "v-1"}, current_sha256="b" * 64
    )
    assert mismatch["last_good_sha"] == digest
    assert mismatch["current_origin"] == "unknown"
    assert mismatch["reason_code"] == "unknown"
