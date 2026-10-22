"""配置目录 API：版本、批次、族、逻辑文件、快照（M6-01 / M6-02 的 HTTP 面）。

验证三件事：

* 门禁与权限在**每一个**新端点上都生效（off→503、readonly→写 503、成员→403）；
* 快照上传走原文字节（`Content-Type: application/octet-stream` + `X-Filename`），
  下载回来的字节与哈希和上传时一致；
* 补发批次后，旧默认批次的下载内容仍然不变。
"""

from __future__ import annotations

import hashlib
import json

import pytest
from authz_fixtures import as_role, make_server, seed_roles

from app.services import config_gate, config_revision

XML_A = b'<MachineConfig xmlns="urn:mc:cfg:1" model="A"><Retry>3</Retry></MachineConfig>'
XML_B = b'<MachineConfig xmlns="urn:mc:cfg:1" model="A"><Retry>4</Retry></MachineConfig>'

OCTET = {
    "Content-Type": "application/octet-stream",
    "X-Filename": "axis_param.xml",
}


@pytest.fixture(autouse=True)
def clean_gate(monkeypatch):
    monkeypatch.delenv(config_gate.ENV_KEY, raising=False)
    config_revision.reset()
    yield
    config_revision.reset()


def _enable(monkeypatch, mode="on"):
    monkeypatch.setenv(config_gate.ENV_KEY, mode)


def _seed_catalog(caller):
    """建一个版本 + 一个族 + 一个 XML 逻辑文件，返回三者。"""
    status, _h, release = caller.json(
        "POST", "/api/v1/config/releases", body={"version_label": "1.1.0", "build": "20260901"}
    )
    assert status == 201, release
    status, _h, family = caller.json(
        "POST", "/api/v1/config/families", body={"name": "轴参数", "code": "AXIS_PARAM"}
    )
    assert status == 201, family
    status, _h, logical = caller.json(
        "POST",
        "/api/v1/config/families/%s/files" % family["item"]["id"],
        body={"role": "xml_export", "display_name": "轴参数.xml"},
    )
    assert status == 201, logical
    return release["item"], family["item"], logical["item"]


def _upload(caller, release_id, logical_file_id, data=XML_A, query="source=archive&pid=A&archive_name=20260901.xml"):
    return caller.json(
        "POST",
        "/api/v1/config/snapshots?release_id=%s&logical_file_id=%s&%s"
        % (release_id, logical_file_id, query),
        body=data,
        headers=OCTET,
    )


# -- 门禁与权限 -------------------------------------------------------------


def test_read_endpoints_are_503_when_off() -> None:
    with make_server() as server:
        seed_roles("p1")
        caller = as_role(server, "admin")
        for path in (
            "/api/v1/config/releases",
            "/api/v1/config/families",
            "/api/v1/config/snapshots",
            "/api/v1/config/variants",
        ):
            status, _h, body = caller.json("GET", path)
            assert status == 503, path
            assert body["error"]["code"] == "CONFIG_DISABLED"


def test_readonly_allows_reads_but_blocks_writes(monkeypatch) -> None:
    _enable(monkeypatch, "readonly")
    with make_server() as server:
        seed_roles("p1")
        caller = as_role(server, "admin")
        status, _h, body = caller.json("GET", "/api/v1/config/releases")
        assert status == 200 and body["items"] == []
        write_status, _h2, write_body = caller.json(
            "POST", "/api/v1/config/releases", body={"version_label": "1.0.0"}
        )
        assert write_status == 503
        assert write_body["error"]["detail"]["mode"] == "readonly"


def test_member_can_read_but_not_write(monkeypatch) -> None:
    _enable(monkeypatch)
    with make_server() as server:
        seed_roles("p1")
        caller = as_role(server, "member")
        status, _h, body = caller.json("GET", "/api/v1/config/families")
        assert status == 200 and body["items"] == []
        write_status, _h2, write_body = caller.json(
            "POST", "/api/v1/config/families", body={"name": "轴参数", "code": "AXIS_PARAM"}
        )
        assert write_status == 403
        assert write_body["error"]["detail"]["action"] == "config_registry_write"


