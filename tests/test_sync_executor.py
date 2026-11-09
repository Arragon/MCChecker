"""M16-23 统一 Sync 执行编排：HTTP/SDA 路由、触发方式、冻结 W/P、恢复。"""

from __future__ import annotations

import hashlib
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from app.core import storage
from app.core.sync_models import SyncRun
from app.services import (
    sync_candidates,
    config_v3_catalog,
    sync_connections,
    sync_http_adapter,
    sync_run_store,
    sync_sources,
    sync_tasks,
    tasks,
    file_versions,
    workspace_catalog,
)
from app.services.context import RequestContext

XML_A = b"<config><a>1</a></config>"
XML_S = b"<config><s>9</s></config>"


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def do_GET(self):  # noqa: N802
        route = self.server.routes.get(self.path.split("?", 1)[0], {"status": 404, "body": b"not found"})
        if isinstance(route, bytes):
            route = {"body": route}
        body = route.get("body", b"")
        self.send_response(route.get("status", 200))
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Content-Type", "application/octet-stream")
        self.end_headers()
        if body:
            self.wfile.write(body)

    def log_message(self, *args):
        pass


@pytest.fixture()
def http_server(monkeypatch):
    monkeypatch.setattr(urllib.request, "getproxies", lambda: {})
    started = []

    def make(routes):
        httpd = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        httpd.daemon_threads = True
        httpd.routes = dict(routes)
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        started.append((httpd, thread))
        return httpd

    yield make
    for httpd, thread in started:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=3)


def _url(httpd, path):
    return "http://127.0.0.1:%d%s" % (httpd.server_address[1], path)


def make_workspace():
    return workspace_catalog.create_workspace("W-exec")["id"]


def ctx(wid):
    return RequestContext(ip="127.0.0.1", profile_id="default", workspace_id=wid)


def make_http_source(wid, url, name="A.xml"):
    with storage.use_workspace(wid), storage.use_profile("default"):
        storage.save_config_file(name, b"<config/>")
        file_id = file_versions.current_identity("default", name)["file_id"]
    return sync_sources.create_source(
        wid,
        {
            "profile_id": "default",
            "name": name,
            "kind": "http_file",
            "target": {"kind": "file_archive", "file_id": file_id},
            "resource_ref": url,
            "display_path": "/synthetic/" + name,
        },
    )


def make_sda_source(wid, connection_id, name="S.xml", kind="sda_file", existing_target=False):
    file_id = "file-0002"
    if existing_target:
        with storage.use_workspace(wid), storage.use_profile("default"):
            storage.save_config_file(name, b"<config/>")
            file_id = file_versions.current_identity("default", name)["file_id"]
    return sync_sources.create_source(
        wid,
        {
            "profile_id": "default",
            "name": name,
            "kind": kind,
            "target": {"kind": "file_archive", "file_id": file_id},
            "connection_id": connection_id,
            "resource_ref": "file:1",
            "display_path": "/sda/" + name,
        },
    )


def make_connection(wid):
    return sync_connections.create_connection(wid, "SDA", "https://sda.invalid")["connection_id"]


