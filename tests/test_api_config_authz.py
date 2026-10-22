"""配置管理动作的权限矩阵与会话能力（M5-07 / INH-1941）。

卡面验收：

* 访客调用配置写端点 403，且权威数据 sha 不变；
* 撤权后旧页面提交被拒、返回明确错误；
* 前端按能力矩阵禁用入口，未知能力不能默认返回 `true`；
* **部署者不因访问配置页自动获得管理员批准权**（本机回环同样不含批准权）。

第 4 条在 `8a1d8ba` 取消部署者身份后的实现口径是：**不存在任何回环特权**，
`deployer` 与 `admin` 是同一个布尔值（决策 D4）。本文件用真实 `127.0.0.1`
未登记管理员的身份把这一点钉住。
"""

from __future__ import annotations

import pytest
from authz_fixtures import as_role, make_server, seed_roles

from app.core.errors import AppError
from app.services import authz
from app.services.auth import Actor


def _actor(ip="10.0.0.9", admin=False, registered=False):
    return Actor(
        ip=ip,
        name="测试",
        registered=registered,
        is_deployer=False,
        is_admin=admin,
    )


CONFIG_ACTIONS = (
    "config_read",
    "config_registry_write",
    "config_tracking_admin",
    "config_change_submit",
    "config_change_approve",
    "config_artifact_generate",
)


def test_every_config_action_is_registered() -> None:
    for action in CONFIG_ACTIONS:
        assert authz.action_level(action) in (authz.ANY, authz.ADMIN)


def test_only_read_is_open_to_everyone() -> None:
    guest = _actor()
    assert authz.allowed(guest, "config_read") is True
    for action in CONFIG_ACTIONS[1:]:
        assert authz.allowed(guest, action) is False, action


def test_admin_gets_all_config_actions() -> None:
    admin = _actor(admin=True, registered=True)
    for action in CONFIG_ACTIONS:
        assert authz.allowed(admin, action) is True, action


def test_capabilities_include_legacy_keys_and_deny_unknown() -> None:
    guest = _actor()
    matrix = authz.capabilities(guest)
    assert matrix["config_read"] is True
    assert matrix["config_change_approve"] is False
    assert matrix["admin"] is False
    assert matrix["deployer"] is False, "部署者与管理员同义，未登记管理员即 False"
    assert authz.has_capability(guest, "no_such_capability") is False


def test_admin_capabilities_mark_admin_and_deployer() -> None:
    matrix = authz.capabilities(_actor(admin=True))
    assert matrix["admin"] is True and matrix["deployer"] is True
    assert all(matrix[action] for action in CONFIG_ACTIONS)


def test_actorless_context_has_no_config_write() -> None:
    """后台任务误用请求上下文时不得获得写权限。"""
    matrix = authz.capabilities(None)
    assert matrix["config_read"] is True, "读是 any 档"
    assert matrix["config_change_approve"] is False
    assert matrix["config_registry_write"] is False


def test_authorize_raises_403_with_action_detail() -> None:
    class _Ctx:
        actor = _actor()

    with pytest.raises(AppError) as error:
        authz.authorize(_Ctx(), "config_change_approve")
    assert error.value.code == "FORBIDDEN"
    assert error.value.detail["action"] == "config_change_approve"


def test_unknown_action_is_a_coding_error_not_403() -> None:
    with pytest.raises(ValueError):
        authz.action_level("no_such_action")


# --- 真实服务：/session 下发能力矩阵 -------------------------------------


def test_session_exposes_capabilities_for_visitor() -> None:
    with make_server() as server:
        seed_roles("p1")
        caller = as_role(server, "visitor")
        status, _headers, body = caller.json("GET", "/api/v1/session?profile=p1")
        assert status == 200
        identity = body["identity"]
        assert identity["is_admin"] is False
        assert identity["capabilities"]["config_read"] is True
        assert identity["capabilities"]["config_change_approve"] is False


def test_session_exposes_capabilities_for_admin() -> None:
    with make_server() as server:
        seed_roles("p1")
        caller = as_role(server, "admin")
        status, _headers, body = caller.json("GET", "/api/v1/session?profile=p1")
        assert status == 200
        caps = body["identity"]["capabilities"]
        assert caps["config_registry_write"] is True
        assert caps["config_change_approve"] is True


def test_deployer_flag_grants_no_config_power() -> None:
    """`is_deployer=True` 不带来任何配置写/批准权（决策 D4：无回环特权）。

    注：测试环境里 conftest 会把 `127.0.0.1` 当成管理员（历史用例约定），
    因此这里直接构造 Actor，避免被那层兼容补丁掩盖真实语义。
    """
    deployer = Actor(
        ip="127.0.0.1",
        name="本机",
        registered=False,
        is_deployer=True,
        is_admin=False,
    )
    matrix = authz.capabilities(deployer)
    assert matrix["config_change_approve"] is False
    assert matrix["config_registry_write"] is False
    assert matrix["deployer"] is False, "兼容键与 admin 同义，不反映 is_deployer 标志"
    assert matrix["config_read"] is True


def test_registered_member_cannot_approve_over_http() -> None:
    """真实服务上，已登记但非管理员的成员拿不到批准权。"""
    with make_server() as server:
        seed_roles("p1")
        caller = as_role(server, "member")
        status, _headers, body = caller.json("GET", "/api/v1/session?profile=p1")
        assert status == 200
        caps = body["identity"]["capabilities"]
        assert caps["config_change_approve"] is False
        assert caps["config_registry_write"] is False
        assert body["identity"]["is_admin"] is False


def test_session_keeps_legacy_identity_fields() -> None:
    """新增 capabilities 不能挤掉旧字段（旧前端仍读 is_admin / is_deployer）。"""
    with make_server() as server:
        seed_roles("p1")
        caller = as_role(server, "member")
        status, _headers, body = caller.json("GET", "/api/v1/session?profile=p1")
        assert status == 200
        identity = body["identity"]
        for key in ("ip", "name", "registered", "is_deployer", "is_admin", "role_label"):
            assert key in identity