def test_write_requires_csrf_header(monkeypatch) -> None:
    _enable(monkeypatch)
    with make_server() as server:
        seed_roles("p1")
        caller = as_role(server, "admin")
        status, _h, body = caller.json(
            "POST",
            "/api/v1/config/families",
            body={"name": "轴参数", "code": "AXIS_PARAM"},
            with_csrf=False,
        )
        assert status == 403
        assert body["error"]["code"] == "FORBIDDEN"


# -- 版本与批次 -------------------------------------------------------------


def test_release_lifecycle_over_http(monkeypatch) -> None:
    _enable(monkeypatch)
    with make_server() as server:
        seed_roles("p1")
        caller = as_role(server, "admin")
        release, family, logical = _seed_catalog(caller)

        status, _h, listed = caller.json("GET", "/api/v1/config/releases")
        assert status == 200
        assert [item["id"] for item in listed["items"]] == [release["id"]]

        status, _h, detail = caller.json("GET", "/api/v1/config/releases/%s" % release["id"])
        assert status == 200
        assert detail["release"]["version_label"] == "1.1.0"
        assert detail["default_batch"] is None
        assert detail["batch_count"] == 0

        missing, _h2, missing_body = caller.json("GET", "/api/v1/config/releases/sr_999999")
        assert missing == 404
        assert missing_body["error"]["code"] == "NOT_FOUND"

        conflict, _h3, conflict_body = caller.json(
            "POST", "/api/v1/config/releases", body={"version_label": "1.1.0", "build": "20260901"}
        )
        assert conflict == 409
        assert conflict_body["error"]["code"] == "DEFINITION_CONFLICT"
        assert conflict_body["error"]["detail"]["release_id"] == release["id"]


def test_snapshot_upload_and_raw_download_roundtrip(monkeypatch) -> None:
    _enable(monkeypatch)
    with make_server() as server:
        seed_roles("p1")
        caller = as_role(server, "admin")
        release, family, logical = _seed_catalog(caller)

        status, _h, body = _upload(caller, release["id"], logical["id"])
        assert status == 201, body
        snapshot = body["item"]
        assert snapshot["status"] == "confirmed"
        assert snapshot["archive_ref"] == {
            "pid": "A",
            "filename": "axis_param.xml",
            "archive_name": "20260901.xml",
        }
        assert snapshot["blob_sha256"] == hashlib.sha256(XML_A).hexdigest()

        status, headers, raw = caller.request(
            "GET", "/api/v1/config/snapshots/%s/raw" % snapshot["id"]
        )
        assert status == 200
        assert raw == XML_A
        assert headers["X-Content-Sha256"] == snapshot["blob_sha256"]
        assert "axis_param.xml" in headers["Content-Disposition"]

        visitor = as_role(server, "visitor")
        v_status, _vh, v_body = visitor.json(
            "GET", "/api/v1/config/snapshots?release_id=%s" % release["id"]
        )
        assert v_status == 200
        assert len(v_body["items"]) == 1


def test_snapshot_upload_dedupes_and_marks_missing_archive(monkeypatch) -> None:
    _enable(monkeypatch)
    with make_server() as server:
        seed_roles("p1")
        caller = as_role(server, "admin")
        release, family, logical = _seed_catalog(caller)

        first_status, _h, first = _upload(caller, release["id"], logical["id"])
        again_status, _h2, again = _upload(caller, release["id"], logical["id"])
        assert first_status == again_status == 201
        assert first["item"]["id"] == again["item"]["id"], "同内容不生成重复修订"

        unconfirmed_status, _h3, unconfirmed = _upload(
            caller, release["id"], logical["id"], data=XML_B, query="source=archive"
        )
        assert unconfirmed_status == 201
        assert unconfirmed["item"]["status"] == "unconfirmed"


