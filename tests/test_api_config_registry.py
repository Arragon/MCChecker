"""参数登记 API（M6-04 / INH-1949）。

覆盖：

* 四种检索维度都定位到稳定 ID（`test_search_hits_all_four_dimensions`）；
* 表单写入带理由与 `expected_revision`：过期即 409 且**不落库**；
* 定义修订只追加（同一参数两次 POST 产生 r1/r2，`supersedes` 串起来）；
* 定义集合冻结：新集合不改旧集合；
* 权限与门禁：成员只读、`readonly` 写 503。
"""

from __future__ import annotations

from urllib.parse import quote

import pytest
from authz_fixtures import as_role, make_server, seed_roles

from app.services import config_gate, config_revision

TEMPLATE = (
    '<?xml version="1.0" encoding="UTF-8"?>\n'
    '<MachineConfig xmlns="urn:mc:cfg:1" model="A">'
    '<PulseWidth unit="ms">650</PulseWidth>'
    "</MachineConfig>"
).encode("utf-8")

OCTET = {"Content-Type": "application/octet-stream", "X-Filename": "axis_param.xml"}


@pytest.fixture(autouse=True)
def clean_gate(monkeypatch):
    monkeypatch.delenv(config_gate.ENV_KEY, raising=False)
    config_revision.reset()
    yield
    config_revision.reset()


def _enable(monkeypatch, mode="on"):
    monkeypatch.setenv(config_gate.ENV_KEY, mode)


def _seed(caller):
    """建族 + 参数 + 版本 + 快照，返回所需 ID。"""
    status, _h, family = caller.json(
        "POST", "/api/v1/config/families", body={"name": "轴参数", "code": "AXIS_PARAM"}
    )
    assert status == 201, family
    status, _h, parameter = caller.json(
        "POST",
        "/api/v1/config/parameters",
        body={"family_id": family["item"]["id"], "name": "脉冲宽度", "category": "时序"},
    )
    assert status == 201, parameter
    status, _h, release = caller.json(
        "POST", "/api/v1/config/releases", body={"version_label": "1.1.0", "build": "20260901"}
    )
    assert status == 201, release
    logical = caller.json(
        "POST",
        "/api/v1/config/families/%s/files" % family["item"]["id"],
        body={"role": "xml_export", "display_name": "轴参数.xml"},
    )[2]["item"]
    snapshot = caller.json(
        "POST",
        "/api/v1/config/snapshots?release_id=%s&logical_file_id=%s&source=upload"
        % (release["item"]["id"], logical["id"]),
        body=TEMPLATE,
        headers=OCTET,
    )[2]["item"]
    return {
        "family": family["item"],
        "parameter": parameter["item"],
        "release": release["item"],
        "logical": logical,
        "snapshot": snapshot,
    }


def _definition_body(**overrides):
    payload = {
        "xml_name": "PulseWidth",
        "xml_loc": {"path": [{"name": "PulseWidth", "occurrence": 1}], "attribute": None},
        "json_key": "PULSE_WIDTH",
        "json_pointer": "/PULSE_WIDTH",
        "type": "decimal",
        "unit": "s",
        "constraints": {"min": "0.0", "max": "10.0"},
        "remarks": "单位改为秒",
        "reason": "1.1.0 起单位改为秒",
    }
    payload.update(overrides)
    return payload


# -- 门禁与权限 -------------------------------------------------------------


def test_registry_requires_gate_and_admin(monkeypatch) -> None:
    with make_server() as server:
        seed_roles("p1")
        caller = as_role(server, "admin")
        status, _h, body = caller.json("GET", "/api/v1/config/parameters")
        assert status == 503 and body["error"]["code"] == "CONFIG_DISABLED"

    _enable(monkeypatch)
    with make_server() as server:
        seed_roles("p1")
        member = as_role(server, "member")
        assert member.json("GET", "/api/v1/config/parameters")[0] == 200
        write_status, _h, write_body = member.json(
            "POST",
            "/api/v1/config/parameters",
            body={"family_id": "cf_000001", "name": "x"},
        )
        assert write_status == 403
        assert write_body["error"]["detail"]["action"] == "config_registry_write"


def test_readonly_blocks_registry_writes(monkeypatch) -> None:
    _enable(monkeypatch, "readonly")
    with make_server() as server:
        seed_roles("p1")
        caller = as_role(server, "admin")
        assert caller.json("GET", "/api/v1/config/parameters")[0] == 200
        status, _h, body = caller.json(
            "POST", "/api/v1/config/parameters", body={"family_id": "cf_000001", "name": "x"}
        )
        assert status == 503
        assert body["error"]["detail"]["mode"] == "readonly"


