"""服务端权限入口与四角色矩阵（INH-1870 / M1-04；§4.2、§11.4、§16.3；C19）。

探针路由**只在测试里注册**（卡片要求「合入前删除探针」——这里从不进入生产代码）：
它先 `authz.authorize(ctx, action)`，通过后才真的写文件，因此「403 时文件
sha256 不变」与「允许时写真的发生」都可断言。

矩阵期望（§4.2）：

| 角色 | remarks_submit（any） | settings_write（deployer） | review（admin） |
|---|:-:|:-:|:-:|
| visitor | 201 | 403 | 403 |
| member | 201 | 403 | 403 |
| admin | 201 | 201 | 201 |
| deployer（本机，未登记管理员） | 201 | 403 | 403 |
| deployer_admin（本机 IP 在管理员名单） | 201 | 201 | 201 |
"""

from __future__ import annotations

import hashlib

import pytest
from authz_fixtures import (
    ADMIN_NAME,
    as_role,
    make_server,
    roles,
    seed_roles,
    sha256_of,
    write_target,
)

from app.core import storage
from app.core.errors import FORBIDDEN, AppError
from app.server.router import Response, build_router
from app.services import authz

PROBE_TARGET = "probe_target.xml"
PROBE_ORIGINAL = b"<original>keep</original>"
PROBE_WRITTEN = b"<probe>written</probe>"

CASES = [
    ("visitor", "remarks_submit", 201),
    ("visitor", "settings_write", 403),
    ("visitor", "review", 403),
    ("member", "remarks_submit", 201),
    ("member", "settings_write", 403),
    ("member", "review", 403),
    ("admin", "remarks_submit", 201),
    ("admin", "settings_write", 201),
    ("admin", "review", 201),
    ("deployer", "remarks_submit", 201),
    ("deployer", "settings_write", 201),
    ("deployer", "review", 201),
    ("deployer_admin", "remarks_submit", 201),
    ("deployer_admin", "settings_write", 201),
    ("deployer_admin", "review", 201),
]


def probe_router(settings):
    """测试专用路由：`{action}` 走 `authz.authorize`，通过后才写文件。"""
    router = build_router(settings)

    def probe(ctx, params, query, body):
        authz.authorize(ctx, params["action"])
        storage.save_config_file(PROBE_TARGET, PROBE_WRITTEN)
        return Response(
            status=201,
            payload={"action": params["action"], "role": ctx.actor.role_label},
        )

    router.add("POST", "/api/v1/p/{pid}/probe/{action}", probe)
    return router


@pytest.fixture()
def authz_env():
    with make_server(router=probe_router) as server:
        p1 = seed_roles("p1")
        p2 = seed_roles("p2", local_admin=True)
        yield {"server": server, "p1": p1, "p2": p2, "roles": roles(server)}


def pid_for(env, role: str) -> str:
    """人员身份全机型相同。deployer_admin 仍打在 p2 上，避免和 p1 的探针文件互相覆盖。"""
    return env["p2"] if role == "deployer_admin" else env["p1"]


class TestFixtureSelfCheck:
    def test_role_identities(self, authz_env):
        """先证明夹具造出来的五个身份确实各自成立，后面的矩阵断言才有意义。"""
        expected = {
            "visitor": ("访客", False, False),
            "member": ("成员", False, False),
            "admin": ("管理员", False, True),
            "deployer": ("管理员", False, True),
            "deployer_admin": ("管理员", False, True),
        }
        for role, (label, is_deployer, is_admin) in expected.items():
            pid = pid_for(authz_env, role)
            status, _, payload = authz_env["roles"][role].json(
                "GET", "/api/v1/session?profile=%s" % pid
            )
            assert status == 200
            identity = payload["identity"]
            assert (identity["role_label"], identity["is_deployer"], identity["is_admin"]) == (
                label,
                is_deployer,
                is_admin,
            ), role


class TestMatrix:
    @pytest.mark.parametrize("role,action,expected", CASES)
    def test_probe_matrix(self, authz_env, role, action, expected):
        pid = pid_for(authz_env, role)
        before = write_target(pid, PROBE_TARGET, PROBE_ORIGINAL)
        assert before != hashlib.sha256(PROBE_WRITTEN).hexdigest()

        status, _, payload = authz_env["roles"][role].json(
            "POST", "/api/v1/p/%s/probe/%s" % (pid, action), body={}
        )

        if expected == 201:
            assert status == 201, (role, action, payload)
            assert payload["action"] == action
            # 允许的请求真的写了文件（否则 403 的「文件不变」断言没有意义）
            assert sha256_of(pid, PROBE_TARGET) == hashlib.sha256(PROBE_WRITTEN).hexdigest()
        else:
            assert status == 403, (role, action, payload)
            assert payload["error"]["code"] == "FORBIDDEN"
            assert payload["error"]["detail"]["action"] == action
            # 403 时对应文件 sha256 不变（§16.3）
            assert sha256_of(pid, PROBE_TARGET) == before

    def test_reads_allowed_for_all_roles(self, authz_env):
        for role in roles(authz_env["server"]):
            pid = pid_for(authz_env, role)
            write_target(pid, "readable.xml", b"<r/>")
            status, _, _ = authz_env["roles"][role].request("GET", "/api/v1/p/%s/files" % pid)
            assert status == 200, role


