"""安全下载器（规范 §10.2 下载策略、§11.2 输入与路径、§11.5 日志脱敏；M3-04 / INH-1889）。

本模块只交付**可信下载原语**，不做任何更新业务：内容是不是 XML/JSON、根节点对不对、
要不要归档，全部由调用方（M3-05 更新源、M3-09 修改记录）判断。

三条不变量，改代码前先读：

1. **每一跳都要重新校验**。白名单在首跳命中不代表第二跳安全：`302 → 169.254.169.254`
   是本卡要拦的头号 SSRF，所以校验挂在重定向回调里，而不是只在入口处做一次。
2. **失败原因可以带，响应正文不能带**。超限走 `TOO_LARGE`，既不返回半截字节，也不把
   正文拼进消息（正文里常带凭据）；`download_file` 保留「下载类失败返回 None」的旧语义，
   但 `TOO_LARGE` 一定向上透传，服务边界才是 413 而不是 502。
3. **日志与消息只出现 `scheme://host/path`**（§11.5）。URL 的 userinfo 与 query 一律抹掉，
   连异常文本里夹带的 URL 也先过一遍脱敏。

已知限制（详见卡报告）：解析地址与实际连接之间存在 TOCTOU 窗口（DNS rebinding），
以及配置了系统/环境代理时由代理侧解析域名——§11.2 未要求覆盖，本卡不顺手实现。
"""

from __future__ import annotations

import base64
import http.client
import ipaddress
import logging
import re
import socket
import string
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, replace
from typing import Any, List, NamedTuple, Optional, Tuple

from ..config import (
    DEFAULT_DOWNLOAD_RETRIES,
    DEFAULT_DOWNLOAD_TIMEOUT_S,
    DEFAULT_UPLOAD_MAX_MB,
    Settings,
    load_settings,
)
from .errors import DOWNLOAD_FAILED, TOO_LARGE, AppError

logger = logging.getLogger(__name__)

#: 旧模块常量：保留给历史 import 与默认值参照。实际重试次数默认来自
#: `Settings.download_retries`，只有调用方显式传 `retries` 才覆盖它。
MAX_RETRIES = DEFAULT_DOWNLOAD_RETRIES

USER_AGENT = "MCChecker/1.0"
#: §10.2：最多 5 次重定向，且**每跳重新执行地址与白名单校验**。
MAX_REDIRECTS = 5
#: §10.2：失败重试间隔 1 s、2 s（之后维持 2 s）。用例通过 `_sleep` 这个 seam 断言退避。
_RETRY_DELAYS: Tuple[float, ...] = (1.0, 2.0)
_READ_CHUNK_BYTES = 64 * 1024

_FORBIDDEN_NOTE = "0.0.0.0/::、169.254.0.0/16、fe80::/10"

#: `AppError.detail["reason"]` 的取值，即 §10.2 列出的四种失败原因。
REASON_HTTP_STATUS = "http_status"
REASON_TIMEOUT = "timeout"
REASON_CONNECTION = "connection"
REASON_POLICY = "policy"


@dataclass(frozen=True)
class DownloadPolicy:
    """一次下载的策略快照（字段名与顺序就是卡 5 / 卡 9 依赖的契约）。

    用 dataclass 而不是一堆关键字参数：策略要在重试循环与重定向回调之间反复传递，
    打包成一个对象才不会出现「第二跳漏了白名单」这种掉字段的问题。字段不可变，
    要调整请用 `dataclasses.replace()`。
    """

    timeout_s: int = DEFAULT_DOWNLOAD_TIMEOUT_S
    retries: int = DEFAULT_DOWNLOAD_RETRIES
    #: 响应体累计上限，来自 `Settings.upload_max_bytes`。
    max_bytes: int = DEFAULT_UPLOAD_MAX_MB * 1024 * 1024
    #: `MCHECKER_DOWNLOAD_ALLOW`：逗号分隔的主机名 / IP / CIDR。为空表示不额外限制
    #: （§11.2 的禁止规则照旧执行）；非空时**每一跳**都必须命中其中一项。
    allow: Tuple[str, ...] = ()
    #: 访客临时导入额外拒绝 127.0.0.0/8 与 ::1（§10.2）。
    block_loopback: bool = False


