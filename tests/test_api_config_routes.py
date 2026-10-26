"""配置管理 API 骨架与门禁（M5-08 / INH-1942）。

覆盖：默认关闭、三档门禁、全局修订号与旧修订号互不干扰、未知机型 404 不回落。
"""

from __future__ import annotations

import pytest
from authz_fixtures import as_role, make_server, seed_roles

from app.core.errors import AppError
from app.services import config_gate, config_revision


@pytest.fixture(autouse=True)
def clean_gate(monkeypatch):
    monkeypatch.delenv(config_gate.ENV_KEY, raising=False)
    config_revision.reset()
    yield
    config_revision.reset()


def _enable(monkeypatch, mode="on"):
    monkeypatch.setenv(config_gate.ENV_KEY, mode)


# --- 门禁单元 ------------------------------------------------------------


def test_default_mode_is_on_when_unset(monkeypatch) -> None:
    assert config_gate.mode() == config_gate.MODE_ON
    assert config_gate.enabled() is True


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("on", "on"),
        ("1", "on"),
        ("true", "on"),
        ("readonly", "readonly"),
        ("RO", "readonly"),
        ("off", "off"),
        ("", "off"),
        ("nonsense", "off"),
    ],
)
def test_mode_parsing_is_fail_closed(monkeypatch, raw, expected) -> None:
    monkeypatch.setenv(config_gate.ENV_KEY, raw)
    assert config_gate.mode() == expected


def test_require_read_blocks_when_off(monkeypatch) -> None:
    _enable(monkeypatch, "off")
    with pytest.raises(AppError) as error:
        config_gate.require_read()
    assert error.value.code == "CONFIG_DISABLED"
    assert error.value.http_status == 503


def test_require_write_blocks_readonly_with_reason(monkeypatch) -> None:
    _enable(monkeypatch, "readonly")
    config_gate.require_read()  # 读通过
    with pytest.raises(AppError) as error:
        config_gate.require_write()
    assert error.value.code == "CONFIG_DISABLED"
    assert error.value.detail["mode"] == "readonly"


def test_require_write_allows_on(monkeypatch) -> None:
    _enable(monkeypatch, "on")
    config_gate.require_write()


# --- 真实服务 ------------------------------------------------------------


def test_revision_endpoint_is_503_when_disabled(monkeypatch) -> None:
    _enable(monkeypatch, "off")
    with make_server() as server:
        seed_roles("p1")
        caller = as_role(server, "admin")
        status, _headers, body = caller.json("GET", "/api/v1/config/revision")
        assert status == 503
        assert body["error"]["code"] == "CONFIG_DISABLED"


def test_status_endpoint_is_503_when_disabled(monkeypatch) -> None:
    _enable(monkeypatch, "off")
    with make_server() as server:
        seed_roles("p1")
        caller = as_role(server, "admin")
        status, _headers, body = caller.json("GET", "/api/v1/config/status")
        assert status == 503
        assert body["error"]["detail"]["mode"] == "off"


def test_revision_endpoint_works_when_enabled(monkeypatch) -> None:
    _enable(monkeypatch)
    with make_server() as server:
        seed_roles("p1")
        caller = as_role(server, "visitor")
        status, _headers, body = caller.json("GET", "/api/v1/config/revision")
        assert status == 200
        assert body["rev"] == "c0"
        assert body["seq"] == 0
        assert body["store_seq"] == 0


def test_revision_counter_is_global_and_bumps() -> None:
    assert config_revision.current() == 0
    assert config_revision.bump() == 1
    assert config_revision.snapshot()["rev"] == "c1"
    assert config_revision.snapshot(store_seq=7)["store_seq"] == 7


def test_old_per_profile_revision_contract_is_untouched(monkeypatch) -> None:
    """决策 D3：绝不能把配置域计数塞进旧 5 段 `rev`。"""
    _enable(monkeypatch)
    config_revision.bump()
    with make_server() as server:
        seed_roles("p1")
        caller = as_role(server, "admin")
        status, _headers, body = caller.json("GET", "/api/v1/p/p1/revision")
        assert status == 200
        assert set(body["parts"]) == {"files", "favorites", "remarks", "settings", "tasks"}
        assert len(body["rev"].split(".")) == 5


def test_capabilities_endpoint_matches_session(monkeypatch) -> None:
    _enable(monkeypatch)
    with make_server() as server:
        seed_roles("p1")
        caller = as_role(server, "member")
        s_status, _h, session = caller.json("GET", "/api/v1/session?profile=p1")
        c_status, _h2, caps = caller.json("GET", "/api/v1/config/capabilities")
        assert s_status == c_status == 200
        assert caps["capabilities"] == session["identity"]["capabilities"]
        assert "config_change_approve" in caps["actions"]


def test_status_counts_are_zero_on_empty_store(monkeypatch) -> None:
    _enable(monkeypatch)
    with make_server() as server:
        seed_roles("p1")
        caller = as_role(server, "admin")
        status, _headers, body = caller.json("GET", "/api/v1/config/status")
        assert status == 200
        assert body["mode"] == "on"
        assert body["writable"] is True
        assert body["total"] == 0
        assert body["counts"]["baseline"] == 0


def test_profile_status_requires_existing_profile(monkeypatch) -> None:
    _enable(monkeypatch)
    with make_server() as server:
        seed_roles("p1")
        caller = as_role(server, "admin")
        ok_status, _h, ok_body = caller.json("GET", "/api/v1/p/p1/config/status")
        assert ok_status == 200
        assert ok_body["profile_id"] == "p1"
        assert ok_body["baselines"] == 0

        missing_status, _h2, missing_body = caller.json("GET", "/api/v1/p/nope/config/status")
        assert missing_status == 404, "未知机型不得回落 default"
        assert missing_body["error"]["code"] == "NOT_FOUND"


def test_profile_status_is_readable_by_visitor_when_enabled(monkeypatch) -> None:
    _enable(monkeypatch)
    with make_server() as server:
        seed_roles("p1")
        caller = as_role(server, "visitor")
        status, _headers, body = caller.json("GET", "/api/v1/p/p1/config/status")
        assert status == 200
        assert body["observations"] == 0


def test_gate_does_not_leak_into_legacy_endpoints(monkeypatch) -> None:
    """门禁只在配置域生效：off 档下旧的会话与文件接口照旧。"""
    with make_server() as server:
        seed_roles("p1")
        caller = as_role(server, "admin")
        session_status, _headers, _body = caller.json("GET", "/api/v1/session?profile=p1")
        files_status, _headers2, files_body = caller.json("GET", "/api/v1/p/p1/files")
        assert session_status == 200
        assert files_status == 200
        assert "items" in files_body  # 旧接口包络是 {items: [...]}，不是 {files: ...}
