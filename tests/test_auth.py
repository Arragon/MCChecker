"""授权与身份识别测试。

覆盖 Actor dataclass、resolve_actor、authorize、
以及 get_active_profile 的显式 default 行为。
"""

import os
import pytest

from app.utils.auth import (
    Actor,
    AuthorizationError,
    SYSTEM_ACTOR,
    authorize,
    get_deployer_ips,
    resolve_actor,
    _get_real_peer_ip,
    _is_deployer_ip,
)


# ===================== Actor 基础测试 =====================


class TestActor:
    def test_guest_actor(self):
        """访客：非 deployer、非 system"""
        guest = Actor("1.2.3.4", "guest", False, set())
        assert guest.is_guest
        assert not guest.is_deployer
        assert not guest.is_system

    def test_deployer_actor(self):
        """部署者：is_deployer=True"""
        deployer = Actor("127.0.0.1", "deployer", True, set())
        assert deployer.is_deployer
        assert not deployer.is_guest
        assert not deployer.is_system

    def test_system_actor(self):
        """系统 actor：is_system=True，is_deployer=True"""
        assert SYSTEM_ACTOR.is_system
        assert SYSTEM_ACTOR.is_deployer
        assert not SYSTEM_ACTOR.is_guest

    def test_actor_frozen(self):
        """Actor 不可变"""
        actor = Actor("1.2.3.4", "test", False, set())
        with pytest.raises(AttributeError):
            actor.ip = "5.6.7.8"

    def test_admin_scope(self):
        """admin scope 包含特定 profile"""
        admin = Actor("1.2.3.4", "admin", True, {"p1", "p2"})
        assert "p1" in admin.profile_admin_scope
        assert "p3" not in admin.profile_admin_scope

    def test_global_admin_scope(self):
        """admin scope 含 * 表示全局管理"""
        admin = Actor("1.2.3.4", "admin", True, {"*"})
        assert "*" in admin.profile_admin_scope


# ===================== authorize 测试 =====================


class TestAuthorize:
    def _ensure_profile(self, monkeypatch, exists: bool = True):
        """mock storage.profile_exists"""
        from app.core import storage
        monkeypatch.setattr(storage, "profile_exists", lambda pid: exists)

    def test_guest_write_denied(self, monkeypatch):
        """访客写操作被拒绝"""
        self._ensure_profile(monkeypatch)
        guest = Actor("1.2.3.4", "guest", False, set())
        with pytest.raises(AuthorizationError, match="Guest"):
            authorize(guest, "write", "default")

    def test_guest_delete_denied(self, monkeypatch):
        """访客删除操作被拒绝"""
        self._ensure_profile(monkeypatch)
        guest = Actor("1.2.3.4", "guest", False, set())
        with pytest.raises(AuthorizationError, match="Guest"):
            authorize(guest, "delete", "default")

    def test_guest_review_denied(self, monkeypatch):
        """访客审阅操作被拒绝"""
        self._ensure_profile(monkeypatch)
        guest = Actor("1.2.3.4", "guest", False, set())
        with pytest.raises(AuthorizationError, match="Guest"):
            authorize(guest, "review", "default")

    def test_deployer_write_allowed(self, monkeypatch):
        """部署者写操作通过"""
        self._ensure_profile(monkeypatch)
        deployer = Actor("127.0.0.1", "deployer", True, set())
        assert authorize(deployer, "write", "default") is True

    def test_deployer_delete_allowed(self, monkeypatch):
        """部署者删除操作通过"""
        self._ensure_profile(monkeypatch)
        deployer = Actor("127.0.0.1", "deployer", True, set())
        assert authorize(deployer, "delete", "default") is True

    def test_unknown_profile_error(self, monkeypatch):
        """未知 profile 抛出 AuthorizationError"""
        self._ensure_profile(monkeypatch, exists=False)
        deployer = Actor("127.0.0.1", "deployer", True, set())
        with pytest.raises(AuthorizationError, match="Unknown profile"):
            authorize(deployer, "write", "nonexistent")

    def test_manage_requires_admin(self, monkeypatch):
        """管理操作需要 admin scope"""
        self._ensure_profile(monkeypatch)
        deployer = Actor("127.0.0.1", "deployer", True, set())  # 不是 admin
        with pytest.raises(AuthorizationError, match="admin"):
            authorize(deployer, "manage", "default")

    def test_admin_manage_allowed(self, monkeypatch):
        """admin 管理操作通过"""
        self._ensure_profile(monkeypatch)
        admin = Actor("127.0.0.1", "admin", True, {"*"})
        assert authorize(admin, "manage", "default") is True

    def test_scoped_admin_manage_allowed(self, monkeypatch):
        """限定 profile 的 admin 管理对应 profile 通过"""
        self._ensure_profile(monkeypatch)
        admin = Actor("127.0.0.1", "admin", True, {"p1"})
        assert authorize(admin, "manage", "p1") is True

    def test_scoped_admin_manage_other_denied(self, monkeypatch):
        """限定 profile 的 admin 管理其他 profile 被拒绝"""
        self._ensure_profile(monkeypatch)
        admin = Actor("127.0.0.1", "admin", True, {"p1"})
        with pytest.raises(AuthorizationError, match="admin"):
            authorize(admin, "manage", "p2")

    def test_system_actor_all_allowed(self, monkeypatch):
        """system actor 所有操作通过"""
        self._ensure_profile(monkeypatch)
        assert authorize(SYSTEM_ACTOR, "write", "default") is True
        assert authorize(SYSTEM_ACTOR, "manage", "default") is True
        assert authorize(SYSTEM_ACTOR, "delete", "any-profile") is True

    def test_read_allowed_for_guest(self, monkeypatch):
        """访客读操作通过"""
        self._ensure_profile(monkeypatch)
        guest = Actor("1.2.3.4", "guest", False, set())
        assert authorize(guest, "read", "default") is True

    def test_unknown_action_denied(self, monkeypatch):
        """未知 action 被拒绝"""
        self._ensure_profile(monkeypatch)
        deployer = Actor("127.0.0.1", "deployer", True, set())
        with pytest.raises(AuthorizationError, match="Unknown action"):
            authorize(deployer, "nuke", "default")


