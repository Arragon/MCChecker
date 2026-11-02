"""M16-34 连接保活 API：只读可见、运维写授权、CAS、运行不伪造与不泄密。

真实 `MCServer` + 真实 HTTP；probe 由 `api_sync_heartbeat.configure_probe` 注入。
断言口径：
- 只读身份能 GET 保活状态，但 PUT/run 即使带 token 也 403；
- 管理员写保活仍需独立运维 token，缺 token 403；
- PUT 用 If-Match 做 revision CAS，缺/过期都是 409；
- 未启用或未注入 probe 时 run 返回 blocked，不写心跳、不伪造成功；
- 成功/失败只保存安全状态，probe 回包里的秘密字段不进入响应或磁盘。
"""

from __future__ import annotations

import json
import os

import pytest
from authz_fixtures import as_role, make_server, seed_roles

from app.server import api_sync_heartbeat
from app.services import sync_paths

OPERATOR_TOKEN = "m16-34-test-operator-token"


@pytest.fixture(autouse=True)
def heartbeat_api_ports(monkeypatch):
    monkeypatch.setenv("MCHECKER_OPERATOR_TOKEN", OPERATOR_TOKEN)
    api_sync_heartbeat.configure_probe(None)
    yield
    api_sync_heartbeat.configure_probe(None)


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


def heartbeat_url(wid, cid):
    return "/api/v3/workspaces/%s/connections/%s/heartbeat" % (wid, cid)


def enable(caller, wid, cid, interval=10, revision=0):
    status, _headers, body = caller.json(
        "PUT",
        heartbeat_url(wid, cid),
        {"interval_minutes": interval, "enabled": True},
        headers=dict(operator_header(), **{"If-Match": str(revision)}),
    )
    assert status == 200, body
    return body


def read_text(path):
    with open(path, "r", encoding="utf-8") as handle:
        return handle.read()


def test_read_only_sees_status_but_writes_need_operator_token_and_capability():
    seed_roles("hb-read")
    with make_server() as server:
        admin = as_role(server, "admin")
        visitor = as_role(server, "visitor")
        wid = make_workspace(admin, "W1")
        cid = make_connection(admin, wid)["connection_id"]
        url = heartbeat_url(wid, cid)

        status, _headers, state = visitor.json("GET", url)
        assert status == 200, state
        assert state["enabled"] is False
        assert state["interval_minutes"] == 10
        assert state["heartbeat_validated"] is False
        assert state["revision"] == 0

        # 只读身份即使带运维 token 也不能写。
        status, _headers, denied = visitor.json(
            "PUT",
            url,
            {"interval_minutes": 5, "enabled": True},
            headers=dict(operator_header(), **{"If-Match": "0"}),
        )
        assert status == 403, denied
        assert denied["error"]["detail"]["action"] == "integration_credentials_manage"

        status, _headers, denied = visitor.json(
            "POST", url + "/run", {}, headers=operator_header()
        )
        assert status == 403, denied
        assert denied["error"]["detail"]["action"] == "integration_credentials_manage"

        # 管理员有能力键，但没有运维 token 仍然 403。
        status, _headers, denied = admin.json(
            "PUT",
            url,
            {"interval_minutes": 5, "enabled": True},
            headers={"If-Match": "0"},
        )
        assert status == 403, denied
        assert denied["error"]["detail"]["field"] == "operator_token"

        status, _headers, denied = admin.json("POST", url + "/run", {})
        assert status == 403, denied
        assert denied["error"]["detail"]["field"] == "operator_token"

        assert not os.path.exists(sync_paths.sync_file(wid, "heartbeat.json"))
        assert not os.path.exists(sync_paths.sync_file(wid, "operations.json"))