def wait_run(wid, run_id, timeout=10.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        doc = sync_run_store.get_run(wid, run_id)
        if doc["run"]["state"] in ("partial", "succeeded", "failed", "canceled", "interrupted"):
            return doc
        time.sleep(0.01)
    raise AssertionError("run 未结束：%s" % run_id)


def wait_task(task, timeout=10.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if task.state in tasks.FINISHED_STATES:
            return task.to_payload()
        time.sleep(0.01)
    raise AssertionError("任务未结束：%s" % task.id)


def current(wid, name, pid="default"):
    with storage.use_workspace(wid), storage.use_profile(pid):
        try:
            return storage.load_config_file(name)
        except Exception:
            return None


def test_mixed_sda_auth_failure_and_http_success_is_partial(http_server):
    wid = make_workspace()
    httpd = http_server({"/A.xml": XML_A})
    http_source = make_http_source(wid, _url(httpd, "/A.xml"), "A.xml")
    sda_source = make_sda_source(wid, make_connection(wid), "S.xml")

    def reader(source, cancel):
        assert source["kind"] == "sda_file"
        return {"status": "failed", "reason_code": "auth_failed", "message": "会话失效"}

    result = sync_tasks.submit_sync_run(
        ctx(wid), [http_source, sda_source], sda_reader=reader
    )
    doc = wait_run(wid, result["run"]["run_id"])
    task = wait_task(tasks.get_runner().get(result["task"]["id"]))

    assert doc["run"]["state"] == "partial"
    assert doc["run"]["counts"]["succeeded"] == 1
    assert doc["run"]["counts"]["failed"] == 1
    assert current(wid, "A.xml") == XML_A
    assert current(wid, "S.xml") is None
    assert task["state"] == "partial"
    assert [item["status"] for item in task["result"]["items"]] == ["succeeded", "failed"]


def test_sda_file_goes_through_the_same_trusted_commit():
    wid = make_workspace()
    source = make_sda_source(wid, make_connection(wid), "S.xml", existing_target=True)

    def reader(source_dict, cancel):
        return {
            "status": "succeeded",
            "items": [
                {
                    "name": "S.xml",
                    "content": XML_S,
                    "sha256": hashlib.sha256(XML_S).hexdigest(),
                }
            ],
        }

    result = sync_tasks.submit_sync_run(ctx(wid), [source], sda_reader=reader)
    doc = wait_run(wid, result["run"]["run_id"])
    task = wait_task(tasks.get_runner().get(result["task"]["id"]))

    assert doc["run"]["state"] == "succeeded"
    assert doc["run"]["counts"]["file_updated"] == 1
    assert current(wid, "S.xml") == XML_S
    assert task["state"] == "succeeded"


def test_sda_table_creates_precise_file_candidate_without_publishing_d0():
    wid = make_workspace()
    with storage.use_workspace(wid), storage.use_profile("default"):
        storage.save_config_file("T.json", b'{"rows":[]}')
        fid = file_versions.current_identity("default", "T.json")["file_id"]
        config = config_v3_catalog.create_config("default", {"code": "TABLE", "name": "Table"})
        release = config_v3_catalog.create_release("default", config["config_id"], {"version": "1"})
    source = sync_sources.create_source(wid, {"profile_id": "default", "name": "T.json", "kind": "sda_table", "connection_id": make_connection(wid), "resource_ref": "table-1", "target": {"kind": "default_candidate", "configuration_id": config["config_id"], "release_id": release["release_id"], "file_id": fid}})
    table = b'{"rows":[{"id":1}]}'

    def reader(source_dict, cancel):
        return {"status": "succeeded", "content": table}

    result = sync_tasks.submit_sync_run(ctx(wid), [source], sda_reader=reader)
    doc = wait_run(wid, result["run"]["run_id"])

    assert doc["run"]["state"] == "succeeded"
    assert doc["run"]["counts"]["candidate_created"] == 1
    assert current(wid, "T.json") == table
    candidates = sync_candidates.list_candidates(wid)
    assert candidates["total"] == 1
    assert candidates["items"][0]["content_hash"] == hashlib.sha256(table).hexdigest()
    with storage.use_workspace(wid), storage.use_profile("default"):
        version = file_versions.find_version("default", candidates["items"][0]["file_version_id"])
        assert version["blob_sha256"] == hashlib.sha256(table).hexdigest()
        assert config_v3_catalog.get_config("default", config["config_id"])["releases"][0]["d0"] == release["d0"]


def test_manual_schedule_and_retry_share_the_same_runner(http_server):
    wid = make_workspace()
    httpd = http_server({"/A.xml": XML_A})
    source = make_http_source(wid, _url(httpd, "/A.xml"), "A.xml")

    for trigger in ("manual", "schedule", "retry"):
        result = sync_tasks.submit_sync_run(ctx(wid), [source], trigger=trigger)
        doc = wait_run(wid, result["run"]["run_id"])
        wait_task(tasks.get_runner().get(result["task"]["id"]))
        assert result["task"]["type"] == "sync_run"
        assert doc["run"]["trigger"] == trigger


def test_frozen_profile_is_not_replaced_by_worker_context(http_server, monkeypatch):
    wid = make_workspace()
    workspace_catalog.add_profile(wid, "p2")
    with storage.use_workspace(wid), storage.use_profile("p2"):
        storage.save_config_file("A.xml", b"<R><p2>old</p2></R>")
    httpd = http_server({"/A.xml": XML_A})
    source = make_http_source(wid, _url(httpd, "/A.xml"), "A.xml")

    real = sync_http_adapter.fetch_source

    def switch_then_fetch(source_model, policy=None, current=None):
        storage.set_active_profile("p2")
        return real(source_model, policy=policy, current=current)

    monkeypatch.setattr(sync_http_adapter, "fetch_source", switch_then_fetch)
    try:
        result = sync_tasks.submit_sync_run(ctx(wid), [source])
        doc = wait_run(wid, result["run"]["run_id"])
        assert doc["run"]["state"] == "succeeded"
        assert current(wid, "A.xml") == XML_A
        assert current(wid, "A.xml", pid="p2") == b"<R><p2>old</p2></R>"
    finally:
        storage.set_active_profile("default")


def test_full_pool_with_one_worker_does_not_deadlock(http_server):
    wid = make_workspace()
    httpd = http_server({"/A.xml": XML_A, "/B.xml": XML_A, "/C.xml": XML_A})
    sources = [
        make_http_source(wid, _url(httpd, "/%s.xml" % name), "%s.xml" % name)
        for name in ("A", "B", "C")
    ]
    tasks.init_runner(1)
    result = sync_tasks.submit_sync_run(ctx(wid), sources)
    doc = wait_run(wid, result["run"]["run_id"], timeout=15)
    task = wait_task(tasks.get_runner().get(result["task"]["id"]), timeout=15)
    assert doc["run"]["state"] == "succeeded"
    assert task["state"] == "succeeded"
    for name in ("A", "B", "C"):
        assert current(wid, "%s.xml" % name) == XML_A


def test_recover_interrupted_runs_never_claims_success():
    wid = make_workspace()
    for index, state in enumerate(("queued", "running")):
        sync_run_store.create_run(
            wid,
            SyncRun(
                run_id="run-orphan-%d" % index,
                workspace_id=wid,
                trigger="manual",
                state=state,
                counts={"total": 2, "queued": 2},
                created_at="2026-11-02T00:00:0%dZ" % index,
            ),
        )
    assert sync_tasks.recover_interrupted_runs(wid) == 2
    assert sync_tasks.recover_interrupted_runs(wid) == 0
    for index in range(2):
        doc = sync_run_store.get_run(wid, "run-orphan-%d" % index)
        assert doc["run"]["state"] == "interrupted"
        assert doc["run"]["counts"]["interrupted"] == 2