def default_policy(settings: Optional[Settings] = None) -> DownloadPolicy:
    """按启动配置构造默认策略：超时、重试、体积上限、白名单都不写死在下载器里。"""
    cfg = settings if settings is not None else load_settings([])
    return DownloadPolicy(
        timeout_s=cfg.download_timeout_s,
        retries=cfg.download_retries,
        max_bytes=cfg.upload_max_bytes,
        allow=tuple(cfg.download_allow),
    )


def fetch(url: str, policy: DownloadPolicy) -> bytes:
    """按策略下载，成功返回正文字节。

    Raises:
        AppError(TOO_LARGE, 413)：声明或实测响应体超过 `policy.max_bytes`；不返回半截正文。
        AppError(DOWNLOAD_FAILED, 502)：超时 / 连接失败 / HTTP 错误状态 / 被策略拒绝。
            `detail["reason"]` 取这四者之一；消息与 detail 既不含响应正文，也不含 URL 的
            userinfo 与 query（§11.5）。
    """
    attempts = max(1, int(policy.retries))
    last: Optional[AppError] = None
    for attempt in range(1, attempts + 1):
        try:
            return _fetch_once(url, policy, attempt, attempts)
        except AppError as exc:
            # 确定性失败不重试：超限与被策略拒绝重试几次都是同样结果。
            if exc.code == TOO_LARGE or exc.detail.get("reason") == REASON_POLICY:
                raise
            last = exc
        if attempt < attempts:
            _sleep(_retry_delay(attempt))
    assert last is not None  # 循环内要么 return 要么给 last 赋值
    detail = dict(last.detail)
    detail["attempts"] = attempts
    raise AppError(
        DOWNLOAD_FAILED, "%s（已尝试 %d 次）" % (last.message, attempts), **detail
    )


def download_file(url: str, retries: Optional[int] = None) -> Optional[bytes]:
    """旧签名（M2-10 `services/upload.py` 与旧 NiceGUI/APScheduler 路径都在调）。

    下载类失败（超时 / 连接失败 / HTTP 错误 / 被策略拒绝）返回 None，链接导入据此给 502；
    **`TOO_LARGE` 原样抛出**，让服务边界给出 413。

    `retries=None` 表示「按 `Settings.download_retries`」；显式传值只覆盖重试次数。
    """
    policy = default_policy()
    if retries is not None:
        policy = replace(policy, retries=max(1, int(retries)))
    try:
        return fetch(url, policy)
    except AppError as exc:
        if exc.code == TOO_LARGE:
            raise
        logger.error("下载最终失败: %s", exc.message)
        return None


# ---------------------------------------------------------------------------
# 内部实现
# ---------------------------------------------------------------------------

# 重试等待的 seam：用例把它换成记录器，于是退避序列可断言而不用真的睡。
# 用函数而不是模块变量，是为了让 `monkeypatch.setattr(downloader, "_sleep", ...)`
# 一定命中（变量名拼错时 monkeypatch 会直接报错，而不是悄悄真的睡）。
def _sleep(seconds: float) -> None:
    time.sleep(seconds)


def _retry_delay(attempt: int) -> float:
    return _RETRY_DELAYS[min(max(1, attempt) - 1, len(_RETRY_DELAYS) - 1)]


def _fetch_once(url: str, policy: DownloadPolicy, attempt: int, attempts: int) -> bytes:
    target = _prepare(url, policy)
    logger.info("下载文件: %s (尝试 %d/%d)", target.masked, attempt, attempts)
    headers = {"User-Agent": USER_AGENT}
    if target.authorization:
        headers["Authorization"] = target.authorization
    request = urllib.request.Request(target.public, headers=headers, method="GET")
    opener = _build_opener(policy)
    try:
        with opener.open(request, timeout=policy.timeout_s) as response:
            content = _read_limited(response, policy.max_bytes)
    except AppError:
        raise
    except urllib.error.HTTPError as exc:
        raise _download_failed(
            "HTTP 状态 %s：%s" % (exc.code, _redact(exc.msg or "")),
            REASON_HTTP_STATUS,
            target.masked,
        )
    except urllib.error.URLError as exc:
        if _is_timeout(exc.reason):
            raise _download_failed("连接超时", REASON_TIMEOUT, target.masked)
        raise _download_failed(
            "连接失败：%s" % _redact(exc.reason or exc), REASON_CONNECTION, target.masked
        )
    except (socket.timeout, TimeoutError):
        raise _download_failed("读取响应超时", REASON_TIMEOUT, target.masked)
    except (OSError, http.client.HTTPException, ValueError) as exc:
        raise _download_failed(
            "连接失败：%s" % _redact(exc), REASON_CONNECTION, target.masked
        )
    logger.info("下载成功: %s, 大小: %d bytes", target.masked, len(content))
    return content