# ===================== resolve_actor 测试 =====================


class TestResolveActor:
    def test_resolve_with_explicit_peer_ip(self, monkeypatch):
        """显式指定 peer_ip 时直接使用"""
        from app.core import storage
        monkeypatch.setattr(storage, "resolve_person_by_ip", lambda ip: "")
        actor = resolve_actor(peer_ip="10.0.0.1")
        assert actor.ip == "10.0.0.1"
        assert actor.display_name == "10.0.0.1"  # 无名时显示 IP

    def test_resolve_with_known_person(self, monkeypatch):
        """IP 对应表有记录时显示人名"""
        from app.core import storage
        monkeypatch.setattr(storage, "resolve_person_by_ip", lambda ip: "张三")
        monkeypatch.setattr(storage, "is_admin_user", lambda name: False)
        actor = resolve_actor(peer_ip="10.0.0.1")
        assert actor.display_name == "张三"

    def test_resolve_admin_scope(self, monkeypatch):
        """管理员有全局 admin scope"""
        from app.core import storage
        monkeypatch.setattr(storage, "resolve_person_by_ip", lambda ip: "管理员A")
        monkeypatch.setattr(storage, "is_admin_user", lambda name: True)
        actor = resolve_actor(peer_ip="10.0.0.1")
        assert "*" in actor.profile_admin_scope

    def test_resolve_deployer_ip(self, monkeypatch):
        """本机 IP 识别为 deployer"""
        from app.core import storage
        monkeypatch.setattr(storage, "resolve_person_by_ip", lambda ip: "")
        actor = resolve_actor(peer_ip="127.0.0.1")
        assert actor.is_deployer is True

    def test_resolve_remote_not_deployer(self, monkeypatch):
        """远程 IP 不是 deployer"""
        from app.core import storage
        monkeypatch.setattr(storage, "resolve_person_by_ip", lambda ip: "")
        actor = resolve_actor(peer_ip="203.0.113.5")
        assert actor.is_deployer is False


# ===================== _get_real_peer_ip 测试 =====================


