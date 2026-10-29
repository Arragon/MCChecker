"""M12-02 参数身份/定义修订/BindingSet 快照（INH-2137）。"""

from __future__ import annotations

from authz_fixtures import as_role, make_server, seed_roles

from app.core import storage
from app.services import config_v3_catalog


def _create(server, name):
    status, _h, body = as_role(server, "admin").json(
        "POST", "/api/v3/workspaces", {"name": name}
    )
    assert status == 201, body
    return body["workspace"]["id"]


def _config(server, wid, code="MC-001"):
    status, _h, body = as_role(server, "admin").json(
        "POST",
        "/api/v3/workspaces/%s/profiles/default/configs" % wid,
        {"code": code, "name": "配置"},
    )
    assert status == 201, body
    return body["config"]["config_id"]


def _release(server, wid, cid, version="690"):
    status, _h, body = as_role(server, "admin").json(
        "POST",
        "/api/v3/workspaces/%s/profiles/default/configs/%s/releases" % (wid, cid),
        {"version": version},
    )
    assert status == 201, body
    return body["release"]["release_id"]


def _param(server, wid, cid, name, directory, **extra):
    payload = {"name": name, "directory": directory, "description": "说明", "type": "number", "unit": "ms"}
    payload.update(extra)
    return as_role(server, "admin").json(
        "POST",
        "/api/v3/workspaces/%s/profiles/default/configs/%s/parameters" % (wid, cid),
        payload,
    )


def test_parameter_identity_and_definition_history_do_not_drift():
    seed_roles("param")
    with make_server() as server:
        admin = as_role(server, "admin")
        w1 = _create(server, "W1")
        cid = _config(server, w1)
        pid1 = _release(server, w1, cid)

        status, _h, first = _param(server, w1, cid, "Pulse", ["A", "B"])
        assert status == 201, first
        param = first["parameter"]
        assert param["parameter_id"].startswith("p-")
        same = _param(server, w1, cid, "Pulse", ["A", "B"])
        assert same[0] == 409, "同目录同名拒绝"
        other = _param(server, w1, cid, "Pulse", ["C"])
        assert other[0] == 201, "同叶名异层级可并存"
        assert other[2]["parameter"]["parameter_id"] != param["parameter_id"]

        status, _h, listed = admin.json(
            "GET",
            "/api/v3/workspaces/%s/profiles/default/configs/%s/parameters" % (w1, cid),
        )
        assert status == 200 and listed["revision"] == 2
        assert {p["parameter_id"] for p in listed["items"]} == {
            param["parameter_id"], other[2]["parameter"]["parameter_id"]
        }

        status, _h, moved = admin.json(
            "PATCH",
            "/api/v3/workspaces/%s/profiles/default/configs/%s/parameters/%s"
            % (w1, cid, param["parameter_id"]),
            {"name": "PulseWidth", "directory": ["A", "B", "C"]},
        )
        assert status == 200, moved
        assert moved["parameter"]["parameter_id"] == param["parameter_id"], "改名/移动不改 stable ID"
        assert moved["parameter"]["name"] == "PulseWidth"

        status, _h, rev1 = admin.json(
            "GET",
            "/api/v3/workspaces/%s/profiles/default/configs/%s/definition-revisions/1"
            % (w1, cid),
        )
        assert status == 200
        old = rev1["revision"]["parameters"][param["parameter_id"]]
        assert old["name"] == "Pulse"
        assert old["directory"] == ["A", "B"], "历史修订不得被最新目录改写"

        status, _h, revs = admin.json(
            "GET",
            "/api/v3/workspaces/%s/profiles/default/configs/%s/definition-revisions" % (w1, cid),
        )
        assert [r["revision"] for r in revs["items"]] == [1, 2, 3]

        # release 捕获当时的 definition_revision，不随最新目录变化。
        status, _h, detail = admin.json(
            "GET",
            "/api/v3/workspaces/%s/profiles/default/configs/%s/releases" % (w1, cid),
        )
        assert status == 200
        release = [r for r in detail["items"] if r["release_id"] == pid1][0]
        assert release["definition_revision"] == 0, "release 捕获的是创建时的定义修订"

        # 128 层通过、129 层拒绝。
        deep = ["d%d" % i for i in range(config_v3_catalog.MAX_DIRECTORY_DEPTH)]
        assert _param(server, w1, cid, "Deep", deep)[0] == 201
        assert _param(server, w1, cid, "TooDeep", deep + ["x"])[0] == 400


def test_binding_candidates_require_confirmation_and_freeze_is_immutable():
    seed_roles("param2")
    with make_server() as server:
        w1 = _create(server, "W1")
        cid = _config(server, w1)
        rid = _release(server, w1, cid)
        status, _h, body = _param(server, w1, cid, "Pulse", ["A"])
        assert status == 201
        param_id = body["parameter"]["parameter_id"]

        base = "/api/v3/workspaces/%s/profiles/default/configs/%s/releases/%s/bindings" % (
            w1, cid, rid
        )
        unconfirmed = as_role(server, "admin").json(
            "POST", base,
            {"parameter_id": param_id, "file_id": "f-1", "version_id": "v-1",
             "locator": "/R[1]/p[1]", "confirmed": False},
        )
        assert unconfirmed[0] == 400, "候选必须确认"
        assert unconfirmed[2]["error"]["detail"]["field"] == "confirmed"

        status, _h, saved = as_role(server, "admin").json(
            "POST", base,
            {"parameter_id": param_id, "file_id": "f-1", "version_id": "v-1",
             "locator": "/R[1]/p[1]", "confirmed": True},
        )
        assert status == 201, saved
        assert saved["binding_set"]["revision"] == 2 and len(saved["binding_set"]["bindings"]) == 1
        duplicate = as_role(server, "admin").json(
            "POST", base,
            {"parameter_id": param_id, "file_id": "f-1", "version_id": "v-2",
             "locator": "/R[1]/p[2]", "confirmed": True},
        )
        assert duplicate[0] == 409, "一个参数只能有一个位置"

        with storage.use_workspace(w1), storage.use_profile("default"):
            frozen = config_v3_catalog.freeze_bindings("default", cid, rid)
        assert frozen["frozen"] is True
        rejected = as_role(server, "admin").json(
            "POST", base,
            {"parameter_id": param_id, "file_id": "f-1", "version_id": "v-3",
             "locator": "/R[1]/p[3]", "confirmed": True},
        )
        assert rejected[0] == 409
