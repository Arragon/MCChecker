"""M16-39 执行器提交前的凭据 epoch/目标绑定二次校验。"""

from __future__ import annotations

import hashlib
import threading
import time

from app.core import storage
from app.services import (
    file_versions,
    sync_connections,
    sync_credentials,
    sync_run_store,
    sync_sources,
    sync_tasks,
    workspace_catalog,
)
from app.services.context import RequestContext


class FakeVault:
    def __init__(self):
        self.items = {}

    def put(self, workspace_id, connection_id, generation, plaintext):
        self.items[(workspace_id, connection_id, generation)] = bytes(plaintext)
        return {"generation": generation}

    def delete(self, workspace_id, connection_id, generation):
        return self.items.pop((workspace_id, connection_id, generation), None) is not None


def make_connection(name="SDA guard"):
    wid = workspace_catalog.create_workspace("W-guard-" + name)["id"]
    connection = sync_connections.create_connection(wid, "SDA", "https://sda.invalid")
    return wid, connection["connection_id"]


def make_source(wid, connection_id, name="A.xml", target=None):
    return sync_sources.create_source(
        wid,
        {
            "profile_id": "default",
            "name": name,
            "kind": "sda_file",
            "target": target or {"kind": "file_archive", "file_id": "file-guard"},
            "connection_id": connection_id,
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


def current(wid, name):
    with storage.use_workspace(wid), storage.use_profile("default"):
        try:
            return storage.load_config_file(name)
        except Exception:
            return None


def test_credential_epoch_change_blocks_commit():
    wid, cid = make_connection("cred")
    source = make_source(wid, cid)
    vault = FakeVault()
    first = sync_credentials.begin_candidate(wid, cid, "op", "pw1", vault)
    sync_credentials.activate_candidate(wid, cid, first["candidate"]["candidate_id"], 0, vault)

    entered = threading.Event()
    release = threading.Event()

    def reader(source_dict, cancel):
        entered.set()
        assert release.wait(5.0)
        return {
            "status": "succeeded",
            "items": [
                {
                    "name": source_dict["name"],
                    "content": b"<R><x>new</x></R>",
                    "sha256": hashlib.sha256(b"<R><x>new</x></R>").hexdigest(),
                }
            ],
        }

    result = sync_tasks.submit_sync_run(ctx(wid), [source], sda_reader=reader)
    assert entered.wait(5.0)
    second = sync_credentials.begin_candidate(wid, cid, "op", "pw2", vault)
    sync_credentials.activate_candidate(wid, cid, second["candidate"]["candidate_id"], 1, vault)
    release.set()
    doc = wait_run(wid, result["run"]["run_id"])

    assert doc["run"]["state"] == "failed"
    assert doc["items"][0]["state"] == "blocked"
    assert doc["items"][0]["reason_code"] == "source_changed"
    assert current(wid, "A.xml") is None


def test_binding_change_blocks_commit():
    wid, cid = make_connection("binding")
    with storage.use_workspace(wid), storage.use_profile("default"):
        storage.save_config_file("A.xml", b"<R><x>old</x></R>")
        row = next(
            item for item in file_versions.list_resources("default") if item["name"] == "A.xml"
        )
    source = make_source(
        wid, cid, "A.xml", {"kind": "file_archive", "file_id": row["file_id"]}
    )
    entered = threading.Event()
    release = threading.Event()
    incoming = b"<R><x>incoming</x></R>"

    def reader(source_dict, cancel):
        entered.set()
        assert release.wait(5.0)
        return {
            "status": "succeeded",
            "items": [
                {
                    "name": "A.xml",
                    "content": incoming,
                    "sha256": hashlib.sha256(incoming).hexdigest(),
                }
            ],
        }

    result = sync_tasks.submit_sync_run(ctx(wid), [source], sda_reader=reader)
    assert entered.wait(5.0)
    with storage.use_workspace(wid), storage.use_profile("default"):
        storage.save_config_file("A.xml", b"<R><x>manual</x></R>")
    release.set()
    doc = wait_run(wid, result["run"]["run_id"])

    assert doc["run"]["state"] == "failed"
    assert doc["items"][0]["reason_code"] == "source_changed"
    assert current(wid, "A.xml") == b"<R><x>manual</x></R>"