def test_put_uses_revision_cas_and_validates_fields():
    seed_roles("hb-cas")
    with make_server() as server:
        admin = as_role(server, "admin")
        visitor = as_role(server, "visitor")
        wid = make_workspace(admin, "W1")
        cid = make_connection(admin, wid)["connection_id"]
        url = heartbeat_url(wid, cid)
        headers = dict(operator_header(), **{"If-Match": "0"})

        status, _headers, missing = admin.json(
            "PUT", url, {"interval_minutes": 5, "enabled": True}, headers=operator_header()
        )
        assert status == 409, missing
        assert missing["error"]["detail"]["field"] == "expected_revision"

        status, _headers, bad = admin.json(
            "PUT", url, {"interval_minutes": 61, "enabled": True}, headers=headers
        )
        assert status == 400, bad
        status, _headers, bad = admin.json(
            "PUT", url, {"interval_minutes": 5, "enabled": "yes"}, headers=headers
        )
        assert status == 400, bad
        status, _headers, bad = admin.json(
            "PUT",
            url,
            {"interval_minutes": 5, "enabled": True, "auto_relogin": True},
            headers=headers,
        )
        assert status == 400, bad
        assert not os.path.exists(sync_paths.sync_file(wid, "heartbeat.json"))

        saved = enable(admin, wid, cid, interval=15, revision=0)
        assert saved["enabled"] is True
        assert saved["interval_minutes"] == 15
        assert saved["revision"] == 1

        status, _headers, stale = admin.json(
            "PUT",
            url,
            {"interval_minutes": 20, "enabled": False},
            headers=headers,
        )
        assert status == 409, stale

        status, _headers, fresh = visitor.json("GET", url)
        assert status == 200, fresh
        assert fresh["enabled"] is True
        assert fresh["interval_minutes"] == 15
        assert fresh["revision"] == 1


def test_run_disabled_is_blocked_and_never_calls_probe():
    calls = []
    api_sync_heartbeat.configure_probe(
        lambda wid, cid: calls.append((wid, cid)) or {
            "status": "succeeded",
            "session_valid": True,
        }
    )
    seed_roles("hb-disabled")
    with make_server() as server:
        admin = as_role(server, "admin")
        wid = make_workspace(admin, "W1")
        cid = make_connection(admin, wid)["connection_id"]

        status, _headers, payload = admin.json(
            "POST", heartbeat_url(wid, cid) + "/run", {}, headers=operator_header()
        )
        assert status == 202, payload
        op = payload["operation"]
        assert op["kind"] == "heartbeat_run"
        assert op["state"] == "blocked"
        assert op["reason_code"] == "disabled"
        assert op["result"]["heartbeat"]["enabled"] is False
        assert calls == [], "未启用保活不得调用 probe"

        status, _headers, state = admin.json("GET", heartbeat_url(wid, cid))
        assert status == 200, state
        assert state["last_heartbeat_at"] is None
        assert state["last_result"] is None


def test_run_without_probe_is_blocked_collector_offline_not_faked():
    seed_roles("hb-noprobe")
    with make_server() as server:
        admin = as_role(server, "admin")
        visitor = as_role(server, "visitor")
        wid = make_workspace(admin, "W1")
        cid = make_connection(admin, wid)["connection_id"]
        enable(admin, wid, cid)

        status, _headers, payload = admin.json(
            "POST", heartbeat_url(wid, cid) + "/run", {}, headers=operator_header()
        )
        assert status == 202, payload
        op = payload["operation"]
        assert op["state"] == "blocked"
        assert op["reason_code"] == "collector_offline"
        assert op["started_at"] and op["finished_at"]

        status, _headers, state = visitor.json("GET", heartbeat_url(wid, cid))
        assert status == 200, state
        assert state["last_heartbeat_at"] is None
        assert state["last_result"] is None
        assert state["heartbeat_validated"] is False

        status, _headers, fetched = visitor.json(
            "GET",
            "/api/v3/workspaces/%s/connections/%s/operations/%s"
            % (wid, cid, op["op_id"]),
        )
        assert status == 200, fetched
        assert fetched["operation"]["reason_code"] == "collector_offline"


