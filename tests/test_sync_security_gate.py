"""M16-38 / INH-2203 统一服务端安全验收。

四组可执行口径（对应卡面四条验收）：

1. **Workspace 隔离**：两个工作区里同名的 profile/source/connection 各有独立稳定 ID；
   跨 W 的读、改、run、候选、连接、凭据一律 404/403，且不会把字节写进另一个 W；
   run 事件、任务项和访问日志不携带另一个 W 的来源路径或正文。
2. **凭据权限**：候选/激活/撤销即使对全局管理员也必须带独立 `X-Operator-Token`；
   注入假 vault 后才真正成功；响应、凭据状态、操作记录都是安全投影，不含
   username/account/password/Cookie/token。
3. **CSRF / Host / Origin**：写请求缺 `X-Requested-With` 时 403 且不落盘；伪造
   `Host` 不能改变工作区范围；跨站 `Origin` 没有自定义头时同样被 CSRF 闸门拒绝；
   OPTIONS 预检不返回任何 CORS 允许头（浏览器无法跨站带自定义头）。
4. **SSRF / 重定向 / 脱敏**：HTTP 来源指向 metadata/私网/跨协议重定向时被下载策略
   或 collector `guard_response` 在读取正文前拒绝，不产生字节、不触发 SDA 登录；
   日志、操作记录、run manifest/events、任务项、备份与数据目录不含假 secret 标记。

所有网络都打本地 `http.server`；metadata/私网地址在连接前就被拒绝，不做真实外网
请求。假 secret 只存在于测试常量与假 vault 内存里，断言失败消息不打印它们的值。
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict, List, Optional

import pytest
from authz_fixtures import as_role, make_server, seed_roles

from app.core import storage
from app.core.errors import AppError
from app.server import api_connections
from app.server.http import CSRF_HEADER
from app.services import (
    file_versions,
    sync_candidates,
    sync_paths,
    sync_run_store,
    sync_tasks,
    tasks,
)
from collector.bounded_fetch import guard_response
from collector.stream_reader import StreamError
from scripts import backup_v3

OPERATOR_TOKEN = "FAKE-OPERATOR-TOKEN-M16-38"
USERNAME = "FAKE-USERNAME-M16-38"
PASSWORD = "FAKE-PASSWORD-M16-38"
COOKIE = "FAKE-COOKIE-M16-38"
CSRF = "FAKE-CSRF-M16-38"
SECRET_MARKERS = (OPERATOR_TOKEN, USERNAME, PASSWORD, COOKIE, CSRF)

FORBIDDEN_KEYS = frozenset(
    {
        "password",
        "passwd",
        "account",
        "username",
        "cookie",
        "set-cookie",
        "csrf",
        "authorization",
        "secret",
        "token",
        "session_id",
        "har",
        "private_key",
        "api_key",
    }
)

XML_W1 = b"<config><workspace>one</workspace></config>"
XML_W2 = b"<config><workspace>two</workspace></config>"
XML_PLAIN = b"<config><plain>ok</plain></config>"
TERMINAL_RUN_STATES = frozenset({"partial", "succeeded", "failed", "canceled", "interrupted"})


class FakeVault:
    """测试专用 vault：只在内存保存密文占位；不落盘，不实现生产 SecretStore。"""

    def __init__(self) -> None:
        self.items: Dict[Any, bytes] = {}
        self.puts: List[Any] = []
        self.deletes: List[Any] = []

    def put(self, workspace_id, connection_id, generation, plaintext):
        key = (workspace_id, connection_id, generation)
        self.items[key] = bytes(plaintext)
        self.puts.append(key)
        return {"generation": generation}

    def delete(self, workspace_id, connection_id, generation):
        key = (workspace_id, connection_id, generation)
        self.deletes.append(key)
        return self.items.pop(key, None) is not None


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def do_GET(self):  # noqa: N802 - http.server 约定
        try:
            self._handle()
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _handle(self) -> None:
        self.server.requests.append((self.path, dict(self.headers.items())))
        route = self.server.routes.get(self.path.split("?", 1)[0])
        if route is None:
            self._reply(404, {}, b"not found")
            return
        if route.get("location"):
            self._reply(route.get("status", 302), {"Location": route["location"]}, b"")
            return
        self._reply(route.get("status", 200), route.get("headers", {}), route.get("body", b""))

    def _reply(self, status: int, headers: Dict[str, str], body: bytes) -> None:
        self.send_response(status)
        for key, value in headers.items():
            self.send_header(key, value)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if body:
            self.wfile.write(body)

    def log_message(self, *args):  # 访问日志会淹掉 pytest 输出
        pass


@pytest.fixture()
def http_server(monkeypatch):
    """本地随机端口 HTTP 服务器；`getproxies` 打桩避免系统代理介入。"""
    monkeypatch.setattr(urllib.request, "getproxies", lambda: {})
    started = []

    def make(routes: Dict[str, Any]):
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


@pytest.fixture(autouse=True)
def _operator_and_vault_ports(monkeypatch):
    monkeypatch.setenv("MCHECKER_OPERATOR_TOKEN", OPERATOR_TOKEN)
    api_connections.configure_vault(None)
    api_connections.configure_probe(None)
    yield
    api_connections.configure_vault(None)
    api_connections.configure_probe(None)


# ---------------------------------------------------------------------------
# 安全断言与小型夹具
# ---------------------------------------------------------------------------


def _assert_absent(text: str, label: str) -> None:
    """逐标记检查；失败消息不打印 marker 或正文的值。"""
    assert all(marker not in text for marker in SECRET_MARKERS), "secret marker leaked into %s" % label


def _walk_keys(value: Any):
    if isinstance(value, dict):
        for key, child in value.items():
            yield key
            yield from _walk_keys(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_keys(child)


def _assert_no_forbidden_keys(value: Any, label: str) -> None:
    bad = [key for key in _walk_keys(value) if str(key).lower() in FORBIDDEN_KEYS]
    assert bad == [], "%s exposes forbidden projection keys" % label


def _scan_tree(root: str) -> str:
    chunks: List[str] = []
    for current, _dirs, files in os.walk(root):
        for name in sorted(files):
            path = os.path.join(current, name)
            try:
                with open(path, "rb") as handle:
                    chunks.append(handle.read().decode("utf-8", "replace"))
            except OSError:
                continue
    return "\n".join(chunks)


def _workspace_tree(wid: str) -> str:
    return os.path.join(storage.DATA_DIR, "workspaces", wid)


def _current_file(wid: str, name: str) -> Optional[bytes]:
    with storage.use_workspace(wid), storage.use_profile("default"):
        try:
            return storage.load_config_file(name)
        except AppError:
            return None


def _operator_headers() -> Dict[str, str]:
    return {}


def _is_csrf_error(payload: Any) -> bool:
    if isinstance(payload, (bytes, str)):
        try:
            payload = json.loads(payload)
        except ValueError:
            return False
    if not isinstance(payload, dict):
        return False
    error = payload.get("error") or {}
    return error.get("code") == "FORBIDDEN" and CSRF_HEADER in str(error.get("message", ""))


def _make_workspace(caller, name: str) -> str:
    status, _headers, body = caller.json("POST", "/api/v3/workspaces", {"name": name})
    assert status == 201, "workspace create failed: %s" % status
    return body["workspace"]["id"]


def _make_connection(caller, wid: str, name: str) -> Dict[str, Any]:
    status, _headers, body = caller.json(
        "POST",
        "/api/v3/workspaces/%s/connections" % wid,
        {"name": name, "origin": "https://sda.example.invalid"},
        headers=_operator_headers(),
    )
    assert status == 201, "connection create failed: %s" % status
    return body


def _source_payload(
    name: str,
    url: Optional[str] = None,
    display_path: Optional[str] = None,
    kind: str = "http_file",
    connection_id: Optional[str] = None,
) -> Dict[str, Any]:
    return {
        "profile_id": "default",
        "name": name,
        "kind": kind,
        "target": {"kind": "file_archive", "file_id": "file-0001"},
        "enabled": True,
        "schedule_mode": "inherit",
        "interval_hours": None,
        "connection_id": connection_id,
        "resource_ref": url,
        "display_path": display_path or "/synthetic/" + name,
        "record_source": None,
        "keyword": "",
    }


def _make_source(
    caller,
    wid: str,
    name: str,
    url: Optional[str],
    display_path: Optional[str] = None,
    kind: str = "http_file",
    connection_id: Optional[str] = None,
    existing_target: bool = False,
) -> Dict[str, Any]:
    payload = _source_payload(name, url, display_path, kind, connection_id)
    if existing_target:
        with storage.use_workspace(wid), storage.use_profile("default"):
            storage.save_config_file(name, b"<config/>")
            payload["target"]["file_id"] = file_versions.current_identity("default", name)["file_id"]
    status, _headers, body = caller.json(
        "POST",
        "/api/v3/workspaces/%s/sync/sources" % wid,
        payload,
    )
    assert status == 201, "source create failed: %s" % status
    return body


def _run_source(caller, wid: str, sid: str) -> Dict[str, Any]:
    status, _headers, body = caller.json(
        "POST",
        "/api/v3/workspaces/%s/sync/sources/%s/run" % (wid, sid),
        {},
    )
    assert status == 202, "source run submit failed: %s" % status
    return body


def _wait_run(wid: str, run_id: str, timeout: float = 10.0) -> Dict[str, Any]:
    deadline = time.time() + timeout
    last: Dict[str, Any] = {}
    while time.time() < deadline:
        last = sync_run_store.get_run(wid, run_id)
        if last["run"]["state"] in TERMINAL_RUN_STATES:
            return last
        time.sleep(0.01)
    raise AssertionError("run %s did not reach a terminal state: %s" % (run_id, last["run"]["state"]))


def _wait_task(task_id: str, timeout: float = 10.0) -> Dict[str, Any]:
    deadline = time.time() + timeout
    while time.time() < deadline:
        task = tasks.get_runner().get(task_id)
        if task is not None and task.state in tasks.FINISHED_STATES:
            return task.to_payload()
        time.sleep(0.01)
    raise AssertionError("task %s did not finish" % task_id)


def _source_total(caller, wid: str) -> int:
    status, _headers, body = caller.json(
        "GET", "/api/v3/workspaces/%s/sync/sources" % wid
    )
    assert status == 200, "source list failed: %s" % status
    return body["total"]


def _wait_for_log(caplog, needle: str, timeout: float = 2.0) -> bool:
    """访问日志在响应写完之后才落；客户端读得快时给它一点追上来的时间。"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if any(needle in record.getMessage() for record in caplog.records):
            return True
        time.sleep(0.01)
    return False


