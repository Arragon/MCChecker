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
import re
import time
import urllib.parse
import uuid
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict, Optional

from .. import __version__
from ..config import Settings
from ..core import device_models, fsutil, log_context, storage
from ..core.errors import (
    FORBIDDEN,
    INVALID_INPUT,
    NOT_FOUND,
    STORAGE_FAILURE,
    TOO_LARGE,
    AppError,
)
from ..services import auth, tasks
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
CSRF_HEADER = "X-Requested-With"
CSRF_VALUE = "MCChecker"
WRITE_METHODS = ("POST", "PUT", "PATCH", "DELETE")

#: §11.3 全站响应头，逐字按规范（`default-src 'self'`，不得放宽成 `*`）。
#: 生产页只有外链脚本（`index.html` 里全是 `<script src=…>`，无内联 `on*=`），所以
#: `script-src 'self'` 不需要 `unsafe-inline`；`style-src` 的 `'unsafe-inline'` 是
#: `MC.util.h()` 写 `style` 属性所必需的，规范也照此写明。图标是内联 SVG sprite
#: （`icons.js:69` 注入的是**固定常量**标记，不含运行期数据），字体走系统本地字体、
#: 不发网络请求，因此 `default-src 'self'` 的兜底够用。
SECURITY_CSP = (
    "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; "
    "script-src 'self'; connect-src 'self'; frame-ancestors 'none'"
)

SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "same-origin",
    "Content-Security-Policy": SECURITY_CSP,
}

#: §16.8 第 3 条：路径参数里的编码分隔符 / 空字节。
#:
#: `_dispatch` 会对整个请求路径做一次 `unquote`，所以 `..%2F..%2Fetc%2Fpasswd` 解码后
#: 就成了 `../../etc/passwd`：路由的 `[^/]+` 不再匹配，请求以 **404「接口不存在」**收场，
#: 而规范要求的是 **400/403**。危险本身不存在（越界的一直是 `validate_filename` +
#: `resolve_within` 两关），但状态码把「被安全层拒绝」说成了「没有这个接口」，
#: M4-08 的 §16.8 门禁按规范字面判。所以在分发之前按 `validate_filename` 的同一口径
#: （`/` 与 `\` 本就是文件名非法字符）提前拒掉。
#:
#: 检查**解码后**的那一份是为了盖住二次编码：`%252F` 解一层成字面 `%2F`，
#: `%25%32%30` 解一层成 `%20`；这两种都不在 raw 的三只 token 里，但二次解码后会变成
#: 另一个路径段。`_LITERAL_PERCENT_ESCAPE_RE` 在解码后的那份上兜住整族 `%XX`。
#: 代价：文件名里真含字面 `%2f`/`%5c`/`%00` 的配置无法经 URL 寻址（`api.seg()`
#: 用 `encodeURIComponent`，正常路径不会走到这一步）。
_ENCODED_UNSAFE = ("%2f", "%5c", "%00")

