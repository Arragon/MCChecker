"""M13-06 提取计划原子提交与补绑（INH-2148）。"""

from __future__ import annotations

import json

from authz_fixtures import as_role, make_server, seed_roles

from app.services import config_v3_catalog


def _create(server, name):
    status, _h, body = as_role(server, "admin").json("POST", "/api/v3/workspaces", {"name": name})
    assert status == 201, body
    return body["workspace"]["id"]


def _config(server, wid):
    status, _h, body = as_role(server, "admin").json(
        "POST", "/api/v3/workspaces/%s/profiles/default/configs" % wid,
        {"code": "MC-001", "name": "配置"},
    )
    assert status == 201, body
    return body["config"]["config_id"]


def _put(server, wid, name, data, overwrite=False):
    status, _h, raw = as_role(server, "admin").request(
        "PUT", "/api/v1/p/default/files/%s%s" % (name, "?overwrite=1" if overwrite else ""),
        body=data, headers={"X-Workspace-Id": wid, "Content-Type": "application/json"},
    )
    assert status in (200, 201), raw
    status, _h, body = as_role(server, "admin").json(
        "GET", "/api/v3/workspaces/%s/profiles/default/file-selector?q=%s" % (wid, name)
    )
    assert status == 200 and body["items"]
    return body["items"][0]


def _plan(server, wid, cid, vid):
    status, _h, body = as_role(server, "admin").json(
        "POST", "/api/v3/workspaces/%s/profiles/default/configs/%s/extraction-plans" % (wid, cid),
        {"version_id": vid},
    )
    assert status == 201, body
    return body["plan"]


def _commit(server, wid, cid, plan_id, payload):
    return as_role(server, "admin").json(
        "POST",
        "/api/v3/workspaces/%s/profiles/default/configs/%s/extraction-plans/%s/commit"
        % (wid, cid, plan_id),
        payload,
    )


def _params(server, wid, cid):
    status, _h, body = as_role(server, "admin").json(
        "GET", "/api/v3/workspaces/%s/profiles/default/configs/%s/parameters" % (wid, cid)
    )
    assert status == 200, body
    return body


def _release(server, wid, cid, version):
    status, _h, body = as_role(server, "admin").json(
        "POST", "/api/v3/workspaces/%s/profiles/default/configs/%s/releases" % (wid, cid),
        {"version": version},
    )
    assert status == 201, body
    return body["release"]["release_id"]


def _freeze(server, wid, cid, rid):
    status, _h, body = as_role(server, "admin").json(
        "POST",
        "/api/v3/workspaces/%s/profiles/default/configs/%s/drafts/d0-%s/freeze" % (wid, cid, rid),
        {"request_id": "f-" + rid},
    )
    assert status == 200, body



def test_3000_existing_plus_50_creates_only_new_and_repeat_is_idempotent():
    seed_roles("commit-bulk")
    first = {"Params": {"P%04d" % i: i for i in range(3000)}}
    second = {"Params": {"P%04d" % i: i for i in range(3050)}}
    with make_server() as server:
        wid = _create(server, "W1")
        cid = _config(server, wid)
        item = _put(server, wid, "bulk.json", json.dumps(first).encode())
        plan = _plan(server, wid, cid, item["version_id"])
        status, _h, body = _commit(server, wid, cid, plan["plan_id"], {"request_id": "bulk-1"})
        assert status == 200 and body["result"]["created"] == 3000, body
        assert len(_params(server, wid, cid)["items"]) == 3000

        _put(server, wid, "bulk.json", json.dumps(second).encode(), overwrite=True)
        status, _h, body = as_role(server, "admin").json(
            "GET", "/api/v3/workspaces/%s/profiles/default/file-selector?q=bulk.json" % wid
        )
        new_vid = body["items"][0]["version_id"]
        plan2 = _plan(server, wid, cid, new_vid)
        assert plan2["counts"]["existing"] == 3000 and plan2["counts"]["new"] == 50
        status, _h, body = _commit(server, wid, cid, plan2["plan_id"], {"request_id": "bulk-2"})
        assert status == 200, body
        assert body["result"]["created"] == 50
        assert body["result"]["unchanged"] == 3000
        assert len(_params(server, wid, cid)["items"]) == 3050

        for request_id in ("bulk-3", "bulk-4", "bulk-5"):
            retry = _plan(server, wid, cid, new_vid)
            status, _h, body = _commit(server, wid, cid, retry["plan_id"], {"request_id": request_id})
            assert status == 200 and body["result"]["created"] == 0, body
        assert len(_params(server, wid, cid)["items"]) == 3050, "重复导入不得重复新建"

        status, _h, replay = _commit(server, wid, cid, plan2["plan_id"], {"request_id": "bulk-2"})
        assert status == 200 and replay["result"]["replayed"] is True
        assert replay["result"]["created"] == 50
        status, _h, mismatch = _commit(
            server, wid, cid, plan2["plan_id"],
            {"request_id": "bulk-2", "add_members": True},
        )
        assert status == 409 and mismatch["error"]["code"] == "IDEMPOTENCY_MISMATCH", mismatch