# ---------------------------------------------------------------------------
# 1. 同名 source/profile/connection 的跨 Workspace 隔离
# ---------------------------------------------------------------------------


def test_same_named_sources_profiles_and_connections_are_workspace_scoped(http_server, caplog):
    caplog.set_level(logging.INFO)
    seed_roles("m16-38-scope")
    httpd = http_server(
        {
            "/w1/shared.xml": {"body": XML_W1},
            "/w2/shared.xml": {"body": XML_W2},
        }
    )
    with make_server() as server:
        admin = as_role(server, "admin")
        visitor = as_role(server, "visitor")
        w1 = _make_workspace(admin, "W-one")
        w2 = _make_workspace(admin, "W-two")

        # 两个 W 的默认 profile 同名（default），连接也同名。
        c1 = _make_connection(admin, w1, "SDA main")
        c2 = _make_connection(admin, w2, "SDA main")
        assert c1["connection_id"] != c2["connection_id"]

        s1 = _make_source(
            admin, w1, "shared.xml", _url(httpd, "/w1/shared.xml"), "/w1/private/shared.xml", existing_target=True
        )
        s2 = _make_source(
            admin, w2, "shared.xml", _url(httpd, "/w2/shared.xml"), "/w2/private/shared.xml", existing_target=True
        )
        assert s1["name"] == s2["name"] == "shared.xml"
        assert s1["profile_id"] == s2["profile_id"] == "default"
        assert s1["source_id"] != s2["source_id"]

        status, _headers, listing1 = visitor.json(
            "GET", "/api/v3/workspaces/%s/sync/sources" % w1
        )
        assert status == 200
        assert [item["source_id"] for item in listing1["items"]] == [s1["source_id"]]
        assert s2["source_id"] not in json.dumps(listing1)

        status, _headers, listing2 = visitor.json(
            "GET", "/api/v3/workspaces/%s/sync/sources" % w2
        )
        assert status == 200
        assert [item["source_id"] for item in listing2["items"]] == [s2["source_id"]]
        assert "/w1/private/shared.xml" not in json.dumps(listing2)

        status, _headers, conns1 = visitor.json(
            "GET", "/api/v3/workspaces/%s/connections" % w1
        )
        assert status == 200
        assert [item["connection_id"] for item in conns1["items"]] == [c1["connection_id"]]
        status, _headers, conns2 = visitor.json(
            "GET", "/api/v3/workspaces/%s/connections" % w2
        )
        assert status == 200
        assert [item["connection_id"] for item in conns2["items"]] == [c2["connection_id"]]

        # 跨 W 改 source：404，且 W2 的 source 不被改。
        status, _headers, cross_patch = admin.json(
            "PATCH",
            "/api/v3/workspaces/%s/sync/sources/%s" % (w2, s1["source_id"]),
            {"name": "cross.xml"},
            headers={"If-Match": "1"},
        )
        assert status == 404, cross_patch
        assert "shared.xml" not in json.dumps(cross_patch)

        # 跨 W 触发 run：404，且不在 W2 建 run。
        runs_before = sync_run_store.list_runs(w2)["items"]
        status, _headers, cross_run = admin.json(
            "POST",
            "/api/v3/workspaces/%s/sync/sources/%s/run" % (w2, s1["source_id"]),
            {},
        )
        assert status == 404, cross_run
        assert sync_run_store.list_runs(w2)["items"] == runs_before

        # 跨 W 读候选：404；W2 列表不出现 W1 候选。
        candidate = sync_candidates.create_candidate(
            w1, "default", s1["source_id"], 1, "a" * 64, "scope:shared"
        )
        status, _headers, cross_candidate = visitor.json(
            "GET",
            "/api/v3/workspaces/%s/sync/candidates/%s" % (w2, candidate["candidate_id"]),
        )
        assert status == 404, cross_candidate
        assert visitor.json("GET", "/api/v3/workspaces/%s/sync/candidates" % w2)[2]["total"] == 0
        assert visitor.json("GET", "/api/v3/workspaces/%s/sync/candidates" % w1)[2]["total"] == 1

        # 跨 W 连接/凭据：404；带运维 token 也不能改另一个 W 的连接。
        status, _headers, cross_creds = visitor.json(
            "GET",
            "/api/v3/workspaces/%s/connections/%s/credentials" % (w2, c1["connection_id"]),
        )
        assert status == 404, cross_creds
        status, _headers, cross_conn_patch = admin.json(
            "PATCH",
            "/api/v3/workspaces/%s/connections/%s" % (w2, c1["connection_id"]),
            {"name": "cross"},
            headers=dict(_operator_headers(), **{"If-Match": "1"}),
        )
        assert status == 404, cross_conn_patch

        # W1 真跑一次：只写 W1。
        submitted = _run_source(admin, w1, s1["source_id"])
        run1 = _wait_run(w1, submitted["run"]["run_id"])
        _wait_task(submitted["task"]["id"])
        assert run1["run"]["state"] == "succeeded", run1["run"]
        assert _current_file(w1, "shared.xml") == XML_W1
        assert _current_file(w2, "shared.xml") == b"<config/>", "W1 的运行不能修改 W2 已有目标"

        # 跨 W 读列表/日志：响应与访问日志不出现 W1 私有路径。
        caplog.clear()
        status, _headers, w2_list = visitor.json(
            "GET", "/api/v3/workspaces/%s/sync/sources" % w2
        )
        assert status == 200
        assert "/w1/private/shared.xml" not in json.dumps(w2_list)
        assert _wait_for_log(caplog, "/api/v3/workspaces"), (
            "访问日志没有被 caplog 捕获，日志脱敏断言会变成空转"
        )
        assert "/w1/private/shared.xml" not in caplog.text

        # W1 产物不出现 W2 的路径/正文；反之亦然。
        w1_text = _scan_tree(_workspace_tree(w1))
        w2_text = _scan_tree(_workspace_tree(w2))
        assert "/w2/private/shared.xml" not in w1_text
        assert "/w1/private/shared.xml" not in w2_text
        assert XML_W2.decode("utf-8") not in w1_text
        assert XML_W1.decode("utf-8") not in w2_text

        # W2 真跑一次：只写 W2，不覆盖 W1。
        submitted2 = _run_source(admin, w2, s2["source_id"])
        run2 = _wait_run(w2, submitted2["run"]["run_id"])
        _wait_task(submitted2["task"]["id"])
        assert run2["run"]["state"] == "succeeded", run2["run"]
        assert _current_file(w2, "shared.xml") == XML_W2
        assert _current_file(w1, "shared.xml") == XML_W1

        events2 = sync_run_store.read_events(w2, run2["run"]["run_id"])
        assert "/w1/private/shared.xml" not in json.dumps(events2)
        assert XML_W1.decode("utf-8") not in json.dumps(events2)
        events1 = sync_run_store.read_events(w1, run1["run"]["run_id"])
        assert "/w2/private/shared.xml" not in json.dumps(events1)
        assert XML_W2.decode("utf-8") not in json.dumps(events1)


