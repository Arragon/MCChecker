"""File update projections filter before pagination using durable ownership."""
from app.core import storage
from app.services import config_v3_catalog, file_versions, sync_candidates, sync_preview, sync_run_store, sync_sources, workspace_catalog
from test_sync_run_store import make_run


def test_configuration_sources_include_its_unique_file_and_preview_is_scoped():
    wid = workspace_catalog.create_workspace("V32 scoped updates")["id"]
    with storage.use_workspace(wid), storage.use_profile("default"):
        storage.save_config_file("A.json", b'{"a":1}')
        file = file_versions.list_resources("default")[0]
        config = config_v3_catalog.create_config("default", {"name": "A", "code": "A", "file_id": file["file_id"]})
    cid = config["config_id"]
    targets = [{"kind": "file_archive", "file_id": file["file_id"]},
               {"kind": "default_candidate", "configuration_id": cid, "release_id": "rel-one"},
               {"kind": "default_candidate", "configuration_id": cid + "-other", "release_id": "rel-one"}]
    sources = [sync_sources.create_source(wid, {"name": "A%d.json" % i, "profile_id": "default", "kind": "http_file", "resource_ref": "https://example.invalid/%d" % i, "target": target}) for i, target in enumerate(targets)]
    page = sync_sources.list_sources(wid, profile_id="default", configuration_id=cid, limit=1)
    assert page["total"] == 2 and page["next_cursor"] == "1"
    second = sync_sources.list_sources(wid, profile_id="default", configuration_id=cid, limit=1, cursor="1")
    assert {page["items"][0]["source_id"], second["items"][0]["source_id"]} == {r["source_id"] for r in sources[:2]}
    # A missing binding is reported as excluded, never replaced by another file.
    preview = sync_preview.create_preview(wid, {"scope": "current_configuration", "profile_id": "default", "configuration_id": cid})
    ids = {row["source_id"] for row in preview["items"] + preview["excluded"]}
    assert ids == {r["source_id"] for r in sources[:2]}
    for i, target in enumerate(targets[1:]):
        sync_candidates.create_candidate(wid, "default", sources[i+1]["source_id"], 1, str(i+1) * 64,
            "default_candidate:%s:rel-one" % target["configuration_id"], file_version_id=file["version_id"])
    assert sync_candidates.list_candidates(wid, profile_id="default", configuration_id=cid, limit=1)["total"] == 1


def test_run_file_scope_uses_frozen_target_even_after_source_rebinding():
    wid = workspace_catalog.create_workspace("V32 historical run ownership")["id"]
    with storage.use_workspace(wid), storage.use_profile("default"):
        config = config_v3_catalog.create_config("default", {"name": "A", "code": "A"})
    cid = config["config_id"]
    for rid, target in [("run-owned", cid), ("run-other", cid + "-other")]:
        sync_run_store.create_run(wid, make_run(wid, rid), frozen={"profile_id": "default", "source_ids": ["same-source"],
            "source_targets": [{"source_id": "same-source", "profile_id": "default", "target": {"kind": "default_candidate", "configuration_id": target}}]})
    sync_run_store.create_run(wid, make_run(wid, "run-legacy"), frozen={"profile_id": "default", "source_ids": ["same-source"]})
    scoped = sync_run_store.list_runs(wid, profile_id="default", configuration_id=cid, limit=1)
    assert scoped["total"] == 1 and scoped["items"][0]["run"]["run_id"] == "run-owned"
    assert sync_run_store.list_runs(wid)["total"] == 3
