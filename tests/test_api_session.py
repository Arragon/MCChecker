"""会话与身份（INH-1869 / M1-03；§4.1、§4.3、§7.2；F24、C18）。

线程内随机端口起真实 server；受信代理场景用 `trusted_proxies=("127.0.0.1",)` +
`X-Forwarded-For` 模拟「经代理访问的其他 IP」。
"""

from __future__ import annotations

import contextlib
import http.client
import json
import os
import threading

import pytest

from app.config import Settings
from app.services import auth

WEB_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app", "web")


@contextlib.contextmanager
def running_server(trusted_proxies=()):
    from app.core import storage
    from app.server.http import MCServer
    from app.server.router import build_router

    settings = Settings(
        host="127.0.0.1",
        port=0,
        data_dir=storage.DATA_DIR,  # conftest 已把数据根重定向到临时目录
        web_dir=WEB_DIR,
        trusted_proxies=tuple(trusted_proxies),
    )
    srv = MCServer(settings, build_router(settings))
    thread = threading.Thread(target=srv.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
    thread.start()
    try:
        yield srv
    finally:
        srv.shutdown()
        srv.server_close()
        thread.join(timeout=5)


def get(server, path, headers=None):
    conn = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=10)
    try:
        conn.request("GET", path, headers=headers or {})
        response = conn.getresponse()
        return response.status, dict(response.getheaders()), response.read()
    finally:
        conn.close()


def session(server, path="/api/v1/session", headers=None):
    status, headers, body = get(server, path, headers)
    assert status == 200, body
    return json.loads(body.decode("utf-8")), headers


def seed_models(*names):
    """建机型，返回 (ids…)。"""
    from app.core import device_models, storage

    storage.set_active_profile("default")
    ids = []
    for name in names:
        ids.append(device_models.add_model(name))
    return ids


class TestLocalDeployer:
    def test_loopback_is_deployer(self):
        with running_server() as server:
            payload, _ = session(server)
        identity = payload["identity"]
        assert identity["ip"] == "127.0.0.1"
        assert identity["is_deployer"] is True
        assert "部署者" in identity["role_label"]
        assert identity["role_label"] != "访客"

    def test_local_access_without_registration_stays_deployer(self):
        """本机访问不再显示「游客」（C18）。"""
        with running_server() as server:
            payload, _ = session(server)
        assert payload["identity"]["registered"] is False
        assert payload["identity"]["role_label"] == "部署者"

    def test_deployer_and_admin_label(self):
        from app.core import storage

        (p1,) = seed_models("p1")
        storage.set_active_profile(p1)
        storage.upsert_ip_mapping("127.0.0.1", "本机管理员")
        storage.upsert_admin_user("本机管理员")

        with running_server() as server:
            payload, _ = session(server, "/api/v1/session?profile=%s" % p1)
        assert payload["identity"]["role_label"] == "部署者 · 管理员"


class TestForwardedFor:
    def test_untrusted_proxy_header_is_ignored(self):
        with running_server() as server:
            payload, _ = session(server, headers={"X-Forwarded-For": "10.9.9.9"})
        assert payload["identity"]["ip"] == "127.0.0.1"
        assert payload["identity"]["is_deployer"] is True

    def test_trusted_proxy_header_is_honored(self):
        with running_server(trusted_proxies=("127.0.0.1",)) as server:
            payload, _ = session(server, headers={"X-Forwarded-For": "10.1.2.3"})
        assert payload["identity"]["ip"] == "10.1.2.3"
        assert payload["identity"]["is_deployer"] is False
        assert payload["identity"]["registered"] is False
        assert payload["identity"]["role_label"] == "访客"

    def test_trusted_proxy_leftmost_valid_ip_wins(self):
        with running_server(trusted_proxies=("127.0.0.1",)) as server:
            payload, _ = session(
                server, headers={"X-Forwarded-For": "10.1.2.3, 10.9.9.9"}
            )
        assert payload["identity"]["ip"] == "10.1.2.3"

    def test_trusted_proxy_invalid_forwarded_falls_back_to_peer(self):
        with running_server(trusted_proxies=("127.0.0.1",)) as server:
            payload, _ = session(server, headers={"X-Forwarded-For": "not-an-ip"})
        assert payload["identity"]["ip"] == "127.0.0.1"

    def test_ipv4_mapped_ipv6_is_normalized(self):
        assert auth.normalize_ip("::ffff:10.1.2.3") == "10.1.2.3"
        assert auth.normalize_ip("[::ffff:10.1.2.3]") == "10.1.2.3"
        assert auth.normalize_ip("10.1.2.3") == "10.1.2.3"
        assert auth.normalize_ip("bad-input") == ""
        # 规范化后命中部署者名单
        assert auth.normalize_ip("::ffff:127.0.0.1") in auth.deployer_ips()


