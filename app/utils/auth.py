"""权限隔离模块。

基于客户端 IP 识别部署者，并结合 IP 对应表与管理员名单识别当前用户身份。
"""

import logging
import socket
from functools import wraps
from typing import Callable

from nicegui import app, context

logger = logging.getLogger(__name__)

# 部署者 IP 缓存
_deployer_ips = None


def get_deployer_ips() -> set:
    """获取部署者 IP 地址集合"""
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
