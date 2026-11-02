"""M16-13 普通 HTTP 文件/记录来源的取数适配（INH-2178）。

只做三件事：

1. 从 `SyncSource` 取 URL / `record_source` / `keyword`；
2. 复用 `core.downloader.fetch` 下载、`core.bundle.expand_update_payload` 展开
   zip/tar 与正则关键词；
3. 返回结构化、可安全放进 Sync run item 的结果（字节、大小、sha256、basename、
   keyword 命中、unchanged）。

**不写 data、不调用 storage、不解析 XML/JSON、不判断根节点、不归档。** 内容校验与
原子提交（`storage.save_config_file_detailed` / `save_record_file`）归 M16-24；
本模块只保证交给提交层的字节与旧 `services.updates` 路径完全一致：同一条 HTTP URL、
同一份 zip/tar、同一个 keyword，展开出的文件名与字节都来自同一个 `bundle` 函数。

字段约定（M16-05 的 `SyncSource` 没有单独 url 字段）：

* `http_file` 的下载地址放在 `resource_ref`；M16-06 把旧映射的 `url` 迁到这里。
* 旧 `record_url` 对应 `record_source`；`keyword` 同名沿用。
* SDA 来源（`sda_*`）不进入本模块。HTTP 500、200 登录页、超限、超时一律是本来源的
  失败或普通内容，不会触发 collector 登录——本模块不 import 任何 SDA/collector 会话。

结果状态用 SyncItem 口径：`succeeded` / `unchanged` / `skipped` / `failed`；
`reason_code` 取 `core.sync_models.REASON_CODES` 里的值。错误消息来自 `downloader`，
已脱敏（无 userinfo、query、响应正文）；`url` 只返回 scheme/host/path 三段。

`unchanged` 的比较基准由调用方传入：`fetch_source(..., current=...)` 是
「目标文件名 → 当前字节」，`fetch_record(..., latest=...)` 是「最新一条记录字节」。
没有基准时返回 `None`/`False`（第一次拉取必然是新增），与旧 `updates` 的
「拿不到基准不做去重」同口径。本模块不读 `update_status.json`，也不写它。
"""

from __future__ import annotations

import hashlib
import os
import urllib.parse
from dataclasses import dataclass, field
from typing import Mapping, Optional, Tuple

from ..core import bundle, downloader
from ..core.errors import INVALID_INPUT, TOO_LARGE, AppError
from ..core.sync_models import SyncSource

KIND_SOURCE = "http_file"
KIND_RECORD = "http_record"

STATUS_SUCCEEDED = "succeeded"
STATUS_UNCHANGED = "unchanged"
STATUS_SKIPPED = "skipped"
STATUS_FAILED = "failed"

REASON_TOO_LARGE = "too_large"
REASON_NETWORK = "network_error"
REASON_REDIRECT = "redirect_blocked"
REASON_INVALID = "invalid_payload"

#: 与 `services.updates` 同文案（旧 `update_status`/任务条目里给用户看的字）。
NO_URL_REASON = "无下载链接"
NO_RECORD_URL_REASON = "未配置修改记录链接"
UNCHANGED_REASON = "内容与当前版本一致，未归档"
RECORD_UNCHANGED_REASON = "内容与最新一条修改记录一致，未新建记录文件"


@dataclass(frozen=True)
class HttpFetchItem:
    """一个待提交的字节块；`basename` 已过 `bundle` 的文件名校验（或记录 URL 叶子）。"""

    basename: str
    #: 响应正文/压缩包成员原文，不参与 repr，避免日志把大块内容或敏感页带出去。
    content: bytes = field(repr=False)
    size: int
    sha256: str
    unchanged: Optional[bool] = None
    #: 仅记录来源使用：按旧 §5.3 规则从 `record_url` 路径推出的扩展名。
    extension: Optional[str] = None


@dataclass(frozen=True)
class HttpFetchResult:
    kind: str
    status: str
    source_id: str
    profile_id: str
    message: str
    reason_code: Optional[str] = None
    #: 脱敏后的 scheme/host/path 三段，绝不带 userinfo/query/fragment。
    url: Optional[str] = None
    remote_basename: Optional[str] = None
    keyword: str = ""
    #: 配置了关键词且展开成功时为 True；未配置关键词为 None。
    keyword_hit: Optional[bool] = None
    unchanged: Optional[bool] = None
    items: Tuple[HttpFetchItem, ...] = ()