def _url(httpd: Any, path: str) -> str:
    return "http://127.0.0.1:%d%s" % (httpd.server_address[1], path)


# ---------------------------------------------------------------------------
# 2. 凭据导入/替换/撤销必须独立运维授权
# ---------------------------------------------------------------------------


def test_credential_import_replace_revoke_requires_admin_and_safe_projection(caplog):
    caplog.set_level(logging.INFO)
    seed_roles("m16-38-cred")
    responses: List[Any] = []
    with make_server() as server:
        admin = as_role(server, "admin")
        visitor = as_role(server, "visitor")
        w1 = _make_workspace(admin, "W-cred-one")
        w2 = _make_workspace(admin, "W-cred-two")
        cid = _make_connection(admin, w1, "SDA main")["connection_id"]
        other_cid = _make_connection(admin, w2, "SDA main")["connection_id"]

        candidate_url = "/api/v3/workspaces/%s/connections/%s/credentials/candidates" % (
            w1,
            cid,
        )

        # 访客即使登记了身份仍无管理权限，且不落 credentials.json。
        status, _headers, denied = visitor.json(
            "POST", candidate_url, {"username": USERNAME, "password": PASSWORD}
        )
        responses.append(denied)
        assert status == 403, denied
        assert denied["error"]["detail"]["action"] == "integration_credentials_manage"
        assert not os.path.exists(sync_paths.sync_file(w1, "credentials.json"))

        # 有 token 但未注入 vault：503，不伪造成功。
        status, _headers, no_vault = admin.json(
            "POST",
            candidate_url,
            {"username": USERNAME, "password": PASSWORD},
            headers=_operator_headers(),
        )
        responses.append(no_vault)
        assert status == 503, no_vault
        assert not os.path.exists(sync_paths.sync_file(w1, "credentials.json"))

        vault = FakeVault()
        api_connections.configure_vault(vault)
        status, _headers, pending = admin.json(
            "POST",
            candidate_url,
            {"username": USERNAME, "password": PASSWORD},
            headers=_operator_headers(),
        )
        responses.append(pending)
        assert status == 201, pending
        assert pending["candidate"]["state"] == "pending"
        _assert_absent(json.dumps(pending), "candidate response")
        _assert_no_forbidden_keys(pending, "candidate response")
        candidate_id = pending["candidate"]["candidate_id"]

        # 只读身份可以看安全投影，但看不到 username/account/secret。
        status, _headers, state = visitor.json(
            "GET",
            "/api/v3/workspaces/%s/connections/%s/credentials" % (w1, cid),
        )
        responses.append(state)
        assert status == 200, state
        assert state["candidate"]["candidate_id"] == candidate_id
        _assert_absent(json.dumps(state), "credential state")
        _assert_no_forbidden_keys(state, "credential state")

        activate_url = (
            "/api/v3/workspaces/%s/connections/%s/credentials/candidates/%s/activate"
            % (w1, cid, candidate_id)
        )
        status, _headers, activate_denied = visitor.json(
            "POST", activate_url, {"probe_epoch": 0}
        )
        responses.append(activate_denied)
        assert status == 403, activate_denied
        assert activate_denied["error"]["detail"]["action"] == "integration_credentials_manage"

        status, _headers, activated = admin.json(
            "POST", activate_url, {"probe_epoch": 0}, headers=_operator_headers()
        )
        responses.append(activated)
        assert status == 202, activated
        operation = activated["operation"]
        assert operation["state"] == "succeeded", operation
        assert operation["result"]["credential"]["state"] == "valid"
        _assert_absent(json.dumps(operation), "activate operation")
        _assert_no_forbidden_keys(operation, "activate operation")
        revision = operation["result"]["credential"]["revision"]

        revoke_url = "/api/v3/workspaces/%s/connections/%s/credentials/revoke" % (w1, cid)
        status, _headers, revoke_denied = visitor.json(
            "POST", revoke_url, headers={"If-Match": str(revision)}
        )
        responses.append(revoke_denied)
        assert status == 403, revoke_denied
        assert revoke_denied["error"]["detail"]["action"] == "integration_credentials_manage"

        status, _headers, revoked = admin.json(
            "POST",
            revoke_url,
            headers=dict(_operator_headers(), **{"If-Match": str(revision)}),
        )
        responses.append(revoked)
        assert status == 200, revoked
        assert revoked["state"] == "unconfigured"
        _assert_absent(json.dumps(revoked), "revoke response")
        _assert_no_forbidden_keys(revoked, "revoke response")
        assert vault.items == {}

        # 跨 W 连接/凭据读仍是 404。
        status, _headers, cross = visitor.json(
            "GET",
            "/api/v3/workspaces/%s/connections/%s/credentials" % (w2, cid),
        )
        responses.append(cross)
        assert status == 404, cross
        assert visitor.json(
            "GET", "/api/v3/workspaces/%s/connections" % w2
        )[2]["items"][0]["connection_id"] == other_cid

        # 响应、操作记录、凭据元数据与日志都不含假 secret/运维 token。
        for index, payload in enumerate(responses):
            _assert_absent(json.dumps(payload, ensure_ascii=False), "response #%d" % index)
        _assert_absent(_scan_tree(_workspace_tree(w1)), "W1 sync/credential files")
        _assert_absent(_scan_tree(_workspace_tree(w2)), "W2 sync/credential files")
        assert _wait_for_log(caplog, "/api/v3/workspaces"), (
            "访问日志没有被 caplog 捕获，日志脱敏断言会变成空转"
        )
        _assert_absent(caplog.text, "server logs")

        with open(sync_paths.sync_file(w1, "operations.json"), "r", encoding="utf-8") as handle:
            operations = json.load(handle)
        _assert_no_forbidden_keys(operations, "operations record")
        with open(sync_paths.sync_file(w1, "credentials.json"), "r", encoding="utf-8") as handle:
            credentials = json.load(handle)
        _assert_no_forbidden_keys(credentials, "credentials metadata")