def test_snapshot_upload_requires_release_and_filename(monkeypatch) -> None:
    _enable(monkeypatch)
    with make_server() as server:
        seed_roles("p1")
        caller = as_role(server, "admin")
        release, family, logical = _seed_catalog(caller)

        status, _h, body = caller.json(
            "POST",
            "/api/v1/config/snapshots?logical_file_id=%s" % logical["id"],
            body=XML_A,
            headers=OCTET,
        )
        assert status == 400
        assert "release_id" in body["error"]["message"]

        no_name = dict(OCTET)
        no_name.pop("X-Filename")
        status, _h2, body2 = caller.json(
            "POST",
            "/api/v1/config/snapshots?release_id=%s&logical_file_id=%s"
            % (release["id"], logical["id"]),
            body=XML_A,
            headers=no_name,
        )
        assert status == 400
        assert "文件名" in body2["error"]["message"]


def test_reissue_keeps_old_default_download_unchanged(monkeypatch) -> None:
    _enable(monkeypatch)
    with make_server() as server:
        seed_roles("p1")
        caller = as_role(server, "admin")
        release, family, logical = _seed_catalog(caller)
        _s, _h, first = _upload(caller, release["id"], logical["id"])
        first_id = first["item"]["id"]

        status, _h2, batch = caller.json(
            "POST",
            "/api/v1/config/releases/%s/default" % release["id"],
            body={"snapshot_ids": [first_id], "note": "首轮"},
        )
        assert status == 201, batch
        assert batch["item"]["supersedes"] is None

        _s2, _h3, second = _upload(
            caller, release["id"], logical["id"], data=XML_B, query="source=upload"
        )
        status, _h4, reissued = caller.json(
            "POST",
            "/api/v1/config/releases/%s/default" % release["id"],
            body={"snapshot_ids": [second["item"]["id"]], "note": "补发"},
        )
        assert status == 201
        assert reissued["item"]["supersedes"] == batch["item"]["id"]
        assert reissued["item"]["id"] != batch["item"]["id"]

        detail_status, _h5, detail = caller.json(
            "GET", "/api/v1/config/releases/%s" % release["id"]
        )
        assert detail_status == 200
        assert detail["default_batch"]["id"] == reissued["item"]["id"]
        assert detail["batch_count"] == 2

        # 旧默认批次的快照下载内容与哈希都没变
        raw_status, raw_headers, raw = caller.request(
            "GET", "/api/v1/config/snapshots/%s/raw" % first_id
        )
        assert raw_status == 200
        assert raw == XML_A
        assert raw_headers["X-Content-Sha256"] == hashlib.sha256(XML_A).hexdigest()
        assert raw_headers["ETag"] == '"%s"' % hashlib.sha256(XML_A).hexdigest()


def test_batch_rejects_empty_or_foreign_snapshot(monkeypatch) -> None:
    _enable(monkeypatch)
    with make_server() as server:
        seed_roles("p1")
        caller = as_role(server, "admin")
        release, family, logical = _seed_catalog(caller)

        status, _h, body = caller.json(
            "POST",
            "/api/v1/config/releases/%s/default" % release["id"],
            body={"snapshot_ids": []},
        )
        assert status == 400
        assert body["error"]["code"] == "INVALID_INPUT"

        _s, _h2, _b, = _upload(caller, release["id"], logical["id"])
        other_status, _h3, other = caller.json(
            "POST", "/api/v1/config/releases", body={"version_label": "1.2.0"}
        )
        assert other_status == 201
        foreign, _h4, foreign_body = caller.json(
            "POST",
            "/api/v1/config/releases/%s/default" % other["item"]["id"],
            body={"snapshot_ids": [body.get("snapshot_id") or "fs_000001"]},
        )
        assert foreign in (400, 404)


# -- 族、逻辑文件与变体 -----------------------------------------------------