class TestPerProfileIdentity:
    def test_same_ip_has_the_same_role_on_every_profile(self):
        from app.core import storage

        (p1, p2) = seed_models("p1", "p2")
        storage.set_active_profile(p1)
        storage.upsert_ip_mapping("10.1.2.3", "张三")
        storage.upsert_admin_user("张三")

        with running_server(trusted_proxies=("127.0.0.1",)) as server:
            on_p1, _ = session(server, "/api/v1/session?profile=%s" % p1,
                               headers={"X-Forwarded-For": "10.1.2.3"})
            on_p2, _ = session(server, "/api/v1/session?profile=%s" % p2,
                               headers={"X-Forwarded-For": "10.1.2.3"})

        assert on_p1["identity"]["name"] == "张三"
        assert on_p1["identity"]["registered"] is True
        assert on_p1["identity"]["is_admin"] is True
        assert on_p1["identity"]["role_label"] == "管理员"
        assert on_p2["identity"]["name"] == "张三"
        assert on_p2["identity"]["registered"] is True
        assert on_p2["identity"]["is_admin"] is True
        assert on_p2["identity"]["role_label"] == "管理员"
        assert not os.path.exists(os.path.join(storage.get_profile_data_dir(p1), "ip_mapping.json"))
        assert os.path.isfile(os.path.join(storage.DATA_DIR, "ip_mapping.json"))

    def test_registered_member_without_admin(self):
        from app.core import storage

        (p1,) = seed_models("p1")
        storage.set_active_profile(p1)
        storage.upsert_ip_mapping("10.1.2.3", "李四")

        with running_server(trusted_proxies=("127.0.0.1",)) as server:
            payload, _ = session(server, "/api/v1/session?profile=%s" % p1,
                                 headers={"X-Forwarded-For": "10.1.2.3"})
        assert payload["identity"]["role_label"] == "成员"
        assert payload["identity"]["is_admin"] is False
        assert payload["identity"]["is_deployer"] is False


class TestSessionCookie:
    def test_first_visit_sets_cookie(self):
        with running_server() as server:
            payload, headers = session(server)
        cookie = headers["Set-Cookie"]
        assert cookie.startswith("mc_cid=")
        assert "Path=/" in cookie
        assert "Max-Age=31536000" in cookie
        assert "HttpOnly" in cookie
        assert "SameSite=Lax" in cookie

    def test_cookie_not_reissued_when_present(self):
        with running_server() as server:
            _, headers = session(server)
            issued = headers["Set-Cookie"]
            value = issued.split(";", 1)[0].split("=", 1)[1]
            payload, headers2 = session(
                server, headers={"Cookie": "mc_cid=" + value}
            )
        assert "Set-Cookie" not in headers2
        assert len(value) >= 32  # token_urlsafe(24)

    def test_two_fresh_clients_get_different_cids(self):
        with running_server() as server:
            _, first = session(server)
            _, second = session(server)
        cid1 = first["Set-Cookie"].split(";", 1)[0]
        cid2 = second["Set-Cookie"].split(";", 1)[0]
        assert cid1 != cid2

    def test_broken_cookie_is_replaced(self):
        with running_server() as server:
            payload, headers = session(server, headers={"Cookie": "mc_cid=   "})
        assert "Set-Cookie" in headers


class TestProfileFallback:
    def test_unknown_profile_falls_back_to_default_only_here(self):
        from app.core import device_models

        seed_models("p1")
        with running_server() as server:
            payload, _ = session(server, "/api/v1/session?profile=nope")
            assert payload["profile"]["id"] == device_models.DEFAULT_MODEL_ID

            # 其它 {pid} 接口不回落 default（§3.4 第 5 步）
            status, _, body = get(server, "/api/v1/p/nope/files")
        assert status == 404
        assert json.loads(body.decode("utf-8"))["error"]["code"] == "NOT_FOUND"

    def test_illegal_profile_param_falls_back(self):
        with running_server() as server:
            payload, _ = session(server, "/api/v1/session?profile=..%2Fx")
        assert payload["profile"]["id"] == "default"

    def test_profiles_list_contains_all_models(self):
        (p1,) = seed_models("p1")
        with running_server() as server:
            payload, _ = session(server, "/api/v1/session?profile=p1")
        ids = [m["id"] for m in payload["profiles"]]
        assert ids[0] == "default"
        assert p1 in ids
        assert payload["profile"]["id"] == p1

    def test_limits_match_settings(self):
        with running_server() as server:
            payload, _ = session(server)
        limits = payload["limits"]
        assert limits["upload_max_bytes"] == 50 * 1024 * 1024
        assert limits["tab_limit"] == 15
        assert limits["temp_ttl_hours"] == 24
        assert payload["server"]["version"]


@pytest.mark.parametrize(
    "cookie,expected",
    [
        ("mc_cid=abc; other=1", "abc"),
        ("a=1; mc_cid=x-y", "x-y"),
        ("  mc_cid=  spaced  ", "spaced"),
        ("other=1", None),
        ("", None),
        (None, None),
        ("mc_cid=" + "x" * 300, None),
    ],
)
def test_cookie_parsing(cookie, expected):
    assert auth.cookie_cid(cookie) == expected
