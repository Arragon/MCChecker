"""Configuration file ownership must be explicit and workspace scoped."""

from concurrent.futures import ThreadPoolExecutor

import pytest

from authz_fixtures import as_role, make_server, seed_roles
from test_v3_baseline_head import _create, _config
from test_v3_large_catalog import _put


def _root(wid):
    return "/api/v3/workspaces/%s/profiles/default" % wid


def test_default_workspace_file_home_accepts_legacy_storage_scope():
    seed_roles("v32-default-home")
    with make_server() as server:
        admin = as_role(server, "admin")
        _put(server, "default", "Default.xml", b"<Root/>", "application/xml")
        for role in (admin, as_role(server, "visitor")):
            status, _, page = role.json("GET", _root("default") + "/config-files")
            assert status == 200, page
            assert any(row["name"] == "Default.xml" for row in page["items"])


def test_file_home_joins_explicit_identity_and_preserves_legacy_configs(monkeypatch):
    seed_roles("v32-files")
    with make_server() as server:
        admin = as_role(server, "admin")
        wid = _create(server, "Files")
        root = _root(wid)
        file = _put(server, wid, "Machine.xml", b"<Root><Value>1</Value></Root>", "application/xml")
        cid = _config(server, wid)
        assert admin.json("GET", root + "/config-files")[2]["items"][0]["configuration"] is None
        path = root + "/configs/" + cid + "/file"
        assert as_role(server, "visitor").json("PUT", path, {"file_id": file["file_id"]})[0] == 403
        status, _, result = admin.json("PUT", path, {"file_id": file["file_id"]})
        assert status == 200 and result["config"]["file_id"] == file["file_id"]
        assert not result["replayed"]
        assert admin.json("GET", root + "/configs/" + cid)[2]["config"]["file"]["name"] == "Machine.xml"
        assert admin.json("PUT", path, {"file_id": file["file_id"]})[2]["replayed"]
        status, _, page = as_role(server, "visitor").json("GET", root + "/config-files?q=Machine&limit=1")
        assert status == 200 and page["limit"] == 1 and page["total"] == 1
        assert page["items"][0]["configuration"]["config_id"] == cid
        assert admin.json("GET", root + "/config-files?offset=1")[2]["items"] == []
        assert admin.json("GET", root + "/config-files?q=missing")[2]["total"] == 0
        _put(server, wid, "Unrelated.xml", b"<Root/>", "application/xml")
        from app.services import file_versions
        original = file_versions.current_identity
        def selected_identity(profile_id, name):
            assert name != "Unrelated.xml", "配置详情不应读取无关文件正文"
            return original(profile_id, name)
        monkeypatch.setattr(file_versions, "current_identity", selected_identity)
        assert admin.json("GET", root + "/configs/" + cid)[2]["config"]["file"]["name"] == "Machine.xml"


def test_create_link_rejects_foreign_file_and_keeps_deleted_generation_separate():
    seed_roles("v32-file-generations")
    with make_server() as server:
        admin = as_role(server, "admin")
        wid, other = _create(server, "Files"), _create(server, "Other")
        file = _put(server, wid, "same.xml", b"<Root/>", "application/xml")
        foreign = _put(server, other, "same.xml", b"<Root/>", "application/xml")
        assert file["file_id"] != foreign["file_id"]
        root = _root(wid)
        payload = {"code": "owned", "name": "Owned", "file_id": foreign["file_id"]}
        assert admin.json("POST", root + "/configs", payload)[0] == 404
        payload["file_id"] = file["file_id"]
        status, _, result = admin.json("POST", root + "/configs", payload)
        assert status == 201, result
        cid = result["config"]["config_id"]
        payload["code"] = "duplicate"
        assert admin.json("POST", root + "/configs", payload)[0] == 409
        status, _, _ = admin.json("DELETE", "/api/v1/p/default/files/same.xml", headers={"X-Workspace-Id": wid})
        assert status == 200
        replacement = _put(server, wid, "same.xml", b"<Root/>", "application/xml")
        assert replacement["file_id"] != file["file_id"]
        assert admin.json("GET", root + "/config-files")[2]["items"][0]["configuration"] is None
        assert admin.json("GET", root + "/configs/" + cid)[2]["config"]["file_id"] == file["file_id"]
        assert admin.json("GET", root + "/configs/" + cid)[2]["config"]["file"]["missing"]
        assert admin.json("GET", _root(other) + "/config-files")[2]["items"][0]["configuration"] is None


def test_concurrent_file_attachment_has_one_owner():
    seed_roles("v32-file-race")
    with make_server() as server:
        admin = as_role(server, "admin")
        wid = _create(server, "Race")
        file = _put(server, wid, "Machine.xml", b"<Root/>", "application/xml")
        root = _root(wid)
        cid = _config(server, wid)
        other = admin.json("POST", root + "/configs", {"code": "second", "name": "Second"})[2]["config"]["config_id"]
        def attach(config_id):
            return as_role(server, "admin").json("PUT", root + "/configs/" + config_id + "/file", {"file_id": file["file_id"]})[0]
        with ThreadPoolExecutor(max_workers=2) as pool:
            assert sorted(pool.map(attach, (cid, other))) == [200, 409]


@pytest.mark.parametrize("query", ["limit=0", "limit=101", "offset=-1", "limit=x"])
def test_file_index_rejects_bad_pagination(query):
    seed_roles("v32-file-page")
    with make_server() as server:
        wid = _create(server, "Page")
        assert as_role(server, "admin").json("GET", _root(wid) + "/config-files?" + query)[0] == 400
