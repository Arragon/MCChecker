"""权限隔离模块

基于客户端 IP 判断部署者权限。
部署者（服务端本机）可修改服务端配置，其他用户仅可查看和临时上传。
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


def require_deployer(func: Callable) -> Callable:
    """装饰器：仅部署者可执行的写操作"""

    @wraps(func)
    async def wrapper(*args, **kwargs):
        if not is_deployer():
            logger.warning("非部署者尝试执行写操作: %s", func.__name__)
            return {"error": "权限不足：仅部署者可执行此操作"}
        return await func(*args, **kwargs)

    return wrapper
