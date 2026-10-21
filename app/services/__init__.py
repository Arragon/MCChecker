"""应用服务层（规范 §3.2、§3.3）。

* 每个公开函数组合 `core` 与 `storage`，不碰 HTTP；
* 授权入口 `authorize(ctx, action)` 与 `RequestContext` 在 M1-03/M1-04 接入，
  在此之前服务函数显式接收 `profile_id`。
"""

from __future__ import annotations