# -- 检索与详情 -------------------------------------------------------------


def test_search_hits_all_four_dimensions(monkeypatch) -> None:
    _enable(monkeypatch)
    with make_server() as server:
        seed_roles("p1")
        caller = as_role(server, "admin")
        data = _seed(caller)
        pmid = data["parameter"]["id"]
        assert caller.json(
            "POST", "/api/v1/config/parameters/%s/definitions" % pmid, body=_definition_body()
        )[0] == 201
        assert caller.json(
            "PATCH",
            "/api/v1/config/parameters/%s" % pmid,
            body={"name": "脉冲宽度时间", "reason": "术语统一"},
        )[0] == 200

        for needle, dimension in (
            ("脉冲宽度时间", "name"),
            ("脉冲宽度", "alias"),
            ("PulseWidth", "xml"),
            ("PULSE_WIDTH", "json"),
        ):
            status, _h, body = caller.json(
                "GET", "/api/v1/config/parameters?q=%s" % quote(needle)
            )
            assert status == 200, needle
            assert [item["id"] for item in body["items"]] == [pmid], needle
            assert dimension in body["items"][0]["matched"], (needle, body["items"][0])
        assert body["dimensions"] == ["name", "alias", "xml", "json"]


def test_parameter_detail_includes_definition_history(monkeypatch) -> None:
    _enable(monkeypatch)
    with make_server() as server:
        seed_roles("p1")
        caller = as_role(server, "admin")
        data = _seed(caller)
        pmid = data["parameter"]["id"]
        first = caller.json(
            "POST", "/api/v1/config/parameters/%s/definitions" % pmid, body=_definition_body()
        )[2]["item"]
        second = caller.json(
            "POST",
            "/api/v1/config/parameters/%s/definitions" % pmid,
            body=_definition_body(remarks="再改一次"),
        )[2]["item"]

        status, _h, body = caller.json("GET", "/api/v1/config/parameters/%s" % pmid)
        assert status == 200
        assert body["item"]["id"] == pmid
        assert [item["id"] for item in body["definitions"]] == [second["id"], first["id"]]
        assert body["current"]["id"] == second["id"]
        assert second["supersedes"] == first["id"]
        assert second["revision_no"] == 2

        listed = caller.json("GET", "/api/v1/config/parameters/%s/definitions" % pmid)[2]
        assert listed["current"]["id"] == second["id"]


def test_stale_expected_revision_is_conflict_and_writes_nothing(monkeypatch) -> None:
    _enable(monkeypatch)
    with make_server() as server:
        seed_roles("p1")
        caller = as_role(server, "admin")
        data = _seed(caller)
        pmid = data["parameter"]["id"]
        stale = data["parameter"]["revision"]

        assert caller.json(
            "PATCH",
            "/api/v1/config/parameters/%s" % pmid,
            body={"name": "改名一", "reason": "第一次", "expected_revision": stale},
        )[0] == 200

        status, _h, body = caller.json(
            "PATCH",
            "/api/v1/config/parameters/%s" % pmid,
            body={"name": "改名二", "reason": "第二次", "expected_revision": stale},
        )
        assert status == 409
        assert body["error"]["code"] == "REVISION_CONFLICT"
        assert body["error"]["detail"]["expected_revision"] == stale
        # 第二次没有落库：读回来仍是第一次的名字
        assert caller.json("GET", "/api/v1/config/parameters/%s" % pmid)[2]["item"]["name"] == "改名一"


def test_definition_write_also_guards_revision(monkeypatch) -> None:
    _enable(monkeypatch)
    with make_server() as server:
        seed_roles("p1")
        caller = as_role(server, "admin")
        data = _seed(caller)
        pmid = data["parameter"]["id"]
        first = caller.json(
            "POST",
            "/api/v1/config/parameters/%s/definitions" % pmid,
            body=_definition_body(expected_definition_id=""),
        )[2]["item"]
        # 拿着 r1 追加：成功，新定义变成当前
        ok = caller.json(
            "POST",
            "/api/v1/config/parameters/%s/definitions" % pmid,
            body=_definition_body(
                expected_definition_id=first["id"], remarks="第二条"
            ),
        )
        assert ok[0] == 201, ok[2]
        # 再拿同一条去追加：并发里的第二个请求必须被挡下
        status, _h, body = caller.json(
            "POST",
            "/api/v1/config/parameters/%s/definitions" % pmid,
            body=_definition_body(
                expected_definition_id=first["id"], remarks="并发里的第二次"
            ),
        )
        assert status == 409
        assert body["error"]["code"] == "REVISION_CONFLICT"
        assert body["error"]["detail"]["actual_definition_id"] == ok[2]["item"]["id"]
        assert len(caller.json("GET", "/api/v1/config/parameters/%s/definitions" % pmid)[2]["items"]) == 2


