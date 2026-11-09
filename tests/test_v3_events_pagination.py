"""Workspace event pages remain profile/workspace scoped and preserve legacy reads."""
from authz_fixtures import as_role, make_server, seed_roles
from app.core import storage
from app.services import config_v3_history


def test_event_pages_and_legacy_response_are_isolated():
    seed_roles("event-pages")
    with make_server() as server:
        admin, visitor = as_role(server, "admin"), as_role(server, "visitor")
        _, _, created = admin.json("POST", "/api/v3/workspaces", {"name": "Event pages"})
        wid = created["workspace"]["id"]
        with storage.use_workspace(wid):
            for index in range(3):
                config_v3_history.record("default", "cfg1", "fixture", request_id="fixture-%s" % index)
        path = "/api/v3/workspaces/%s/profiles/default/events" % wid
        status, _, page = visitor.json("GET", path + "?limit=1&offset=1")
        assert status == 200, page
        assert page["total"] == 3 and len(page["items"]) == 1 and page["offset"] == 1
        assert page["items"][0]["request_id"] == "fixture-1"
        config_path = "/api/v3/workspaces/%s/profiles/default/configs/cfg1/events" % wid
        status, _, config_page = visitor.json("GET", config_path + "?limit=1&offset=1")
        assert status == 200 and config_page["total"] == 3 and len(config_page["items"]) == 1
        assert visitor.json("GET", config_path + "?limit=201")[0] == 400
        assert visitor.json("GET", path + "?offset=-1")[0] == 400
        _, _, legacy = visitor.json("GET", path)
        assert len(legacy["items"]) == 3 and "total" not in legacy
        _, _, other = visitor.json("GET", "/api/v3/workspaces/default/profiles/default/events?limit=1")
        assert other["items"] == [] and other["total"] == 0