def _build_opener(policy: DownloadPolicy) -> urllib.request.OpenerDirector:
    """只装我们自己的重定向处理器：默认处理器会把 302 一路跟到元数据地址。"""
    return urllib.request.build_opener(_PolicyRedirectHandler(policy))


class _PolicyRedirectHandler(urllib.request.HTTPRedirectHandler):
    """自己解析 `Location`，校验完再发下一跳——校验的 URL 就是请求的 URL。

    不挂进基类的 `redirect_request`：基类在回调我们之前就把 `file:` 之类判成 HTTPError，
    失败原因会被记成「HTTP 状态」而不是「被策略拒绝」，而且它跟随的 URL 与我们看到的
    URL 之间存在编码差异。

    注意 `http_error_301/303/307` 在基类里是**指向基类函数的别名**，子类只覆盖
    `http_error_302` 是不生效的，所以这几个别名必须在本类里重新指过来（308 基类不管，
    这里一并接上）。
    """

    def __init__(self, policy: DownloadPolicy) -> None:
        self._policy = policy

    def http_error_302(self, req, fp, code, msg, headers):  # noqa: N802 - urllib 约定
        masked_from = _mask(req.full_url)
        location = (headers.get("Location") or headers.get("URI") or "").strip()
        hops = int(getattr(req, "mc_redirect_hops", 0))
        if not location:
            _close_quietly(fp)
            raise _download_failed(
                "HTTP 状态 %s：重定向响应没有 Location" % code, REASON_HTTP_STATUS, masked_from
            )
        # 与基类同样的预处理：ISO-8859-1 解码回来的裸字节先百分号编码，再按当前 URL 解析成绝对。
        quoted = urllib.parse.quote(location, encoding="iso-8859-1", safe=string.punctuation)
        next_url = urllib.parse.urljoin(req.full_url, quoted.replace(" ", "%20"))
        if hops >= MAX_REDIRECTS:
            _close_quietly(fp)
            raise _rejected(
                "重定向次数超过上限 %d 次" % MAX_REDIRECTS, _mask(next_url)
            )
        target = _prepare(next_url, self._policy)
        _close_quietly(fp)  # 3xx 的正文没有价值，也不该占着连接
        logger.info("跟随重定向: %s → %s (第 %d 跳)", masked_from, target.masked, hops + 1)
        request = urllib.request.Request(
            target.public, headers=_hop_headers(req, target), method="GET"
        )
        request.mc_redirect_hops = hops + 1
        return self.parent.open(request, timeout=req.timeout)

    http_error_301 = http_error_302
    http_error_303 = http_error_302
    http_error_307 = http_error_302
    http_error_308 = http_error_302


def _hop_headers(req, target: _Target) -> dict:
    """下一跳的请求头：凭据只在同一主机名之间延续，跨主机一律不带。"""
    headers = {"User-Agent": USER_AGENT}
    if target.authorization:
        headers["Authorization"] = target.authorization
        return headers
    inherited = req.headers.get("Authorization") or req.unredirected_hdrs.get("Authorization")
    if inherited and target.hostname == _hostname_of(req.full_url):
        headers["Authorization"] = inherited
    return headers


def _close_quietly(fp) -> None:
    try:
        fp.close()
    except Exception:  # pragma: no cover - 关闭失败不改变「拒绝」这个结论
        pass


