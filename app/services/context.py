"""请求上下文（§3.4 第 4 步）。

M1-03 定义结构并在 HTTP 层统一构造；`authorize(ctx, action)` 的权限矩阵
强制在 M1-04 落地（届时 handler 内的写操作全部走它）。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from .auth import Actor


@dataclass(frozen=True)
class RequestContext:
    """一次请求的已解析上下文。

    * `ip`——已按 §4.1 解析的客户端 IP（所有接口都有）；
    * `cid`——`mc_cid` cookie，None 表示尚未下发（由 /session 首访下发）；
    * `profile_id`——路径中的 `{pid}`；`/session` 这类无 pid 接口为空串；
    * `actor`——有 `{pid}` 时已按该机型解析；`/session` 由 handler 按查询
      参数（含回落 default 的特例）自行解析。
    """

    ip: str
    cid: Optional[str] = None
    profile_id: str = ""
    actor: Optional[Actor] = None
