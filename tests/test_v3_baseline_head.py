"""M12-04 Bn 草稿/固化/不可变 head（INH-2139）。"""

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


def _d0_patch(server, wid, cid, did, pid, value):
    return as_role(server, "admin").json(
        "PATCH",
        "/api/v3/workspaces/%s/profiles/default/configs/%s/drafts/%s/values" % (wid, cid, did),
        {"request_id": "d0-" + value, "items": [
            {"parameter_id": pid, "membership": "included", "enabled": True,
             "value_state": "present", "value": value}
        ]},
        headers={"If-Match": '"0"'},
    )


def _freeze(server, wid, cid, did):
    return as_role(server, "admin").json(
        "POST",
        "/api/v3/workspaces/%s/profiles/default/configs/%s/drafts/%s/freeze"
        % (wid, cid, did),
        {"request_id": "freeze"},
    )


def _new_baseline(server, wid, cid, rid):
    return as_role(server, "admin").json(
        "POST",
        "/api/v3/workspaces/%s/profiles/default/configs/%s/releases/%s/baseline-drafts"
        % (wid, cid, rid)
    )


def _patch_baseline(server, wid, cid, did, pid, value, etag='"0"'):
    return as_role(server, "admin").json(
        "PATCH",
        "/api/v3/workspaces/%s/profiles/default/configs/%s/baseline-drafts/%s/values"
        % (wid, cid, did),
        {"request_id": "b-" + value, "items": [
            {"parameter_id": pid, "value_state": "present", "value": value}
        ]},
        headers={"If-Match": etag},
    )


def _publish(server, wid, cid, did, request_id, etag='"1"'):
    return as_role(server, "admin").json(
        "POST",
        "/api/v3/workspaces/%s/profiles/default/configs/%s/baseline-drafts/%s/publish"
        % (wid, cid, did),
        {"request_id": request_id},
        headers={"If-Match": etag},
    )


def test_b1_from_d0_and_old_baseline_is_not_overwritten():
    seed_roles("base")
    with make_server() as server:
        admin = as_role(server, "admin")
        w1 = _create(server, "W1")
        cid = _config(server, w1)
        pid = _param(server, w1, cid, "Pulse")
        rid = _release(server, w1, cid)
        did = "d0-" + rid

        # D0 未冻结：不能派生基线。
        assert _new_baseline(server, w1, cid, rid)[0] == 400
        assert _d0_patch(server, w1, cid, did, pid, "1")[0] == 200
        assert _freeze(server, w1, cid, did)[0] == 200

        status, _h, draft = _new_baseline(server, w1, cid, rid)
        assert status == 201, draft
        bd1 = draft["draft"]["draft_id"]
        assert draft["draft"]["values"][pid]["value"] == "1"
        assert _patch_baseline(server, w1, cid, bd1, pid, "2")[0] == 200
        status, _h, b1 = _publish(server, w1, cid, bd1, "p1")
        assert status == 200, b1
        assert b1["baseline"]["label"] == "B1"
        b1id = b1["baseline"]["baseline_id"]

        status, _h, branch = admin.json(
            "GET", "/api/v3/workspaces/%s/profiles/default/configs/%s/branches/main" % (w1, cid)
        )
        assert status == 200 and branch["head_id"] == b1id
        assert [b["label"] for b in branch["baselines"]] == ["B1"]

        status, _h, d2 = _new_baseline(server, w1, cid, rid)
        assert status == 201 and d2["draft"]["values"][pid]["value"] == "2"
        bd2 = d2["draft"]["draft_id"]
        assert _patch_baseline(server, w1, cid, bd2, pid, "3")[0] == 200
        status, _h, b2 = _publish(server, w1, cid, bd2, "p2")
        assert status == 200 and b2["baseline"]["label"] == "B2"

        status, _h, old = admin.json(
            "GET", "/api/v3/workspaces/%s/profiles/default/configs/%s/baselines/%s"
            % (w1, cid, b1id)
        )
        assert status == 200 and old["baseline"]["values"][pid]["value"] == "2", (
            "旧 B1 不得被 B2 覆盖"
        )
        status, _h, branch = admin.json(
            "GET", "/api/v3/workspaces/%s/profiles/default/configs/%s/branches/main" % (w1, cid)
        )
        assert branch["head_id"] == b2["baseline"]["baseline_id"]


def test_concurrent_publish_has_one_winner():
    seed_roles("base2")
    with make_server() as server:
        w1 = _create(server, "W1")
        cid = _config(server, w1)
        pid = _param(server, w1, cid, "Pulse")
        rid = _release(server, w1, cid)
        did = "d0-" + rid
        assert _d0_patch(server, w1, cid, did, pid, "1")[0] == 200
        assert _freeze(server, w1, cid, did)[0] == 200
        bd = _new_baseline(server, w1, cid, rid)[2]["draft"]["draft_id"]
        assert _patch_baseline(server, w1, cid, bd, pid, "2")[0] == 200

        def fire(key):
            return _publish(server, w1, cid, bd, key, '"1"')[0]

        with ThreadPoolExecutor(max_workers=2) as pool:
            statuses = sorted(pool.map(fire, ["a", "b"]))
        assert statuses == [200, 409], statuses
