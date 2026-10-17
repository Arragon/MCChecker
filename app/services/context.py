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
    """一次请求的已解析上下文（§3.4 第 4 步）。

    * `ip`——已按 §4.1 解析的客户端 IP；
    * `cid`——`mc_cid` cookie，None 表示尚未下发（由 /session 首访下发）；
    * `profile_id`——路径中的 `{pid}`；`/session` 这类无 pid 接口为空串；
    * `actor`——**每个请求都解析**：有 `{pid}` 时按该机型解析，否则按 `default`
      机型解析（全局写接口如后续的 `/profiles`、`/tasks` 靠它做权限判定，
      身份切换后下一次请求即生效）。
    """

    ip: str
    cid: Optional[str] = None
    profile_id: str = ""
    actor: Optional[Actor] = None
    #: 上传类请求的原始文件名（`X-Filename`，已在 HTTP 层做过百分号解码，C26）。
    #: 合法性由各服务用 `fsutil.validate_filename` 判定——上下文不做策略。
    filename: Optional[str] = None