def test_run_success_records_safe_state_and_never_echoes_probe_secrets():
    api_sync_heartbeat.configure_probe(
        lambda _wid, _cid: {
            "status": "succeeded",
            "session_valid": True,
            "renewal_observed": False,
            "password": "super-secret-pw",
            "cookie": "SESSION=abc123",
        }
    )
    seed_roles("hb-ok")
    with make_server() as server:
        admin = as_role(server, "admin")
        visitor = as_role(server, "visitor")
        wid = make_workspace(admin, "W1")
        cid = make_connection(admin, wid)["connection_id"]
        enable(admin, wid, cid, interval=5)

        status, _headers, payload = admin.json(
            "POST", heartbeat_url(wid, cid) + "/run", {}, headers=operator_header()
        )
        assert status == 202, payload
        op = payload["operation"]
        assert op["state"] == "succeeded"
        assert op["reason_code"] is None
        assert op["result"]["heartbeat"]["last_result"] == "success"

        status, _headers, state = visitor.json("GET", heartbeat_url(wid, cid))
        assert status == 200, state
        assert state["last_result"] == "success"
        assert state["last_heartbeat_at"]
        assert state["next_due_at"]
        assert state["heartbeat_validated"] is False, "成功心跳不能冒充真实 idle 验证"

        raw = json.dumps(payload, ensure_ascii=False)
        assert "super-secret-pw" not in raw
        assert "SESSION=abc123" not in raw
        for name in ("heartbeat.json", "operations.json"):
            text = read_text(sync_paths.sync_file(wid, name))
            assert "super-secret-pw" not in text
            assert "SESSION=abc123" not in text


def test_run_failure_keeps_reason_without_echoing_secret():
    api_sync_heartbeat.configure_probe(
        lambda _wid, _cid: {
            "status": "failed",
            "session_valid": False,
            "reason_code": "bad_credentials",
            "password": "super-secret-pw",
        }
    )
    seed_roles("hb-fail")
    with make_server() as server:
        admin = as_role(server, "admin")
        wid = make_workspace(admin, "W1")
        cid = make_connection(admin, wid)["connection_id"]
        enable(admin, wid, cid)

        status, _headers, payload = admin.json(
            "POST", heartbeat_url(wid, cid) + "/run", {}, headers=operator_header()
        )
        assert status == 202, payload
        op = payload["operation"]
        assert op["state"] == "failed"
        assert op["reason_code"] == "bad_credentials"
        assert op["result"]["heartbeat"]["last_result"] == "failed"
        assert op["result"]["heartbeat"]["reason_code"] == "bad_credentials"

        status, _headers, state = admin.json("GET", heartbeat_url(wid, cid))
        assert state["last_result"] == "failed"
        assert state["reason_code"] == "bad_credentials"
        assert state["heartbeat_validated"] is False

        assert "super-secret-pw" not in json.dumps(payload, ensure_ascii=False)
        for name in ("heartbeat.json", "operations.json"):
            assert "super-secret-pw" not in read_text(sync_paths.sync_file(wid, name))


def test_cross_workspace_heartbeat_is_404():
    seed_roles("hb-scope")
    with make_server() as server:
        admin = as_role(server, "admin")
        wid1 = make_workspace(admin, "W1")
        wid2 = make_workspace(admin, "W2")
        cid = make_connection(admin, wid1)["connection_id"]

        status, _headers, payload = admin.json("GET", heartbeat_url(wid2, cid))
        assert status == 404, payload
        status, _headers, payload = admin.json(
            "PUT",
            heartbeat_url(wid2, cid),
            {"interval_minutes": 5, "enabled": True},
            headers=dict(operator_header(), **{"If-Match": "0"}),
        )
        assert status == 404, payload
        status, _headers, payload = admin.json(
            "POST", heartbeat_url(wid2, cid) + "/run", {}, headers=operator_header()
        )
        assert status == 404, payload
        assert not os.path.exists(sync_paths.sync_file(wid2, "operations.json"))
