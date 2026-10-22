"""追踪策略与旧入口守卫的 HTTP 行为（M6-05 / M6-06 / INH-1950、INH-1951）。

卡面要求的两条验收在这里落成断言：

1. **策略可开关、可继承、关掉不删历史**：`archive_only ↔ managed` 双向切换，
   历史修订一条不少，机型级覆盖类别级且回带 `source`。
2. **五条旧旁路逐一不能越 gate**：`PUT /files`、URL 导入、定时下载、旧 review
   提交、删除文件——`managed` 下都只能进各自的受控通道（observation /
   candidate / change_source / reassociate），或在文件未登记时被 `POLICY_BLOCKED`
   拦下；**没有任何一条通道能发布基线**。

另外钉住两条边界：`archive_only` 下旧行为逐字节不变；特性门禁关闭时守卫一律放行
（旧功能零影响）。
"""

from __future__ import annotations

from urllib.parse import quote

import pytest
from authz_fixtures import as_role, make_server, seed_roles

from app.server import api_upload
from app.services import config_gate, config_revision

TEMPLATE = (
    '<?xml version="1.0" encoding="UTF-8"?>\n'
    '<MachineConfig xmlns="urn:mc:cfg:1" model="A">'
    '<PulseWidth unit="ms">650</PulseWidth>'
    "</MachineConfig>"
).encode("utf-8")

XML_NAME = "轴参数.xml"
JSON_NAME = "轴参数.json"
UNKNOWN_NAME = "没人认识.xml"


@pytest.fixture(autouse=True)
def clean_state(monkeypatch):
    monkeypatch.delenv(config_gate.ENV_KEY, raising=False)
    config_revision.reset()
    yield
    config_revision.reset()


def _enable(monkeypatch, mode="on"):
    monkeypatch.setenv(config_gate.ENV_KEY, mode)


def _seed_family(caller):
    """建族 + 两个逻辑文件（XML 出口 / 现场 JSON），返回族与文件。"""
    status, _h, body = caller.json(
        "POST", "/api/v1/config/families", body={"name": "轴参数", "code": "AXIS_PARAM"}
    )
    assert status == 201, body
    family = body["item"]
    xml_file = caller.json(
        "POST",
        "/api/v1/config/families/%s/files" % family["id"],
        body={"role": "xml_export", "display_name": XML_NAME},
    )[2]["item"]
    json_file = caller.json(
        "POST",
        "/api/v1/config/families/%s/files" % family["id"],
        body={"role": "field_json", "display_name": JSON_NAME},
    )[2]["item"]
    return {"family": family, "xml": xml_file, "json": json_file}


def _policy(caller, *, scope, mode, reason="受管上线", family_id=None, profile_id=None):
    payload = {"scope": scope, "mode": mode, "reason": reason}
    if family_id is not None:
        payload["family_id"] = family_id
    if profile_id is not None:
        payload["profile_id"] = profile_id
    return caller.json("POST", "/api/v1/config/tracking", body=payload)


# -- 门禁与权限 -------------------------------------------------------------


def test_tracking_requires_gate_and_admin(monkeypatch) -> None:
    with make_server() as server:
        seed_roles("p1")
        admin = as_role(server, "admin")
        status, _h, body = admin.json("GET", "/api/v1/config/tracking")
        assert status == 503 and body["error"]["code"] == "CONFIG_DISABLED"

    _enable(monkeypatch)
    with make_server() as server:
        seed_roles("p1")
        member = as_role(server, "member")
        assert member.json("GET", "/api/v1/config/tracking")[0] == 200
        status, _h, body = member.json(
            "POST",
            "/api/v1/config/tracking",
            body={"scope": "model", "mode": "managed", "reason": "x"},
        )
        assert status == 403
        # 写接口用的是配置域管理动作，不是旧的 settings_write
        assert body["error"]["detail"]["action"] == "config_tracking_admin"


def test_readonly_blocks_policy_writes(monkeypatch) -> None:
    _enable(monkeypatch, "readonly")
    with make_server() as server:
        seed_roles("p1")
        admin = as_role(server, "admin")
        assert admin.json("GET", "/api/v1/config/tracking")[0] == 200
        status, _h, body = admin.json(
            "POST",
            "/api/v1/config/tracking",
            body={"scope": "model", "mode": "managed", "reason": "x"},
        )
        assert status == 503
        assert body["error"]["detail"]["mode"] == "readonly"