class _Target(NamedTuple):
    """校验通过的请求目标。

    `public` 已剥掉 userinfo（凭据走 Authorization 头），`masked` 是 `scheme://host/path`，
    只用于日志与错误消息。
    """

    public: str
    masked: str
    hostname: str
    authorization: Optional[str]


def _prepare(url: str, policy: DownloadPolicy) -> _Target:
    """解析并校验 URL：协议 → 主机 → 地址段 → 白名单。通过后给出脱敏形态。"""
    raw = str(url or "").strip()
    try:
        parts = urllib.parse.urlsplit(raw)
    except ValueError:
        raise _rejected("URL 无法解析", _mask(raw))
    scheme = (parts.scheme or "").lower()
    if scheme not in ("http", "https"):
        raise _rejected(
            "仅允许 http/https 协议（当前为 %s）" % (scheme or "无协议"), _mask(raw)
        )
    try:
        hostname = parts.hostname
        port = parts.port
    except ValueError:
        raise _rejected("URL 端口非法", _mask(raw))
    if not hostname:
        raise _rejected("URL 缺少主机名", _mask(raw))
    if port is None:
        port = 443 if scheme == "https" else 80

    netloc = parts.netloc.rpartition("@")[2]
    public = urllib.parse.urlunsplit((scheme, netloc, parts.path or "/", parts.query, ""))
    masked = urllib.parse.urlunsplit((scheme, netloc, parts.path or "/", "", ""))

    addresses = _resolve(hostname, port, masked)
    for address in addresses:
        if _is_denied_address(address, policy.block_loopback):
            raise _rejected(
                "目标地址 %s 属于禁止范围（%s%s）"
                % (
                    address,
                    _FORBIDDEN_NOTE,
                    "、127.0.0.0/8 与 ::1" if policy.block_loopback else "",
                ),
                masked,
            )
    if policy.allow and not any(_allow_matches(item, hostname, addresses) for item in policy.allow):
        raise _rejected("目标不在下载白名单 MCHECKER_DOWNLOAD_ALLOW 内", masked)
    return _Target(
        public=public, masked=masked, hostname=hostname.lower(), authorization=_basic_auth(parts)
    )


def _resolve(hostname: str, port: int, masked: str) -> List[Any]:
    """主机 → 地址清单。IP 字面量自己解析，不走 DNS（也顺带让禁止段用例可复现）。"""
    try:
        return [ipaddress.ip_address(hostname)]
    except ValueError:
        pass
    try:
        infos = socket.getaddrinfo(hostname, port, proto=socket.IPPROTO_TCP)
    except (socket.gaierror, UnicodeError, OSError) as exc:
        raise _download_failed(
            "无法解析主机：%s" % _redact(exc), REASON_CONNECTION, masked
        )
    addresses: List[Any] = []
    for info in infos:
        raw = str(info[4][0]).split("%")[0]  # 去掉 IPv6 的 zone id
        try:
            addresses.append(ipaddress.ip_address(raw))
        except ValueError:
            continue
    if not addresses:
        raise _download_failed("主机没有可用地址", REASON_CONNECTION, masked)
    return addresses


def _is_denied_address(address: Any, block_loopback: bool) -> bool:
    """§10.2/§11.2 的拒绝清单。私网段**故意不拒绝**——这工具本来就在内网里取配置。

    `::ffff:a.b.c.d` 这类 IPv4-mapped 地址要按里面的 v4 再判一次，否则
    `[::ffff:169.254.169.254]` 就是绕过元数据地址拦截的后门。
    """
    candidates = [address]
    mapped = getattr(address, "ipv4_mapped", None)
    if mapped is not None:
        candidates.append(mapped)
    for item in candidates:
        if item.is_unspecified or item.is_link_local:
            return True
        if block_loopback and item.is_loopback:
            return True
    return False


def _allow_matches(item: str, hostname: str, addresses: List[Any]) -> bool:
    """白名单条目语义：主机名精确匹配（大小写不敏感）、IP 相等、或 CIDR 覆盖。"""
    entry = str(item or "").strip()
    if not entry:
        return False
    if entry.lower().rstrip(".") == hostname.lower().rstrip("."):
        return True
    if "/" in entry:
        try:
            network = ipaddress.ip_network(entry, strict=False)
        except ValueError:
            return False
        return any(_in_network(ip, network) for ip in addresses)
    try:
        wanted = ipaddress.ip_address(entry)
    except ValueError:
        return False
    return any(ip == wanted or getattr(ip, "ipv4_mapped", None) == wanted for ip in addresses)


