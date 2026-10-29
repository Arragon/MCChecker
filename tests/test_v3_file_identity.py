"""M11-01 FileResource/FileVersion 不可变身份（INH-2130）。"""

from __future__ import annotations

import hashlib
import json
import os

from authz_fixtures import as_role, make_server, seed_roles

from app.core import storage
from app.services import file_versions

FILE = "identity.xml"


def _sha(data):
    return hashlib.sha256(data).hexdigest()


def _create(server, name):
    status, _h, body = as_role(server, "admin").json(
        "POST", "/api/v3/workspaces", {"name": name}
    )
    assert status == 201, body
    return body["workspace"]["id"]


def _put(server, wid, data, overwrite=False):
    url = "/api/v1/p/default/files/%s%s" % (FILE, "?overwrite=1" if overwrite else "")
    status, _h, raw = as_role(server, "admin").request(
        "PUT", url, body=data,
        headers={"X-Workspace-Id": wid, "Content-Type": "application/xml"},
    )
    assert status in (200, 201), raw
    return json.loads(raw.decode("utf-8"))


def _get(server, wid, path):
    status, _h, body = as_role(server, "visitor").json(
        "GET", "/api/v1/p/default" + path, headers={"X-Workspace-Id": wid}
    )
    return status, body


def test_identity_is_stable_and_versions_are_immutable():
    seed_roles("identity")
    with make_server() as server:
        w1, w2 = _create(server, "W1"), _create(server, "W2")
        v1 = b"<R><item>first</item></R>"
        v2 = b"<R><item>second</item></R>"

        assert _put(server, w1, v1)["archived"] is None
        with storage.use_workspace(w1):
            data_dir = storage.get_profile_data_dir("default")
            listing_before = os.listdir(data_dir)
        with storage.use_workspace(w1), storage.use_profile("default"):
            before = file_versions.current_identity("default", FILE)
            assert before["blob_sha256"] == _sha(v1)
        with storage.use_workspace(w1):
            assert os.listdir(data_dir) == listing_before, "读身份不得写数据目录"

        saved = _put(server, w1, v2, overwrite=True)
        archived = saved["archived"]
        assert archived
        with storage.use_workspace(w1), storage.use_profile("default"):
            after = file_versions.current_identity("default", FILE)
            old = file_versions.identity("default", FILE, "archive:" + archived)
            found = file_versions.find_version_by_sha("default", FILE, _sha(v1))
        assert after["file_id"] == before["file_id"], "同名覆盖 file_id 必须稳定"
        assert after["version_id"] != before["version_id"], "覆盖后 version_id 必须变化"
        assert after["blob_sha256"] == _sha(v2)
        assert old["blob_sha256"] == _sha(v1)
        assert old["version_id"] == before["version_id"], "旧 current 的版本身份不变"
        assert found and found["ref"] == "archive:" + archived

        _put(server, w2, b"<R><item>other</item></R>")
        with storage.use_workspace(w2), storage.use_profile("default"):
            other = file_versions.current_identity("default", FILE)
        assert other["file_id"] != before["file_id"], "同 W 不同 P/W 必须有不同 file_id"

        # 旧文件 API 仍可用：current/archive/raw/document/records。
        assert _get(server, w1, "/files")[0] == 200
        assert _get(server, w1, "/files/%s/document" % FILE)[0] == 200
        assert _get(server, w1, "/files/%s/archives" % FILE)[0] == 200
        assert _get(server, w1, "/files/%s/records" % FILE)[0] == 200
        status, _h, raw = as_role(server, "visitor").request(
            "GET",
            "/api/v1/p/default/files/%s/raw?version=archive:%s" % (FILE, archived),
            headers={"X-Workspace-Id": w1},
        )
        assert status == 200 and raw == v1

        with storage.use_workspace(w1), storage.use_profile("default"):
            resources = file_versions.list_resources("default")
            rows = file_versions.versions("default", FILE)
        assert [r["file_id"] for r in resources] == [before["file_id"]]
        assert {r["ref"] for r in rows} == {"current", "archive:" + archived}


def test_remarks_generated_file_is_its_own_resource():
    seed_roles("identity2")
    with make_server() as server:
        w1 = _create(server, "W1")
        v1 = b"<R><params><item>first</item></params></R>"
        _put(server, w1, v1)
        headers = {"X-Workspace-Id": w1}
        status, _h, remark = as_role(server, "member").json(
            "POST", "/api/v1/p/default/remarks",
            {"file": FILE, "sha256": _sha(v1), "loc": "/R[1]/params[1]/item[1]",
             "proposed_value": "approved"},
            headers=headers,
        )
        assert status == 201, remark
        status, _h, review = as_role(server, "admin").json(
            "GET", "/api/v1/p/default/review", headers=headers
        )
        group = {f["file"]: f for f in review["files"]}[FILE]["nodes"][0]["group_key"]
        status, _h, out = as_role(server, "admin").json(
            "POST", "/api/v1/p/default/review/submit",
            {"request_id": "identity-1", "file": FILE,
             "source_sha": _sha(v1),
             "decisions": [{"group_key": group, "choice": remark["item"]["id"]}]},
            headers=headers,
        )
        assert status == 200 and out["generated_file"], out
        with storage.use_workspace(w1), storage.use_profile("default"):
            source = file_versions.current_identity("default", FILE)
            generated = file_versions.current_identity("default", out["generated_file"])
        assert generated["file_id"] != source["file_id"], "生成文件是独立 FileResource"
        assert generated["blob_sha256"] != source["blob_sha256"]
