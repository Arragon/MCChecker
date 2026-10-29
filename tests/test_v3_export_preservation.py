"""M14-04 精确模板导出（INH-2153）。"""

from __future__ import annotations

import hashlib
import json
import xml.etree.ElementTree as ET

from authz_fixtures import as_role, make_server, seed_roles


def _base(server):
    wid = as_role(server, "admin").json("POST", "/api/v3/workspaces", {"name": "W1"})[2]["workspace"]["id"]
    cid = as_role(server, "admin").json(
        "POST", "/api/v3/workspaces/%s/profiles/default/configs" % wid,
        {"code": "MC-001", "name": "配置"},
    )[2]["config"]["config_id"]
    return wid, cid


def _param(server, wid, cid, name, type_name="number"):
    return as_role(server, "admin").json(
        "POST", "/api/v3/workspaces/%s/profiles/default/configs/%s/parameters" % (wid, cid),
        {"name": name, "directory": ["G"], "type": type_name},
    )[2]["parameter"]["parameter_id"]


def _release(server, wid, cid):
    return as_role(server, "admin").json(
        "POST", "/api/v3/workspaces/%s/profiles/default/configs/%s/releases" % (wid, cid),
        {"version": "690"},
    )[2]["release"]["release_id"]


def _values(server, wid, cid, rid, items, expected=0):
    status, _h, body = as_role(server, "admin").json(
        "PATCH", "/api/v3/workspaces/%s/profiles/default/configs/%s/drafts/d0-%s/values" % (wid, cid, rid),
        {"request_id": "v", "expected_revision": expected, "items": items},
        headers={"If-Match": '"%d"' % expected},
    )
    assert status == 200, body


def _freeze(server, wid, cid, rid):
    status, _h, body = as_role(server, "admin").json(
        "POST", "/api/v3/workspaces/%s/profiles/default/configs/%s/drafts/d0-%s/freeze" % (wid, cid, rid),
        {"request_id": "f"},
    )
    assert status == 200, body


def _put(server, wid, name, data, ctype):
    status, _h, raw = as_role(server, "admin").request(
        "PUT", "/api/v1/p/default/files/%s" % name, body=data,
        headers={"X-Workspace-Id": wid, "Content-Type": ctype},
    )
    assert status in (200, 201), raw
    return as_role(server, "admin").json(
        "GET", "/api/v3/workspaces/%s/profiles/default/file-selector?q=%s" % (wid, name)
    )[2]["items"][0]


def _binding(server, wid, cid, rid, pid, locator, item):
    status, _h, body = as_role(server, "admin").json(
        "POST", "/api/v3/workspaces/%s/profiles/default/configs/%s/releases/%s/bindings" % (wid, cid, rid),
        {"parameter_id": pid, "locator": locator, "file_id": item["file_id"],
         "version_id": item["version_id"], "confirmed": True},
    )
    assert status == 201, body


def _export_plan(server, wid, cid, payload):
    return as_role(server, "admin").json(
        "POST", "/api/v3/workspaces/%s/profiles/default/configs/%s/export-plans" % (wid, cid), payload
    )


