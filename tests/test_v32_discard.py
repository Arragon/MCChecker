"""Discard removes only the reviewed draft, preserving all saved records."""
from authz_fixtures import as_role, make_server, seed_roles
from test_v3_machine_branch import _create, _config, _param, _release, _freeze_d0, _machine


def test_discard_unsaved_machine_is_scoped_cas_guarded_and_replayable():
    seed_roles("v32-discard-machine")
    with make_server() as server:
        admin = as_role(server, "admin")
        wid = _create(server, "Discard")
        cid = _config(server, wid)
        pid = _param(server, wid, cid)
        rid = _release(server, wid, cid)
        _freeze_d0(server, wid, cid, pid, rid)
        machine = _machine(server, wid, cid, rid)[2]["machine"]
        base = "/api/v3/workspaces/%s/profiles/default/configs/%s" % (wid, cid)
        body = {"request_id": "discard-new", "draft_id": machine["draft"]["draft_id"], "expected_revision": 0}
        assert as_role(server, "visitor").json("POST", base + "/discard-draft", body)[0] == 403
        assert admin.json("POST", base + "/discard-draft", dict(body, expected_revision=1))[0] == 409
        other = _create(server, "Other")
        assert admin.json("POST", base.replace(wid, other) + "/discard-draft", body)[0] == 404
        status, _, discarded = admin.json("POST", base + "/discard-draft", body)
        assert status == 200 and discarded["removed_object"] is True, discarded
        assert admin.json("GET", base + "/machines")[2]["items"] == []
        assert admin.json("GET", base + "/revisions/d0-" + rid)[0] == 200
        replay = admin.json("POST", base + "/discard-draft", body)[2]
        assert replay["replayed"] is True
        assert len([e for e in admin.json("GET", base + "/events")[2]["items"] if e["type"] == "discard_draft"]) == 1


def test_discard_upgrade_restores_saved_machine_version_and_leaves_snapshot_unchanged():
    seed_roles("v32-discard-upgrade")
    with make_server() as server:
        admin = as_role(server, "admin")
        wid = _create(server, "Upgrade")
        cid = _config(server, wid)
        pid = _param(server, wid, cid)
        rid = _release(server, wid, cid)
        _freeze_d0(server, wid, cid, pid, rid)
        machine = _machine(server, wid, cid, rid)[2]["machine"]
        base = "/api/v3/workspaces/%s/profiles/default/configs/%s" % (wid, cid)
        path = base + "/machines/" + machine["machine_id"]
        published = admin.json("POST", path + "/publish", {"expected_revision": 0})[2]["machine"]["head_id"]
        before = admin.json("GET", base + "/revisions/" + published)[2]
        new = admin.json("POST", base + "/releases", {"version": "692"})[2]["release"]["release_id"]
        _freeze_d0(server, wid, cid, pid, new)
        draft = admin.json("POST", path + "/drafts", {"release_id": new})[2]["machine"]["draft"]
        body = {"request_id": "discard-upgrade", "draft_id": draft["draft_id"], "expected_revision": 0}
        status, _, result = admin.json("POST", base + "/discard-draft", body)
        assert status == 200 and result["return_revision_id"] == published, result
        restored = admin.json("GET", path)[2]["machine"]
        assert restored["release_id"] == rid and restored["draft"] is None and restored["head_id"] == published
        assert admin.json("GET", base + "/revisions/" + published)[2] == before
        assert admin.json("POST", base + "/discard-draft", dict(body, request_id="cannot-delete-saved", draft_id=published))[0] == 404


def test_discard_new_version_and_standard_draft_preserves_saved_history():
    seed_roles("v32-discard-standard")
    with make_server() as server:
        admin = as_role(server, "admin")
        wid, cid = _create(server, "Standards"), None
        cid = _config(server, wid)
        rid = _release(server, wid, cid)
        base = "/api/v3/workspaces/%s/profiles/default/configs/%s" % (wid, cid)
        assert admin.json("POST", base + "/discard-draft", {"request_id": "remove-version", "draft_id": "d0-" + rid, "expected_revision": 0})[0] == 200
        assert admin.json("GET", base)[2]["config"]["releases"] == []
        rid = _release(server, wid, cid)
        pid = _param(server, wid, cid)
        # This version initially has an empty definition; freeze that real draft.
        assert admin.json("POST", base + "/drafts/d0-" + rid + "/freeze", {"request_id": "freeze", "expected_revision": 0})[0] == 200
        draft = admin.json("POST", base + "/releases/" + rid + "/baseline-drafts")[2]["draft"]
        assert admin.json("POST", base + "/discard-draft", {"request_id": "remove-standard-draft", "draft_id": draft["draft_id"], "expected_revision": 0})[0] == 200
        assert admin.json("GET", base + "/revisions/d0-" + rid)[0] == 200
        assert not admin.json("GET", base)[2]["config"]["releases"][0]["baseline_draft"]


def test_discard_version_refuses_existing_version_and_update_source_references():
    seed_roles("v32-discard-references")
    with make_server() as server:
        admin = as_role(server, "admin")
        wid = _create(server, "References")
        cid = _config(server, wid)
        rid = _release(server, wid, cid)
        base = "/api/v3/workspaces/%s/profiles/default/configs/%s" % (wid, cid)
        body = {"draft_id": "d0-" + rid, "expected_revision": 0, "request_id": "referenced-version"}
        child = admin.json("POST", base + "/releases", {"version": "child", "predecessor_id": rid})[2]["release"]["release_id"]
        assert admin.json("POST", base + "/discard-draft", body)[0] == 409
        assert admin.json("POST", base + "/discard-draft", dict(body, draft_id="d0-" + child, request_id="child"))[0] == 200
        sources = "/api/v3/workspaces/%s/sync/sources" % wid
        status, _, source = admin.json("POST", sources, {"profile_id": "default", "name": "Reference", "kind": "http_file",
            "resource_ref": "https://example.invalid/draft.json", "target": {"kind": "default_candidate", "configuration_id": cid, "release_id": rid}})
        assert status == 201, source
        assert admin.json("POST", base + "/discard-draft", body)[0] == 409
        assert admin.json("DELETE", sources + "/" + source["source_id"], headers={"If-Match": '"1"'})[0] == 200
        assert admin.json("POST", base + "/discard-draft", body)[0] == 200