# ---------------------------------------------------------------------------
# 3. CSRF / Origin / 伪造 Host
# ---------------------------------------------------------------------------


def test_csrf_origin_and_forged_host_do_not_cross_workspace_boundaries():
    seed_roles("m16-38-csrf")
    with make_server() as server:
        admin = as_role(server, "admin")
        visitor = as_role(server, "visitor")
        w1 = _make_workspace(admin, "W-csrf-one")
        w2 = _make_workspace(admin, "W-csrf-two")
        _make_source(admin, w1, "one.xml", "https://source.example.invalid/one.xml")
        _make_source(admin, w2, "two.xml", "https://source.example.invalid/two.xml")
        before = _source_total(visitor, w1)

        # 缺 CSRF 自定义头的写请求：403，且 W1 来源数不变。
        status, _headers, raw = admin.request(
            "POST",
            "/api/v3/workspaces/%s/sync/sources" % w1,
            body=_source_payload("csrf.xml", "https://source.example.invalid/csrf.xml"),
            with_csrf=False,
        )
        assert status == 403, raw
        assert _is_csrf_error(raw), raw

        # 跨站 Origin / null Origin 但缺自定义头：同样被 CSRF 闸门拒绝。
        for origin in ("https://evil.example", "null"):
            status, _headers, raw = admin.request(
                "POST",
                "/api/v3/workspaces/%s/sync/sources" % w1,
                body=_source_payload(
                    "origin.xml", "https://source.example.invalid/origin.xml"
                ),
                headers={"Origin": origin, "Sec-Fetch-Site": "cross-site"},
                with_csrf=False,
            )
            assert status == 403, (origin, raw)
            assert _is_csrf_error(raw), (origin, raw)
        assert _source_total(visitor, w1) == before

        # 伪造 Host：直接 421/403，不进入路由；读/写都不落对象。
        status, _headers, raw = admin.request(
            "GET",
            "/api/v3/workspaces/%s/sync/sources" % w1,
            headers={"Host": "attacker.example"},
        )
        assert status in (403, 421), raw
        status, _headers, raw = admin.request(
            "POST",
            "/api/v3/workspaces/%s/sync/sources" % w1,
            body=_source_payload("host-scope.xml", "https://source.example.invalid/host.xml"),
            headers={"Host": "attacker.example", "X-Workspace-Id": w2},
        )
        assert status in (403, 421), raw
        assert _source_total(visitor, w1) == before
        w2_names = [
            item["name"]
            for item in visitor.json(
                "GET", "/api/v3/workspaces/%s/sync/sources" % w2
            )[2]["items"]
        ]
        assert w2_names == ["two.xml"]

        # 浏览器跨站自定义头必须先过 CORS 预检；服务端不提供任何允许头。
        status, headers, raw = admin.request(
            "OPTIONS",
            "/api/v3/workspaces/%s/sync/sources" % w1,
            headers={
                "Origin": "https://evil.example",
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "x-requested-with",
            },
        )
        assert status == 501, raw
        assert not any(
            name.lower().startswith("access-control-allow-") for name in headers
        )