# -- 策略读写与继承 ---------------------------------------------------------


def test_default_is_archive_only_without_any_policy(monkeypatch) -> None:
    _enable(monkeypatch)
    with make_server() as server:
        seed_roles("p1")
        caller = as_role(server, "admin")
        body = caller.json("GET", "/api/v1/config/tracking/effective")[2]
        assert body["mode"] == "archive_only"
        assert body["source"] == "default"
        assert body["policy_id"] is None


def test_model_policy_overrides_family_and_reports_source(monkeypatch) -> None:
    _enable(monkeypatch)
    with make_server() as server:
        seed_roles("p1")
        caller = as_role(server, "admin")
        world = _seed_family(caller)
        family_id = world["family"]["id"]

        status, _h, family_policy = _policy(
            caller, scope="family", mode="managed", family_id=family_id
        )
        assert status == 201, family_policy

        effective = caller.json(
            "GET",
            "/api/v1/config/tracking/effective?family_id=%s&profile_id=p1" % family_id,
        )[2]
        assert effective["mode"] == "managed"
        assert effective["source"] == "family"
        assert effective["policy_id"] == family_policy["item"]["id"]
        # 继承链按优先级从高到低列出**实际存在**的策略（默认档不入链）
        assert [step["source"] for step in effective["chain"]] == ["family"]

        # 机型级收窄回 archive_only → 覆盖类别级
        status, _h, model_policy = _policy(
            caller, scope="model", mode="archive_only", profile_id="p1", reason="本机先不切"
        )
        assert status == 201, model_policy
        narrowed = caller.json(
            "GET",
            "/api/v1/config/tracking/effective?family_id=%s&profile_id=p1" % family_id,
        )[2]
        assert narrowed["mode"] == "archive_only"
        assert narrowed["source"] == "model"
        assert narrowed["policy_id"] == model_policy["item"]["id"]


def test_policy_change_appends_revision_and_keeps_history(monkeypatch) -> None:
    """验收第 1 条：改开关是追加修订，历史与既有观测都不被删。"""
    _enable(monkeypatch)
    with make_server() as server:
        seed_roles("p1")
        caller = as_role(server, "admin")
        world = _seed_family(caller)

        first = _policy(
            caller, scope="family", mode="managed", family_id=world["family"]["id"]
        )[2]["item"]
        policy_id = first["id"]

        second = caller.json(
            "POST",
            "/api/v1/config/tracking",
            body={
                "scope": "family",
                "mode": "archive_only",
                "family_id": world["family"]["id"],
                "reason": "回退到归档",
                "expected_revision": first["revision"],
            },
        )[2]["item"]
        # 同一个策略对象原地升修订，不是新建
        assert second["id"] == policy_id
        assert second["revision"] != first["revision"]

        history = caller.json(
            "GET", "/api/v1/config/tracking/%s/history" % policy_id
        )[2]
        assert len(history["revisions"]) >= 2
        assert first["revision"] in history["revisions"]
        # 当前档已回退，但那两条修订都还在（不只是"最后一条"）
        assert history["current"]["mode"] == "archive_only"
        assert second["revision"] in history["revisions"]

        overview = caller.json("GET", "/api/v1/config/tracking")[2]
        assert [item["id"] for item in overview["policies"]] == [policy_id]
        assert overview["counts"] == {"archive_only": 1}


def test_stale_expected_revision_is_rejected(monkeypatch) -> None:
    _enable(monkeypatch)
    with make_server() as server:
        seed_roles("p1")
        caller = as_role(server, "admin")
        world = _seed_family(caller)
        first = _policy(
            caller, scope="family", mode="managed", family_id=world["family"]["id"]
        )[2]["item"]
        status, _h, body = caller.json(
            "POST",
            "/api/v1/config/tracking",
            body={
                "scope": "family",
                "mode": "archive_only",
                "family_id": world["family"]["id"],
                "reason": "并发改动",
                "expected_revision": "r999",
            },
        )
        assert status == 409
        assert body["error"]["code"] == "REVISION_CONFLICT"
        assert first["revision"] == "r1"


