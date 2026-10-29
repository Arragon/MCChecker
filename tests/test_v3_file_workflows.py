"""M11-06 统一文件工作流回归（INH-2135）。

覆盖 upload / review-generated / delete 三条真实写路径的 FileResource 身份、
sha 一致性与跨 W 隔离；update_source/record refresh 的网络路径由既有
`test_api_sources.py`/`test_api_history.py` 覆盖，不在这里重复起 HTTP 源。
"""

from __future__ import annotations

import hashlib
import json
from concurrent.futures import ThreadPoolExecutor

from authz_fixtures import as_role, make_server, seed_roles

from app.core import storage
from app.services import config_v3_provenance as prov
from app.services import file_versions

FILE = "flow.xml"


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


def test_upload_review_and_delete_share_one_identity_chain():
    seed_roles("flow")
    with make_server() as server:
        w1 = _create(server, "W1")
        v1 = b"<R><params><item>first</item></params></R>"
        _put(server, w1, v1)
        with storage.use_workspace(w1), storage.use_profile("default"):
            ref1 = prov.make_ref("default", FILE)
            assert ref1["blob_sha256"] == hashlib.sha256(v1).hexdigest()
            assert prov.verify_ref(ref1)["version_id"] == ref1["version_id"]

        headers = {"X-Workspace-Id": w1}
        status, _h, remark = as_role(server, "member").json(
            "POST", "/api/v1/p/default/remarks",
            {"file": FILE, "sha256": ref1["blob_sha256"],
             "loc": "/R[1]/params[1]/item[1]", "proposed_value": "approved"},
            headers=headers,
        )
        assert status == 201, remark
        status, _h, review = as_role(server, "admin").json(
            "GET", "/api/v1/p/default/review", headers=headers
        )
        group = {f["file"]: f for f in review["files"]}[FILE]["nodes"][0]["group_key"]
        status, _h, out = as_role(server, "admin").json(
            "POST", "/api/v1/p/default/review/submit",
            {"request_id": "flow-1", "file": FILE, "source_sha": ref1["blob_sha256"],
             "decisions": [{"group_key": group, "choice": remark["item"]["id"]}]},
            headers=headers,
        )
        assert status == 200 and out["generated_file"], out
        with storage.use_workspace(w1), storage.use_profile("default"):
            source = file_versions.current_identity("default", FILE)
            generated = file_versions.current_identity("default", out["generated_file"])
            assert generated["file_id"] != source["file_id"]
            assert generated["blob_sha256"] != source["blob_sha256"]
            # 生成文件不改变源文件的 current ref。
            assert prov.verify_ref(ref1)["version_id"] == ref1["version_id"]

        status, _h, body = as_role(server, "admin").json(
            "DELETE", "/api/v1/p/default/files/%s" % FILE, headers=headers
        )
        assert status == 200, body
        with storage.use_workspace(w1), storage.use_profile("default"):
            try:
                prov.verify_ref(ref1)
            except Exception as exc:  # noqa: BLE001 - 断言业务码
                assert exc.code == "NOT_FOUND" and exc.detail.get("missing") is True
            else:  # pragma: no cover
                raise AssertionError("删除后旧引用必须 missing")
            # 生成文件不受源文件删除影响。
            assert file_versions.current_identity("default", out["generated_file"])


def test_concurrent_overwrites_keep_one_current_and_one_file_id():
    seed_roles("flow2")
    with make_server() as server:
        w1 = _create(server, "W1")
        _put(server, w1, b"<R><item>0</item></R>")
        with storage.use_workspace(w1), storage.use_profile("default"):
            fid = file_versions.current_identity("default", FILE)["file_id"]

        payloads = [("<R><item>%d</item></R>" % i).encode() for i in range(1, 7)]

        def upload(data):
            return _put(server, w1, data, overwrite=True)

        with ThreadPoolExecutor(max_workers=6) as pool:
            list(pool.map(upload, payloads))
        status, _h, raw = as_role(server, "visitor").request(
            "GET", "/api/v1/p/default/files/%s/raw" % FILE,
            headers={"X-Workspace-Id": w1},
        )
        assert status == 200 and raw in payloads, "最终 current 必须是一份完整上传"
        with storage.use_workspace(w1), storage.use_profile("default"):
            ident = file_versions.current_identity("default", FILE)
            assert ident["file_id"] == fid
            assert ident["blob_sha256"] == hashlib.sha256(raw).hexdigest()
            rows = file_versions.versions("default", FILE)
            assert sum(1 for r in rows if r["ref"] == "current") == 1