class TestCsrfHeader:
    def test_missing_header_is_forbidden_even_for_deployer_admin(self, authz_env):
        pid = authz_env["p2"]
        before = write_target(pid, PROBE_TARGET, PROBE_ORIGINAL)
        status, _, payload = authz_env["roles"]["deployer_admin"].json(
            "POST",
            "/api/v1/p/%s/probe/settings_write" % pid,
            body={},
            with_csrf=False,
        )
        assert status == 403
        assert payload["error"]["code"] == "FORBIDDEN"
        assert sha256_of(pid, PROBE_TARGET) == before

    def test_wrong_header_value_is_forbidden(self, authz_env):
        pid = authz_env["p1"]
        status, _, _ = authz_env["roles"]["deployer"].request(
            "POST",
            "/api/v1/p/%s/probe/settings_write" % pid,
            body={},
            headers={"X-Requested-With": "something-else"},
        )
        assert status == 403


class TestRevocation:
    def test_removing_admin_takes_effect_on_next_request(self, authz_env):
        pid = authz_env["p1"]
        write_target(pid, PROBE_TARGET, PROBE_ORIGINAL)
        admin = authz_env["roles"]["admin"]

        status, _, _ = admin.json("POST", "/api/v1/p/%s/probe/review" % pid, body={})
        assert status == 201

        storage.set_active_profile(pid)
        assert storage.remove_admin_user(ADMIN_NAME) is True

        write_target(pid, PROBE_TARGET, PROBE_ORIGINAL)
        status, _, payload = admin.json("POST", "/api/v1/p/%s/probe/review" % pid, body={})
        assert status == 403
        assert payload["error"]["code"] == "FORBIDDEN"
        assert sha256_of(pid, PROBE_TARGET) == hashlib.sha256(PROBE_ORIGINAL).hexdigest()


class TestEntryPoints:
    def test_action_matrix_snapshot(self):
        """§4.2 的矩阵快照：档位改动必须是有意识的（不允许顺手放宽）。

        配置域 6 个动作是 M5-07（INH-1941）有意新增的：读=全部角色，
        其余 5 个一律管理员，**不引入回环特权**（决策 D4）。
        """
        assert authz.ACTIONS == {
            "read": authz.ANY,
            "temp_upload": authz.ANY,
            "favorites_write": authz.ANY,
            "remarks_submit": authz.ANY,
            "dl_compute": authz.ANY,
            "upload_persist": authz.ADMIN,
            "settings_write": authz.ADMIN,
            "tasks_run": authz.ADMIN,
            "profiles_write": authz.ADMIN,
            "review": authz.ADMIN,
            "delete_file": authz.ADMIN,
            "config_read": authz.ANY,
            "config_registry_write": authz.ADMIN,
            "config_tracking_admin": authz.ADMIN,
            "config_change_submit": authz.ADMIN,
            "config_change_approve": authz.ADMIN,
            "config_artifact_generate": authz.ADMIN,
        }

    def test_unknown_action_fails_fast(self):
        """写错 action 是编码错误：不能伪装成 403（否则权限漏洞会被当成正常拒绝）。"""
        with pytest.raises(ValueError):
            authz.action_level("no_such_action")

    def test_require_decorator(self):
        from app.services.auth import Actor
        from app.services.context import RequestContext

        @authz.require("review")
        def protected(ctx):
            return "ok"

        admin = RequestContext(
            ip="10.0.0.3",
            actor=Actor(ip="10.0.0.3", name="管理员乙", registered=True, is_deployer=False, is_admin=True),
        )
        deployer = RequestContext(
            ip="127.0.0.1",
            actor=Actor(ip="127.0.0.1", name="127.0.0.1", registered=False, is_deployer=True, is_admin=False),
        )
        assert protected(admin) == "ok"
        with pytest.raises(AppError) as excinfo:
            protected(deployer)
        assert excinfo.value.code == FORBIDDEN

    def test_authorize_without_actor_is_denied(self):
        from app.services.context import RequestContext

        with pytest.raises(AppError) as excinfo:
            authz.authorize(RequestContext(ip="10.0.0.1", actor=None), "settings_write")
        assert excinfo.value.code == FORBIDDEN
        # 但 any 级别的操作不需要身份
        authz.authorize(RequestContext(ip="10.0.0.1", actor=None), "read")

    def test_as_role_rejects_unknown_role(self, authz_env):
        with pytest.raises(KeyError):
            as_role(authz_env["server"], "superuser")
