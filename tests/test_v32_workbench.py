"""Read-only V3.2 queries filter before paging and enforce snapshot ownership."""

import pytest

from authz_fixtures import as_role, make_server, seed_roles
from test_v3_baseline_head import _create, _config, _param, _release, _d0_patch, _freeze, _new_baseline, _patch_baseline, _publish


def test_workbench_search_directory_and_pagination_cover_3248_definitions():
    seed_roles("v32-workbench-large")
    with make_server() as server:
        wid = _create(server, "Workbench")
        cid = _config(server, wid)
        from app.core import storage
        from app.services import config_v3_catalog
        # Large synthetic metadata stays inside the pytest-isolated data root.
        with storage.use_workspace(wid), storage.use_profile("default"):
            catalog = config_v3_catalog._load("default")
            config = catalog["configs"][cid]
            definition = config_v3_catalog._definition(config)
            definition["current"]["parameters"] = {
                "p-%04d" % i: {"parameter_id": "p-%04d" % i, "name": "Parameter %04d" % i,
                                "type": "string", "directory": ["G", "H"] if i == 3247 else ["G"], "unit": ""}
                for i in range(3248)
            }
            config_v3_catalog._snapshot_definition(config)
            config_v3_catalog._save("default", catalog)
        rid = _release(server, wid, cid)
        with storage.use_workspace(wid), storage.use_profile("default"):
            catalog = config_v3_catalog._load("default")
            config = catalog["configs"][cid]
            config["releases"][rid]["binding_set"]["bindings"]["fixture-field"] = {"parameter_id": "p-3247", "locator": "/Entire/Long/Field"}
            config_v3_catalog._save("default", catalog)
            before = open(config_v3_catalog._path("default"), "rb").read()
        path = "/api/v3/workspaces/%s/profiles/default/configs/%s/parameter-query" % (wid, cid)
        visitor = as_role(server, "visitor")
        payload = {"revision_id": "d0-" + rid, "limit": 30}
        status, _, page = visitor.json("POST", path, payload)
        assert status == 200, page
        assert page["total"] == 3248 and len(page["items"]) == 30
        last = visitor.json("POST", path, dict(payload, offset=3240))[2]
        assert len(last["items"]) == 8 and last["items"][-1]["parameter_id"] == "p-3247"
        assert visitor.json("POST", path, dict(payload, q="long/field"))[2]["total"] == 1
        assert visitor.json("POST", path, dict(payload, directory="G/H"))[2]["total"] == 1
        assert visitor.json("POST", path, dict(payload, filter="modified", changed_ids=["p-3247"]))[2]["total"] == 1
        with storage.use_workspace(wid), storage.use_profile("default"):
            assert open(config_v3_catalog._path("default"), "rb").read() == before


def test_workbench_difference_and_foreign_records_use_exact_snapshots():
    seed_roles("v32-workbench-snapshots")
    with make_server() as server:
        wid, other = _create(server, "Snapshots"), _create(server, "Other")
        cid, other_cid = _config(server, wid), _config(server, other)
        pid = _param(server, wid, cid, "Speed")
        rid = _release(server, wid, cid)
        assert _d0_patch(server, wid, cid, "d0-" + rid, pid, "0")[0] == 200
        assert _freeze(server, wid, cid, "d0-" + rid)[0] == 200
        draft = _new_baseline(server, wid, cid, rid)[2]["draft"]["draft_id"]
        assert _patch_baseline(server, wid, cid, draft, pid, "3")[0] == 200
        saved = _publish(server, wid, cid, draft, "save")[2]["baseline"]["baseline_id"]
        visitor = as_role(server, "visitor")
        path = "/api/v3/workspaces/%s/profiles/default/configs/%s/parameter-query" % (wid, cid)
        payload = {"revision_id": saved, "reference_id": "d0-" + rid, "filter": "different"}
        status, _, page = visitor.json("POST", path, payload)
        assert status == 200 and page["total"] == 1, page
        assert page["items"][0]["member"]["value"] == "3" and page["items"][0]["reference"]["value"] == "0"
        status, _, preview = visitor.json("POST", path, dict(payload, edits={pid: {"value_state": "present", "value": "0", "intent": "inherit"}}))
        assert status == 200 and preview["total"] == 0
        assert visitor.json("POST", path, {"revision_id": saved})[2]["items"][0]["member"]["value"] == "3"
        assert visitor.json("POST", path.replace(wid, other).replace(cid, other_cid), payload)[0] == 404
        assert visitor.json("POST", path, {"revision_id": saved, "filter": "different"})[0] == 400


def test_parameter_metadata_and_directories_follow_record_definition_snapshot():
    seed_roles("v32-record-definitions")
    with make_server() as server:
        wid = _create(server, "Definitions")
        cid = _config(server, wid)
        pid = _param(server, wid, cid, "Original")
        rid = _release(server, wid, cid)
        assert _d0_patch(server, wid, cid, "d0-" + rid, pid, "1")[0] == 200
        assert _freeze(server, wid, cid, "d0-" + rid)[0] == 200
        first = _new_baseline(server, wid, cid, rid)[2]["draft"]["draft_id"]
        assert _patch_baseline(server, wid, cid, first, pid, "2")[0] == 200
        saved = _publish(server, wid, cid, first, "saved-definition")[2]["baseline"]["baseline_id"]
        admin = as_role(server, "admin")
        base = "/api/v3/workspaces/%s/profiles/default/configs/%s" % (wid, cid)
        assert admin.json("POST", base + "/parameters", {"name": "New parameter", "type": "string", "directory": ["New directory"]})[0] == 201
        draft = _new_baseline(server, wid, cid, rid)[2]["draft"]["draft_id"]
        from app.core import storage
        from app.services import config_v3_catalog
        # Simulate a newer draft definition while leaving initial/history pinned.
        with storage.use_workspace(wid), storage.use_profile("default"):
            doc = config_v3_catalog._load("default")
            config = doc["configs"][cid]
            config["releases"][rid]["baseline_draft"]["definition_revision"] = config["definition"]["revision"]
            config_v3_catalog._save("default", doc)
        visitor = as_role(server, "visitor")
        for record, count in [(saved, 1), (draft, 2), ("d0-" + rid, 1)]:
            status, _, metadata = visitor.json("GET", base + "/parameters?release_id=" + rid + "&revision_id=" + record)
            assert status == 200 and metadata["total"] == count, metadata
            tree = visitor.json("GET", base + "/parameter-tree?revision_id=" + record)[2]
            assert any(row["name"] == "New directory" for row in tree["directories"]) is (count == 2)
        assert visitor.json("GET", base + "/parameters?revision_id=bl-foreign")[0] == 404
        assert visitor.json("GET", base + "/parameter-tree?release_id=other&revision_id=" + saved)[0] == 400


@pytest.mark.parametrize("invalid", [{"limit": 10}, {"limit": 30.5}, {"offset": True}, {"offset": -1},
                                    {"q": []}, {"filter": "unknown"}, {"changed_ids": [1]}])
def test_workbench_rejects_invalid_read_queries(invalid):
    seed_roles("v32-workbench-invalid")
    with make_server() as server:
        wid, cid = _create(server, "Invalid"), None
        cid = _config(server, wid)
        path = "/api/v3/workspaces/%s/profiles/default/configs/%s/parameter-query" % (wid, cid)
        assert as_role(server, "visitor").json("POST", path, invalid)[0] == 400
