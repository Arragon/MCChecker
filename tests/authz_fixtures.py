"""四角色测试夹具（M1-04；§4.2、§16.3）。

后续每张写卡在自己的 API 测试里复用：

```python
from authz_fixtures import make_server, seed_roles, as_role

with make_server() as server:
    p1 = seed_roles("p1")                    # visitor / member / admin / deployer
    caller = as_role(server, "admin")
    status, headers, body = caller.request("POST", "/api/v1/p/%s/xxx" % p1, body={...})
```

角色怎么来的（IP 识别，§4.1）：

| 角色 | 客户端 IP | 机型内的登记 |
|---|---|---|
| `visitor` | 10.0.0.1（经受信代理注入 XFF） | 未登记 |
| `member` | 10.0.0.2 | 登记为姓名，不在管理员名单 |
| `admin` | 10.0.0.3 | 登记为姓名且在管理员名单 |
| `deployer` | 127.0.0.1（真实对端，不带 XFF） | 未登记 → 部署者但非管理员 |
| `deployer_admin` | ::1（经受信代理注入 XFF） | 登记为姓名且在管理员名单（`seed_roles(..., local_admin=True)`） |

人员表全机型共用，不能再靠「只在另一个机型登记 127.0.0.1」拆出两个本机身份。
`::1` 同样算部署者，所以 `deployer_admin` 走这个地址，`127.0.0.1` 保持未登记。
"""

from __future__ import annotations

import contextlib
import http.client
import json
import os
import threading
from typing import Dict, List, Optional

WEB_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app", "web")

XFF_VISITOR = "10.0.0.1"
XFF_MEMBER = "10.0.0.2"
XFF_ADMIN = "10.0.0.3"
MEMBER_NAME = "成员甲"
ADMIN_NAME = "管理员乙"
LOCAL_ADMIN_NAME = "本机管理员"

WRITE_METHODS = ("POST", "PUT", "PATCH", "DELETE")


@contextlib.contextmanager
def make_server(trusted_proxies=("127.0.0.1",), router=None):
    """线程内随机端口启动真实 server；XFF 只有在受信代理（默认 127.0.0.1）时才被采信。"""
    from app.core import storage
    from app.config import Settings
    from app.server.http import MCServer
    from app.server.router import build_router

    settings = Settings(
        host="127.0.0.1",
        port=0,
        data_dir=storage.DATA_DIR,  # conftest 已把数据根重定向到临时目录
        web_dir=WEB_DIR,
        trusted_proxies=tuple(trusted_proxies),
    )
    srv = MCServer(settings, (router or build_router)(settings))
    thread = threading.Thread(target=srv.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
    thread.start()
    try:
        yield srv
    finally:
        srv.shutdown()
        srv.server_close()
        thread.join(timeout=5)


def seed_roles(profile_name: str = "p1", local_admin: bool = False) -> str:
    """建机型并在其中登记成员/管理员两个地址；返回 pid。

    `local_admin=True` 时把部署者地址 `::1` 登记为管理员（用于 `deployer_admin` 角色）。
    直连的 `127.0.0.1` 保持未登记，这样两种本机身份可以同时存在。
    """
    from app.core import device_models, storage

    storage.set_active_profile("default")
    pid = device_models.add_model(profile_name)
    storage.set_active_profile(pid)
    storage.upsert_ip_mapping(XFF_MEMBER, MEMBER_NAME)
    storage.upsert_ip_mapping(XFF_ADMIN, ADMIN_NAME)
    storage.upsert_admin_user(ADMIN_NAME)
    if local_admin:
        # ::1 也是部署者地址，且和直连的 127.0.0.1 不是同一条人员记录。
        storage.upsert_ip_mapping("::1", LOCAL_ADMIN_NAME)
        storage.upsert_admin_user(LOCAL_ADMIN_NAME)
    return pid


def http_request(server, method: str, path: str, body=None, headers: Optional[Dict[str, str]] = None):
    """发一个请求；`body` 为 dict/list 时按 JSON 序列化。返回 (status, headers, body)。"""
    merged = dict(headers or {})
    payload = None
    if isinstance(body, (dict, list)):
        payload = json.dumps(body, ensure_ascii=False).encode("utf-8")
        merged.setdefault("Content-Type", "application/json; charset=utf-8")
    elif isinstance(body, bytes):
        payload = body

    conn = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=10)
    try:
        conn.request(method, path, body=payload, headers=merged)
        response = conn.getresponse()
        return response.status, dict(response.getheaders()), response.read()
    finally:
        conn.close()


class RoleCaller:
    """某个角色的调用器：自动注入该角色的 XFF 与写请求的 `X-Requested-With`。"""

    def __init__(self, server, role: str, xff: Optional[str] = None) -> None:
        self.server = server
        self.role = role
        self.xff = xff

    def request(
        self,
        method: str,
        path: str,
        body=None,
        headers: Optional[Dict[str, str]] = None,
        with_csrf: bool = True,
    ):
        merged = dict(headers or {})
        if self.xff and "X-Forwarded-For" not in merged:
            merged["X-Forwarded-For"] = self.xff
        if method.upper() in WRITE_METHODS and with_csrf:
            merged.setdefault("X-Requested-With", "MCChecker")
        return http_request(self.server, method, path, body=body, headers=merged)

    def json(self, method: str, path: str, body=None, **kwargs):
        status, headers, raw = self.request(method, path, body=body, **kwargs)
        return status, headers, json.loads(raw.decode("utf-8")) if raw else None


_ROLE_XFF = {
    "visitor": XFF_VISITOR,
    "member": XFF_MEMBER,
    "admin": XFF_ADMIN,
    "deployer": None,
    "deployer_admin": "::1",
}


def roles(server) -> Dict[str, RoleCaller]:
    """五种角色的调用器（卡片要求 `as_role(visitor|member|admin|deployer)`）。"""
    return {role: RoleCaller(server, role, xff) for role, xff in _ROLE_XFF.items()}


def as_role(server, role: str) -> RoleCaller:
    try:
        return roles(server)[role]
    except KeyError:
        raise KeyError("未知角色：%s（可选 %s）" % (role, sorted(_ROLE_XFF)))


def sha256_of(pid: str, name: str) -> str:
    """读机型 configs/ 下的文件内容 sha256（不走 HTTP，用于「403 时文件不变」断言）。"""
    import hashlib

    from app.core import storage

    with storage.use_profile(pid):
        with open(storage.get_config_path(name), "rb") as fh:
            return hashlib.sha256(fh.read()).hexdigest()


def write_target(pid: str, name: str, content: bytes) -> str:
    """准备好被测文件并返回其 sha256。"""
    import hashlib

    from app.core import storage

    storage.set_active_profile(pid)
    storage.save_config_file(name, content)
    return hashlib.sha256(content).hexdigest()