def test_json_export_preserves_unmatched_fields_and_is_repeatable():
    seed_roles("export-json")
    with make_server() as server:
        wid, cid = _base(server)
        p1 = _param(server, wid, cid, "P1")
        rid = _release(server, wid, cid)
        _values(server, wid, cid, rid, [
            {"parameter_id": p1, "membership": "included", "enabled": True,
             "value_state": "present", "value": "5"},
        ])
        template = _put(server, wid, "tpl.json", b'{"G":{"P1":1,"Keep":"x"}}', "application/json")
        _binding(server, wid, cid, rid, p1, "json:/G/P1", template)
        _freeze(server, wid, cid, rid)
        payload = {"release_id": rid, "revision_id": "d0-" + rid, "version_id": template["version_id"]}
        status, _h, body = _export_plan(server, wid, cid, payload)
        assert status == 201, body
        plan = body["plan"]
        assert plan["applied"] == 1
        assert json.loads(plan["preview"]) == {"G": {"P1": 5, "Keep": "x"}}
        again = _export_plan(server, wid, cid, payload)[2]["plan"]
        assert again["digest"] == plan["digest"], "同输入三次导出必须同 hash"
        third = _export_plan(server, wid, cid, payload)[2]["plan"]
        assert third["digest"] == plan["digest"]

        status, _h, body = as_role(server, "admin").json(
            "POST", "/api/v3/workspaces/%s/profiles/default/configs/%s/export-plans/%s/commit"
            % (wid, cid, plan["plan_id"]),
            {"preview_token": "wrong"},
        )
        assert status == 409, body
        status, _h, body = as_role(server, "admin").json(
            "POST", "/api/v3/workspaces/%s/profiles/default/configs/%s/export-plans/%s/commit"
            % (wid, cid, plan["plan_id"]),
            {"preview_token": plan["preview_token"]},
        )
        assert status == 200, body
        artifact = body["artifact"]
        assert artifact["deployed"] is False and artifact["digest"] == plan["digest"]
        status, _h, raw = as_role(server, "admin").request(
            "GET", "/api/v3/workspaces/%s/profiles/default/exports/%s/download"
            % (wid, artifact["artifact_id"])
        )
        assert status == 200 and hashlib.sha256(raw).hexdigest() == plan["digest"]
        assert json.loads(raw.decode())["G"] == {"P1": 5, "Keep": "x"}
        status, _h, original = as_role(server, "admin").request(
            "GET", "/api/v1/p/default/files/tpl.json/raw", headers={"X-Workspace-Id": wid}
        )
        assert original == b'{"G":{"P1":1,"Keep":"x"}}', "原模板不得被覆盖"


def test_xml_binding_leaf_and_removed_requires_confirmation():
    seed_roles("export-xml")
    with make_server() as server:
        wid, cid = _base(server)
        p1 = _param(server, wid, cid, "P1")
        rid = _release(server, wid, cid)
        _values(server, wid, cid, rid, [
            {"parameter_id": p1, "membership": "included", "enabled": True,
             "value_state": "present", "value": "7"},
        ])
        template = _put(server, wid, "tpl.xml", b"<R><G><P1>1</P1><Keep>x</Keep></G></R>", "application/xml")
        _binding(server, wid, cid, rid, p1, "/R[1]/G[1]/P1[1]", template)
        _freeze(server, wid, cid, rid)
        payload = {"release_id": rid, "revision_id": "d0-" + rid, "version_id": template["version_id"]}
        status, _h, body = _export_plan(server, wid, cid, payload)
        assert status == 201, body
        plan = body["plan"]
        root = ET.fromstring(plan["preview"])
        assert root.find("G/P1").text == "7"
        assert root.find("G/Keep").text == "x"


def test_ini_binding_preserves_other_keys():
    seed_roles("export-ini")
    with make_server() as server:
        wid, cid = _base(server)
        p1 = _param(server, wid, cid, "P1")
        rid = _release(server, wid, cid)
        _values(server, wid, cid, rid, [
            {"parameter_id": p1, "membership": "included", "enabled": True,
             "value_state": "present", "value": "5"},
        ])
        template = _put(server, wid, "tpl.ini", b"[s]\nkey=1\nkeep=x\n", "text/plain")
        _binding(server, wid, cid, rid, p1, "s/key", template)
        _freeze(server, wid, cid, rid)
        status, _h, body = _export_plan(server, wid, cid, {
            "release_id": rid, "revision_id": "d0-" + rid, "version_id": template["version_id"],
        })
        assert status == 201, body
        preview = body["plan"]["preview"]
        assert "key=5" in preview and "keep=x" in preview
        status, _h, committed = as_role(server, "admin").json(
            "POST", "/api/v3/workspaces/%s/profiles/default/configs/%s/export-plans/%s/commit"
            % (wid, cid, body["plan"]["plan_id"]),
            {"preview_token": body["plan"]["preview_token"]},
        )
        assert status == 200, committed
        status, _h, raw = as_role(server, "admin").request(
            "GET", "/api/v3/workspaces/%s/profiles/default/exports/%s/download"
            % (wid, committed["artifact"]["artifact_id"])
        )
        assert status == 200
        assert raw.decode() == "[s]\nkey=5\nkeep=x\n"