#: `router.match` 会对捕获段**再解一次**，所以任何二次编码都会让删除端拿到另一个名字：
#: `测试%20甲` 的路径段是 `测试%2520甲`，解两层后变成 `测试 甲`，可能误删同名单里的
#: 另一个人（M3-13 评审 F1）。两种写法都要拦：
#: ① raw 里的 `%25XX`（`%2520`）；
#: ② 第一次 `unquote` 之后仍在的 `%XX`（`%25%32%30` → `%20`，`%25%32%35%32%30` → `%2520`）。
_DOUBLE_ENCODED_UNSAFE_RE = re.compile(r"%25[0-9A-Fa-f]{2}", re.IGNORECASE)
_LITERAL_PERCENT_ESCAPE_RE = re.compile(r"%[0-9A-Fa-f]{2}")

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
        request_id = uuid.uuid4().hex[:12]
        token = log_context.set_request_id(request_id)
        self._response_status = 500
        self._access_ip = auth.client_ip(self)
        self._access_pid = "-"
        try:
            if method in WRITE_METHODS and self.headers.get(CSRF_HEADER) != CSRF_VALUE:
                # 未读 body 即拒绝：连接里可能残留未读数据，必须关闭连接
                self.close_connection = True
                raise AppError(
                    FORBIDDEN,
                    "状态变更请求必须带 %s: %s" % (CSRF_HEADER, CSRF_VALUE),
                )
            path = urllib.parse.unquote(split.path)
            _reject_encoded_separators(split.path, path)
            body = self._read_body() if method in BODY_METHODS else None
            self._handle(method, path, split.query, body)
        except AppError as exc:
            self._send_json(exc.http_status, {"error": _client_error_payload(exc)})
        except Exception:  # 未知异常：日志留堆栈，响应不含堆栈（§3.4 第 8 步）
            logger.exception("未处理异常：%s %s", method, split.path)
            self._send_json(
                500, {"error": {"code": STORAGE_FAILURE, "message": "服务器内部错误", "detail": {}}}
            )
        finally:
            duration_ms = int((time.time() - started) * 1000)
            # §13 访问日志：method path status duration_ms actor_ip profile request_id；
            # 只写 path，不写 query（query 里可能有 URL 凭据）。
            logger.info(
                "%s %s %d %dms ip=%s pid=%s",
                method,
                split.path,
                getattr(self, "_response_status", 500),
                duration_ms,
                getattr(self, "_access_ip", "-"),
                getattr(self, "_access_pid", "-"),
            )
            log_context.reset_request_id(token)

    def _handle(self, method: str, path: str, query: str, body: Optional[bytes]) -> None:
        if path in BLOCKED_PATHS or path.startswith(BLOCKED_PREFIXES):
            raise AppError(NOT_FOUND, "资源不存在")
        # §7.12：旧 NiceGUI 静态资源不再提供（`/static/*` 一律 404，不落到路由表）。
        if path == "/static" or path.startswith("/static/"):
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
        self._access_pid = pid or "-"
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
            # 条件请求头（M3-11 `PUT /dl/{item}` 的 If-Match）要能被 handler 读到；
            # 头名大小写不敏感，这里统一归一成小写（`self.headers` 本身也是不敏感的）。
            headers={name.lower(): value for name, value in self.headers.items()},
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
        self._response_status = status
        headers = dict(extra or {})
        # §11.3：安全头在**唯一的发送出口**上统一加，304 与错误封套也不例外
        # （handler 显式给了同名头时以 handler 的为准，所以用 setdefault）。
        for name, value in SECURITY_HEADERS.items():
            headers.setdefault(name, value)
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

    def send_error(self, code, message=None, explain=None):  # noqa: N802 - 基类同名方法
        """§11.3：把标准库的 `send_error` 旁路也接回统一出口（含全站安全头）。

        501「未实现的方法」（OPTIONS/TRACE/…）、400「请求行畸形」、414「URI 过长」、
        505「版本不支持」都发生在 `_dispatch` **之前**，走的是 `BaseHTTPRequestHandler`
        自己的 `send_response`，所以这些响应原先一条安全头都没有（实测 OPTIONS 只回
        `Server/Date/Connection/Content-Type: text/html;charset=utf-8` 加一段 HTML 错误页）。
        「全站统一」要包括它们，否则「安全头在 HTTP 层统一加」这句话就是假的。

        同时把响应体换成本仓统一的 JSON 错误封套（§3.4）：这段 HTML 错误页会把请求行
        回显进 `text/html`，而我们已经不再声明页面可以在任何帧里被嵌（`frame-ancestors 'none'`）。
        保留基类的 `Connection: close` 与「连接已断时不抛」两个语义。
        """
        try:
            short, _long = self.responses[code]
        except KeyError:
            short = "服务器错误"
        # `explain` 是基类为那段 HTML 错误页准备的长文案，JSON 封套里不收（§11.5）。
        summary = message or short
        self.log_error("code %d, message %s", code, summary)
        self.close_connection = True
        body = json.dumps(
            {"error": {"code": _transport_error_code(code), "message": summary, "detail": {}}},
            ensure_ascii=False,
        ).encode("utf-8")
        try:
            self._send_bytes(code, body, "application/json; charset=utf-8",
                             extra={"Connection": "close"})
        except OSError:
            # 与基类一致：畸形请求常常在半途断开，这里没有可以补救的对象。
            pass

    def log_message(self, fmt: str, *args: Any) -> None:
        # 访问日志的正式格式（含 request_id）在 T-25/M4-03；这里先降噪到 DEBUG。
        logger.debug("%s %s", self.address_string(), fmt % args)

    def log_error(self, fmt: str, *args: Any) -> None:
        logger.warning("%s %s", self.address_string(), fmt % args)


def _content_type(path: str) -> str:
    _, ext = os.path.splitext(path)
    return _CONTENT_TYPES.get(ext.lower(), "application/octet-stream")


def _reject_encoded_separators(raw_path: str, decoded_path: str) -> None:
    """§16.8 第 3 条：编码的分隔符/空字节一律 400，不让它退化成「404 接口不存在」。

    另外拦住二次编码：raw 里的 `%25XX`，以及第一次 `unquote` 之后仍在的 `%XX`
    （`%25%32%30` 这类百分号转义的十六进制位）。`router.match` 会再解一次，放过去
    就会让写进名单的名字在删除端变成另一个名字（M3-13 评审 F1）。
    """
    if _DOUBLE_ENCODED_UNSAFE_RE.search(raw_path) or _LITERAL_PERCENT_ESCAPE_RE.search(
        decoded_path
    ):
        raise AppError(INVALID_INPUT, "请求路径含二次编码的字符")
    haystacks = (raw_path.lower(), decoded_path.lower())
    for token in _ENCODED_UNSAFE:
        if any(token in hay for hay in haystacks):
            raise AppError(INVALID_INPUT, "请求路径含编码的分隔符或空字节")


