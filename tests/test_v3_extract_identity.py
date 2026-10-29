"""M13-04 JSON/XML 全量树与增量身份匹配计划（INH-2146）。"""

from __future__ import annotations

import hashlib
import json
import os

from authz_fixtures import as_role, make_server, seed_roles

from app.core import storage


def _create(server, name):
    status, _h, body = as_role(server, "admin").json(
        "POST", "/api/v3/workspaces", {"name": name}
    )
    assert status == 201, body
    return body["workspace"]["id"]


def _config(server, wid):
    status, _h, body = as_role(server, "admin").json(
        "POST", "/api/v3/workspaces/%s/profiles/default/configs" % wid,
        {"code": "MC-001", "name": "配置"},
    )
    assert status == 201, body
    return body["config"]["config_id"]


def _put(server, wid, name, data, content_type="application/json", overwrite=False):
    url = "/api/v1/p/default/files/%s%s" % (name, "?overwrite=1" if overwrite else "")
    status, _h, raw = as_role(server, "admin").request(
        "PUT", url, body=data, headers={"X-Workspace-Id": wid, "Content-Type": content_type}
    )
    assert status in (200, 201), raw
    return _selector(server, wid, name)


def _selector(server, wid, name):
    status, _h, body = as_role(server, "admin").json(
        "GET", "/api/v3/workspaces/%s/profiles/default/file-selector?q=%s" % (wid, name)
    )
    assert status == 200, body
    return body["items"][0]


def _preview(server, wid, cid, vid, **query):
    path = "/api/v3/workspaces/%s/profiles/default/configs/%s/extract-preview?version_id=%s" % (
        wid,
        cid,
        vid,
    )
    for key, value in query.items():
        path += "&%s=%s" % (key, value)
    return as_role(server, "admin").json("GET", path)


def _param(server, wid, cid, name, directory, type_name="", source=None):
    payload = {"name": name, "directory": directory, "type": type_name}
    if source:
        payload["source"] = source
    status, _h, body = as_role(server, "admin").json(
        "POST",
        "/api/v3/workspaces/%s/profiles/default/configs/%s/parameters" % (wid, cid),
        payload,
    )
    assert status == 201, body
    return body["parameter"]["parameter_id"]


def _catalog_digest(wid):
    with storage.use_workspace(wid), storage.use_profile("default"):
        path = os.path.join(storage.get_profile_data_dir("default"), "configs_v3", "catalog.json")
    with open(path, "rb") as handle:
        return hashlib.sha256(handle.read()).hexdigest()


def _by_path(body):
    return {item["display_path"]: item for item in body["items"]}


def test_xml_preview_classifies_and_writes_nothing():
    seed_roles("extract-xml")
    xml = (
        b"<R>"
        b"<G><Gain>1.5</Gain><NewFlag>true</NewFlag></G>"
        b"<H><Gain>2</Gain></H>"
        b"<Empty/>"
        b"<Case><Gain>3</Gain><gain>4</gain></Case>"
        b"</R>"
    )
    with make_server() as server:
        wid = _create(server, "W1")
        cid = _config(server, wid)
        item = _put(server, wid, "extract.xml", xml, "application/xml")
        vid = item["version_id"]
        before = _catalog_digest(wid)

        status, _h, body = _preview(server, wid, cid, vid)
        assert status == 200, body
        assert body["source"]["file_id"] == item["file_id"]
        assert body["source"]["version_id"] == vid
        assert body["counts"]["total"] == 6
        assert body["counts"]["new"] == 5
        assert body["counts"]["null_uncertain"] == 1
        rows = _by_path(body)
        assert rows["/R[1]/G[1]/Gain[1]"]["directory"] == ["G"]
        assert rows["/R[1]/H[1]/Gain[1]"]["directory"] == ["H"]
        assert rows["/R[1]/Empty[1]"]["status"] == "null_uncertain"
        assert rows["/R[1]/Empty[1]"]["type_options"]
        assert rows["/R[1]/Case[1]/Gain[1]"]["leaf_name"] == "Gain"
        assert rows["/R[1]/Case[1]/gain[1]"]["leaf_name"] == "gain"
        for row in body["items"]:
            assert "value" not in row, "预览不得返回值"
        assert _catalog_digest(wid) == before, "预览必须零写"

        gain = rows["/R[1]/G[1]/Gain[1]"]
        _param(
            server, wid, cid, "Gain", ["G"], "number",
            {"canonical_locator": gain["canonical_locator"], "file_id": item["file_id"], "active": True},
        )
        gain_h = rows["/R[1]/H[1]/Gain[1]"]
        _param(
            server, wid, cid, "Gain", ["H"], "number",
            {"canonical_locator": gain_h["canonical_locator"], "file_id": item["file_id"], "active": True},
        )
        status, _h, again = _preview(server, wid, cid, vid)
        assert status == 200, again
        after = _by_path(again)
        assert after["/R[1]/G[1]/Gain[1]"]["status"] == "existing"
        assert after["/R[1]/G[1]/Gain[1]"]["matched_by"] == "source_locator"
        assert after["/R[1]/H[1]/Gain[1]"]["status"] == "existing"
        assert after["/R[1]/Case[1]/Gain[1]"]["status"] == "ambiguous"
        assert len(after["/R[1]/Case[1]/Gain[1]"]["candidates"]) == 2
        assert after["/R[1]/Case[1]/gain[1]"]["status"] == "new"
        assert after["/R[1]/G[1]/NewFlag[1]"]["status"] == "new"
        assert again["counts"]["existing"] == 2
        assert again["counts"]["ambiguous"] == 1

        conflict = _preview(server, wid, cid, vid, filter="conflict")[2]
        assert {row["status"] for row in conflict["items"]} <= {
            "same_name_other_path", "ambiguous", "type_conflict", "null_uncertain"
        }
        page = _preview(server, wid, cid, vid, offset=1, limit=2)[2]
        assert page["total"] == 6 and len(page["items"]) == 2
        assert [row["row_id"] for row in page["items"]] == [row["row_id"] for row in again["items"][1:3]]


