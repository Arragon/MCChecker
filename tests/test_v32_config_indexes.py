"""V3.2 indexes preserve saved ownership independently of pending drafts."""

import pytest

from authz_fixtures import as_role, make_server, seed_roles
from test_v3_baseline_head import _create, _config, _param, _release, _d0_patch, _freeze
from test_v3_machine_branch import _machine, _publish


def test_machine_index_groups_by_saved_release_and_is_scoped_and_paginated():
    seed_roles("v32-index")
    with make_server() as server:
        admin = as_role(server, "admin")
        wid = _create(server, "Index")
        other = _create(server, "Other")
        cid = _config(server, wid)
        pid = _param(server, wid, cid, "Pulse")
        releases = [_release(server, wid, cid, version) for version in ("690", "691")]
        for rid in releases:
            assert _d0_patch(server, wid, cid, "d0-" + rid, pid, "1")[0] == 200
            assert _freeze(server, wid, cid, "d0-" + rid)[0] == 200
        machine = _machine(server, wid, cid, releases[0])[2]["machine"]
        mid = machine["machine_id"]
        assert _publish(server, wid, cid, mid)[0] == 200
        path = "/api/v3/workspaces/%s/profiles/default" % wid
        status, _, result = admin.json("POST", path + "/configs/%s/machines/%s/drafts" % (cid, mid),
                                      {"release_id": releases[1], "base_kind": "default"})
        assert status == 201, result
        status, _, result = as_role(server, "visitor").json("GET", path + "/config-machines?status=draft&limit=1")
        assert status == 200, result
        assert result["total"] == 1 and result["limit"] == 1
        row = result["items"][0]
        assert row["release_id"] == releases[0] and row["version"] == "690"
        assert row["draft"]["release_id"] == releases[1]
        assert "values" not in row["draft"]
        assert admin.json("GET", path + "/config-machines?offset=1")[2]["items"] == []
        assert admin.json("GET", path + "/config-machines?release_id=" + releases[1])[2]["total"] == 0
        assert admin.json("GET", path.replace(wid, other) + "/config-machines")[2]["total"] == 0


def test_standard_index_includes_unsaved_initial_draft_and_release_filters():
    seed_roles("v32-standards")
    with make_server() as server:
        wid = _create(server, "Standards")
        cid = _config(server, wid)
        rid = _release(server, wid, cid)
        url = "/api/v3/workspaces/%s/profiles/default/config-standards" % wid
        admin = as_role(server, "admin")
        status, _, result = admin.json("GET", url + "?status=unsaved")
        assert status == 200, result
        assert result["total"] == 1 and result["limit"] == 50
        row = result["items"][0]
        assert row["saved"] is None and row["initial_revision_id"] is None
        assert row["draft"]["draft_id"] == "d0-" + rid
        assert admin.json("GET", url + "?q=690&config_id=" + cid)[2]["total"] == 1
        assert admin.json("GET", url + "?release_id=missing")[2]["total"] == 0


def test_v32_manual_standard_save_does_not_invent_software_initial_values():
    seed_roles("v32-initial-separation")
    with make_server() as server:
        wid = _create(server, "Manual standard")
        cid = _config(server, wid)
        pid = _param(server, wid, cid, "Pulse")
        admin = as_role(server, "admin")
        root = "/api/v3/workspaces/%s/profiles/default" % wid
        status, _, created = admin.json("POST", root + "/configs/" + cid + "/releases",
                                        {"version": "3.2", "initial_mode": "separate"})
        assert status == 201, created
        rid = created["release"]["release_id"]
        assert _d0_patch(server, wid, cid, "d0-" + rid, pid, "5")[0] == 200
        assert _freeze(server, wid, cid, "d0-" + rid)[0] == 200
        release = admin.json("GET", root + "/configs/" + cid)[2]["config"]["releases"][0]
        assert release["default_revision_id"] and release["initial_revision_id"] is None
        row = admin.json("GET", root + "/config-standards")[2]["items"][0]
        assert row["saved"]["revision_id"] == release["default_revision_id"]
        assert row["initial_revision_id"] is None


@pytest.mark.parametrize("query", ["limit=0", "limit=101", "limit=x", "offset=-1", "status=unknown"])
def test_index_rejects_invalid_filters(query):
    seed_roles("v32-invalid")
    with make_server() as server:
        wid = _create(server, "Invalid")
        url = "/api/v3/workspaces/%s/profiles/default/config-standards?%s" % (wid, query)
        assert as_role(server, "admin").json("GET", url)[0] == 400