# ---------------------------------------------------------------------------
# 4. SSRF / 重定向 / collector 响应守卫
# ---------------------------------------------------------------------------


def test_ssrf_metadata_private_redirect_and_collector_guard(http_server, monkeypatch):
    calls: List[int] = []
    previous_reader = sync_tasks._SDA_READER
    sync_tasks.register_sda_reader(lambda *_args: calls.append(1) or {"status": "succeeded"})
    try:
        httpd = http_server(
            {
                "/redirect-metadata": {
                    "location": "http://169.254.169.254/latest/meta-data/iam/security-credentials/"
                },
                "/redirect-private": {"location": "http://10.0.0.1/secret.xml"},
                "/redirect-file": {"location": "file:///etc/passwd"},
            }
        )
        seed_roles("m16-38-ssrf")
        with make_server() as server:
            admin = as_role(server, "admin")
            w1 = _make_workspace(admin, "W-ssrf")
            status, _headers, userinfo = admin.json(
                "POST",
                "/api/v3/workspaces/%s/sync/sources" % w1,
                _source_payload(
                    "meta-userinfo.xml",
                    "http://%s:%s@169.254.169.254/latest/meta-data/"
                    % (USERNAME, PASSWORD),
                ),
            )
            assert status == 400, userinfo
            assert userinfo["error"]["detail"]["field"] == "resource_ref"

            direct = _make_source(
                admin, w1, "meta.xml", "http://169.254.169.254/latest/meta-data/"
            )
            redirect_meta = _make_source(
                admin, w1, "redir-meta.xml", _url(httpd, "/redirect-metadata")
            )
            redirect_private = _make_source(
                admin, w1, "redir-private.xml", _url(httpd, "/redirect-private")
            )
            redirect_file = _make_source(
                admin, w1, "redir-file.xml", _url(httpd, "/redirect-file")
            )

            # 直连 metadata：默认策略（白名单为空）也必须由 link-local 拒绝，不发请求、不落字节。
            submitted = _run_source(admin, w1, direct["source_id"])
            run = _wait_run(w1, submitted["run"]["run_id"])
            task = _wait_task(submitted["task"]["id"])
            assert run["run"]["state"] == "failed", run["run"]
            assert task["result"]["items"][0]["reason_code"] == "redirect_blocked"
            assert _current_file(w1, "meta.xml") is None
            assert httpd.requests == []
            _assert_absent(
                json.dumps(run) + json.dumps(task)
                + json.dumps(sync_run_store.read_events(w1, run["run"]["run_id"])),
                "metadata run artifacts",
            )

            # 只放行本地首跳；私网重定向因此会在连接前被白名单拒绝。
            monkeypatch.setenv("MCHECKER_DOWNLOAD_ALLOW", "127.0.0.1")

            # 重定向到 metadata / 私网 / file:：只完成首跳，不跟随。
            for source in (redirect_meta, redirect_private, redirect_file):
                seen = len(httpd.requests)
                submitted = _run_source(admin, w1, source["source_id"])
                run = _wait_run(w1, submitted["run"]["run_id"])
                task = _wait_task(submitted["task"]["id"])
                assert run["run"]["state"] == "failed", (source["name"], run["run"])
                item = task["result"]["items"][0]
                assert item["reason_code"] == "redirect_blocked", (source["name"], item)
                assert _current_file(w1, source["name"]) is None
                assert len(httpd.requests) == seen + 1, "只允许首跳请求"
                _assert_absent(
                    json.dumps(run) + json.dumps(task)
                    + json.dumps(sync_run_store.read_events(w1, run["run"]["run_id"])),
                    "%s run artifacts" % source["name"],
                )
            assert calls == [], "普通 HTTP 来源不得触发 SDA/collector 登录"
    finally:
        sync_tasks.register_sda_reader(previous_reader)

    # collector 响应守卫：跨域/HTTPS 降级/重定向都在读正文前拒绝。
    for final_url, origin, status in (
        ("https://evil.example/x", "https://sda.example", 200),
        ("http://sda.example/x", "https://sda.example", 200),
        ("https://sda.example/x", "https://sda.example", 302),
    ):
        with pytest.raises(StreamError) as excinfo:
            guard_response(status, {}, final_url, origin, 1024)
        assert excinfo.value.reason_code == "redirect_blocked"

    safe = guard_response(
        200,
        {"content-type": "application/octet-stream", "set-cookie": COOKIE},
        "https://sda.example/x",
        "https://sda.example",
        1024,
    )
    assert sorted(safe) == ["content_length", "content_type", "status"]
    _assert_absent(json.dumps(safe), "collector guard result")


