"""权限隔离模块。

基于客户端 IP 识别部署者，并结合 IP 对应表与管理员名单识别当前用户身份。
提供 Actor 身份抽象与显式授权检查，防止访客直接调用管理写入口。
"""

import logging
import socket
from dataclasses import dataclass, field
from functools import wraps
from typing import Callable, Optional, Set

from nicegui import app, context

logger = logging.getLogger(__name__)

# 部署者 IP 缓存
_deployer_ips = None


# ===================== Actor 身份抽象 =====================


@dataclass(frozen=True)
class Actor:
    """操作者身份。

    Attributes:
        ip: 真实 peer IP 地址
        display_name: 显示名称（人员名或 IP）
        is_deployer: 是否为部署者（本机地址）
        profile_admin_scope: 有管理权限的 profile_id 集合，
            含 "*" 表示可管理所有 profile
    """
    ip: str
    display_name: Optional[str]
    is_deployer: bool
    profile_admin_scope: Set[str] = field(default_factory=set)

    @property
    def is_system(self) -> bool:
        """是否为系统后台 actor"""
        return self.ip == "system"

    @property
    def is_guest(self) -> bool:
        """是否为访客（非部署者且非系统）"""
        return not self.is_deployer and not self.is_system


# 系统后台任务专用 actor
SYSTEM_ACTOR = Actor(
    ip="system",
    display_name="System",
    is_deployer=True,
    profile_admin_scope=set(),  # system 可以操作所有 profile（特殊判断）
)


# ===================== 授权异常 =====================


class AuthorizationError(Exception):
    """授权失败。

    当访客尝试写操作、管理操作非 admin、或 profile 不存在时抛出。
    """
    pass


# ===================== 原有 IP/身份识别函数 =====================


def get_deployer_ips() -> set:
    """获取部署者 IP 地址集合（本机地址）"""
    global _deployer_ips
    if _deployer_ips is None:
        ips = {"127.0.0.1", "::1"}
        try:
            hostname = socket.gethostname()
            local_ips = socket.getaddrinfo(hostname, None)
            for addr_info in local_ips:
                ip = addr_info[4][0]
                ips.add(ip)
        except Exception as e:
            logger.warning("获取本机 IP 失败: %s", e)
        _deployer_ips = ips
        logger.info("部署者 IP: %s", ips)
    return _deployer_ips


def is_deployer() -> bool:
    """判断当前客户端是否为部署者"""
    try:
        client = context.client
        # 获取客户端 IP
        if hasattr(client, "address"):
            ip = client.address
        else:
            # 从 request 获取
            request = getattr(client, "request", None)
            if request:
                ip = request.client.host if request.client else "unknown"
            else:
                ip = "unknown"

        deployer_ips = get_deployer_ips()
        return ip in deployer_ips
    except Exception as e:
        logger.debug("判断部署者权限时出错: %s", e)
        return False


def get_client_ip() -> str:
    """获取当前客户端 IP"""
    try:
        client = context.client
        if hasattr(client, "address"):
            return client.address
        request = getattr(client, "request", None)
        if request and request.client:
            return request.client.host
    except Exception:
        pass
    return "unknown"


def get_current_user_name() -> str:
    """根据当前客户端 IP 解析人员名称。"""
    from app.core import storage

    ip = get_client_ip()
    name = storage.resolve_person_by_ip(ip)
    return name or ip


def is_admin() -> bool:
    """判断当前客户端是否属于管理员名单。"""
    from app.core import storage

    try:
        return storage.is_admin_user(get_current_user_name())
    except Exception as e:
        logger.debug("判断管理员权限时出错: %s", e)
        return False


def get_identity_info() -> dict:
    """获取当前访问身份信息。"""
    ip = get_client_ip()
    name = get_current_user_name()
    admin = is_admin()
    deployer = is_deployer()
    return {
        "ip": ip,
        "name": name,
        "is_admin": admin,
        "is_deployer": deployer,
        "role_label": "管理员" if admin else "游客",
    }


def require_deployer(func: Callable) -> Callable:
    """装饰器：仅部署者可执行的写操作"""

    @wraps(func)
    async def wrapper(*args, **kwargs):
        if not is_deployer():
            logger.warning("非部署者尝试执行写操作: %s", func.__name__)
            return {"error": "权限不足：仅部署者可执行此操作"}
        return await func(*args, **kwargs)

    return wrapper