def _transport_error_code(status: int) -> str:
    """`send_error` 旁路的错误码（§7.1 那套 `code` 口径，但触发方是标准库不是业务）。

    本应用实际只会走到 400（请求行畸形）、414、501、505；403/404 一并给出映射，
    是因为任何「绕过 `_dispatch` 的错误」将来也应当能从状态码读出封套里的 `code`。
    """
    if status == 403:
        return FORBIDDEN
    if status == 404:
        return NOT_FOUND
    if status in (413, 414):
        return TOO_LARGE
    # 501（方法没实现）与 505（版本不支持）跟 4xx 同源：坏的是请求，服务器没有真的故障，
    # 所以不许它挂 `STORAGE_FAILURE`——那个码在前端按「服务器内部错误」口径呈现。
    return INVALID_INPUT if status in (501, 505) or status < 500 else STORAGE_FAILURE


def _redact_absolute_path(value: Any) -> Any:
    """§11.5：绝对路径不出服务器。只认「整个值就是一个绝对路径」这一种形状。

    刻意不做子串替换：`detail.path` 也可能是收藏定位符（`root/R/params/param_01`），
    那不是磁盘路径，改它会把前端用来定位的键改坏。
    """
    if isinstance(value, str) and os.path.isabs(value):
        return os.path.basename(value)
    return value


def _client_error_payload(exc: AppError) -> Dict[str, Any]:
    """送给客户端的错误封套。

    `CorruptDataError` 们（`errors.py:80`，由 `storage.py:1745`、`dltool.py:81` 等以绝对
    路径抛出）会把磁盘布局写进 `detail.path`。那些文件不在本卡可改范围，而且将来还会
    有新的抛点是常态，所以在这里——响应离开进程前的最后一步——统一收敛。服务端日志
    不受影响（`logger.exception` 仍拿到完整信息）。
    """
    payload = exc.to_payload()
    payload["message"] = _redact_absolute_path(payload.get("message"))
    detail = payload.get("detail")
    if isinstance(detail, dict):
        payload["detail"] = {k: _redact_absolute_path(v) for k, v in detail.items()}
    return payload


class MCServer(ThreadingHTTPServer):
    """收敛并发与连接行为（§3.5）。

    `runner`（卡 6 的后台任务队列）一旦注入，`shutdown()` 就**先排空在途任务再关
    HTTP**（R9）。次序的理由不是「工作线程非 daemon、进程不退出」——`ThreadPoolExecutor`
    的 worker 从**提交方**继承 daemon 位（`threading.py:828`），而 `submit` 正是这批
    HTTP 处理线程调的，所以现网的 `mctask_N` 就是 daemon；它们照样会被等到，因为
    `concurrent.futures.thread` 注册了 `_python_exit`，解释器退出前会把它们 join 掉。
    真正的问题是**停止序列比 join 先走完**：`serve_forever` 一退出，主线程一路往下
    删单实例锁，而 worker 还在写 `data/`。所以次序仍是「先 `runner.shutdown()` 排空、
    后关 HTTP」，且删锁前必须按**线程名**数人（谓词是 `tasks.worker_threads()`，
    见 `run.stop_and_release`，C1）。这个次序放在服务器自己身上，是为了让「任何调用方
    关掉这个 server」都自动满足它；`run.ordered_stop` 里还显式排了同一次序
    （`runner.shutdown()` 幂等，两次调用不会重复 join，见 R10）。

    `daemon_threads = True` 只管 HTTP 处理线程本身；它既不决定 worker 的 daemon 位
    （那随提交方继承），也不是「还有没有人在写 `data/`」的判据——判据是线程名前缀。
    """

    daemon_threads = True
    request_queue_size = 128

    def __init__(
        self, settings: Settings, router: Router, runner: Optional[tasks.TaskRunner] = None
    ) -> None:
        self.settings = settings
        self.router = router
        self.runner = runner
        super().__init__((settings.host, settings.port), MCRequestHandler)

    def shutdown(self) -> None:
        """有序停止：先等后台任务收尾（至多 30 秒），再让 `serve_forever` 退出。"""
        if self.runner is not None:
            self.runner.shutdown(tasks.DEFAULT_SHUTDOWN_TIMEOUT)
        super().shutdown()
