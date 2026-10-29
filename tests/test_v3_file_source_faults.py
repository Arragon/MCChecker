"""M11-06 FileResource 故障注入（INH-2135）。

坏字节/写入失败后：current 文件不变、旧引用仍可验证、没有半份 current 指针，
也不产生不可达的“业务成功”事件。
"""

from __future__ import annotations

import hashlib
import json

import pytest

from authz_fixtures import as_role, make_server, seed_roles

from app.core import storage
from app.core.errors import AppError
from app.services import config_v3_provenance as prov
from app.services import documents, file_versions, recovery, review

FILE = "fault.xml"


def _create(server, name):
    status, _h, body = as_role(server, "admin").json(
        "POST", "/api/v3/workspaces", {"name": name}
    )
    assert status == 201, body
    return body["workspace"]["id"]


def _put(server, wid, data, overwrite=False):
    status, _h, raw = as_role(server, "admin").request(
        "PUT", "/api/v1/p/default/files/%s%s" % (FILE, "?overwrite=1" if overwrite else ""),
        body=data, headers={"X-Workspace-Id": wid, "Content-Type": "application/xml"},
    )
    return status, raw


def test_bad_bytes_do_not_replace_current_or_create_tombstone():
    seed_roles("fault")
    with make_server() as server:
        w1 = _create(server, "W1")
        good = b"<R><item>good</item></R>"
        assert _put(server, w1, good)[0] == 201
        with storage.use_workspace(w1), storage.use_profile("default"):
            before = file_versions.current_identity("default", FILE)

        status, raw = _put(server, w1, b"<R><item>broken</R>", overwrite=True)
        assert status == 422, raw
        status, _h, current = as_role(server, "visitor").request(
            "GET", "/api/v1/p/default/files/%s/raw" % FILE, headers={"X-Workspace-Id": w1}
        )
        assert status == 200 and current == good
        with storage.use_workspace(w1), storage.use_profile("default"):
            after = file_versions.current_identity("default", FILE)
            assert after["version_id"] == before["version_id"]
            assert not file_versions.is_missing("default", before["file_id"], before["version_id"])


def test_delete_failure_keeps_current_ref_verifiable(monkeypatch):
    seed_roles("fault2")
    with make_server() as server:
        w1 = _create(server, "W1")
        assert _put(server, w1, b"<R><item>keep</item></R>")[0] == 201
        with storage.use_workspace(w1), storage.use_profile("default"):
            ref = prov.make_ref("default", FILE)

        def boom(*_a, **_k):
            raise RuntimeError("move failed")

        monkeypatch.setattr(recovery, "move_in", boom)
        with storage.use_workspace(w1), storage.use_profile("default"):
            with pytest.raises(RuntimeError):
                recovery.delete_file("default", FILE)
            assert prov.verify_ref(ref)["version_id"] == ref["version_id"]
            assert not file_versions.is_missing("default", ref["file_id"], ref["version_id"])


def test_review_generated_write_failure_leaves_source_and_pending(monkeypatch):
    seed_roles("fault3")
    with make_server() as server:
        w1 = _create(server, "W1")
        v1 = b"<R><params><item>first</item></params></R>"
        assert _put(server, w1, v1)[0] == 201
        headers = {"X-Workspace-Id": w1}
        status, _h, remark = as_role(server, "member").json(
            "POST", "/api/v1/p/default/remarks",
            {"file": FILE, "sha256": hashlib.sha256(v1).hexdigest(),
             "loc": "/R[1]/params[1]/item[1]", "proposed_value": "approved"},
            headers=headers,
        )
        assert status == 201, remark
        status, _h, review_data = as_role(server, "admin").json(
            "GET", "/api/v1/p/default/review", headers=headers
        )
        group = {f["file"]: f for f in review_data["files"]}[FILE]["nodes"][0]["group_key"]

        def boom(*_a, **_k):
            raise AppError("STORAGE_FAILURE", "磁盘写入失败")

        monkeypatch.setattr(storage, "save_review_generated_file", boom)
        status, _h, raw = as_role(server, "admin").request(
            "POST", "/api/v1/p/default/review/submit",
            body=json.dumps(
                {"request_id": "fault-1", "file": FILE,
                 "source_sha": hashlib.sha256(v1).hexdigest(),
                 "decisions": [{"group_key": group, "choice": remark["item"]["id"]}]}
            ).encode(),
            headers=dict(headers, **{"Content-Type": "application/json"}),
        )
        assert status >= 500, raw
        with storage.use_workspace(w1), storage.use_profile("default"):
            names = [f["name"] for f in documents.list_files("default")]
            assert names == [FILE], "失败后不得留下生成文件"
            assert review.list_items("default", FILE, "pending"), "建议必须仍是 pending"