def test_json_type_conflict_null_and_case_preserved():
    seed_roles("extract-json")
    payload = {
        "Channels": [{"Gain": 0}, {"Gain": 1}],
        "Meta": {"flag": False, "note": None, "Big": {"X": 100}},
        "CaseName": "a",
        "casename": "b",
        "Vals": [1, 2],
    }
    with make_server() as server:
        wid = _create(server, "W1")
        cid = _config(server, wid)
        item = _put(server, wid, "extract.json", json.dumps(payload).encode("utf-8"))
        vid = item["version_id"]
        status, _h, body = _preview(server, wid, cid, vid)
        assert status == 200, body
        rows = {(row["leaf_name"], tuple(row["directory"])): row for row in body["items"]}
        assert rows[("Gain", ("Channels", "[0]"))]["status"] == "new"
        assert rows[("Gain", ("Channels", "[0]"))]["suggested_type"] == "number"
        assert rows[("flag", ("Meta",))]["suggested_type"] == "boolean"
        assert rows[("note", ("Meta",))]["status"] == "null_uncertain"
        assert rows[("X", ("Meta", "Big"))]["status"] == "new"
        assert rows[("CaseName", ())]["leaf_name"] == "CaseName"
        assert rows[("casename", ())]["leaf_name"] == "casename"
        unsupported = [row for row in body["items"] if row["status"] == "unsupported"]
        assert {row["reasons"][0] for row in unsupported} == {"no_leaf_name"}
        assert len(unsupported) == 2, "数组标量没有名称，不得被当成参数"

        _param(server, wid, cid, "Gain", ["Channels", "[0]"], "string")
        _param(server, wid, cid, "flag", ["Meta"], "boolean")
        status, _h, again = _preview(server, wid, cid, vid)
        rows2 = {(row["leaf_name"], tuple(row["directory"])): row for row in again["items"]}
        assert rows2[("Gain", ("Channels", "[0]"))]["status"] == "type_conflict"
        assert rows2[("Gain", ("Channels", "[0]"))]["matched_by"] == "name_directory"
        assert rows2[("Gain", ("Channels", "[1]"))]["status"] == "same_name_other_path"
        assert rows2[("flag", ("Meta",))]["status"] == "existing"
        assert again["counts"]["type_conflict"] == 1
        assert again["counts"]["same_name_other_path"] == 1


def test_repeated_xml_leaf_is_ambiguous_not_overwritten():
    seed_roles("extract-repeat")
    xml = b"<R><L><v>1</v><v>2</v></L><M><v>3</v></M></R>"
    with make_server() as server:
        wid = _create(server, "W1")
        cid = _config(server, wid)
        item = _put(server, wid, "repeat.xml", xml, "application/xml")
        status, _h, body = _preview(server, wid, cid, item["version_id"])
        assert status == 200, body
        assert body["counts"]["total"] == 3
        duplicates = [row for row in body["items"] if row["directory"] == ["L"]]
        assert len(duplicates) == 2
        assert all(row["status"] == "ambiguous" for row in duplicates)
        assert all("duplicate_source_leaf" in row["reasons"] for row in duplicates)
        assert body["items"][2]["directory"] == ["M"]
        assert body["items"][2]["status"] == "new"


