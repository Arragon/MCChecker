"""HTTP 骨架：静态资源、`/config.js`、JSON API 与错误封套（§3.1、§3.4、§3.5、§8.9）。

标准库 `ThreadingHTTPServer`。本模块只做「解析请求 → 调 handler → 序列化响应」，
handler 不得直接写文件（§3.3）。业务接口从 M1 起挂到 `router.py`。

单实例：`SingleInstanceLock` 以独占方式创建 `data/.mcchecker.lock`（H4、§3.5）。
"""

from __future__ import annotations

import gzip
import json
import logging
import os
import time
import urllib.parse
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict, Optional

from .. import __version__
from ..config import Settings
from ..core import device_models, fsutil, storage
from ..core.errors import (
    FORBIDDEN,
    INVALID_INPUT,
    NOT_FOUND,
    STORAGE_FAILURE,
    TOO_LARGE,
    AppError,
)
from ..services import auth
from ..services.context import RequestContext
from .router import Response, Router

logger = logging.getLogger(__name__)

#: §3.4 第 3 步：JSON 请求体上限 1 MB；原文上传用 `settings.upload_max_bytes`。
JSON_BODY_LIMIT = 1024 * 1024

#: §3.4 第 7 步：>1 KB 且客户端接受 gzip 才压缩。
GZIP_MIN_BYTES = 1024

#: §8.9：生产环境不提供 mock 演示与测试页（避免误用 mock）。
BLOCKED_PREFIXES = ("/assets/js/mock/", "/tests/")
BLOCKED_PATHS = ("/template.html",)

#: 可能带请求体的方法；GET/HEAD/DELETE 不要求 `Content-Length`。
BODY_METHODS = ("POST", "PUT", "PATCH")

#: §11.4：状态变更接口必须带自定义头（跨站表单无法设置自定义头）。
#: 其余安全头策略（Content-Type 白名单、CSP 等）在 M4-02。
CSRF_HEADER = "X-Requested-With"
CSRF_VALUE = "MCChecker"
WRITE_METHODS = ("POST", "PUT", "PATCH", "DELETE")

#: §8.9 第 2 条要求的确切内容。
CONFIG_JS = "window.MC=window.MC||{};window.MC.config={mock:false};"

LOCK_FILENAME = ".mcchecker.lock"

_CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".mjs": "application/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".map": "application/json; charset=utf-8",
    ".txt": "text/plain; charset=utf-8",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".ico": "image/x-icon",
    ".woff": "font/woff",
    ".woff2": "font/woff2",
}

_COMPRESSIBLE_PREFIXES = (
    "text/",
    "application/javascript",
    "application/json",
    "image/svg+xml",
)


def _process_alive(pid: int) -> bool:
    """判断 pid 是否仍是活进程。

    Windows 上**不能**用 `os.kill(pid, 0)`：那里会走 `TerminateProcess`，等于
    把另一个 MCChecker 真的杀掉。改用 `OpenProcess` 探测。
    """
    if pid <= 0:
        return False
    if os.name == "nt":  # pragma: no cover - 本机（macOS）无法覆盖
        import ctypes

        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(0x00100000, False, pid)  # SYNCHRONIZE
        if not handle:
            return False
        kernel32.CloseHandle(handle)
        return True
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


class SingleInstanceLock:
    """`data/.mcchecker.lock`：单进程独占（§3.5、H4）。

    以 `O_CREAT|O_EXCL` 独占创建（POSIX 与 Windows 都是原子的），写入
    pid / 端口 / 启动时间。锁文件存在且记录的进程仍存活 → 拒绝启动；进程已不在
    （上次崩溃/强杀留下的残留锁）→ 接管。进程退出时删除。
    """

    def __init__(self, data_dir: str, port: int) -> None:
        self.data_dir = os.path.abspath(data_dir)
        self.path = os.path.join(self.data_dir, LOCK_FILENAME)
        self.port = port
        self._held = False

    def acquire(self) -> None:
        os.makedirs(self.data_dir, exist_ok=True)
        payload = {
            "pid": os.getpid(),
            "port": self.port,
            "started_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }
        try:
            fd = self._create_exclusive()
        except FileExistsError:
            existing = self._read_existing()
            pid = existing.get("pid")
            if isinstance(pid, int) and _process_alive(pid):
                raise RuntimeError(
                    "MCChecker 已在运行：pid=%s、端口=%s、启动于 %s。"
                    "确认是残留锁时删除 %s 后重试" % (pid, existing.get("port"), existing.get("started_at"), self.path)
                )
            logger.warning("发现残留锁文件（pid=%s 已不在），接管 %s", pid, self.path)
            try:
                os.unlink(self.path)
            except OSError:
                pass
            fd = self._create_exclusive()

        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(payload, fh, ensure_ascii=False)
        except BaseException:
            try:
                os.unlink(self.path)
            except OSError:
                pass
            raise
        self._held = True

    def _create_exclusive(self) -> int:
        return os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)

    def _read_existing(self) -> Dict[str, Any]:
        try:
            with open(self.path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, ValueError):
            return {}
        return data if isinstance(data, dict) else {}

    def release(self) -> None:
        if not self._held:
            return
        try:
            os.unlink(self.path)
        except FileNotFoundError:
            pass
        except OSError as exc:
            logger.warning("删除锁文件失败：%s", exc)
        self._held = False

    def __enter__(self) -> "SingleInstanceLock":
        self.acquire()
        return self

    def __exit__(self, *_exc: Any) -> bool:
        self.release()
        return False


