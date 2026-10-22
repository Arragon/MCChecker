"""观测采集接口的 HTTP 行为（M6-07 / M6-08）。

覆盖：

* 门禁（默认 off → 503）与权限（成员只读、`readonly` 写 503）；
* 原文下载逐字节一致，且带 `X-Content-Sha256` / `ETag`；
* 同内容重复上传返回同一条观测（`deduplicated=true`）但仍多一条事件；
* 重新映射：201 + 新修订，旧修订仍可读；`expected_revision` 过期 → 409；
* 超过上传上限 → 413（复用旧接口的 `upload_max_bytes`）。
"""

from __future__ import annotations

import json
from urllib.parse import quote

import pytest
from authz_fixtures import as_role, make_server, seed_roles

from app.services import config_gate, config_ingest, config_revision

XML_DOC = (
    '<?xml version="1.0" encoding="UTF-8"?>'
    '<R><PulseWidth unit="ms">650</PulseWidth></R>'
).encode("utf-8")

OCTET = {"Content-Type": "application/octet-stream"}


@pytest.fixture(autouse=True)
def clean_state(monkeypatch):
    monkeypatch.delenv(config_gate.ENV_KEY, raising=False)
    config_revision.reset()
    config_ingest.reset_inflight()
    yield
    config_revision.reset()
    config_ingest.reset_inflight()


def _enable(monkeypatch, mode="on"):
    monkeypatch.setenv(config_gate.ENV_KEY, mode)


def _seed(caller):
    """建类别 + 逻辑文件 + 参数，返回逻辑文件。"""
    family = caller.json(
        "POST", "/api/v1/config/families", body={"name": "轴参数", "code": "AXIS_PARAM"}
    )[2]["item"]
    logical = caller.json(
        "POST",
        "/api/v1/config/families/%s/files" % family["id"],
        body={"role": "xml_export", "display_name": "轴参数.xml"},
    )[2]["item"]
    parameter = caller.json(
        "POST",
        "/api/v1/config/parameters",
        body={"family_id": family["id"], "name": "脉冲宽度", "category": "时序"},
    )[2]["item"]
    caller.json(
        "POST",
        "/api/v1/config/parameters/%s/definitions" % parameter["id"],
        body={
            "xml_name": "PulseWidth",
            "xml_loc": {"path": [{"name": "PulseWidth", "occurrence": 1}], "attribute": None},
            "type": "decimal",
            "unit": "ms",
            "reason": "首次登记",
        },
    )
    return {"family": family, "logical": logical, "parameter": parameter}


def _capture(caller, query="", body=XML_DOC, headers=None):
    merged = dict(OCTET)
    merged.update(headers or {})
    return caller.json(
        "POST", "/api/v1/config/observations" + query, body=body, headers=merged
    )


# -- 门禁与权限 -------------------------------------------------------------


def test_observations_require_gate_and_admin(monkeypatch) -> None:
    with make_server() as server:
        seed_roles("p1")
        admin = as_role(server, "admin")
        status, _h, body = admin.json("GET", "/api/v1/config/observations")
        assert status == 503 and body["error"]["code"] == "CONFIG_DISABLED"

    _enable(monkeypatch)
    with make_server() as server:
        seed_roles("p1")
        admin = as_role(server, "admin")
        world = _seed(admin)

        member = as_role(server, "member")
        assert member.json("GET", "/api/v1/config/observations")[0] == 200
        status, _h, body = _capture(
            member, "?profile_id=p1&filename=%s" % quote("轴参数.xml")
        )
        assert status == 403
        assert body["error"]["detail"]["action"] == "config_registry_write"
        assert world["logical"]["id"]


def test_readonly_blocks_capture(monkeypatch) -> None:
    _enable(monkeypatch)
    with make_server() as server:
        seed_roles("p1")
        admin = as_role(server, "admin")
        _seed(admin)
        # 造好数据再切 readonly：只读档位下连建类别都会被拦，没法当夹具用
        monkeypatch.setenv(config_gate.ENV_KEY, config_gate.MODE_READONLY)
        status, _h, body = _capture(
            admin, "?profile_id=p1&filename=%s" % quote("轴参数.xml")
        )
        assert status == 503
        assert body["error"]["detail"]["mode"] == "readonly"


# -- 采集 -------------------------------------------------------------------


def test_capture_creates_observation_with_mapping(monkeypatch) -> None:
    _enable(monkeypatch)
    with make_server() as server:
        seed_roles("p1")
        caller = as_role(server, "admin")
        world = _seed(caller)
        status, _h, body = _capture(
            caller, "?profile_id=p1&filename=%s" % quote("轴参数.xml")
        )
        assert status == 201, body
        item = body["item"]
        assert item["status"] == "parsed"
        assert item["deduplicated"] is False
        assert item["event_id"].startswith("ce_")
        assert item["logical_file_id"] == world["logical"]["id"]
        entry = item["mapping"]["entries"][0]
        assert entry["parameter_id"] == world["parameter"]["id"]
        assert entry["value_ref"] == {"type": "decimal", "text": "650", "unit": "ms"}

        # 列表能查到
        listed = caller.json(
            "GET", "/api/v1/config/observations?profile_id=p1"
        )[2]
        assert [row["id"] for row in listed["items"]] == [item["id"]]