def test_empty_patch_writes_nothing(monkeypatch) -> None:
    _enable(monkeypatch)
    with make_server() as server:
        seed_roles("p1")
        caller = as_role(server, "admin")
        data = _seed(caller)
        pmid = data["parameter"]["id"]
        status, _h, body = caller.json(
            "PATCH", "/api/v1/config/parameters/%s" % pmid, body={"reason": "无变化"}
        )
        assert status == 200
        assert body["changed"] == []
        assert body["item"]["revision"] == data["parameter"]["revision"]


# -- 定义集合与模板对账 -----------------------------------------------------


def test_definition_set_is_frozen_and_verifiable(monkeypatch) -> None:
    _enable(monkeypatch)
    with make_server() as server:
        seed_roles("p1")
        caller = as_role(server, "admin")
        data = _seed(caller)
        pmid = data["parameter"]["id"]
        definition = caller.json(
            "POST", "/api/v1/config/parameters/%s/definitions" % pmid, body=_definition_body()
        )[2]["item"]

        verify_status, _h, verified = caller.json(
            "POST",
            "/api/v1/config/definitions/%s/verify?snapshot_id=%s"
            % (definition["id"], data["snapshot"]["id"]),
        )
        assert verify_status == 200
        assert verified["item"]["converted"] == "0.65"
        assert verified["item"]["template_unit"] == "ms"

        status, _h, created = caller.json(
            "POST",
            "/api/v1/config/definition-sets",
            body={
                "release_id": data["release"]["id"],
                "definition_ids": [definition["id"]],
                "note": "1.1.0 初版",
            },
        )
        assert status == 201, created
        set_id = created["item"]["id"]

        second_definition = caller.json(
            "POST",
            "/api/v1/config/parameters/%s/definitions" % pmid,
            body=_definition_body(remarks="再改"),
        )[2]["item"]
        caller.json(
            "POST",
            "/api/v1/config/definition-sets",
            body={"release_id": data["release"]["id"], "definition_ids": [second_definition["id"]]},
        )

        detail_status, _h, detail = caller.json(
            "GET", "/api/v1/config/definition-sets/%s" % set_id
        )
        assert detail_status == 200
        assert detail["definition_set"]["definition_ids"] == [definition["id"]]
        assert [item["id"] for item in detail["definitions"]] == [definition["id"]]

        listed = caller.json(
            "GET", "/api/v1/config/definition-sets?release_id=%s" % data["release"]["id"]
        )[2]
        assert len(listed["items"]) == 2
        assert listed["latest_id"] != set_id


def test_definition_set_ambiguity_over_http(monkeypatch) -> None:
    _enable(monkeypatch)
    with make_server() as server:
        seed_roles("p1")
        caller = as_role(server, "admin")
        data = _seed(caller)
        one = data["parameter"]["id"]
        other = caller.json(
            "POST",
            "/api/v1/config/parameters",
            body={"family_id": data["family"]["id"], "name": "脉冲宽度备份"},
        )[2]["item"]["id"]
        first = caller.json(
            "POST", "/api/v1/config/parameters/%s/definitions" % one, body=_definition_body()
        )[2]["item"]
        second = caller.json(
            "POST", "/api/v1/config/parameters/%s/definitions" % other, body=_definition_body()
        )[2]["item"]
        status, _h, body = caller.json(
            "POST",
            "/api/v1/config/definition-sets",
            body={
                "release_id": data["release"]["id"],
                "definition_ids": [first["id"], second["id"]],
            },
        )
        assert status == 422
        assert body["error"]["code"] == "MAPPING_AMBIGUOUS"


def test_retire_definition_over_http(monkeypatch) -> None:
    _enable(monkeypatch)
    with make_server() as server:
        seed_roles("p1")
        caller = as_role(server, "admin")
        data = _seed(caller)
        pmid = data["parameter"]["id"]
        definition = caller.json(
            "POST", "/api/v1/config/parameters/%s/definitions" % pmid, body=_definition_body()
        )[2]["item"]
        status, _h, body = caller.json(
            "POST",
            "/api/v1/config/definitions/%s/retire" % definition["id"],
            body={"reason": "改为另一处定位"},
        )
        assert status == 200
        assert body["item"]["status"] == "retired"
        assert caller.json("GET", "/api/v1/config/parameters/%s" % pmid)[2]["current"] is None