# ---------------------------------------------------------------------------
# 5. 日志 / 操作记录 / run 事件 / 任务 / 备份脱敏
# ---------------------------------------------------------------------------


def test_release_package_allowlist_cannot_carry_runtime_data_or_tests():
    """离线包只从源码白名单构建：运行时数据根与测试目录都不是成员。"""
    from scripts import make_release

    assert {"data", "tests"} <= set(make_release.FORBIDDEN_COMPONENTS)
    assert not any(
        entry == "data"
        or entry.startswith("data/")
        or entry == "tests"
        or entry.startswith("tests/")
        for entry in make_release.RELEASE_ENTRIES
    ), "发布包白名单不得包含运行时数据或测试目录"


def test_logs_operations_runs_and_backup_do_not_contain_secret_markers(
    http_server, caplog, tmp_path
):
    caplog.set_level(logging.DEBUG)
    seed_roles("m16-38-redact")
    responses: List[Any] = []
    httpd = http_server({"/plain.xml": {"body": XML_PLAIN}})
    with make_server() as server:
        admin = as_role(server, "admin")
        w1 = _make_workspace(admin, "W-redact")
        cid = _make_connection(admin, w1, "SDA main")["connection_id"]

        vault = FakeVault()
        api_connections.configure_vault(vault)
        api_connections.configure_probe(lambda *_args: {"ok": True})

        status, _headers, pending = admin.json(
            "POST",
            "/api/v3/workspaces/%s/connections/%s/credentials/candidates" % (w1, cid),
            {"username": USERNAME, "password": PASSWORD},
            headers=_operator_headers(),
        )
        responses.append(pending)
        assert status == 201, pending
        candidate_id = pending["candidate"]["candidate_id"]

        status, _headers, probed = admin.json(
            "POST",
            "/api/v3/workspaces/%s/connections/%s/credentials/probe" % (w1, cid),
            {"candidate_id": candidate_id},
            headers=_operator_headers(),
        )
        responses.append(probed)
        assert status == 202, probed
        assert probed["operation"]["state"] == "succeeded"

        status, _headers, activated = admin.json(
            "POST",
            "/api/v3/workspaces/%s/connections/%s/credentials/candidates/%s/activate"
            % (w1, cid, candidate_id),
            {"probe_epoch": 0},
            headers=_operator_headers(),
        )
        responses.append(activated)
        assert status == 202, activated
        revision = activated["operation"]["result"]["credential"]["revision"]

        status, _headers, revoked = admin.json(
            "POST",
            "/api/v3/workspaces/%s/connections/%s/credentials/revoke" % (w1, cid),
            headers=dict(_operator_headers(), **{"If-Match": str(revision)}),
        )
        responses.append(revoked)
        assert status == 200, revoked

        source = _make_source(
            admin, w1, "plain.xml", _url(httpd, "/plain.xml"), "/plain/plain.xml", existing_target=True
        )
        submitted = _run_source(admin, w1, source["source_id"])
        run = _wait_run(w1, submitted["run"]["run_id"])
        task = _wait_task(submitted["task"]["id"])
        assert run["run"]["state"] == "succeeded", run["run"]

        # 伪造 Cookie/CSRF/运维 token 头做一次普通读；响应与日志都不得回显。
        status, headers, raw = admin.request(
            "GET",
            "/api/v3/workspaces/%s/sync/sources" % w1,
            headers={
                "Cookie": "mc_cid=" + COOKIE,
                "X-Csrf-Token": CSRF,
                "X-Operator-Token": OPERATOR_TOKEN + "-wrong",
            },
        )
        assert status == 200, raw
        responses.append(json.loads(raw.decode("utf-8")))
        assert not any(name.lower() == "set-cookie" for name in headers)

        # 数据目录（含 credentials/operations/sources/runs/candidates）不含 marker。
        _assert_absent(_scan_tree(storage.DATA_DIR), "data root")
        assert _wait_for_log(caplog, "/api/v3/workspaces"), (
            "访问日志没有被 caplog 捕获，日志脱敏断言会变成空转"
        )
        _assert_absent(caplog.text, "server logs")

        # 备份整棵数据根后再扫一遍（backup 会排除 *.log，日志由 caplog 覆盖）。
        backup_dir = str(tmp_path / "backup-m16-38")
        backup_v3.backup(storage.DATA_DIR, backup_dir)
        _assert_absent(_scan_tree(backup_dir), "backup")
        assert backup_v3.verify(backup_dir)["ok"] is True

        for index, payload in enumerate(responses):
            _assert_absent(json.dumps(payload, ensure_ascii=False), "response #%d" % index)
            _assert_no_forbidden_keys(payload, "response #%d" % index)
        _assert_absent(json.dumps(task), "task payload")
        _assert_no_forbidden_keys(task, "task payload")
        _assert_absent(
            json.dumps(sync_run_store.read_events(w1, run["run"]["run_id"])),
            "run events",
        )

        with open(sync_paths.sync_file(w1, "operations.json"), "r", encoding="utf-8") as handle:
            operations = json.load(handle)
        _assert_no_forbidden_keys(operations, "operations record")
        with open(sync_paths.sync_file(w1, "credentials.json"), "r", encoding="utf-8") as handle:
            credentials = json.load(handle)
        _assert_no_forbidden_keys(credentials, "credentials metadata")
