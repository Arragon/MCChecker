"""M11-04 精确来源引用与删除 tombstone（INH-2133）。"""

from __future__ import annotations

import hashlib
import json

import pytest

from authz_fixtures import as_role, make_server, seed_roles

from app.core import storage
from app.core.errors import NOT_FOUND, SOURCE_CHANGED, AppError
from app.services import config_v3_provenance as prov
from app.services import file_versions

FILE = "prov.xml"


def _create(server, name):
    status, _h, body = as_role(server, "admin").json(
        "POST", "/api/v3/workspaces", {"name": name}
    )
    assert status == 201, body
    return body["workspace"]["id"]


def _put(server, wid, data):
    status, _h, raw = as_role(server, "admin").request(
        "PUT", "/api/v1/p/default/files/%s?overwrite=1" % FILE, body=data,
        headers={"X-Workspace-Id": wid, "Content-Type": "application/xml"},
    )
    assert status in (200, 201), raw
    return json.loads(raw.decode("utf-8"))


def _make(wid, version="current"):
    with storage.use_workspace(wid), storage.use_profile("default"):
        return prov.make_ref("default", FILE, version)


def test_archive_ref_survives_three_overwrites_and_delete_blocks_reuse():
    seed_roles("prov")
    with make_server() as server:
        w1, w2 = _create(server, "W1"), _create(server, "W2")
        v1 = b"<R><item>one</item></R>"
        _put(server, w1, v1)
        ref_v1 = _make(w1)
        first = _put(server, w1, b"<R><item>two</item></R>")
        archived = first["archived"]
        assert archived
        ref_archive = _make(w1, "archive:" + archived)
        _put(server, w1, b"<R><item>three</item></R>")
        _put(server, w1, b"<R><item>four</item></R>")

        current = _make(w1)
        assert prov.verify_ref(current)["version_id"] == current["version_id"]
        with pytest.raises(AppError) as exc:
            prov.verify_ref(ref_v1)
        assert exc.value.code == SOURCE_CHANGED
        old = prov.verify_ref(ref_archive)
        assert old["blob_sha256"] == hashlib.sha256(v1).hexdigest()

        status, _h, body = as_role(server, "admin").json(
            "DELETE", "/api/v1/p/default/files/%s" % FILE,
            headers={"X-Workspace-Id": w1},
        )
        assert status == 200, body
        with pytest.raises(AppError) as exc:
            prov.verify_ref(current)
        assert exc.value.code == NOT_FOUND and exc.value.detail.get("missing") is True

        _put(server, w1, b"<R><item>five</item></R>")
        with storage.use_workspace(w1), storage.use_profile("default"):
            new_id = file_versions.current_identity("default", FILE)["file_id"]
        assert new_id != current["file_id"], "删除后同名上传必须得到新 FileResource"
        with pytest.raises(AppError) as exc:
            prov.verify_ref(current)
        assert exc.value.code == NOT_FOUND, "旧引用不得被同名新文件复活"

        # 另一个 W 的同名上传与 W1 的旧引用无关。
        _put(server, w2, b"<R><item>other</item></R>")
        with pytest.raises(AppError) as exc:
            prov.verify_ref(current)
        assert exc.value.code == NOT_FOUND


def test_make_ref_current_records_expected_version_and_history_does_not():
    seed_roles("prov2")
    with make_server() as server:
        w1 = _create(server, "W1")
        _put(server, w1, b"<R><item>a</item></R>")
        cur = _make(w1)
        assert cur["expected_current_version_id"] == cur["version_id"]
        assert cur["workspace_id"] == w1 and cur["profile_id"] == "default"
        assert set(cur) >= {"file_id", "version_id", "blob_sha256", "ref"}

        _put(server, w1, b"<R><item>b</item></R>")
        with pytest.raises(AppError) as exc:
            prov.verify_ref(cur)
        assert exc.value.code == SOURCE_CHANGED
