"""M16-30 连接与认证管理 API：安全摘要、运维授权、幂等与异步操作。

真实 `MCServer` + 真实 HTTP；vault/probe 由测试注入，明文只存在于假 vault 的
内存里。断言口径：
- 只读用户看得到连接/凭据状态，看不到 account/secret；
- 凭据写接口即使管理员也必须带独立运维 token；
- 跨 workspace 的连接/操作一律 404，不泄露另一个工作区的对象；
- CSRF 头缺失的跨站写请求 403 且不落盘；伪造 Host 不能改变工作区/授权判定；
- 错密码锁存后重复 probe 不再调用凭据提供方；
- 未配置 probe/vault 时操作 blocked/503，绝不伪造成功；
- 同一幂等键 + 同一请求返回同一操作，不同请求 409。
"""

from __future__ import annotations

import json
import os

import pytest
from authz_fixtures import as_role, make_server, seed_roles

from app.server import api_connections
from app.services import authz, sync_paths

OPERATOR_TOKEN = "m16-30-test-operator-token"
FORBIDDEN_KEYS = frozenset(
    {
        "password",
        "passwd",
        "account",
        "username",
        "cookie",
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


class FakeVault:
    """测试专用：只在内存里保存密文占位；生产由 collector SecretStore 实现。"""

    def __init__(self) -> None:
        self.items = {}
        self.puts = []
        self.deletes = []
        self.fail_delete = False

    def put(self, workspace_id, connection_id, generation, plaintext):
        key = (workspace_id, connection_id, generation)
        self.items[key] = bytes(plaintext)
        self.puts.append(key)
        return {"generation": generation}

    def delete(self, workspace_id, connection_id, generation):
        key = (workspace_id, connection_id, generation)
        self.deletes.append(key)
        if self.fail_delete and key in self.items:
            raise RuntimeError("disk full")
        return self.items.pop(key, None) is not None


@pytest.fixture(autouse=True)
def connection_api_ports(monkeypatch):
    monkeypatch.setenv("MCHECKER_OPERATOR_TOKEN", OPERATOR_TOKEN)
    api_connections.configure_vault(None)
    api_connections.configure_probe(None)
    yield
    api_connections.configure_vault(None)
    api_connections.configure_probe(None)


def operator_header():
    return {"X-Operator-Token": OPERATOR_TOKEN}


def make_workspace(caller, name):
    status, _headers, body = caller.json("POST", "/api/v3/workspaces", {"name": name})
    assert status == 201, body
    return body["workspace"]["id"]


def make_connection(caller, wid, name="SDA main"):
    status, _headers, body = caller.json(
        "POST",
        "/api/v3/workspaces/%s/connections" % wid,
        {"name": name, "origin": "https://sda.example.invalid"},
        headers=operator_header(),
    )
    assert status == 201, body
    return body


def begin_candidate(caller, wid, cid, vault, username="operator-account", password="super-secret-pw"):
    api_connections.configure_vault(vault)
    status, _headers, body = caller.json(
        "POST",
        "/api/v3/workspaces/%s/connections/%s/credentials/candidates" % (wid, cid),
        {"username": username, "password": password},
        headers=operator_header(),
    )
    assert status == 201, body
    return body


def walk_keys(value):
    if isinstance(value, dict):
        for key, child in value.items():
            yield key
            yield from walk_keys(child)
    elif isinstance(value, list):
        for child in value:
            yield from walk_keys(child)


def assert_no_secret_keys(payload):
    for key in walk_keys(payload):
        assert str(key).lower() not in FORBIDDEN_KEYS, key


def read_file(path):
    with open(path, "r", encoding="utf-8") as handle:
        return handle.read()


def test_read_only_sees_connection_status_without_account_or_secret():
    seed_roles("conn-read")
    with make_server() as server:
        admin = as_role(server, "admin")
        visitor = as_role(server, "visitor")
        wid = make_workspace(admin, "W1")
        connection = make_connection(admin, wid)
        cid = connection["connection_id"]
        vault = FakeVault()
        pending = begin_candidate(admin, wid, cid, vault)
        assert pending["candidate"]["state"] == "pending"

        status, _headers, listing = visitor.json(
            "GET", "/api/v3/workspaces/%s/connections" % wid
        )
        assert status == 200, listing
        assert len(listing["items"]) == 1
        item = listing["items"][0]
        assert item["connection_id"] == cid
        assert item["enabled"] is True
        assert item["credential"]["state"] == "unconfigured"
        assert item["credential"]["candidate"]["state"] == "pending"
        assert_no_secret_keys(listing)
        raw = json.dumps(listing, ensure_ascii=False)
        assert "operator-account" not in raw
        assert "super-secret-pw" not in raw

        status, _headers, state = visitor.json(
            "GET", "/api/v3/workspaces/%s/connections/%s/credentials" % (wid, cid)
        )
        assert status == 200, state
        assert state["state"] == "unconfigured"
        assert state["candidate"]["candidate_id"] == pending["candidate"]["candidate_id"]
        assert_no_secret_keys(state)

        credentials_text = read_file(sync_paths.sync_file(wid, "credentials.json"))
        assert "operator-account" not in credentials_text
        assert "super-secret-pw" not in credentials_text


def test_credential_writes_require_operator_token_even_for_admin():
    seed_roles("conn-auth")
    with make_server() as server:
        admin = as_role(server, "admin")
        visitor = as_role(server, "visitor")
        wid = make_workspace(admin, "W1")
        cid = make_connection(admin, wid)["connection_id"]
        candidate_url = "/api/v3/workspaces/%s/connections/%s/credentials/candidates" % (
            wid,
            cid,
        )

        status, _headers, payload = admin.json(
            "POST", candidate_url, {"username": "u", "password": "p"}
        )
        assert status == 403, payload
        assert payload["error"]["detail"]["field"] == "operator_token"
        assert not os.path.exists(sync_paths.sync_file(wid, "credentials.json"))

        status, _headers, payload = visitor.json(
            "POST",
            candidate_url,
            {"username": "u", "password": "p"},
            headers=operator_header(),
        )
        assert status == 403, payload
        assert payload["error"]["detail"]["action"] == "integration_credentials_manage"

        status, _headers, payload = admin.json(
            "PATCH",
            "/api/v3/workspaces/%s/connections/%s" % (wid, cid),
            {"name": "renamed"},
            headers={"If-Match": "1"},
        )
        assert status == 403, payload
        assert payload["error"]["detail"]["field"] == "operator_token"

        api_connections.configure_vault(FakeVault())
        status, _headers, body = admin.json(
            "POST",
            candidate_url,
            {"username": "u", "password": "p"},
            headers=operator_header(),
        )
        assert status == 201, body
        assert body["candidate"]["state"] == "pending"


def test_cross_workspace_and_forged_host_cannot_reach_another_workspace():
    seed_roles("conn-scope")
    with make_server() as server:
        admin = as_role(server, "admin")
        wid1 = make_workspace(admin, "W1")
        wid2 = make_workspace(admin, "W2")
        connection = make_connection(admin, wid1, "Secret conn")
        cid = connection["connection_id"]

        status, _headers, other = admin.json(
            "GET", "/api/v3/workspaces/%s/connections" % wid2
        )
        assert status == 200
        assert other["items"] == []

        status, _headers, payload = admin.json(
            "GET", "/api/v3/workspaces/%s/connections/%s/credentials" % (wid2, cid)
        )
        assert status == 404, payload
        assert "Secret conn" not in json.dumps(payload, ensure_ascii=False)

        status, _headers, payload = admin.json(
            "PATCH",
            "/api/v3/workspaces/%s/connections/%s" % (wid2, cid),
            {"name": "cross"},
            headers=dict(operator_header(), **{"If-Match": "1"}),
        )
        assert status == 404, payload

        status, _headers, payload = admin.json(
            "POST",
            "/api/v3/workspaces/%s/connections/%s/credentials/probe" % (wid2, cid),
            {},
            headers=operator_header(),
        )
        assert status == 404, payload
        assert not os.path.exists(sync_paths.sync_file(wid2, "operations.json"))

        # 在 W1 建一个操作，再从 W2 路径读取：不能跨工作区看见。
        api_connections.configure_vault(FakeVault())
        begin_candidate(admin, wid1, cid, FakeVault())
        api_connections.configure_probe(lambda _w, _c, _cid: {"ok": True})
        status, _headers, probe = admin.json(
            "POST",
            "/api/v3/workspaces/%s/connections/%s/credentials/probe" % (wid1, cid),
            {},
            headers=operator_header(),
        )
        assert status == 202, probe
        op_id = probe["operation"]["op_id"]
        status, _headers, payload = admin.json(
            "GET",
            "/api/v3/workspaces/%s/connections/%s/operations/%s" % (wid2, cid, op_id),
        )
        assert status == 404, payload

        # 伪造 Host 不能改变工作区范围，也不能绕过运维授权。
        status, _headers, forged = admin.request(
            "GET",
            "/api/v3/workspaces/%s/connections" % wid1,
            headers={"Host": "attacker.example"},
        )
        assert status in (403, 421), forged
        status, _headers, payload = admin.json(
            "POST",
            "/api/v3/workspaces/%s/connections/%s/credentials/revoke" % (wid1, cid),
            headers=dict(
                {"Host": "attacker.example", "If-Match": "1"}, **operator_header()
            ),
        )
        assert status in (403, 421), payload

        # 跨站写：缺少 X-Requested-With → 403 且不产生操作。
        status, _headers, raw = admin.request(
            "POST",
            "/api/v3/workspaces/%s/connections/%s/credentials/probe" % (wid1, cid),
            body={},
            headers=operator_header(),
            with_csrf=False,
        )
        assert status == 403, raw
        assert b"X-Requested-With" in raw


def test_probe_without_configured_probe_is_blocked_not_faked():
    seed_roles("conn-probe-default")
    with make_server() as server:
        admin = as_role(server, "admin")
        wid = make_workspace(admin, "W1")
        cid = make_connection(admin, wid)["connection_id"]
        begin_candidate(admin, wid, cid, FakeVault())

        status, _headers, payload = admin.json(
            "POST",
            "/api/v3/workspaces/%s/connections/%s/credentials/probe" % (wid, cid),
            {},
            headers=operator_header(),
        )
        assert status == 202, payload
        op = payload["operation"]
        assert op["state"] == "blocked"
        assert op["reason_code"] == "collector_offline"
        assert op["result"]["probe"] == "blocked"
        assert op["started_at"] and op["finished_at"]
        assert_no_secret_keys(op)

        status, _headers, state = admin.json(
            "GET", "/api/v3/workspaces/%s/connections/%s/credentials" % (wid, cid)
        )
        assert status == 200, state
        assert state["candidate"]["state"] == "pending"
        assert state["state"] == "unconfigured"

        status, _headers, fetched = as_role(server, "visitor").json(
            "GET",
            "/api/v3/workspaces/%s/connections/%s/operations/%s"
            % (wid, cid, op["op_id"]),
        )
        assert status == 200, fetched
        assert fetched["operation"]["op_id"] == op["op_id"]
        assert fetched["operation"]["state"] == "blocked"
        assert "super-secret-pw" not in json.dumps(fetched, ensure_ascii=False)


def test_bad_credentials_latches_and_repeated_probe_does_not_call_provider_again():
    seed_roles("conn-probe-lock")
    calls = []

    def probe(_wid, _cid, candidate_id):
        calls.append(candidate_id)
        if len(calls) == 1:
            return {"ok": True}
        return {"ok": False, "reason_code": "bad_credentials"}

    api_connections.configure_probe(probe)
    with make_server() as server:
        admin = as_role(server, "admin")
        wid = make_workspace(admin, "W1")
        cid = make_connection(admin, wid)["connection_id"]
        pending = begin_candidate(admin, wid, cid, FakeVault())
        candidate_id = pending["candidate"]["candidate_id"]

        status, _headers, first = admin.json(
            "POST",
            "/api/v3/workspaces/%s/connections/%s/credentials/probe" % (wid, cid),
            {"candidate_id": candidate_id},
            headers=operator_header(),
        )
        assert status == 202, first
        assert first["operation"]["state"] == "succeeded"
        assert first["operation"]["result"]["probe"] == "succeeded"
        assert len(calls) == 1

        status, _headers, second = admin.json(
            "POST",
            "/api/v3/workspaces/%s/connections/%s/credentials/probe" % (wid, cid),
            {"candidate_id": candidate_id},
            headers=operator_header(),
        )
        assert status == 202, second
        assert second["operation"]["state"] == "failed"
        assert second["operation"]["reason_code"] == "bad_credentials"
        assert second["operation"]["result"]["credential"]["state"] == "locked"
        assert len(calls) == 2

        status, _headers, third = admin.json(
            "POST",
            "/api/v3/workspaces/%s/connections/%s/credentials/probe" % (wid, cid),
            {"candidate_id": candidate_id},
            headers=operator_header(),
        )
        assert status == 202, third
        assert third["operation"]["state"] == "blocked"
        assert third["operation"]["reason_code"] == "locked"
        assert len(calls) == 2, "锁定后不得再次调用凭据提供方"

        status, _headers, state = admin.json(
            "GET", "/api/v3/workspaces/%s/connections/%s/credentials" % (wid, cid)
        )
        assert status == 200, state
        assert state["state"] == "locked"
        assert state["last_error"] == "bad_credentials"
        assert state["active_generation"] is None
        assert state["candidate"]["state"] == "failed"
        assert_no_secret_keys(state)

        credentials_text = read_file(sync_paths.sync_file(wid, "credentials.json"))
        operations_text = read_file(sync_paths.sync_file(wid, "operations.json"))
        for text in (credentials_text, operations_text):
            assert "super-secret-pw" not in text
            assert "operator-account" not in text


def test_activate_stale_epoch_is_409_and_success_returns_202_operation():
    seed_roles("conn-activate")
    with make_server() as server:
        admin = as_role(server, "admin")
        wid = make_workspace(admin, "W1")
        cid = make_connection(admin, wid)["connection_id"]
        pending = begin_candidate(admin, wid, cid, FakeVault())
        candidate_id = pending["candidate"]["candidate_id"]
        url = "/api/v3/workspaces/%s/connections/%s/credentials/candidates/%s/activate" % (
            wid,
            cid,
            candidate_id,
        )

        status, _headers, stale = admin.json(
            "POST", url, {"probe_epoch": 99}, headers=operator_header()
        )
        assert status == 409, stale
        assert stale["error"]["code"] == "SOURCE_CHANGED"
        assert not os.path.exists(sync_paths.sync_file(wid, "operations.json"))

        status, _headers, activated = admin.json(
            "POST", url, {"probe_epoch": 0}, headers=operator_header()
        )
        assert status == 202, activated
        op = activated["operation"]
        assert op["state"] == "succeeded"
        assert op["result"]["credential"]["state"] == "valid"
        assert op["result"]["credential"]["active_generation"] == 1
        assert op["result"]["credential"]["auth_epoch"] == 1
        assert_no_secret_keys(op)

        status, _headers, state = admin.json(
            "GET", "/api/v3/workspaces/%s/connections/%s/credentials" % (wid, cid)
        )
        assert status == 200, state
        assert state["state"] == "valid"
        assert state["active_generation"] == 1

        status, _headers, again = admin.json(
            "POST", url, {"probe_epoch": 0}, headers=operator_header()
        )
        assert status in (404, 409), again


def test_revoke_requires_operator_and_storage_failure_is_not_reported_as_success():
    seed_roles("conn-revoke")
    with make_server() as server:
        admin = as_role(server, "admin")
        wid = make_workspace(admin, "W1")
        cid = make_connection(admin, wid)["connection_id"]
        vault = FakeVault()
        pending = begin_candidate(admin, wid, cid, vault)
        activate_url = "/api/v3/workspaces/%s/connections/%s/credentials/candidates/%s/activate" % (
            wid,
            cid,
            pending["candidate"]["candidate_id"],
        )
        status, _headers, activated = admin.json(
            "POST", activate_url, {"probe_epoch": 0}, headers=operator_header()
        )
        assert status == 202, activated
        revision = activated["operation"]["result"]["credential"]["revision"]
        url = "/api/v3/workspaces/%s/connections/%s/credentials/revoke" % (wid, cid)

        status, _headers, denied = admin.json(
            "POST", url, headers={"If-Match": str(revision)}
        )
        assert status == 403, denied
        assert denied["error"]["detail"]["field"] == "operator_token"

        vault.fail_delete = True
        status, _headers, failed = admin.json(
            "POST", url, headers=dict(operator_header(), **{"If-Match": str(revision)})
        )
        assert status == 500, failed
        assert failed["error"]["code"] == "STORAGE_FAILURE"
        status, _headers, still = admin.json(
            "GET", "/api/v3/workspaces/%s/connections/%s/credentials" % (wid, cid)
        )
        assert still["state"] == "valid"
        assert still["active_generation"] == 1

        vault.fail_delete = False
        status, _headers, revoked = admin.json(
            "POST", url, headers=dict(operator_header(), **{"If-Match": str(revision)})
        )
        assert status == 200, revoked
        assert revoked["state"] == "unconfigured"
        assert revoked["active_generation"] is None
        assert revoked["auth_epoch"] == 2
        assert vault.items == {}


def test_idempotency_same_key_same_body_returns_same_operation():
    seed_roles("conn-idem")
    calls = []

    def probe(_wid, _cid, candidate_id):
        calls.append(candidate_id)
        return {"ok": True}

    api_connections.configure_probe(probe)
    with make_server() as server:
        admin = as_role(server, "admin")
        wid = make_workspace(admin, "W1")
        cid = make_connection(admin, wid)["connection_id"]
        pending = begin_candidate(admin, wid, cid, FakeVault())
        candidate_id = pending["candidate"]["candidate_id"]
        url = "/api/v3/workspaces/%s/connections/%s/credentials/probe" % (wid, cid)
        headers = dict(operator_header(), **{"Idempotency-Key": "probe-key-1"})

        status, _headers, first = admin.json(
            "POST", url, {"candidate_id": candidate_id}, headers=headers
        )
        assert status == 202, first
        status, _headers, replay = admin.json(
            "POST", url, {"candidate_id": candidate_id}, headers=headers
        )
        assert status == 202, replay
        assert replay["operation"]["op_id"] == first["operation"]["op_id"]
        assert len(calls) == 1

        second = begin_candidate(admin, wid, cid, FakeVault())
        status, _headers, conflict = admin.json(
            "POST",
            url,
            {"candidate_id": second["candidate"]["candidate_id"]},
            headers=headers,
        )
        assert status == 409, conflict
        assert conflict["error"]["code"] == "IDEMPOTENCY_MISMATCH"
        assert len(calls) == 1


def test_candidate_without_vault_is_503_and_writes_nothing():
    seed_roles("conn-novault")
    with make_server() as server:
        admin = as_role(server, "admin")
        wid = make_workspace(admin, "W1")
        cid = make_connection(admin, wid)["connection_id"]
        status, _headers, payload = admin.json(
            "POST",
            "/api/v3/workspaces/%s/connections/%s/credentials/candidates" % (wid, cid),
            {"username": "u", "password": "p"},
            headers=operator_header(),
        )
        assert status == 503, payload
        assert payload["error"]["code"] == "CONFIG_DISABLED"
        assert not os.path.exists(sync_paths.sync_file(wid, "credentials.json"))
        assert not os.path.exists(sync_paths.sync_file(wid, "operations.json"))


def test_connection_create_patch_cas_and_capabilities():
    seed_roles("conn-crud")
    with make_server() as server:
        admin = as_role(server, "admin")
        visitor = as_role(server, "visitor")
        wid = make_workspace(admin, "W1")

        status, _headers, denied = visitor.json(
            "POST",
            "/api/v3/workspaces/%s/connections" % wid,
            {"name": "n", "origin": "https://sda.example.invalid"},
            headers=operator_header(),
        )
        assert status == 403, denied
        assert denied["error"]["detail"]["action"] == "integration_credentials_manage"

        connection = make_connection(admin, wid)
        cid = connection["connection_id"]
        status, _headers, updated = admin.json(
            "PATCH",
            "/api/v3/workspaces/%s/connections/%s" % (wid, cid),
            {"name": "renamed"},
            headers=dict(operator_header(), **{"If-Match": "1"}),
        )
        assert status == 200, updated
        assert updated["name"] == "renamed"
        assert updated["policy_revision"] == 2

        status, _headers, stale = admin.json(
            "PATCH",
            "/api/v3/workspaces/%s/connections/%s" % (wid, cid),
            {"name": "stale"},
            headers=dict(operator_header(), **{"If-Match": "1"}),
        )
        assert status == 409, stale

        from app.services.auth import Actor

        admin_actor = Actor(
            ip="10.0.0.3", name="管理员乙", registered=True, is_deployer=False, is_admin=True
        )
        guest = Actor(ip="10.0.0.1", name="访客", registered=False, is_deployer=False, is_admin=False)
        assert authz.capabilities(admin_actor)["integration_credentials_manage"] is True
        assert authz.capabilities(guest)["integration_credentials_manage"] is False
