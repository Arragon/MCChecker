"""M16-22 SDA 内容读取的响应守卫、真流式限额与解压炸弹预检。

collector 面对的是受保护会话，错误响应/重定向/大对象都不能把 Cookie 或半个
文件带进业务目录。本模块只做三件事：

1. `guard_response`：在读取正文**之前**检查状态码、Content-Length、Content-Type
   与最终 URL；跨域、HTTPS 降级、登录 HTML、500 空 body 分别给出安全 reason；
2. `read_response`：有界的流式读取（或已缓冲 body 的二次限额），超限/超时/取消
   一律 abort staging；不把 Set-Cookie 等响应头向上返回；
3. `preflight_archive`：只读 zip/tar 中央目录/成员头，声明解压总量、条目数或
   压缩比越界就在解压写盘前失败。

真实 SDA 响应头/重定向链仍待 E2；这里用合成响应钉住 MCChecker 侧的保护。
"""

from __future__ import annotations

import os
import tarfile
import zipfile
from typing import Any, Dict, Optional
from urllib.parse import urlsplit

from .stream_reader import StreamError, _fail, read_limited

MAX_ARCHIVE_ENTRIES = 1000
MAX_COMPRESSION_RATIO = 100
RATIO_MIN_BYTES = 1024 * 1024
_HTML_TYPES = ("text/html", "application/xhtml+xml")


def _headers(value: Any) -> Dict[str, str]:
    try:
        items = dict(value or {})
    except (TypeError, ValueError):
        return {}
    return {str(key).lower(): str(item) for key, item in items.items()}


def _same_origin(url: str, origin: str) -> bool:
    left = urlsplit(str(url or ""))
    right = urlsplit(str(origin or ""))
    return (
        left.scheme == right.scheme
        and left.hostname == right.hostname
        and (left.port or 443) == (right.port or 443)
    )


def _content_length(headers: Dict[str, str]) -> Optional[int]:
    raw = headers.get("content-length")
    if raw is None or str(raw).strip() == "":
        return None
    try:
        value = int(str(raw).strip())
    except ValueError:
        raise StreamError("invalid_payload", "content-length invalid")
    if value < 0:
        raise StreamError("invalid_payload", "content-length invalid")
    return value


def guard_response(
    status: Any,
    headers: Any,
    final_url: str,
    origin: str,
    limit_bytes: int,
    body_prefix: bytes = b"",
) -> Dict[str, Any]:
    """读取正文前的响应守卫；不返回任何 Cookie/CSRF 头。"""
    try:
        code = int(status)
    except (TypeError, ValueError):
        raise StreamError("invalid_payload", "http status invalid")
    safe_headers = _headers(headers)
    if final_url and not _same_origin(final_url, origin):
        raise StreamError("redirect_blocked", "cross-origin response")
    if urlsplit(str(final_url or "")).scheme not in ("https", ""):
        raise StreamError("redirect_blocked", "https downgrade")
    if code in (301, 302, 303, 307, 308):
        raise StreamError("redirect_blocked", "http redirect not followed")
    if code == 403:
        raise StreamError("permission_denied", "http 403")
    if code == 500 and _content_length(safe_headers) in (None, 0):
        raise StreamError("auth_failed", "http 500 empty body")
    if code != 200:
        raise StreamError("business_error", "http_status=%d" % code)

    content_type = safe_headers.get("content-type", "").lower()
    if any(marker in content_type for marker in _HTML_TYPES):
        raise StreamError("invalid_payload", "login/html response rejected")
    prefix = bytes(body_prefix or b"").lstrip()
    if prefix.startswith(b"<"):
        raise StreamError("invalid_payload", "login/html body rejected")
    length = _content_length(safe_headers)
    if length is not None and length > limit_bytes:
        raise StreamError("too_large", "content-length exceeds limit")
    return {"status": code, "content_length": length, "content_type": content_type}


def read_response(
    response: Any,
    sink: Any,
    origin: str,
    limit_bytes: int,
    deadline: Optional[float] = None,
    cancel_event=None,
    chunk_size: int = 64 * 1024,
    clock=None,
) -> Dict[str, Any]:
    """先守卫再读；未知长度也按 limit 截断，失败时 staging 已 abort。"""
    headers = _headers(getattr(response, "headers", {}))
    try:
        guard_response(
            getattr(response, "status", 0),
            headers,
            str(getattr(response, "url", "") or origin),
            origin,
            limit_bytes,
        )
    except StreamError:
        try:
            sink.abort()
        except Exception:
            pass
        raise
    reader = getattr(response, "read", None)
    if callable(reader):
        kwargs = {
            "limit_bytes": limit_bytes,
            "deadline": deadline,
            "cancel_event": cancel_event,
            "chunk_size": chunk_size,
        }
        if clock is not None:
            kwargs["clock"] = clock
        return read_limited(response, sink, **kwargs)

    body = getattr(response, "body", None)
    if not callable(body):
        _fail(sink, "protocol_mismatch", "response has no readable body")
    try:
        raw = bytes(body() or b"")
    except Exception as exc:
        _fail(sink, "network_error", str(exc))
    if len(raw) > limit_bytes:
        _fail(sink, "too_large", "content exceeds limit")
    if raw.lstrip().startswith(b"<"):
        _fail(sink, "invalid_payload", "login/html body rejected")
    try:
        sink.write(raw)
    except Exception as exc:
        _fail(sink, "storage_failure", str(exc))
    import hashlib

    return {"size": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}


def preflight_archive(
    path: str,
    limit_bytes: int,
    max_entries: int = MAX_ARCHIVE_ENTRIES,
) -> Dict[str, Any]:
    """解压前只读目录/成员头；声明大小/条目/压缩比越界即失败。"""
    if not os.path.isfile(path):
        raise StreamError("not_found", "archive not found")
    if zipfile.is_zipfile(path):
        with zipfile.ZipFile(path) as archive:
            infos = archive.infolist()
            _check_entries_and_sizes(
                [
                    (
                        info.filename,
                        int(info.file_size),
                        int(info.compress_size),
                    )
                    for info in infos
                ],
                limit_bytes,
                max_entries,
            )
            return {"kind": "zip", "entries": len(infos), "uncompressed_bytes": sum(int(info.file_size) for info in infos)}
    try:
        with tarfile.open(path, "r:*") as archive:
            members = []
            for member in archive:
                if not member.isfile():
                    continue
                members.append((member.name, int(member.size), None))
            _check_entries_and_sizes(members, limit_bytes, max_entries)
            return {"kind": "tar", "entries": len(members), "uncompressed_bytes": sum(int(item[1]) for item in members)}
    except (tarfile.TarError, OSError) as exc:
        raise StreamError("invalid_payload", "archive invalid: %s" % type(exc).__name__)


def _check_entries_and_sizes(entries, limit_bytes: int, max_entries: int) -> None:
    if len(entries) > max_entries:
        raise StreamError("too_large", "archive entry count exceeds limit")
    total = 0
    for name, size, compressed in entries:
        if size < 0:
            raise StreamError("invalid_payload", "archive member size invalid")
        total += size
        if total > limit_bytes:
            raise StreamError("too_large", "archive uncompressed size exceeds limit")
        if (
            compressed is not None
            and size >= RATIO_MIN_BYTES
            and compressed > 0
            and size / compressed > MAX_COMPRESSION_RATIO
        ):
            raise StreamError("too_large", "archive compression ratio exceeds limit")
