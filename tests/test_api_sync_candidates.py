"""M16-26 candidate read API tests."""

from authz_fixtures import as_role, make_server, seed_roles

from app.core import storage
from app.services import (
    config_v3_catalog,
    config_v3_drafts,
    file_versions,
    sync_candidates,
)


def make_workspace(caller, name):
    status, _headers, body = caller.json("POST", "/api/v3/workspaces", {"name": name})
    assert status == 201, body
    return body["workspace"]["id"]


def make_candidate(wid):
    with storage.use_workspace(wid), storage.use_profile("default"):
        storage.save_config_file("A.xml", b"<R><x>1</x></R>")
        rows = file_versions.list_resources("default")
    version_id = next(row["version_id"] for row in rows if row["name"] == "A.xml")
    return sync_candidates.create_candidate(
        wid, "default", "src-1", 1, "a" * 64, "default:cfg:rel", file_version_id=version_id
    )


def test_candidate_list_get_and_isolation():
    seed_roles("sync-candidates")
    with make_server() as server:
        admin = as_role(server, "admin")
        visitor = as_role(server, "visitor")
        w1 = make_workspace(admin, "W1")
        w2 = make_workspace(admin, "W2")
        candidate = make_candidate(w1)

        status, _headers, listing = visitor.json(
            "GET", "/api/v3/workspaces/%s/sync/candidates" % w1
        )
        assert status == 200, listing
        assert listing["total"] == 1
        assert listing["items"][0]["candidate_id"] == candidate["candidate_id"]

        status, _headers, fetched = visitor.json(
            "GET",
            "/api/v3/workspaces/%s/sync/candidates/%s" % (w1, candidate["candidate_id"]),
        )
        assert status == 200, fetched
        assert fetched["candidate_id"] == candidate["candidate_id"]

        status, _headers, other = visitor.json(
            "GET",
            "/api/v3/workspaces/%s/sync/candidates/%s" % (w2, candidate["candidate_id"]),
        )
        assert status == 404, other


def _make_config_candidate(wid):
    with storage.use_workspace(wid), storage.use_profile("default"):
        storage.save_config_file("A.xml", b"<R><x>2</x></R>")
    with storage.use_workspace(wid):
        config = config_v3_catalog.create_config("default", {"code": "C1", "name": "Config"})
        cid = config["config_id"]
        config_v3_catalog.create_parameter(
            "default", cid, {"name": "x", "directory": [], "type": "int"}
        )
        release = config_v3_catalog.create_release("default", cid, {"version": "1"})
        did = "d0-" + release["release_id"]
        config_v3_drafts.patch_values(
            "default",
            cid,
            did,
            {
                "request_id": "seed",
                "items": [
                    {
                        "parameter_id": config_v3_catalog.list_parameters("default", cid)["items"][0]["parameter_id"],
                        "membership": "included",
                        "value_state": "present",
                        "value": "1",
                    }
                ],
            },
            if_match='"0"',
        )
        with storage.use_profile("default"):
            row = next(
                item for item in file_versions.list_resources("default") if item["name"] == "A.xml"
            )
    candidate = sync_candidates.create_candidate(
        wid,
        "default",
        "src-1",
        1,
        row["blob_sha256"],
        "file_archive:%s" % row["file_id"],
        file_version_id=row["version_id"],
    )
    return cid, did, candidate["candidate_id"]


def test_candidate_apply_preview_and_apply_write_only_draft():
    seed_roles("sync-candidates-apply")
    with make_server() as server:
        admin = as_role(server, "admin")
        visitor = as_role(server, "visitor")
        wid = make_workspace(admin, "W-apply")
        cid, did, candidate_id = _make_config_candidate(wid)
        url = "/api/v3/workspaces/%s/sync/candidates/%s" % (wid, candidate_id)

        status, _headers, preview = visitor.json(
            "POST", url + "/apply-preview", {"config_id": cid, "draft_id": did}
        )
        assert status == 201, preview
        assert preview["candidate_id"] == candidate_id
        assert any(item["action"] == "from_new" for item in preview["items"])

        status, _headers, denied = visitor.json(
            "POST",
            url + "/apply",
            {"preview_id": preview["preview_id"], "request_id": "r1", "decisions": {}},
        )
        assert status == 403, denied

        status, _headers, applied = admin.json(
            "POST",
            url + "/apply",
            {"preview_id": preview["preview_id"], "request_id": "r1", "decisions": {}},
        )
        assert status == 200, applied
        assert applied["changed"]
        with storage.use_workspace(wid):
            draft = config_v3_drafts.get_draft("default", cid, did)
        assert list(draft["values"].values())[0]["value"] == "2"
