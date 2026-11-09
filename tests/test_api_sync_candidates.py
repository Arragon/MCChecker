"""M16-26 candidate read API tests."""

from authz_fixtures import as_role, make_server, seed_roles

from app.core import storage
from app.services import (
    config_v3_catalog,
    config_v3_drafts,
    file_versions,
    sync_candidates,
    sync_sources,
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
    source = _make_source(wid, row["file_id"], "A.xml")
    candidate = sync_candidates.create_candidate(
        wid,
        "default",
        source["source_id"],
        1,
        row["blob_sha256"],
        "file_archive:%s" % row["file_id"],
        file_version_id=row["version_id"],
    )
    return cid, did, candidate["candidate_id"]


def _make_source(wid, file_id, name):
    return sync_sources.create_source(wid, {"profile_id": "default", "name": name,
        "kind": "http_file", "resource_ref": "https://example.invalid/source.xml",
        "target": {"kind": "file_archive", "file_id": file_id}})


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
        assert preview["base_known"] is False
        conflicts = [item for item in preview["items"] if item["action"] == "conflict"]
        assert conflicts, "Unknown original source must not silently overwrite local values"

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
        assert status == 400, applied
        status, _headers, applied = admin.json("POST", url + "/apply", {
            "preview_id": preview["preview_id"], "request_id": "r2",
            "decisions": {item["parameter_id"]: "use_new" for item in conflicts},
        })
        assert status == 200, applied
        assert applied["changed"]
        with storage.use_workspace(wid):
            draft = config_v3_drafts.get_draft("default", cid, did)
        assert list(draft["values"].values())[0]["value"] == "2"


def test_original_file_snapshot_is_the_base_and_client_cannot_replace_it():
    seed_roles("sync-source-base")
    with make_server() as server:
        admin = as_role(server, "admin")
        wid = make_workspace(admin, "Original source")
        config_id, draft_id, original_candidate_id = _make_config_candidate(wid)
        incoming = sync_candidates.get_candidate(wid, original_candidate_id)
        with storage.use_workspace(wid), storage.use_profile("default"):
            storage.save_config_file("A.xml", b"<R><x>0</x></R>")
            original = file_versions.current_identity("default", "A.xml")
        source = _make_source(wid, original["file_id"], "Known base")
        candidate = sync_candidates.create_candidate(wid, "default", source["source_id"], 1,
            incoming["content_hash"], incoming["target_scope"], file_version_id=incoming["file_version_id"],
            base_file_version_id=original["version_id"])
        url = "/api/v3/workspaces/%s/sync/candidates/%s/apply-preview" % (wid, candidate["candidate_id"])
        payload = {"config_id": config_id, "draft_id": draft_id}
        status, _, preview = admin.json("POST", url, payload)
        assert status == 201 and preview["base_known"] is True, preview
        item = next(row for row in preview["items"] if row.get("theirs"))
        assert item["action"] == "conflict"
        assert item["base"]["value"]["text"] == "0"
        assert item["ours"]["value"]["text"] == "1"
        assert item["theirs"]["value"]["text"] == "2"
        assert admin.json("POST", url, dict(payload, base_revision_id=draft_id))[0] == 400


def test_unchanged_local_value_can_adopt_new_source_from_known_snapshot():
    seed_roles("sync-known-unchanged-local")
    with make_server() as server:
        admin = as_role(server, "admin")
        wid = make_workspace(admin, "Known original")
        with storage.use_workspace(wid), storage.use_profile("default"):
            storage.save_config_file("A.xml", b"<R><x>1</x></R>")
            original = file_versions.current_identity("default", "A.xml")
        config_id, draft_id, original_candidate_id = _make_config_candidate(wid)
        incoming = sync_candidates.get_candidate(wid, original_candidate_id)
        source = _make_source(wid, original["file_id"], "Known unchanged")
        candidate = sync_candidates.create_candidate(wid, "default", source["source_id"], 1,
            incoming["content_hash"], incoming["target_scope"], file_version_id=incoming["file_version_id"],
            base_file_version_id=original["version_id"])
        url = "/api/v3/workspaces/%s/sync/candidates/%s/apply-preview" % (wid, candidate["candidate_id"])
        status, _, preview = admin.json("POST", url, {"config_id": config_id, "draft_id": draft_id})
        assert status == 201 and preview["can_apply"], preview
        assert next(row for row in preview["items"] if row.get("theirs"))["action"] == "from_new"


def test_candidate_apply_rechecks_source_binding_and_missing_source_is_read_only():
    seed_roles("candidate-source-cas")
    with make_server() as server:
        admin = as_role(server, "admin")
        wid = make_workspace(admin, "Binding check")
        config_id, draft_id, candidate_id = _make_config_candidate(wid)
        candidate = sync_candidates.get_candidate(wid, candidate_id)
        source = sync_sources.get_source(wid, candidate["source_id"])
        url = "/api/v3/workspaces/%s/sync/candidates/%s" % (wid, candidate_id)
        payload = {"config_id": config_id, "draft_id": draft_id}
        status, _, preview = admin.json("POST", url + "/apply-preview", payload)
        assert status == 201, preview
        source = sync_sources.update_source(wid, source["source_id"], source["revision"], {"resource_ref": "https://example.invalid/rebound.xml"})
        status, _, rejected = admin.json("POST", url + "/apply", {"preview_id": preview["preview_id"], "request_id": "stale-binding",
            "decisions": {row["parameter_id"]: "use_new" for row in preview["items"] if row["action"] == "conflict"}})
        assert status == 409, rejected
        sync_sources.delete_source(wid, source["source_id"], source["revision"])
        assert admin.json("POST", url + "/apply-preview", payload)[0] == 409
        with storage.use_workspace(wid):
            assert config_v3_drafts.get_draft("default", config_id, draft_id)["revision"] == 1
