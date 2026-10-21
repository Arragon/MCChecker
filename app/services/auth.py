"""身份解析（§4.1；T-07；F24、C18）。

IP 识别不是强认证（§11.1）：仅适用于可信内网。`X-Forwarded-For` 只有在对端
位于 `MCHECKER_TRUSTED_PROXIES` 时才被采信；未配置受信代理却收到该头时只
WARNING 一次（§11.1），其余请求静默忽略。
"""

from __future__ import annotations

import ipaddress
import logging
import secrets
import socket
from dataclasses import dataclass
from typing import Optional

from ..core import storage

logger = logging.getLogger(__name__)

#: 会话 cookie 名与有效期（§7.2：1 年、HttpOnly、SameSite=Lax）。
SESSION_COOKIE = "mc_cid"
SESSION_COOKIE_MAX_AGE = 31536000

_deployer_ips: Optional[frozenset] = None
_WARNED_UNTRUSTED_XFF = False


@dataclass(frozen=True)
class Actor:
    """一次请求识别出的访问者（§4.1）。"""

    ip: str
    name: str  # IP 对应表中的姓名；未登记时等于 ip
    registered: bool
    is_deployer: bool
    is_admin: bool
    is_system: bool = False  # 调度任务

    @property
    def role_label(self) -> str:
        """角色标签（§4.1）：本机访问不再显示「游客」。"""
        if self.is_admin:
            return "管理员"
        return "成员" if self.registered else "访客"


def deployer_ips() -> frozenset:
    """部署者 = 127.0.0.1、::1 与本机所有接口地址（启动时计算并缓存，§4.1）。"""
    global _deployer_ips
    if _deployer_ips is None:
        ips = {"127.0.0.1", "::1"}
        try:
            for info in socket.getaddrinfo(socket.gethostname(), None):
                ips.add(normalize_ip(info[4][0]))
        except OSError as exc:
            logger.warning("获取本机地址失败，部署者仅限回环地址：%s", exc)
        _deployer_ips = frozenset(ip for ip in ips if ip)
        logger.info("部署者地址：%s", sorted(_deployer_ips))
    return _deployer_ips


def reset_deployer_cache() -> None:
    """清空本机地址缓存（测试钩子）。"""
    global _deployer_ips
    _deployer_ips = None


def normalize_ip(raw: object) -> str:
    """规范化 IP：`::ffff:10.1.2.3` → `10.1.2.3`；无法解析时返回空串。"""
    text = str(raw or "").strip().strip("[]")
    if not text:
        return ""
    try:
        parsed = ipaddress.ip_address(text)
    except ValueError:
        return ""
    if isinstance(parsed, ipaddress.IPv6Address) and parsed.ipv4_mapped is not None:
        return str(parsed.ipv4_mapped)
    return str(parsed)


def client_ip(handler) -> str:
    """取客户端 IP：直连用对端地址；仅当对端在受信代理中才读 `X-Forwarded-For`。"""
    peer = normalize_ip(handler.client_address[0]) or str(handler.client_address[0])
    trusted = {normalize_ip(value) for value in handler.settings.trusted_proxies}
    forwarded = handler.headers.get("X-Forwarded-For", "")
    if peer not in trusted:
        if forwarded:
            _warn_untrusted_xff_once()
        return peer
    for part in forwarded.split(","):
        candidate = normalize_ip(part)
        if candidate:
            return candidate
    return peer


def _warn_untrusted_xff_once() -> None:
    global _WARNED_UNTRUSTED_XFF
    if not _WARNED_UNTRUSTED_XFF:
        _WARNED_UNTRUSTED_XFF = True
        logger.warning("收到 X-Forwarded-For 但未配置 MCHECKER_TRUSTED_PROXIES，已忽略该头（§11.1）")


def resolve_actor(ip: str, profile_id: str) -> Actor:
    """解析身份（§4.1）。IP 对应表与管理员名单全机型共用，不随机型变化。

    `profile_id` 仍由调用方传入（路由都带机型），身份解析不再读机型目录。
    """
    del profile_id
    registered_name = storage.resolve_person_by_ip(ip)
    name = registered_name or ip
    return Actor(
        ip=ip,
        name=name,
        registered=bool(registered_name),
        is_deployer=False,
        is_admin=storage.is_admin_ip(ip),
    )


def new_session_cid() -> str:
    """新会话 ID：24 字节随机数（§7.2）。"""
    return secrets.token_urlsafe(24)


def session_cookie(cid: str) -> str:
    """`Set-Cookie` 值（§7.2）：`Path=/; Max-Age=31536000; HttpOnly; SameSite=Lax`。"""
    return "%s=%s; Path=/; Max-Age=%d; HttpOnly; SameSite=Lax" % (
        SESSION_COOKIE,
        cid,
        SESSION_COOKIE_MAX_AGE,
    )


def cookie_cid(cookie_header: Optional[str]) -> Optional[str]:
    """从 `Cookie` 头取 `mc_cid`；缺失或明显不合法（空白/超长）返回 None。"""
    if not cookie_header:
        return None
    for part in cookie_header.split(";"):
        name, separator, value = part.partition("=")
        if separator and name.strip() == SESSION_COOKIE:
            value = value.strip()
            if value and len(value) <= 128:
                return value
            return None
    return None