def test_3001_same_leaf_names_are_distinct_rows():
    seed_roles("extract-3001")
    payload = {"Channels": [{"Gain": index} for index in range(3001)]}
    with make_server() as server:
        wid = _create(server, "W1")
        cid = _config(server, wid)
        item = _put(server, wid, "many.json", json.dumps(payload).encode("utf-8"))
        vid = item["version_id"]
        before = _catalog_digest(wid)
        rows = []
        for offset in (0, 1000, 2000, 3000):
            status, _h, body = _preview(server, wid, cid, vid, offset=offset, limit=1000)
            assert status == 200, body
            assert body["counts"]["total"] == 3001
            assert body["total"] == 3001
            rows.extend(body["items"])
        assert len(rows) == 3001
        assert len({row["canonical_locator"] for row in rows}) == 3001
        assert len({(row["leaf_name"], tuple(row["directory"])) for row in rows}) == 3001
        assert {row["leaf_name"] for row in rows} == {"Gain"}
        assert all(row["status"] == "new" for row in rows)
        assert _catalog_digest(wid) == before


def test_new_version_only_new_fields_are_candidates():
    seed_roles("extract-version")
    with make_server() as server:
        wid = _create(server, "W1")
        cid = _config(server, wid)
        first = _put(server, wid, "versioned.json", b'{"A":1,"B":2}')
        status, _h, body = _preview(server, wid, cid, first["version_id"])
        assert status == 200, body
        for row in body["items"]:
            _param(
                server, wid, cid, row["leaf_name"], row["directory"], "",
                {"canonical_locator": row["canonical_locator"], "file_id": first["file_id"]},
            )
        old_vid = first["version_id"]
        _put(server, wid, "versioned.json", b'{"A":1,"B":2,"C":3}', overwrite=True)
        current = _selector(server, wid, "versioned.json")
        assert current["version_id"] != old_vid
        status, _h, updated = _preview(server, wid, cid, current["version_id"])
        assert status == 200, updated
        rows = {(row["leaf_name"], tuple(row["directory"])): row for row in updated["items"]}
        assert rows[("A", ())]["status"] == "existing"
        assert rows[("A", ())]["matched_by"] == "source_locator"
        assert rows[("B", ())]["status"] == "existing"
        assert rows[("C", ())]["status"] == "new"
        assert len(updated["items"]) == 3

        status, _h, old = _preview(server, wid, cid, old_vid)
        assert status == 200, old
        assert {row["leaf_name"] for row in old["items"]} == {"A", "B"}
        assert all(row["status"] == "existing" for row in old["items"])


def test_preview_rejects_unsafe_or_unsupported_sources_and_cross_workspace():
    seed_roles("extract-errors")
    with make_server() as server:
        wid = _create(server, "W1")
        other = _create(server, "W2")
        cid = _config(server, wid)
        ini = _put(server, wid, "legacy.ini", b"[s]\nk=v\n", "text/plain")
        status, _h, body = _preview(server, wid, cid, ini["version_id"])
        assert status == 400 and body["error"]["code"] == "INVALID_INPUT", body

        status, _h, raw = as_role(server, "admin").request(
            "PUT",
            "/api/v1/p/default/files/dup.json",
            body=b'{"A":1,"A":2}',
            headers={"X-Workspace-Id": wid, "Content-Type": "application/json"},
        )
        assert status == 422 and b"PARSE_FAILED" in raw, raw
        dtd = _put(server, wid, "dtd.xml", b"<!DOCTYPE R><R><V>1</V></R>", "application/xml")
        status, _h, body = _preview(server, wid, cid, dtd["version_id"])
        assert status == 422 and body["error"]["code"] == "PARSE_FAILED", body

        status, _h, body = as_role(server, "admin").json(
            "GET", "/api/v3/workspaces/%s/profiles/default/configs/%s/extract-preview" % (wid, cid)
        )
        assert status == 400 and body["error"]["code"] == "INVALID_INPUT", body

        foreign = _put(server, other, "foreign.json", b'{"Z":1}')
        status, _h, body = _preview(server, wid, cid, foreign["version_id"])
        assert status == 404, body

        status, _h, body = as_role(server, "member").json(
            "GET",
            "/api/v3/workspaces/%s/profiles/default/configs/%s/extract-preview?version_id=%s"
            % (wid, cid, ini["version_id"]),
        )
        assert status == 400, body
        good = _put(server, wid, "ok.json", b'{"Z":1}')
        status, _h, body = as_role(server, "member").json(
            "GET",
            "/api/v3/workspaces/%s/profiles/default/configs/%s/extract-preview?version_id=%s"
            % (wid, cid, good["version_id"]),
        )
        assert status == 200 and body["counts"]["new"] == 1, body
