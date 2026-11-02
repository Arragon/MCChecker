"""M16-26 candidate read API tests."""

from authz_fixtures import as_role, make_server, seed_roles

from app.core import storage
from app.services import file_versions, sync_candidates


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