def test_policy_requires_reason_and_valid_mode(monkeypatch) -> None:
    _enable(monkeypatch)
    with make_server() as server:
        seed_roles("p1")
        caller = as_role(server, "admin")
        status, _h, body = caller.json(
            "POST",
            "/api/v1/config/tracking",
            body={"scope": "model", "mode": "managed", "reason": "  "},
        )
        assert status == 400
        assert body["error"]["detail"]["field"] == "reason"

        status, _h, body = caller.json(
            "POST",
            "/api/v1/config/tracking",
            body={"scope": "model", "mode": "yolo", "reason": "x"},
        )
        assert status == 400
        assert body["error"]["detail"]["field"] == "mode"


# -- 五条旧旁路（M6-06）------------------------------------------------------


def _managed(caller, world):
    assert (
        _policy(
            caller, scope="family", mode="managed", family_id=world["family"]["id"]
        )[0]
        == 201
    )


def test_legacy_upload_routes_into_controlled_channel(monkeypatch) -> None:
    _enable(monkeypatch)
    with make_server() as server:
        seed_roles("p1")
        caller = as_role(server, "admin")
        world = _seed_family(caller)
        _managed(caller, world)

        # XML 出口 → candidate
        status, _h, body = caller.json(
            "PUT",
            "/api/v1/p/p1/files/%s" % quote(XML_NAME),
            body=TEMPLATE,
        )
        assert status in (200, 201), body
        assert body["tracking"] == {"route": "candidate", "mode": "managed"}

        # 现场 JSON → observation
        status, _h, body = caller.json(
            "PUT",
            "/api/v1/p/p1/files/%s" % quote(JSON_NAME),
            body=b'{"PULSE_WIDTH": 650}',
        )
        assert status in (200, 201), body
        assert body["tracking"] == {"route": "observation", "mode": "managed"}

        # 未登记文件 → 直接拦下（不知道是什么就不能落盘）。
        # 只设类别级不够：未登记文件查不到所属类别，要机型整体受管才拦得住。
        assert (
            _policy(caller, scope="model", mode="managed", profile_id="p1")[0] == 201
        )
        status, _h, body = caller.json(
            "PUT",
            "/api/v1/p/p1/files/%s" % quote(UNKNOWN_NAME),
            body=TEMPLATE,
        )
        assert status == 403
        assert body["error"]["code"] == "POLICY_BLOCKED"


def test_legacy_url_import_respects_guard(monkeypatch) -> None:
    """只有 `persist=true` 才落盘，所以守卫只挂在那条分支上。"""
    _enable(monkeypatch)
    with make_server() as server:
        seed_roles("p1")
        caller = as_role(server, "admin")
        world = _seed_family(caller)
        # 机型全域受管，未登记文件才判 blocked
        assert (
            _policy(caller, scope="model", mode="managed", profile_id="p1")[0] == 201
        )
        assert world["family"]["id"]

        # persist=true → 落盘前就被拦，根本不会发起抓取
        status, _h, body = caller.json(
            "POST",
            "/api/v1/p/p1/import-url",
            body={
                "url": "http://example.invalid/x",
                "name": UNKNOWN_NAME,
                "persist": True,
            },
        )
        assert status == 403, body
        assert body["error"]["code"] == "POLICY_BLOCKED"

        # persist=false 只进临时区，不设守卫——用替身避免真发网络请求
        calls = []

        def fake_import(pid, cid, ip, url, name, persist, overwrite, settings, actor=""):
            calls.append((pid, name, persist))
            return {"temp": True, "name": name}

        monkeypatch.setattr(api_upload.upload, "import_url", fake_import)
        status, _h, body = caller.json(
            "POST",
            "/api/v1/p/p1/import-url",
            body={"url": "http://example.invalid/x", "name": UNKNOWN_NAME, "persist": False},
        )
        assert status == 200, body
        assert calls == [("p1", UNKNOWN_NAME, False)]
        # 临时分支不写盘，也就不带 tracking 结论
        assert "tracking" not in body


def test_legacy_review_submit_cannot_bypass_gate(monkeypatch) -> None:
    """旧节点审阅是"变更来源"，受管模式下也未登记文件一律拿不到入口。

    这里刻意让请求体本身是**残缺**的（没有 `request_id` / `decisions`）：守卫在
    handler 里先于服务层执行，所以能拿到 `POLICY_BLOCKED` 就证明它真的挡在了前面，
    而不是先被参数校验拦下。
    """
    _enable(monkeypatch)
    with make_server() as server:
        seed_roles("p1")
        caller = as_role(server, "admin")
        world = _seed_family(caller)
        # 机型全域受管：未登记文件才可能被判 blocked（只设类别级不够）
        assert (
            _policy(caller, scope="model", mode="managed", profile_id="p1")[0] == 201
        )
        assert world["family"]["id"]

        status, _h, body = caller.json(
            "POST",
            "/api/v1/p/p1/review/submit",
            body={"file": UNKNOWN_NAME, "decisions": []},
        )
        assert status == 403, body
        assert body["error"]["code"] == "POLICY_BLOCKED"
        assert body["error"]["detail"]["route"] == "blocked"