class MCRequestHandler(BaseHTTPRequestHandler):
    """单请求处理：静态资源 → `/config.js` → 路由 → 错误封套。"""

    server_version = "MCChecker/" + __version__
    protocol_version = "HTTP/1.1"
    #: §3.5：单个连接的读超时。
    timeout = 60

    # 以下两处从 MCServer 取（`self.server` 由 http.server 装配）。
    @property
    def settings(self) -> Settings:
        return self.server.settings  # type: ignore[attr-defined]

    @property
    def router(self) -> Router:
        return self.server.router  # type: ignore[attr-defined]

    # ===================== 分发 =====================

    def do_GET(self) -> None:  # noqa: N802 - http.server 约定
        self._dispatch("GET")

    def do_POST(self) -> None:  # noqa: N802
        self._dispatch("POST")

    def do_PUT(self) -> None:  # noqa: N802
        self._dispatch("PUT")

    def do_PATCH(self) -> None:  # noqa: N802
        self._dispatch("PATCH")

    def do_DELETE(self) -> None:  # noqa: N802
        self._dispatch("DELETE")

    def _dispatch(self, method: str) -> None:
        started = time.time()
        split = urllib.parse.urlsplit(self.path)
        try:
            if method in WRITE_METHODS and self.headers.get(CSRF_HEADER) != CSRF_VALUE:
                # 未读 body 即拒绝：连接里可能残留未读数据，必须关闭连接
                self.close_connection = True
                raise AppError(
                    FORBIDDEN,
                    "状态变更请求必须带 %s: %s" % (CSRF_HEADER, CSRF_VALUE),
                )
            path = urllib.parse.unquote(split.path)
            body = self._read_body() if method in BODY_METHODS else None
            self._handle(method, path, split.query, body)
        except AppError as exc:
            self._send_json(exc.http_status, {"error": exc.to_payload()})
        except Exception:  # 未知异常：日志留堆栈，响应不含堆栈（§3.4 第 8 步）
            logger.exception("未处理异常：%s %s", method, split.path)
            self._send_json(
                500, {"error": {"code": STORAGE_FAILURE, "message": "服务器内部错误", "detail": {}}}
            )
        finally:
            logger.debug(
                "%s %s %dms", method, split.path, int((time.time() - started) * 1000)
            )

    def _handle(self, method: str, path: str, query: str, body: Optional[bytes]) -> None:
        if path in BLOCKED_PATHS or path.startswith(BLOCKED_PREFIXES):
            raise AppError(NOT_FOUND, "资源不存在")
        if path == "/config.js":
            return self._send_bytes(
                200,
                CONFIG_JS.encode("utf-8"),
                "application/javascript; charset=utf-8",
                extra={"Cache-Control": "no-cache"},
            )
        if path in ("/", "/index.html"):
            return self._serve_static("/index.html")
        if path.startswith("/assets/"):
            return self._serve_static(path)

        handler, params = self.router.match(method, path)
        if handler is None:
            raise AppError(NOT_FOUND, "接口不存在")
        query_params = urllib.parse.parse_qs(query, keep_blank_values=True)

        # §3.4 第 4、5、6 步：解析客户端 IP 与会话 cookie，组装 RequestContext；
        # 路径含 {pid} 时先校验机型存在（不回落 default），再在
        # `use_profile(pid)` 上下文里按该机型解析身份并调用 handler。
        ip = auth.client_ip(self)
        cid = auth.cookie_cid(self.headers.get("Cookie"))
        pid = params.get("pid") if params else None
        if pid is not None and not device_models.exists(pid):
            raise AppError(NOT_FOUND, "机型不存在")

        # §3.4 第 4 步：每个请求都重新解析身份（撤权后下一次请求即 403）；
        # 无 {pid} 的全局接口按 default 机型解析。
        actor = auth.resolve_actor(ip, pid or device_models.DEFAULT_MODEL_ID)
        ctx = RequestContext(
            ip=ip,
            cid=cid,
            profile_id=pid or "",
            actor=actor,
            # C26：X-Filename 先按百分号解码，文件名合法性留给服务层用 validate_filename 判。
            filename=urllib.parse.unquote(self.headers.get("X-Filename") or "") or None,
        )
        if pid is None:
            self._send_response(handler(ctx, params, query_params, body))
            return
        with storage.use_profile(pid):
            self._send_response(handler(ctx, params, query_params, body))

    # ===================== 请求体 =====================

    def _read_body(self) -> bytes:
        raw = self.headers.get("Content-Length")
        if raw is None:
            # 没有长度就无法安全读取（不支持 chunked），按超限处理（§3.4 第 3 步）。
            self.close_connection = True
            raise AppError(TOO_LARGE, "请求体必须带 Content-Length")
        try:
            length = int(raw)
        except (TypeError, ValueError):
            self.close_connection = True
            raise AppError(INVALID_INPUT, "Content-Length 不是整数")
        if length < 0:
            self.close_connection = True
            raise AppError(INVALID_INPUT, "Content-Length 不是整数")
        limit = self._body_limit()
        if length > limit:
            # 未读取的 body 不能留在连接里，否则下一个请求会被错位解析。
            self.close_connection = True
            raise AppError(TOO_LARGE, "请求体超过上限（%d 字节）" % limit)
        if length == 0:
            return b""
        return self.rfile.read(length)

    def _body_limit(self) -> int:
        content_type = self.headers.get("Content-Type", "")
        if content_type.split(";")[0].strip().lower() == "application/octet-stream":
            return self.settings.upload_max_bytes
        return JSON_BODY_LIMIT

    # ===================== 静态资源 =====================

    def _serve_static(self, path: str) -> None:
        try:
            target = fsutil.resolve_within(self.settings.web_dir, path.lstrip("/"))
        except AppError:
            # 越界（含符号链接）一律当作不存在，不暴露目录结构。
            raise AppError(NOT_FOUND, "资源不存在")
        if not os.path.isfile(target):
            raise AppError(NOT_FOUND, "资源不存在")

        stat = os.stat(target)
        etag = '"%x-%x"' % (int(stat.st_mtime_ns), stat.st_size)
        headers = {"ETag": etag, "Cache-Control": "no-cache"}
        if self.headers.get("If-None-Match") == etag:
            return self._send_bytes(304, b"", None, extra=headers)
        with open(target, "rb") as fh:
            data = fh.read()
        self._send_bytes(200, data, _content_type(target), extra=headers)

    # ===================== 响应 =====================

    def _send_response(self, response: Response) -> None:
        """发送 handler 的响应；`ETag` 命中 `If-None-Match` 时降级为 304（§6.3）。"""
        headers = dict(response.headers)
        etag = headers.get("ETag")
        if etag and self.headers.get("If-None-Match") == etag:
            self._send_bytes(304, b"", None, extra=headers)
            return
        if response.body is not None:
            self._send_bytes(response.status, response.body, response.content_type, extra=headers)
            return
        body = json.dumps(response.payload, ensure_ascii=False).encode("utf-8")
        self._send_bytes(
            response.status,
            body,
            response.content_type or "application/json; charset=utf-8",
            extra=headers,
        )

    def _send_json(self, status: int, payload: Any) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self._send_bytes(status, body, "application/json; charset=utf-8")

    def _send_bytes(
        self,
        status: int,
        body: bytes,
        content_type: Optional[str],
        extra: Optional[Dict[str, str]] = None,
    ) -> None:
        headers = dict(extra or {})
        compressible = content_type is not None and content_type.startswith(_COMPRESSIBLE_PREFIXES)
        if status != 304 and compressible and len(body) > GZIP_MIN_BYTES and self._accepts_gzip():
            body = gzip.compress(body, compresslevel=6)
            headers["Content-Encoding"] = "gzip"
        if compressible:
            headers["Vary"] = "Accept-Encoding"
        if content_type:
            headers["Content-Type"] = content_type
        if status != 304:
            headers["Content-Length"] = str(len(body))

        self.send_response(status)
        for name, value in headers.items():
            self.send_header(name, value)
        self.end_headers()
        if body and status != 304:
            self.wfile.write(body)

    def _accepts_gzip(self) -> bool:
        return "gzip" in self.headers.get("Accept-Encoding", "").lower()

    def log_message(self, fmt: str, *args: Any) -> None:
        # 访问日志的正式格式（含 request_id）在 T-25/M4-03；这里先降噪到 DEBUG。
        logger.debug("%s %s", self.address_string(), fmt % args)

    def log_error(self, fmt: str, *args: Any) -> None:
        logger.warning("%s %s", self.address_string(), fmt % args)


def _content_type(path: str) -> str:
    _, ext = os.path.splitext(path)
    return _CONTENT_TYPES.get(ext.lower(), "application/octet-stream")


class MCServer(ThreadingHTTPServer):
    """收敛并发与连接行为（§3.5）。"""

    daemon_threads = True
    request_queue_size = 128

    def __init__(self, settings: Settings, router: Router) -> None:
        self.settings = settings
        self.router = router
        super().__init__((settings.host, settings.port), MCRequestHandler)