# ===================== 身份解析与授权 =====================


def _get_real_peer_ip(request) -> str:
    """获取真实 peer IP。

    只在配置了 trusted proxy 时才接受 X-Forwarded-For；
    否则仅使用 request.client.host，防止反向代理部署时
    把所有客户端误认成 deployer。
    """
    if request is None:
        return "unknown"

    # 检查是否配置了 trusted proxy
    # 通过 app.storage 或环境变量判断
    trusted_proxy = False
    try:
        # 支持通过环境变量配置 trusted proxy 地址
        import os
        trusted_proxy_ips = os.environ.get("MCHECKER_TRUSTED_PROXY", "").strip()
        if trusted_proxy_ips:
            # 支持逗号分隔多个 trusted proxy IP
            proxy_list = {ip.strip() for ip in trusted_proxy_ips.split(",") if ip.strip()}
            client_host = getattr(getattr(request, "client", None), "host", "")
            if client_host in proxy_list:
                trusted_proxy = True
    except Exception:
        pass

    if trusted_proxy:
        # 从 X-Forwarded-For 获取真实客户端 IP（取第一个，即最原始的客户端）
        xff = request.headers.get("x-forwarded-for", "")
        if xff:
            # X-Forwarded-For: client, proxy1, proxy2
            first_ip = xff.split(",")[0].strip()
            if first_ip:
                return first_ip

    # 默认使用直连 IP
    if hasattr(request, "client") and request.client:
        return request.client.host or "unknown"
    return "unknown"


def _is_deployer_ip(ip: str) -> bool:
    """检查 IP 是否为部署者（本机地址）。"""
    return ip in get_deployer_ips()


def resolve_actor(request=None, peer_ip: Optional[str] = None) -> Actor:
    """解析操作者身份。

    只使用真实 peer address；只有配置了 trusted proxy 才接受
    forwarded client identity。不靠隐藏按钮授权。

    Args:
        request: HTTP request 对象（可选）
        peer_ip: 直接指定 peer IP（可选，优先于 request）

    Returns:
        Actor 身份对象
    """
    from app.core import storage

    if peer_ip is None and request is not None:
        peer_ip = _get_real_peer_ip(request)

    if peer_ip is None:
        try:
            peer_ip = get_client_ip()
        except Exception:
            peer_ip = "unknown"

    # 查询 IP mapping 解析人员名称
    try:
        person = storage.resolve_person_by_ip(peer_ip)
    except Exception:
        person = ""

    is_deployer_flag = _is_deployer_ip(peer_ip)

    # 确定 admin scope
    admin_scope: Set[str] = set()
    if person:
        try:
            if storage.is_admin_user(person):
                admin_scope = {"*"}  # 管理所有 profile
        except Exception:
            pass

    display_name = person or peer_ip

    return Actor(
        ip=peer_ip,
        display_name=display_name,
        is_deployer=is_deployer_flag,
        profile_admin_scope=admin_scope,
    )


def authorize(actor: Actor, action: str, profile_id: str) -> bool:
    """授权检查。

    在提交时重新授权，不只依赖按钮显示。
    显式 default 不再读浏览器机型。
    未知 profile 返回明确错误。

    Args:
        actor: 操作者身份
        action: 操作类型 ("read", "write", "delete", "review", "manage")
        profile_id: 目标 profile ID

    Returns:
        True 如果授权通过

    Raises:
        AuthorizationError: 授权失败时抛出
    """
    from app.core import storage

    # 检查 profile 存在
    if not storage.profile_exists(profile_id):
        raise AuthorizationError(f"Unknown profile: {profile_id!r}")

    # system actor 放行所有操作
    if actor.is_system:
        return True

    # 读操作放行
    if action == "read":
        return True

    # 写/删/审阅/管理操作：需要 deployer 或 system
    if action in ("write", "delete", "review", "manage"):
        if not actor.is_deployer:
            raise AuthorizationError(
                f"Guest cannot {action}: actor={actor.display_name!r}"
            )

        # manage 操作额外需要 admin scope
        if action == "manage":
            if "*" not in actor.profile_admin_scope:
                if profile_id not in actor.profile_admin_scope:
                    raise AuthorizationError(
                        f"Not admin for profile: {profile_id!r}"
                    )

        return True

    # 未知 action 类型，保守拒绝
    raise AuthorizationError(f"Unknown action: {action!r}")