def test_legacy_delete_routes_to_reassociate(monkeypatch) -> None:
    _enable(monkeypatch)
    with make_server() as server:
        seed_roles("p1")
        caller = as_role(server, "admin")
        world = _seed_family(caller)
        _managed(caller, world)

        caller.json(
            "PUT",
            "/api/v1/p/p1/files/%s" % quote(XML_NAME),
            body=TEMPLATE,
        )
        status, _h, body = caller.json(
            "DELETE", "/api/v1/p/p1/files/%s" % quote(XML_NAME)
        )
        assert status in (200, 204), body
        if status == 200:
            assert body["tracking"]["route"] == "reassociate"


def test_none_of_the_legacy_routes_publish_baseline(monkeypatch) -> None:
    """结构性保证：五条旁路返回的 route 都不是 `baseline`。"""
    from app.services import config_policies

    for route in (
        config_policies.ROUTE_LEGACY,
        config_policies.ROUTE_OBSERVATION,
        config_policies.ROUTE_CANDIDATE,
        config_policies.ROUTE_CHANGE_SOURCE,
        config_policies.ROUTE_REASSOCIATE,
        config_policies.ROUTE_BLOCKED,
    ):
        assert config_policies.may_publish_baseline(route) is False


# -- 回归：archive_only 与门禁关闭 -------------------------------------------


def test_archive_only_keeps_legacy_behaviour(monkeypatch) -> None:
    _enable(monkeypatch)
    with make_server() as server:
        seed_roles("p1")
        caller = as_role(server, "admin")
        world = _seed_family(caller)
        # 默认档就是 archive_only，连策略都不需要设置
        status, _h, body = caller.json(
            "PUT",
            "/api/v1/p/p1/files/%s" % quote(XML_NAME),
            body=TEMPLATE,
        )
        assert status in (200, 201), body
        assert body["tracking"] == {"route": "legacy", "mode": "archive_only"}

        # 未登记文件在 archive_only 下照旧可写
        status, _h, body = caller.json(
            "PUT",
            "/api/v1/p/p1/files/%s" % quote(UNKNOWN_NAME),
            body=TEMPLATE,
        )
        assert status in (200, 201), body
        assert body["tracking"]["route"] == "legacy"
        assert world["family"]["id"]


def test_disabled_gate_bypasses_guard_entirely() -> None:
    """特性关闭：守卫一律放行，旧功能零影响（连 managed 策略都不生效）。"""
    with make_server() as server:
        seed_roles("p1")
        caller = as_role(server, "admin")
        status, _h, body = caller.json(
            "PUT",
            "/api/v1/p/p1/files/%s" % quote(UNKNOWN_NAME),
            body=TEMPLATE,
        )
        assert status in (200, 201), body
        # 特性未开启：响应体与旧版逐字节一致，不多出 tracking
        assert "tracking" not in body


# -- 定时下载：批量任务里单条被拦 ------------------------------------------------


def test_schedule_download_marks_blocked_result(monkeypatch) -> None:
    """定时下载一次提交多份配置：被拦的单条回 `blocked`，整批不中断。

    `_commit_config` 在**最前面**过守卫，所以被拦的条目根本不会走到解析与落盘，
    也就不可能借定时任务绕进基线。
    """
    from app.services import config_policies, updates

    _enable(monkeypatch)
    repo = config_policies.repository()
    config_policies.set_policy(
        repo,
        {"ip": "127.0.0.1", "name": "测试管理员"},
        scope="model",
        mode="managed",
        profile_id="p1",
        reason="受管",
    )

    blocked = updates._commit_config("p1", UNKNOWN_NAME, TEMPLATE)
    assert blocked["status"] == "blocked"
    assert blocked["name"] == UNKNOWN_NAME
    assert "未登记" in blocked["reason"]