def fetch_source(
    source: SyncSource,
    policy: Optional[downloader.DownloadPolicy] = None,
    current: Optional[Mapping[str, bytes]] = None,
) -> HttpFetchResult:
    """下载并展开一个 `http_file` 来源，返回待提交字节，不落盘。

    Args:
        source: `kind == "http_file"` 的 SyncSource；URL 取 `resource_ref`。
        policy: 下载策略；None 时复用 `downloader.default_policy()`（与旧路径同源）。
        current: 目标文件名 → 当前字节。给出时逐块比较 sha256；全等才返回
            `unchanged`（与 `storage.save_config_file_detailed().unchanged` 同判据）。
    """
    src = _require_http_source(source)
    chosen = _policy(policy)
    url = str(src.resource_ref or "").strip()
    masked = _mask(url)
    if not url:
        return _skipped(src, KIND_SOURCE, NO_URL_REASON, url=None)

    content, failure = _download(src, KIND_SOURCE, url, masked, chosen)
    if failure is not None:
        return failure

    try:
        pieces = bundle.expand_update_payload(
            content,
            keyword=src.keyword,
            source_name=src.name,
            remote_name=_remote_basename(url),
            max_bytes=chosen.max_bytes,
        )
    except AppError as exc:
        return _failed(
            src,
            KIND_SOURCE,
            url=masked,
            remote_basename=_remote_basename(url),
            reason_code=REASON_TOO_LARGE if exc.code == TOO_LARGE else REASON_INVALID,
            message=exc.message,
        )

    items = tuple(
        _item(name, body, _unchanged(name, body, current)) for name, body in pieces
    )
    overall = None if current is None else all(item.unchanged for item in items)
    return HttpFetchResult(
        kind=KIND_SOURCE,
        status=STATUS_UNCHANGED if overall else STATUS_SUCCEEDED,
        source_id=src.source_id,
        profile_id=src.profile_id,
        message=UNCHANGED_REASON if overall else "已获取 %d 个文件" % len(items),
        reason_code="unchanged" if overall else None,
        url=masked,
        remote_basename=_remote_basename(url),
        keyword=src.keyword,
        keyword_hit=True if src.keyword else None,
        unchanged=overall,
        items=items,
    )


def fetch_record(
    source: SyncSource,
    policy: Optional[downloader.DownloadPolicy] = None,
    latest: Optional[bytes] = None,
) -> HttpFetchResult:
    """下载一个来源的修改记录原文，返回待提交字节，不落盘（旧 §10.3 语义）。

    记录内容**不要求可解析**：这里不做 XML/JSON 校验，也不生成 `record_*` 文件名。
    调用方拿 `items[0].content` / `sha256` 与 `latest` 比较后，再用
    `storage.save_record_file` 落盘（命名与同秒冲突处理仍归 storage）。
    """
    src = _require_http_source(source)
    chosen = _policy(policy)
    url = str(src.record_source or "").strip()
    masked = _mask(url)
    if not url:
        return _skipped(src, KIND_RECORD, NO_RECORD_URL_REASON, url=None)

    content, failure = _download(src, KIND_RECORD, url, masked, chosen)
    if failure is not None:
        return failure

    unchanged = latest is not None and _sha(latest) == _sha(content)
    item = HttpFetchItem(
        basename=_record_basename(url, src.name),
        content=content,
        size=len(content),
        sha256=_sha(content),
        unchanged=unchanged,
        extension=_record_extension(url),
    )
    return HttpFetchResult(
        kind=KIND_RECORD,
        status=STATUS_UNCHANGED if unchanged else STATUS_SUCCEEDED,
        source_id=src.source_id,
        profile_id=src.profile_id,
        message=RECORD_UNCHANGED_REASON if unchanged else "已获取修改记录 %s" % item.basename,
        reason_code="unchanged" if unchanged else None,
        url=masked,
        remote_basename=_remote_basename(url),
        keyword=src.keyword,
        keyword_hit=None,
        unchanged=unchanged,
        items=(item,),
    )


# ---------------------------------------------------------------------------
# 内部实现
# ---------------------------------------------------------------------------


def _require_http_source(source: object) -> SyncSource:
    if not isinstance(source, SyncSource):
        raise AppError(INVALID_INPUT, "source 必须是 SyncSource", field="source")
    if source.kind != KIND_SOURCE:
        raise AppError(
            INVALID_INPUT,
            "sync_http_adapter 只处理 http_file 来源（当前为 %s）" % source.kind,
            field="kind",
        )
    return source


def _policy(policy: Optional[downloader.DownloadPolicy]) -> downloader.DownloadPolicy:
    """默认策略完全来自启动配置，与旧 `updates._policy` 同一出处。"""
    return policy if policy is not None else downloader.default_policy()


