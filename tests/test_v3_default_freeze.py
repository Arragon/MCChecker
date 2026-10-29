"""M12-03 canonical D0 唯一冻结（INH-2138）。"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

from authz_fixtures import as_role, make_server, seed_roles


def _create(server, name):
    status, _h, body = as_role(server, "admin").json(
        "POST", "/api/v3/workspaces", {"name": name}
    )
    assert status == 201, body
    return body["workspace"]["id"]


def _config(server, wid):
    status, _h, body = as_role(server, "admin").json(
        "POST", "/api/v3/workspaces/%s/profiles/default/configs" % wid,
        {"code": "MC-001", "name": "配置"},
    )
    assert status == 201, body
    return body["config"]["config_id"]


def _param(server, wid, cid, name):
    status, _h, body = as_role(server, "admin").json(
        "POST",
        "/api/v3/workspaces/%s/profiles/default/configs/%s/parameters" % (wid, cid),
        {"name": name, "directory": ["G"], "type": "number"},
    )
    assert status == 201, body
    return body["parameter"]["parameter_id"]


def _release(server, wid, cid, version="690"):
    status, _h, body = as_role(server, "admin").json(
        "POST",
        "/api/v3/workspaces/%s/profiles/default/configs/%s/releases" % (wid, cid),
        {"version": version},
    )
    assert status == 201, body
    return body["release"]["release_id"]


def _patch_values(server, wid, cid, did, pid, value, etag='"0"'):
    return as_role(server, "admin").json(
        "PATCH",
        "/api/v3/workspaces/%s/profiles/default/configs/%s/drafts/%s/values" % (wid, cid, did),
        {"request_id": "v1", "items": [
            {"parameter_id": pid, "membership": "included", "enabled": True,
             "value_state": "present", "value": value}
        ]},
        headers={"If-Match": etag},
    )


def _freeze(server, wid, cid, did, request_id):
    return as_role(server, "admin").json(
        "POST",
        "/api/v3/workspaces/%s/profiles/default/configs/%s/drafts/%s/freeze"
        % (wid, cid, did),
        {"request_id": request_id},
    )


def test_d0_freezes_once_and_is_idempotent_by_request_id():
    seed_roles("freeze")
    with make_server() as server:
        w1 = _create(server, "W1")
        cid = _config(server, w1)
        pid = _param(server, w1, cid, "Pulse")
        rid = _release(server, w1, cid)
        did = "d0-" + rid
        assert _patch_values(server, w1, cid, did, pid, "1.25")[0] == 200
        status, _h, bound = as_role(server, "admin").json(
            "POST",
            "/api/v3/workspaces/%s/profiles/default/configs/%s/releases/%s/bindings"
            % (w1, cid, rid),
            {"parameter_id": pid, "file_id": "f-1", "version_id": "v-1",
             "locator": "/R[1]/p[1]", "confirmed": True},
        )
        assert status == 201, bound

        status, _h, frozen = _freeze(server, w1, cid, did, "f1")
        assert status == 200, frozen
        assert frozen["frozen"] is True
        assert frozen["values"][pid]["value"] == "1.25"
        assert frozen["revision"] == 1
        status, _h, bindings = as_role(server, "admin").json(
            "GET",
            "/api/v3/workspaces/%s/profiles/default/configs/%s/releases/%s/bindings"
            % (w1, cid, rid),
        )
        assert status == 200 and bindings["binding_set"]["frozen"] is True
        status, _h, releases = as_role(server, "admin").json(
            "GET",
            "/api/v3/workspaces/%s/profiles/default/configs/%s/releases" % (w1, cid),
        )
        assert [r for r in releases["items"] if r["release_id"] == rid][0]["default_revision_id"] == did

        status, _h, again = _freeze(server, w1, cid, did, "f2")
        assert status == 409 and again["error"]["code"] == "DEFAULT_ALREADY_FROZEN"
        status, _h, replay = _freeze(server, w1, cid, did, "f1")
        assert status == 200 and replay["replayed"] is True

        status, _h, rejected = _patch_values(server, w1, cid, did, pid, "2", '"1"')
        assert status == 409, "冻结后值不可再改"


def test_concurrent_freeze_has_exactly_one_winner():
    seed_roles("freeze2")
    with make_server() as server:
        w1 = _create(server, "W1")
        cid = _config(server, w1)
        pid = _param(server, w1, cid, "Pulse")
        rid = _release(server, w1, cid)
        did = "d0-" + rid
        assert _patch_values(server, w1, cid, did, pid, "1")[0] == 200

        def fire(key):
            return _freeze(server, w1, cid, did, key)[0]

        with ThreadPoolExecutor(max_workers=2) as pool:
            statuses = sorted(pool.map(fire, ["a", "b"]))
        assert statuses == [200, 409], statuses