def _in_network(ip: Any, network: Any) -> bool:
    for candidate in (ip, getattr(ip, "ipv4_mapped", None)):
        if candidate is not None and candidate.version == network.version and candidate in network:
            return True
    return False


def _basic_auth(parts) -> Optional[str]:
    """URL 里的 userinfo 换成 Basic 头（§10.2 容忍 userinfo，但不让它进连接、日志、消息）。"""
    if "@" not in parts.netloc:
        return None
    username = parts.username
    if not username:
        return None
    raw = ("%s:%s" % (username, parts.password or "")).encode("utf-8")
    return "Basic " + base64.b64encode(raw).decode("ascii")


def _read_limited(response, max_bytes: int) -> bytes:
    """分块读取，累计超过上限就报 `TOO_LARGE`，不把半截正文交出去。"""
    declared = _declared_length(response)
    if declared is not None and declared > max_bytes:
        raise AppError(
            TOO_LARGE,
            "响应体声明 %d 字节，超过下载上限 %d 字节" % (declared, max_bytes),
            limit_bytes=max_bytes,
        )
    chunks: List[bytes] = []
    total = 0
    while True:
        chunk = response.read(_READ_CHUNK_BYTES)
        if not chunk:
            break
        total += len(chunk)
        if total > max_bytes:
            raise AppError(TOO_LARGE, "响应体超过下载上限 %d 字节" % max_bytes, limit_bytes=max_bytes)
        chunks.append(chunk)
    return b"".join(chunks)


def _declared_length(response) -> Optional[int]:
    value = getattr(response, "headers", None)
    value = value.get("Content-Length") if value is not None else None
    try:
        return int(str(value).strip()) if value else None
    except ValueError:
        return None


def _is_timeout(reason: Any) -> bool:
    return isinstance(reason, (socket.timeout, TimeoutError))


def _download_failed(message: str, reason: str, url: str) -> AppError:
    masked = _mask(url)
    return AppError(
        DOWNLOAD_FAILED, "下载失败：%s" % _redact(message), reason=reason, url=masked
    )


def _rejected(message: str, masked: str) -> AppError:
    return AppError(
        DOWNLOAD_FAILED,
        "下载被策略拒绝：%s（%s）" % (_redact(message), _mask(masked)),
        reason=REASON_POLICY,
        url=_mask(masked),
    )


#: 异常文本里可能夹带完整 URL（含凭据与 query），凡形如 `scheme://...` 的片段都要脱敏。
_URL_RE = re.compile(r"[a-zA-Z][a-zA-Z0-9+.\-]*://[^\s'\"<>()]+")


def _redact(text: Any) -> str:
    value = str(text).strip()
    if not value:
        return "未知原因"
    return _URL_RE.sub(lambda match: _mask(match.group(0)), value)[:200]


def _mask(url: Any) -> str:
    """URL → `scheme://host/path`：抹掉 userinfo、query、fragment（§11.5）。"""
    raw = str(url or "")
    try:
        parts = urllib.parse.urlsplit(raw)
    except ValueError:
        return "<无法解析的 URL>"
    if not parts.scheme and not parts.netloc:
        return raw[:80]
    netloc = parts.netloc.rpartition("@")[2]
    return urllib.parse.urlunsplit((parts.scheme.lower(), netloc, parts.path, "", ""))


def _hostname_of(url: Any) -> str:
    try:
        return (urllib.parse.urlsplit(str(url)).hostname or "").lower()
    except ValueError:
        return ""


__all__ = [
    "DownloadPolicy",
    "MAX_REDIRECTS",
    "MAX_RETRIES",
    "REASON_CONNECTION",
    "REASON_HTTP_STATUS",
    "REASON_POLICY",
    "REASON_TIMEOUT",
    "default_policy",
    "download_file",
    "fetch",
]
