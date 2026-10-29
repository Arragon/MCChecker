"""M12-06 草稿值批量 CAS/幂等（INH-2141）。"""

from __future__ import annotations

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


def _param(server, wid, cid, name, type_name="number"):
    status, _h, body = as_role(server, "admin").json(
        "POST",
        "/api/v3/workspaces/%s/profiles/default/configs/%s/parameters" % (wid, cid),
        {"name": name, "directory": ["G"], "type": type_name},
    )
    assert status == 201, body
    return body["parameter"]["parameter_id"]


def _release(server, wid, cid):
    status, _h, body = as_role(server, "admin").json(
        "POST",
        "/api/v3/workspaces/%s/profiles/default/configs/%s/releases" % (wid, cid),
        {"version": "690"},
    )
    assert status == 201, body
    return body["release"]["release_id"]


def _patch(server, wid, cid, did, payload, etag):
    headers = {"If-Match": etag} if etag is not None else {}
    return as_role(server, "admin").json(
        "PATCH",
        "/api/v3/workspaces/%s/profiles/default/configs/%s/drafts/%s/values"
        % (wid, cid, did),
        payload,
        headers=headers,
    )


def test_value_states_are_distinct_and_cas_batch_is_atomic():
    seed_roles("draft")
    with make_server() as server:
        admin = as_role(server, "admin")
        w1 = _create(server, "W1")
        cid = _config(server, w1)
        p_num = _param(server, w1, cid, "Pulse", "number")
        p_bool = _param(server, w1, cid, "Enabled", "bool")
        p_str = _param(server, w1, cid, "Name", "string")
        rid = _release(server, w1, cid)
        did = "d0-" + rid

        status, headers, draft = admin.json(
            "GET", "/api/v3/workspaces/%s/profiles/default/configs/%s/drafts/%s" % (w1, cid, did)
        )
        assert status == 200 and draft["revision"] == 0 and draft["values"] == {}
        assert headers["ETag"] == '"0"'

        items = [
            {"parameter_id": p_num, "value_state": "present", "value": "1.2300"},
            {"parameter_id": p_bool, "value_state": "present", "value": False},
            {"parameter_id": p_str, "value_state": "present", "value": ""},
            {"parameter_id": p_num + "-missing", "value_state": "null"},
        ]
        status, _h, bad = _patch(
            server, w1, cid, did, {"request_id": "r1", "items": items}, '"0"'
        )
        assert status == 404, bad
        status, _h, after_bad = admin.json(
            "GET", "/api/v3/workspaces/%s/profiles/default/configs/%s/drafts/%s" % (w1, cid, did)
        )
        assert after_bad["revision"] == 0, "任一行非法时整批不写"

        items = [
            {"parameter_id": p_num, "value_state": "present", "value": "1.2300"},
            {"parameter_id": p_bool, "value_state": "present", "value": False},
            {"parameter_id": p_str, "value_state": "present", "value": ""},
            {"parameter_id": p_str, "value_state": "null"},
        ]
        # 同一参数出现两次：后一条覆盖前一条，不会产生两个 real value。
        status, headers, saved = _patch(
            server, w1, cid, did, {"request_id": "r1", "items": items}, '"0"'
        )
        assert status == 200, saved
        assert saved["revision"] == 1 and headers["ETag"] == '"1"'
        assert saved["values"][p_num]["value"] == "1.2300", "decimal 原文本必须保留"
        assert saved["values"][p_num]["value_state"] == "present"
        assert saved["values"][p_bool]["value"] == "false"
        assert saved["values"][p_str]["value_state"] == "null"
        assert "value" not in saved["values"][p_str]

        status, _h, replay = _patch(
            server, w1, cid, did, {"request_id": "r1", "items": items}, '"1"'
        )
        assert status == 200 and replay["replayed"] is True and replay["revision"] == 1

        # 0、false、"" 与 unset/null 不混。
        status, _h, saved2 = _patch(
            server, w1, cid, did,
            {"request_id": "r2", "items": [
                {"parameter_id": p_num, "value_state": "present", "value": "0"},
                {"parameter_id": p_bool, "value_state": "present", "value": True},
                {"parameter_id": p_str, "value_state": "present", "value": ""},
            ]},
            '"1"',
        )
        assert status == 200 and saved2["revision"] == 2
        assert saved2["values"][p_num]["value"] == "0"
        assert saved2["values"][p_bool]["value"] == "true"
        assert saved2["values"][p_str]["value"] == ""
        assert all(v["value_state"] == "present" for v in saved2["values"].values())

        # 非数值不写 0；旧值保持。
        status, _h, invalid = _patch(
            server, w1, cid, did,
            {"request_id": "r3", "items": [
                {"parameter_id": p_num, "value_state": "present", "value": "abc"}
            ]},
            '"2"',
        )
        assert status == 400 and invalid["error"]["detail"]["field"] == "value"
        status, _h, unchanged = admin.json(
            "GET", "/api/v3/workspaces/%s/profiles/default/configs/%s/drafts/%s" % (w1, cid, did)
        )
        assert unchanged["revision"] == 2 and unchanged["values"][p_num]["value"] == "0"

        # 超 100 条拒绝。
        too_many = [{"parameter_id": p_num, "value_state": "unset"} for _ in range(101)]
        assert _patch(
            server, w1, cid, did, {"request_id": "r4", "items": too_many}, '"2"'
        )[0] == 400

        # 过期 ETag 409，客户端输入不落盘。
        status, _h, conflict = _patch(
            server, w1, cid, did,
            {"request_id": "r5", "items": [
                {"parameter_id": p_num, "value_state": "present", "value": "9"}
            ]},
            '"1"',
        )
        assert status == 409 and conflict["error"]["code"] == "REVISION_CONFLICT"
        status, _h, still = admin.json(
            "GET", "/api/v3/workspaces/%s/profiles/default/configs/%s/drafts/%s" % (w1, cid, did)
        )
        assert still["revision"] == 2 and still["values"][p_num]["value"] == "0"