def test_logical_file_rename_and_retire_over_http(monkeypatch) -> None:
    _enable(monkeypatch)
    with make_server() as server:
        seed_roles("p1")
        caller = as_role(server, "admin")
        release, family, logical = _seed_catalog(caller)
        _upload(caller, release["id"], logical["id"])

        status, _h, renamed = caller.json(
            "PATCH",
            "/api/v1/config/files/%s" % logical["id"],
            body={"display_name": "轴参数表.xml", "reason": "对齐命名"},
        )
        assert status == 200
        assert renamed["item"]["display_name"] == "轴参数表.xml"
        open_aliases = [a["name"] for a in renamed["item"]["aliases"] if a["until"] is None]
        assert open_aliases == ["轴参数表.xml"]

        status, _h2, retired = caller.json(
            "POST", "/api/v1/config/files/%s/retire" % logical["id"], body={"reason": "合并"}
        )
        assert status == 200
        assert retired["item"]["status"] == "retired"

        listed_status, _h3, listed = caller.json(
            "GET", "/api/v1/config/families/%s/files" % family["id"]
        )
        assert listed_status == 200
        assert [item["id"] for item in listed["items"]] == [logical["id"]]
        # 历史快照仍然按稳定 ID 可查
        snap_status, _h4, snaps = caller.json(
            "GET", "/api/v1/config/snapshots?logical_file_id=%s" % logical["id"]
        )
        assert snap_status == 200 and len(snaps["items"]) == 1


def test_representation_name_conflict_over_http(monkeypatch) -> None:
    _enable(monkeypatch)
    with make_server() as server:
        seed_roles("p1")
        caller = as_role(server, "admin")
        _release, family, _logical = _seed_catalog(caller)
        status, _h, body = caller.json(
            "POST",
            "/api/v1/config/families/%s/files" % family["id"],
            body={"role": "field_json", "display_name": "轴参数.xml"},
        )
        assert status == 409
        assert body["error"]["code"] == "DEFINITION_CONFLICT"


def test_variant_endpoint_registers_and_reuses(monkeypatch) -> None:
    _enable(monkeypatch)
    with make_server() as server:
        seed_roles("p1")
        caller = as_role(server, "admin")
        release, family, logical = _seed_catalog(caller)
        _s, _h, snapshot = _upload(caller, release["id"], logical["id"])
        payload = {
            "family_id": family["id"],
            "release_id": release["id"],
            "template_snapshot_id": snapshot["item"]["id"],
        }
        first_status, _h2, first = caller.json("POST", "/api/v1/config/variants", body=payload)
        assert first_status == 201, first
        second_status, _h3, second = caller.json("POST", "/api/v1/config/variants", body=payload)
        assert second_status == 201
        assert first["item"]["id"] == second["item"]["id"]

        listed_status, _h4, listed = caller.json(
            "GET", "/api/v1/config/variants?release_id=%s" % release["id"]
        )
        assert listed_status == 200
        assert [item["id"] for item in listed["items"]] == [first["item"]["id"]]
        assert len(first["item"]["structure_signature"]) == 64


def test_idempotency_key_replays_same_release(monkeypatch) -> None:
    _enable(monkeypatch)
    with make_server() as server:
        seed_roles("p1")
        caller = as_role(server, "admin")
        headers = {"Idempotency-Key": "rel-1"}
        first_status, _h, first = caller.json(
            "POST",
            "/api/v1/config/releases",
            body={"version_label": "2.0.0", "build": "1"},
            headers=headers,
        )
        second_status, _h2, second = caller.json(
            "POST",
            "/api/v1/config/releases",
            body={"version_label": "2.0.0", "build": "1"},
            headers=headers,
        )
        assert first_status == 201 and second_status == 201
        assert first["item"]["id"] == second["item"]["id"]

        mismatch_status, _h3, mismatch = caller.json(
            "POST",
            "/api/v1/config/releases",
            body={"version_label": "2.0.1", "build": "1"},
            headers=headers,
        )
        assert mismatch_status == 409
        assert mismatch["error"]["code"] == "IDEMPOTENCY_MISMATCH"


def test_unknown_family_is_404(monkeypatch) -> None:
    _enable(monkeypatch)
    with make_server() as server:
        seed_roles("p1")
        caller = as_role(server, "admin")
        status, _h, body = caller.json("POST", "/api/v1/config/releases", body={"version_label": "1.0.0"})
        assert status == 201
        files_status, _h2, files_body = caller.json(
            "GET", "/api/v1/config/families/cf_999999/files"
        )
        assert files_status == 200
        assert files_body["items"] == []
        create_status, _h3, create_body = caller.json(
            "POST",
            "/api/v1/config/families/cf_999999/files",
            body={"role": "xml_export", "display_name": "x.xml"},
        )
        assert create_status == 404
        assert create_body["error"]["code"] == "NOT_FOUND"