class TestGetRealPeerIp:
    def test_no_request(self):
        """无 request 返回 unknown"""
        assert _get_real_peer_ip(None) == "unknown"

    def test_direct_connection(self):
        """无 trusted proxy 时使用直连 IP"""

        class FakeClient:
            host = "192.168.1.100"

        class FakeRequest:
            client = FakeClient()
            headers = {}

        assert _get_real_peer_ip(FakeRequest()) == "192.168.1.100"

    def test_xff_ignored_without_trusted_proxy(self, monkeypatch):
        """无 trusted proxy 配置时忽略 X-Forwarded-For"""
        monkeypatch.delenv("MCHECKER_TRUSTED_PROXY", raising=False)

        class FakeClient:
            host = "10.0.0.1"

        class FakeRequest:
            client = FakeClient()
            headers = {"x-forwarded-for": "1.2.3.4, 10.0.0.1"}

        # 即使有 XFF，也只用直连 IP
        assert _get_real_peer_ip(FakeRequest()) == "10.0.0.1"

    def test_xff_accepted_with_trusted_proxy(self, monkeypatch):
        """配置 trusted proxy 后接受 X-Forwarded-For"""
        monkeypatch.setenv("MCHECKER_TRUSTED_PROXY", "10.0.0.1")

        class FakeClient:
            host = "10.0.0.1"  # proxy 地址

        class FakeRequest:
            client = FakeClient()
            headers = {"x-forwarded-for": "1.2.3.4, 10.0.0.1"}

        assert _get_real_peer_ip(FakeRequest()) == "1.2.3.4"


# ===================== get_active_profile 显式传递测试 =====================


class TestExplicitProfile:
    def test_explicit_profile_returns_given(self, monkeypatch, isolated_data_env):
        """显式指定 profile 直接返回"""
        from app.core import storage
        # 创建 profile 目录使其存在
        profile_dir = os.path.join(storage.PROFILES_DIR, "my-profile")
        os.makedirs(profile_dir, exist_ok=True)
        result = storage.get_active_profile("my-profile")
        assert result == "my-profile"

    def test_explicit_unknown_profile_raises(self, monkeypatch, isolated_data_env):
        """显式指定未知 profile 抛出 ValueError"""
        from app.core import storage
        with pytest.raises(ValueError, match="Unknown profile"):
            storage.get_active_profile("nonexistent-profile")

    def test_explicit_default_not_read_browser(self, monkeypatch, isolated_data_env):
        """显式指定 default 不读浏览器状态"""
        from app.core import storage

        # 模拟浏览器存储了其他机型
        class FakeUserStorage(dict):
            pass

        class FakeApp:
            class storage:
                user = FakeUserStorage({"device_model": "other-model"})

        monkeypatch.setattr("nicegui.app", FakeApp())

        # 显式指定 default，应返回 default，不读浏览器
        result = storage.get_active_profile("default")
        assert result == "default"

    def test_no_explicit_falls_back_to_contextvar(self, isolated_data_env):
        """无显式指定时回落 ContextVar"""
        from app.core import storage
        storage.set_active_profile("ctx-profile")
        # 创建目录使 profile_exists 返回 True
        os.makedirs(
            os.path.join(storage.PROFILES_DIR, "ctx-profile"),
            exist_ok=True,
        )
        result = storage.get_active_profile()
        assert result == "ctx-profile"

    def test_explicit_default_always_works(self, isolated_data_env):
        """显式 default 始终可用"""
        from app.core import storage
        result = storage.get_active_profile("default")
        assert result == "default"


# ===================== profile_exists 测试 =====================


class TestProfileExists:
    def test_default_always_exists(self, isolated_data_env):
        """default profile 始终存在"""
        from app.core import storage
        assert storage.profile_exists("default") is True

    def test_existing_dir(self, isolated_data_env):
        """目录存在时返回 True"""
        from app.core import storage
        profile_dir = os.path.join(storage.PROFILES_DIR, "test-prof")
        os.makedirs(profile_dir, exist_ok=True)
        assert storage.profile_exists("test-prof") is True

    def test_nonexistent(self, isolated_data_env):
        """不存在时返回 False"""
        from app.core import storage
        assert storage.profile_exists("no-such-profile") is False


# ===================== is_known_deployer_ip 测试 =====================


class TestIsKnownDeployerIp:
    def test_localhost_is_deployer(self):
        """本机 IP 是 deployer"""
        from app.core import storage
        assert storage.is_known_deployer_ip("127.0.0.1") is True

    def test_remote_not_deployer(self):
        """远程 IP 不是 deployer"""
        from app.core import storage
        assert storage.is_known_deployer_ip("203.0.113.5") is False
