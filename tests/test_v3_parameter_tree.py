"""M13-PERF 目录分组树分页（INH-2163）。"""

from __future__ import annotations

from authz_fixtures import as_role, make_server, seed_roles


def _base(server):
    wid = as_role(server, "admin").json("POST", "/api/v3/workspaces", {"name": "W1"})[2]["workspace"]["id"]
    cid = as_role(server, "admin").json(
        "POST", "/api/v3/workspaces/%s/profiles/default/configs" % wid,
        {"code": "MC-001", "name": "配置"},
    )[2]["config"]["config_id"]
    return wid, cid


def _param(server, wid, cid, name, directory):
    status, _h, body = as_role(server, "admin").json(
        "POST", "/api/v3/workspaces/%s/profiles/default/configs/%s/parameters" % (wid, cid),
        {"name": name, "directory": directory, "type": "number"},
    )
    assert status == 201, body


def _tree(server, wid, cid, **query):
    path = "/api/v3/workspaces/%s/profiles/default/configs/%s/parameter-tree" % (wid, cid)
    parts = ["%s=%s" % (k, v) for k, v in query.items()]
    if parts:
        path += "?" + "&".join(parts)
    status, _h, body = as_role(server, "admin").json("GET", path)
    assert status == 200, body
    return body


def test_tree_returns_only_direct_children_and_current_page():
    seed_roles("param-tree")
    with make_server() as server:
        wid, cid = _base(server)
        for group in range(3):
            for index in range(40):
                _param(server, wid, cid, "P%02d" % index, ["G%d" % group])
        for index in range(5):
            _param(server, wid, cid, "S%02d" % index, ["G0", "Sub"])
        root = _tree(server, wid, cid, parent="", limit=10)
        assert [row["name"] for row in root["directories"]] == ["G0", "G1", "G2"]
        assert [row["count"] for row in root["directories"]] == [45, 40, 40]
        assert root["parameter_total"] == 0 and root["items"] == []
        first = _tree(server, wid, cid, parent="G0", limit=10, offset=0)
        assert first["parameter_total"] == 40 and len(first["items"]) == 10
        assert [row["name"] for row in first["directories"]] == ["Sub"]
        assert first["directories"][0]["count"] == 5
        seen = []
        for offset in range(0, 40, 10):
            page = _tree(server, wid, cid, parent="G0", limit=10, offset=offset)
            seen.extend(row["parameter_id"] for row in page["items"])
        assert len(seen) == 40 and len(set(seen)) == 40
        sub = _tree(server, wid, cid, parent="G0/Sub")
        assert sub["parameter_total"] == 5 and sub["directories"] == []