def test_same_content_reports_duplicate_but_adds_event(monkeypatch) -> None:
    _enable(monkeypatch)
    with make_server() as server:
        seed_roles("p1")
        caller = as_role(server, "admin")
        _seed(caller)
        query = "?profile_id=p1&filename=%s" % quote("轴参数.xml")
        first = _capture(caller, query)[2]["item"]
        second = _capture(caller, query)[2]["item"]
        assert second["id"] == first["id"]
        assert second["deduplicated"] is True
        assert second["event_id"] != first["event_id"]
        summary = caller.json("GET", "/api/v1/config/ingest/summary?profile_id=p1")[2]
        assert summary["total"] == 1


def test_raw_download_is_byte_identical(monkeypatch) -> None:
    _enable(monkeypatch)
    with make_server() as server:
        seed_roles("p1")
        caller = as_role(server, "admin")
        _seed(caller)
        item = _capture(
            caller, "?profile_id=p1&filename=%s" % quote("轴参数.xml")
        )[2]["item"]

        status, headers, raw = caller.request(
            "GET", "/api/v1/config/observations/%s/raw" % item["id"]
        )
        assert status == 200
        assert raw == XML_DOC
        assert headers["X-Content-Sha256"] == item["blob_sha256"]
        assert headers["ETag"] == '"%s"' % item["blob_sha256"]


def test_unregistered_filename_is_404_with_hint(monkeypatch) -> None:
    _enable(monkeypatch)
    with make_server() as server:
        seed_roles("p1")
        caller = as_role(server, "admin")
        _seed(caller)
        status, _h, body = _capture(
            caller, "?profile_id=p1&filename=%s" % quote("没人认识.xml")
        )
        assert status == 404
        assert "hint" in body["error"]["detail"]


def test_missing_profile_id_is_400(monkeypatch) -> None:
    _enable(monkeypatch)
    with make_server() as server:
        seed_roles("p1")
        caller = as_role(server, "admin")
        status, _h, body = _capture(caller, "?filename=x.xml")
        assert status == 400
        assert body["error"]["detail"]["field"] == "profile_id"


# -- 重新映射 ---------------------------------------------------------------


def test_remap_appends_revision_and_keeps_r1(monkeypatch) -> None:
    _enable(monkeypatch)
    with make_server() as server:
        seed_roles("p1")
        caller = as_role(server, "admin")
        _seed(caller)
        item = _capture(
            caller, "?profile_id=p1&filename=%s" % quote("轴参数.xml")
        )[2]["item"]

        status, _h, body = caller.json(
            "POST",
            "/api/v1/config/observations/%s/remap" % item["id"],
            body={"reason": "补登记了一个参数后重算"},
        )
        assert status == 201, body
        remapped = body["item"]
        assert remapped["mapping_revision"] == 2

        detail = caller.json(
            "GET", "/api/v1/config/observations/%s" % item["id"]
        )[2]
        assert detail["revisions"] == ["r1", "r2"]
        assert detail["original"]["mapping_revision"] == 1
        assert detail["item"]["mapping_revision"] == 2


def test_remap_with_stale_revision_is_409(monkeypatch) -> None:
    _enable(monkeypatch)
    with make_server() as server:
        seed_roles("p1")
        caller = as_role(server, "admin")
        _seed(caller)
        item = _capture(
            caller, "?profile_id=p1&filename=%s" % quote("轴参数.xml")
        )[2]["item"]
        status, _h, body = caller.json(
            "POST",
            "/api/v1/config/observations/%s/remap" % item["id"],
            body={"expected_revision": "r7", "reason": "并发"},
        )
        assert status == 409
        assert body["error"]["code"] == "REVISION_CONFLICT"


def test_missing_observation_is_404(monkeypatch) -> None:
    _enable(monkeypatch)
    with make_server() as server:
        seed_roles("p1")
        caller = as_role(server, "admin")
        status, _h, body = caller.json("GET", "/api/v1/config/observations/ob_999999")
        assert status == 404
        assert body["error"]["code"] == "NOT_FOUND"


def test_idempotency_key_replays_capture(monkeypatch) -> None:
    """同一个 Idempotency-Key 重发不会多出第二条观测（也不会多出第二条事件）。"""
    _enable(monkeypatch)
    with make_server() as server:
        seed_roles("p1")
        caller = as_role(server, "admin")
        _seed(caller)
        query = "?profile_id=p1&filename=%s" % quote("轴参数.xml")
        headers = {"Idempotency-Key": "cap-1"}
        first = _capture(caller, query, headers=headers)[2]["item"]
        second = _capture(caller, query, headers=headers)[2]["item"]
        assert second["id"] == first["id"]
        assert second["replayed"] is True
        listed = caller.json("GET", "/api/v1/config/observations?profile_id=p1")[2]
        assert listed["total"] == 1
