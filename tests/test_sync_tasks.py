"""M16-12 SyncRun/TaskRunner 整合测试。

覆盖卡面四条：一成功一 blocked 聚合为 partial、全失败聚合为 failed、取消保留
已提交项、同幂等键同参数复用同一 run；另钉住新旧任务互斥与任务状态词汇。
下载一律打本地 `http.server`，所有写入都在临时数据根里。
"""

from __future__ import annotations

import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from app.core import storage
from app.core.errors import BUSY, REVISION_CONFLICT, AppError
from app.core.sync_models import SyncRun
from app.services import (
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
XML_B = b"<config><b>2</b></config>"
TERMINAL = frozenset({"partial", "succeeded", "failed", "canceled", "interrupted"})


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def do_GET(self):  # noqa: N802 - http.server 约定
        self.server.requests.append(self.path)
        route = self.server.routes.get(self.path.split("?", 1)[0])
        if route is None:
            route = {"status": 404, "body": b"not found"}
        elif isinstance(route, bytes):
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
        httpd.requests = []
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
    return workspace_catalog.create_workspace("W-sync-task")["id"]


def ctx(wid):
    return RequestContext(ip="127.0.0.1", profile_id="default", workspace_id=wid)


def make_http_source(wid, url, name="A.xml", kind="http_file", record_url=None):
    with storage.use_workspace(wid), storage.use_profile("default"):
        if storage.load_config_file(name) is None:
            storage.save_config_file(name, b"<config/>")
        file_id = file_versions.current_identity("default", name)["file_id"]
    payload = {
        "profile_id": "default",
        "name": name,
        "kind": kind,
        "target": {"kind": "file_archive", "file_id": file_id},
        "resource_ref": url,
        "display_path": "/synthetic/" + name,
        "record_source": record_url,
        "keyword": "",
    }
    return sync_sources.create_source(wid, payload)


def make_sda_source(wid, connection_id, name="B.xml"):
    return sync_sources.create_source(
        wid,
        {
            "profile_id": "default",
            "name": name,
            "kind": "sda_file",
            "target": {"kind": "file_archive", "file_id": "file-0002"},
            "connection_id": connection_id,
            "resource_ref": "file:1",
            "display_path": "/sda/" + name,
        },
    )


def make_connection(wid):
    return sync_connections.create_connection(wid, "SDA", "https://sda.invalid")


def wait_run(wid, run_id, timeout=10.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        doc = sync_run_store.get_run(wid, run_id)
        if doc["run"]["state"] in TERMINAL:
            return doc
        time.sleep(0.01)
    raise AssertionError("run %s 未在 %.1f 秒内进入终态：%s" % (run_id, timeout, sync_run_store.get_run(wid, run_id)))


def wait_task(task, timeout=10.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if task.state in tasks.FINISHED_STATES:
            return task.to_payload()
        time.sleep(0.01)
    raise AssertionError("任务 %s 未进入终态（当前 %s）" % (task.id, task.state))


def current_file(wid, name):
    with storage.use_workspace(wid), storage.use_profile("default"):
        try:
            return storage.load_config_file(name)
        except AppError:
            return None


def test_one_success_one_blocked_is_partial_and_keeps_successful_file(http_server):
    wid = make_workspace()
    httpd = http_server({"/A.xml": XML_A})
    http_source = make_http_source(wid, _url(httpd, "/A.xml"), "A.xml")
    sda_source = make_sda_source(wid, make_connection(wid)["connection_id"], "B.xml")

    result = sync_tasks.submit_sync_run(ctx(wid), [http_source, sda_source])
    run_id = result["run"]["run_id"]
    doc = wait_run(wid, run_id)
    task = wait_task(tasks.get_runner().get(result["task"]["id"]))

    assert doc["run"]["state"] == "partial"
    assert doc["run"]["counts"]["total"] == 2
    assert doc["run"]["counts"]["succeeded"] == 1
    assert doc["run"]["counts"]["blocked"] == 1
    assert doc["run"]["counts"]["file_updated"] == 1
    assert current_file(wid, "A.xml") == XML_A
    assert current_file(wid, "B.xml") is None
    assert task["state"] == "partial"
    assert [item["status"] for item in task["result"]["items"]] == ["succeeded", "blocked"]
    assert task["result"]["items"][1]["reason_code"] == "collector_offline"

    events = sync_run_store.read_events(wid, run_id)
    assert [event["status"] for event in events if event.get("status")] == [
        "succeeded",
        "blocked",
        "partial",
    ]


def test_all_sources_failed_aggregates_to_failed():
    wid = make_workspace()
    connection_id = make_connection(wid)["connection_id"]
    first = make_sda_source(wid, connection_id, "B.xml")
    second = make_sda_source(wid, connection_id, "C.xml")

    result = sync_tasks.submit_sync_run(ctx(wid), [first, second])
    doc = wait_run(wid, result["run"]["run_id"])
    task = wait_task(tasks.get_runner().get(result["task"]["id"]))

    assert doc["run"]["state"] == "failed"
    assert doc["run"]["counts"]["blocked"] == 2
    assert doc["run"]["counts"]["failed"] == 0
    assert task["state"] == "failed"
    assert all(item["status"] == "blocked" for item in task["result"]["items"])


def test_source_changed_during_read_never_commits_old_response(http_server, monkeypatch):
    wid = make_workspace()
    httpd = http_server({"/A.xml": XML_A})
    source = make_http_source(wid, _url(httpd, "/A.xml"))
    fetch = sync_http_adapter.fetch_source

    def changed_while_fetching(model, **kwargs):
        result = fetch(model, **kwargs)
        sync_sources.update_source(wid, source["source_id"], source["revision"], {"resource_ref": _url(httpd, "/different.xml")})
        return result

    monkeypatch.setattr(sync_http_adapter, "fetch_source", changed_while_fetching)
    result = sync_tasks.submit_sync_run(ctx(wid), [source])
    doc = wait_run(wid, result["run"]["run_id"])
    assert doc["items"][0]["state"] == "blocked"
    assert doc["items"][0]["reason_code"] == "source_changed"
    assert current_file(wid, "A.xml") == b"<config/>"


def test_cancel_keeps_committed_file_and_cancels_the_rest(http_server, monkeypatch):
    wid = make_workspace()
    httpd = http_server({"/A.xml": XML_A, "/B.xml": XML_B})
    first = make_http_source(wid, _url(httpd, "/A.xml"), "A.xml")
    second = make_http_source(wid, _url(httpd, "/B.xml"), "B.xml")

    real_fetch = sync_http_adapter.fetch_source
    calls = []
    entered = threading.Event()
    release = threading.Event()

    def gated_fetch(source, policy=None, current=None):
        calls.append(source.name)
        if len(calls) == 2:
            entered.set()
            assert release.wait(5.0), "测试没有放行第二次取数"
        return real_fetch(source, policy=policy, current=current)

    monkeypatch.setattr(sync_tasks.sync_http_adapter, "fetch_source", gated_fetch)

    result = sync_tasks.submit_sync_run(ctx(wid), [first, second])
    run_id = result["run"]["run_id"]
    assert entered.wait(5.0), "第二次取数没有开始"

    canceled = sync_tasks.cancel_run(wid, run_id)
    assert canceled["run"]["state"] in ("queued", "running")
    release.set()

    doc = wait_run(wid, run_id)
    task = wait_task(tasks.get_runner().get(result["task"]["id"]))

    assert doc["run"]["state"] == "partial"
    assert doc["run"]["counts"]["succeeded"] == 1
    assert doc["run"]["counts"]["canceled"] == 1
    assert current_file(wid, "A.xml") == XML_A
    assert current_file(wid, "B.xml") == b"<config/>", "取消必须保留原目标，不写入未完成的下载"
    assert [item["status"] for item in task["result"]["items"]] == ["succeeded", "canceled"]
    assert task["state"] == "partial"


def test_idempotency_key_reuses_run_and_rejects_different_parameters(http_server):
    wid = make_workspace()
    httpd = http_server({"/A.xml": XML_A})
    source = make_http_source(wid, _url(httpd, "/A.xml"), "A.xml")

    first = sync_tasks.submit_sync_run(ctx(wid), [source], idempotency_key="idem-1")
    doc = wait_run(wid, first["run"]["run_id"])
    assert doc["run"]["state"] == "succeeded"
    wait_task(tasks.get_runner().get(first["task"]["id"]))
    assert len(httpd.requests) == 1

    second = sync_tasks.submit_sync_run(ctx(wid), [source], idempotency_key="idem-1")
    assert second["reused"] is True
    assert second["run"]["run_id"] == first["run"]["run_id"]
    assert second["task"] is None
    assert len(httpd.requests) == 1

    with pytest.raises(AppError) as exc:
        sync_tasks.submit_sync_run(
            ctx(wid), [source], trigger="schedule", idempotency_key="idem-1"
        )
    assert exc.value.code == REVISION_CONFLICT

    third = sync_tasks.submit_sync_run(ctx(wid), [source], idempotency_key="idem-2")
    assert third["run"]["run_id"] != first["run"]["run_id"]
    wait_run(wid, third["run"]["run_id"])


def test_cancel_run_without_live_worker_marks_queued_run_canceled():
    wid = make_workspace()
    run = SyncRun(
        run_id="run-canceled-1",
        workspace_id=wid,
        trigger="manual",
        state="queued",
        counts={"total": 2, "queued": 2},
        created_at="2026-11-02T00:00:00Z",
    )
    sync_run_store.create_run(wid, run)
    doc = sync_tasks.cancel_run(wid, run.run_id)
    assert doc["run"]["state"] == "canceled"
    assert doc["run"]["counts"] == {"total": 2, "canceled": 2}
    again = sync_tasks.cancel_run(wid, run.run_id)
    assert again["run"]["state"] == "canceled"


def test_sync_run_conflicts_with_old_update_tasks(http_server, monkeypatch):
    wid = make_workspace()
    httpd = http_server({"/A.xml": XML_A})
    source = make_http_source(wid, _url(httpd, "/A.xml"), "A.xml")

    real_fetch = sync_http_adapter.fetch_source
    entered = threading.Event()
    release = threading.Event()

    def gated_fetch(source_model, policy=None, current=None):
        entered.set()
        assert release.wait(5.0), "测试没有放行取数"
        return real_fetch(source_model, policy=policy, current=current)

    import app.services.sync_http_adapter as adapter

    monkeypatch.setattr(adapter, "fetch_source", gated_fetch)
    result = sync_tasks.submit_sync_run(ctx(wid), [source])
    try:
        assert entered.wait(5.0)
        with pytest.raises(AppError) as exc:
            tasks.get_runner().submit(ctx(wid), "update_source", "A.xml", lambda task: None)
        assert exc.value.code == BUSY
    finally:
        release.set()
    wait_run(wid, result["run"]["run_id"])


def test_task_state_aggregation_vocabulary():
    def payload(*states):
        return [{"status": state, "name": str(index)} for index, state in enumerate(states)]

    assert tasks._state_from_items(payload("succeeded", "blocked")) == "partial"
    assert tasks._state_from_items(payload("blocked", "failed")) == "failed"
    assert tasks._state_from_items(payload("canceled", "canceled")) == "canceled"
    assert tasks._state_from_items(payload("canceled", "skipped")) == "canceled"
    assert tasks._state_from_items(payload("skipped")) == "succeeded"
    assert tasks._state_from_items([]) == "succeeded"
    assert "sync_run" in tasks.TASK_TYPES
    assert tasks._CONFLICTING_TYPES["refresh_records"] == ("update_source", "update_all")