def test_frozen_release_rejects_and_missing_bindings_are_opt_in():
    seed_roles("commit-bind")
    with make_server() as server:
        wid = _create(server, "W1")
        cid = _config(server, wid)
        status, _h, body = as_role(server, "admin").json(
            "POST", "/api/v3/workspaces/%s/profiles/default/configs/%s/parameters" % (wid, cid),
            {"name": "Gain", "directory": ["G"], "type": "number",
             "source": {"canonical_locator": "json:/G/Gain", "file_id": "f-seed"}},
        )
        assert status == 201, body
        item = _put(server, wid, "bind.json", b'{"G":{"Gain":1}}')
        release = _release(server, wid, cid, "690")
        _freeze(server, wid, cid, release)
        plan = _plan(server, wid, cid, item["version_id"])
        status, _h, body = _commit(
            server, wid, cid, plan["plan_id"],
            {"request_id": "bind-frozen", "release_id": release, "include_existing_missing_bindings": True},
        )
        assert status == 409 and body["error"]["code"] == "LIFECYCLE_BLOCKED", body

        release2 = _release(server, wid, cid, "691")
        plan2 = _plan(server, wid, cid, item["version_id"])
        status, _h, body = _commit(server, wid, cid, plan2["plan_id"], {"request_id": "bind-off"})
        assert status == 200 and body["result"]["bound"] == 0, body
        plan3 = _plan(server, wid, cid, item["version_id"])
        status, _h, body = _commit(
            server, wid, cid, plan3["plan_id"],
            {"request_id": "bind-on", "release_id": release2, "include_existing_missing_bindings": True},
        )
        assert status == 200 and body["result"]["bound"] == 1, body
        bindings = as_role(server, "admin").json(
            "GET",
            "/api/v3/workspaces/%s/profiles/default/configs/%s/releases/%s/bindings"
            % (wid, cid, release2),
        )[2]["binding_set"]
        assert len(bindings["bindings"]) == 1
        binding = list(bindings["bindings"].values())[0]
        assert binding["locator"] == "json:/G/Gain"


def test_commit_is_atomic_when_persistence_fails(monkeypatch):
    seed_roles("commit-atomic")
    payload = {"Params": {"P%04d" % i: i for i in range(2500)}}
    with make_server() as server:
        wid = _create(server, "W1")
        cid = _config(server, wid)
        item = _put(server, wid, "atomic.json", json.dumps(payload).encode())
        plan = _plan(server, wid, cid, item["version_id"])
        def boom(*args, **kwargs):
            raise RuntimeError("disk full at row 2500")

        with monkeypatch.context() as patch:
            patch.setattr(config_v3_catalog, "_save", boom)
            status, _h, body = _commit(server, wid, cid, plan["plan_id"], {"request_id": "atomic"})
            assert status >= 500, body
        assert _params(server, wid, cid)["items"] == [], "失败后不得有半批参数写入"


def test_commit_rejects_stale_plan():
    seed_roles("commit-stale")
    with make_server() as server:
        wid = _create(server, "W1")
        cid = _config(server, wid)
        item = _put(server, wid, "stale.json", b'{"A":1}')
        plan = _plan(server, wid, cid, item["version_id"])
        assert as_role(server, "admin").json(
            "POST", "/api/v3/workspaces/%s/profiles/default/configs/%s/parameters" % (wid, cid),
            {"name": "Other", "directory": ["X"]},
        )[0] == 201
        status, _h, body = _commit(server, wid, cid, plan["plan_id"], {"request_id": "stale"})
        assert status == 409 and body["error"]["code"] == "PLAN_STALE", body
        assert _params(server, wid, cid)["items"][0]["name"] == "Other"
        assert len(_params(server, wid, cid)["items"]) == 1
