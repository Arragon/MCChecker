"""HTTP 层（规范 §3.2）。

* `router.py`——路由表与路径参数
* `api_*.py`——按领域分组的 handler（§7）
* `http.py`——MCServer、Handler、静态文件、gzip、错误映射、单实例锁

分层规则（§3.3）：server 只解析请求、调用 services/core、序列化响应，
**不得**在 handler 里直接写文件。
"""

from __future__ import annotations