def _download(
    source: SyncSource,
    kind: str,
    url: str,
    masked: Optional[str],
    policy: downloader.DownloadPolicy,
) -> Tuple[bytes, Optional[HttpFetchResult]]:
    try:
        return downloader.fetch(url, policy), None
    except AppError as exc:
        return b"", _failed(
            source,
            kind,
            url=masked,
            remote_basename=_remote_basename(url),
            reason_code=_reason_code(exc),
            message=exc.message,
        )


def _reason_code(exc: AppError) -> str:
    """下载失败 → Sync reason code；下载器自己的 detail 不直接外泄。"""
    if exc.code == TOO_LARGE:
        return REASON_TOO_LARGE
    if exc.detail.get("reason") == downloader.REASON_POLICY:
        # 白名单/禁止地址/非法跳转都发生在连接或跟随之前，按「被策略阻断」归类。
        return REASON_REDIRECT
    return REASON_NETWORK


def _failed(
    source: SyncSource,
    kind: str,
    *,
    url: Optional[str],
    remote_basename: Optional[str],
    reason_code: str,
    message: str,
) -> HttpFetchResult:
    return HttpFetchResult(
        kind=kind,
        status=STATUS_FAILED,
        source_id=source.source_id,
        profile_id=source.profile_id,
        message=message,
        reason_code=reason_code,
        url=url,
        remote_basename=remote_basename,
        keyword=source.keyword,
        keyword_hit=None,
        unchanged=None,
        items=(),
    )


def _skipped(
    source: SyncSource, kind: str, message: str, *, url: Optional[str]
) -> HttpFetchResult:
    return HttpFetchResult(
        kind=kind,
        status=STATUS_SKIPPED,
        source_id=source.source_id,
        profile_id=source.profile_id,
        message=message,
        url=url,
        keyword=source.keyword,
        unchanged=None,
    )


def _item(
    name: str, content: bytes, unchanged: Optional[bool]
) -> HttpFetchItem:
    return HttpFetchItem(
        basename=name, content=content, size=len(content), sha256=_sha(content), unchanged=unchanged
    )


def _unchanged(
    name: str, content: bytes, current: Optional[Mapping[str, bytes]]
) -> Optional[bool]:
    if current is None:
        return None
    baseline = current.get(name)
    return baseline is not None and _sha(baseline) == _sha(content)


def _sha(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _remote_basename(url: str) -> str:
    """与 `updates._remote_basename` 同口径：URL 路径叶子，百分号解码。

    与旧路径唯一的差别是坏 URL 返回空串而不是抛 `ValueError`：下载失败时也要能
    构造安全的源级结果，不能因为诊断字段把整个 run item 打崩。
    """
    try:
        path = urllib.parse.urlsplit(str(url or "")).path
    except ValueError:
        return ""
    return urllib.parse.unquote(path.rsplit("/", 1)[-1])


def _record_basename(url: str, fallback: str) -> str:
    """记录文件名只用于展示/诊断；去掉解码后可能出现的路径分隔符。"""
    name = _remote_basename(url).replace("\\", "/").rsplit("/", 1)[-1]
    if name in ("", ".", ".."):
        return fallback
    return name


def _record_extension(url: str) -> str:
    """旧 §5.3：`record_url` 路径扩展名，含点 ≤10 字符且只含字母数字/点，否则 `.txt`。"""
    try:
        ext = os.path.splitext(urllib.parse.urlsplit(str(url or "")).path)[1]
    except ValueError:
        return ".txt"
    if ext and len(ext) <= 10 and all(ch.isalnum() or ch == "." for ch in ext):
        return ext
    return ".txt"


def _mask(url: str) -> Optional[str]:
    """URL → scheme/host/path 三段：抹掉 userinfo、query、fragment（§11.5）。"""
    raw = str(url or "").strip()
    if not raw:
        return None
    try:
        parts = urllib.parse.urlsplit(raw)
    except ValueError:
        return "<无法解析的 URL>"
    if not parts.scheme and not parts.netloc:
        return raw[:80]
    netloc = parts.netloc.rpartition("@")[2]
    return urllib.parse.urlunsplit((parts.scheme.lower(), netloc, parts.path, "", ""))


__all__ = [
    "HttpFetchItem",
    "HttpFetchResult",
    "KIND_RECORD",
    "KIND_SOURCE",
    "REASON_INVALID",
    "REASON_NETWORK",
    "REASON_REDIRECT",
    "REASON_TOO_LARGE",
    "STATUS_FAILED",
    "STATUS_SKIPPED",
    "STATUS_SUCCEEDED",
    "STATUS_UNCHANGED",
    "fetch_record",
    "fetch_source",
]
