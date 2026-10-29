"""文档服务：文件列表、compact 文档与原文字节（§6.3–§6.5、§7.4；T-06）。

**缓存**（§6.5）
* 内存 LRU：键 `(realpath, size, mtime_ns)`，条目上限 = `max(24, 2 × 该机型配置文件数)`
  （M4-05 实测定档，见 `memory_cache_limit`），且总量 ≤ 256 MB。命中直接返回
  **已序列化**的响应体，不再读盘、不再解析、不再序列化。
* 磁盘缓存：`cache/doc_v2/<sha256>.json.gz`，按内容 sha 命名，属**派生数据**——
  损坏即丢弃重建，永远不以它为准判断权威内容。
* 旧 `cache/parse_tree` 保持只读兼容，本卡不动它。

**分层**：本模块只读权威原文（`configs/`、`archive/`、`records/`）并组合
`core.parser`；HTTP 细节（304、`Content-Disposition`、gzip）由 server 层完成。
身份与权限（`RequestContext`/`authorize`）在 M1-03/M1-04 接入，因此这里显式接收
`profile_id`。

**已知取舍**：内存缓存只存未压缩响应体，gzip 由 server 层按需进行（§6.5 提到
「已 gzip 的响应体」）。M4-05 用 `scripts/bench_api.py` 量过要不要把压缩结果也进缓存：
5 万节点文件命中缓存后 identity p50 2.3–3.3 ms、gzip 18.6–19.7 ms（八次运行），差额
≈16 ms，最紧的 §12「5 万节点 ≤ 300 ms」那一行仍有 280 ms 余量，而缓存压缩体要为每个
文档多驻留约 1/6 的字节、直接挤占 256 MB 预算 ⇒ **结论是不缓存 gzip**，本卡不改。
"""

from __future__ import annotations

import gzip
import hashlib
import json
import logging
import os
import threading
from collections import OrderedDict
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, List, NamedTuple, Optional, Tuple

from ..core import fsutil, parser, storage
from ..core.errors import INVALID_INPUT, NOT_FOUND, PARSE_FAILED, AppError

logger = logging.getLogger(__name__)

#: 文档缓存 schema：与 `PARSER_VERSION` 一起决定磁盘缓存是否可用（§6.5 第 3 步）。
CACHE_SCHEMA = 1

MEMORY_CACHE_MAX_ENTRIES = 24
MEMORY_CACHE_MAX_BYTES = 256 * 1024 * 1024
#: 条目上限按「该机型 `configs/` 里的文件数」放大多少倍（M4-05 / INH-1907，见 `memory_cache_limit`）。
#: 置 0 即关掉按文件数放大、把容量钉死在 `MEMORY_CACHE_MAX_ENTRIES`，供基准与测试做对照。
MEMORY_CACHE_FILES_MULTIPLIER = 2

DISK_CACHE_DIRNAME = "doc_v2"

#: 原文下载的 Content-Type（§7.4「Content-Type 按扩展名」）。
RAW_CONTENT_TYPES = {
    "xml": "application/xml; charset=utf-8",
    "json": "application/json; charset=utf-8",
    "txt": "text/plain; charset=utf-8",
    "ini": "text/plain; charset=utf-8",
    "cfg": "text/plain; charset=utf-8",
    "conf": "text/plain; charset=utf-8",
}

#: 只做原文管理、**不伪装结构树**的文本格式（M11-02 / INH-2131）。
TEXT_FORMATS = ("ini", "cfg", "conf", "txt")

CacheKey = Tuple[str, int, int]


class FileRef(NamedTuple):
    """一次请求指向的原文文件（§6.4）。"""

    profile: str
    name: str
    kind: str  # current | archive | record
    version: str  # current | archive:<文件名> | record:<文件名>
    version_label: str
    path: str


@dataclass(frozen=True)
class RawFile:
    """原文下载的数据面。"""

    data: bytes
    filename: str
    content_type: str
    sha256: str
    size: int
    updated_at: str


class _MemoryEntry(NamedTuple):
    body: bytes  # 完整的 §6.3 响应体（已序列化）
    etag: str
    sha256: str
    node_count: int


_MEMORY_CACHE: "OrderedDict[CacheKey, _MemoryEntry]" = OrderedDict()
_MEMORY_BYTES = 0
_CACHE_LOCK = threading.Lock()
_STATS = {"hits": 0, "misses": 0}


def cache_info() -> Dict[str, int]:
    """缓存观测（测试钩子）：命中/未命中/条目数/占用字节。"""
    with _CACHE_LOCK:
        return {
            "hits": _STATS["hits"],
            "misses": _STATS["misses"],
            "entries": len(_MEMORY_CACHE),
            "bytes": _MEMORY_BYTES,
        }


def reset_cache() -> None:
    """清空内存缓存与计数（测试用；不影响磁盘缓存）。"""
    global _MEMORY_BYTES
    with _CACHE_LOCK:
        _MEMORY_CACHE.clear()
        _MEMORY_BYTES = 0
        _STATS["hits"] = 0
        _STATS["misses"] = 0


def etag_for_sha(sha256: str) -> str:
    """`"<sha256>-v2"`（§6.3）。"""
    return '"%s-v%d"' % (sha256, parser.PARSER_VERSION)


def _count_profile_config_files(profile_id: str) -> int:
    """该机型 `configs/` 下的普通文件数；**读路径不创建目录**（与 `_count_files` 同规矩）。"""
    directory = storage.get_configs_dir(profile_id)
    try:
        entries = os.listdir(directory)
    except OSError:
        return 0
    return sum(1 for entry in entries if os.path.isfile(os.path.join(directory, entry)))


def memory_cache_limit(profile_id: str) -> int:
    """条目上限 = `max(MEMORY_CACHE_MAX_ENTRIES, MEMORY_CACHE_FILES_MULTIPLIER × 机型文件数)`。

    **为什么改（M4-05 / INH-1907，D3 交给本卡的实测）**：一次全局搜索要触碰机型里
    每个当前文件，而 `search._may_hit` 与 `load_document` 各触碰一遍。钉死 24 时，
    30 文件 × 3 万节点的机型里每个文件每次搜索都要重新
    读原文 → gunzip → `json.loads` → `json.dumps`：本机八次运行实测 p50 725–753 ms（
    p95 最高 761.2），超 §12 表格的 500 ms；按文件数放大后同一批语料 p50 282–296 ms
    （钉死 30 项、钉死 64 项与按文件数放大三档，八次运行差异在噪声内；
    `scripts/bench_api.py` 的容量对比表）。

    **为什么安全、以及它救不了什么**：内存的硬护栏一直是 `MEMORY_CACHE_MAX_BYTES`
    （§12「+LRU ≤ 256 MB」），放大条目数不会让驻留字节越过它。同脚本实测 100 文件 ×
    3 万节点的机型里，条目上限算出来是 200，但字节上限先在 **71 项 / 254 MB** 处开始
    逐出，每轮搜索仍要重读全部 100 个文件 → p50 2363–2431 ms（三次规模运行），照样超 500 ms。也就是
    说本规则把「超过 24 个文件就退化」推后到「超过 ~71 个同规模文件」，再大的机型要
    满足 §12 得动别的机制（§7.6 的预计算小写数组、索引，或放宽内存预算），不是把这里
    的数字再调大。正因如此，条目数放大**不**配套放松字节上限。
    """
    return max(
        MEMORY_CACHE_MAX_ENTRIES,
        MEMORY_CACHE_FILES_MULTIPLIER * _count_profile_config_files(profile_id),
    )


# ===================== 路径解析（§6.4） =====================


def resolve_ref(profile_id: str, name: str, version: str = "current") -> FileRef:
    """把 `(profile, name, version)` 解析为一个**存在**的原文路径。

    文件名与版本文件名都过 `fsutil.validate_filename`（拒绝 `../x`、路径分隔符
    等），路径再过 `resolve_within`，确保读不到目录之外。
    """
    clean_name = fsutil.validate_filename(name)
    raw_version = (version or "current").strip() or "current"

    if raw_version == "current":
        kind, version_label = "current", "当前版本"
        path = fsutil.resolve_within(storage.get_configs_dir(profile_id), clean_name)
    elif raw_version.startswith("archive:"):
        filename = fsutil.validate_filename(raw_version.split(":", 1)[1])
        kind, version_label = "archive", filename
        root = os.path.join(storage.get_archive_root_dir(profile_id), clean_name)
        path = fsutil.resolve_within(root, filename)
    elif raw_version.startswith("record:"):
        filename = fsutil.validate_filename(raw_version.split(":", 1)[1])
        kind, version_label = "record", filename
        root = os.path.join(storage.get_records_root_dir(profile_id), clean_name)
        path = fsutil.resolve_within(root, filename)
    else:
        raise AppError(
            INVALID_INPUT,
            "version 只允许 current、archive:<文件名>、record:<文件名>",
            field="version",
        )

    if not os.path.isfile(path):
        raise AppError(NOT_FOUND, "文件不存在", name=clean_name, version=raw_version)

    return FileRef(
        profile=profile_id,
        name=clean_name,
        kind=kind,
        version=raw_version,
        version_label=version_label,
        path=path,
    )


# ===================== 文件列表（§7.4） =====================


def list_files(profile_id: str) -> List[Dict[str, Any]]:
    """`GET /p/{pid}/files`：只列 `configs/` 下的普通文件，按名称排序。"""
    mapping = {str(item.get("name") or "") for item in storage.load_config_mapping()}
    archive_root = storage.get_archive_root_dir(profile_id)
    records_root = storage.get_records_root_dir(profile_id)

    # 待审建议一次读完再按文件计数（避免每个文件都扫一遍 JSON）
    pending: Dict[str, int] = {}
    for item in storage.load_edit_remarks():
        if item.get("status") != "pending":
            continue
        source = str(item.get("source_file") or "")
        if source:
            pending[source] = pending.get(source, 0) + 1

    items: List[Dict[str, Any]] = []
    for name in storage.list_config_files():
        path = storage.get_config_path(name)
        if not os.path.isfile(path):
            continue
        stat = os.stat(path)
        items.append(
            {
                "name": name,
                "format": _format_of(name),
                "size": stat.st_size,
                "updated_at": _fmt_time(stat.st_mtime),
                "mapped": name in mapping,
                "archive_count": _count_files(os.path.join(archive_root, name), skip_diff=True),
                "record_count": _count_files(os.path.join(records_root, name)),
                "pending_remarks": pending.get(name, 0),
            }
        )
    return items


def _count_files(directory: str, skip_diff: bool = False) -> int:
    """只数普通文件；读路径不创建目录（不改动数据目录）。"""
    if not os.path.isdir(directory):
        return 0
    total = 0
    for entry in os.listdir(directory):
        if skip_diff and entry.endswith(".diff.json"):
            continue
        if os.path.isfile(os.path.join(directory, entry)):
            total += 1
    return total


# ===================== 原文字节（§7.4） =====================


def load_raw(profile_id: str, name: str, version: str = "current") -> RawFile:
    ref = resolve_ref(profile_id, name, version)
    data, stat = _read_source(ref.path)
    return RawFile(
        data=data,
        filename=ref.name if ref.kind == "current" else os.path.basename(ref.path),
        content_type=RAW_CONTENT_TYPES.get(_format_of(ref.name), "application/octet-stream"),
        sha256=_sha256(data),
        size=stat.st_size,
        updated_at=_fmt_time(stat.st_mtime),
    )


# ===================== compact 文档（§6.3、§6.5） =====================


def document_bytes(profile_id: str, name: str, version: str = "current") -> Tuple[bytes, str]:
    """HTTP 快路径：返回 `(§6.3 响应体字节, ETag)`，命中缓存时不读盘不解析。"""
    ref = resolve_ref(profile_id, name, version)
    _, entry = _document_entry(ref)
    return entry.body, entry.etag


def load_document(profile_id: str, name: str, version: str = "current") -> Dict[str, Any]:
    """解析后的 compact 文档（§6.5 的 `load_document`）。"""
    body, _etag = document_bytes(profile_id, name, version)
    return json.loads(body.decode("utf-8"))


def _document_entry(ref: FileRef) -> Tuple[CacheKey, _MemoryEntry]:
    """按 `(realpath, size, mtime_ns)` 取缓存；未命中则读盘 → 磁盘缓存 → 解析。"""
    stat = os.stat(ref.path)
    key: CacheKey = (os.path.realpath(ref.path), stat.st_size, stat.st_mtime_ns)

    with _CACHE_LOCK:
        entry = _MEMORY_CACHE.get(key)
        if entry is not None:
            _MEMORY_CACHE.move_to_end(key)
            _STATS["hits"] += 1
            return key, entry
        _STATS["misses"] += 1

    data, stat = _read_source(ref.path)
    key = (os.path.realpath(ref.path), stat.st_size, stat.st_mtime_ns)
    sha = _sha256(data)

    with _CACHE_LOCK:  # 并发下别人可能已经填好同一个键
        cached = _MEMORY_CACHE.get(key)
    if cached is not None:
        with _CACHE_LOCK:
            _MEMORY_CACHE.move_to_end(key)
        return key, cached

    document = _disk_load(ref.profile, sha)
    if document is None:
        document = _parse(ref, data)
        _disk_store(ref.profile, sha, document)

    body = json.dumps(
        _with_file_block(document, ref, sha, stat), ensure_ascii=False
    ).encode("utf-8")
    entry = _MemoryEntry(
        body=body,
        etag=etag_for_sha(sha),
        sha256=sha,
        node_count=len(document["nodes"]["p"]) - 1,
    )
    _memory_put(key, entry, ref.profile)
    return key, entry


def _with_file_block(
    document: Dict[str, Any], ref: FileRef, sha: str, stat: os.stat_result
) -> Dict[str, Any]:
    """补齐 §6.3 的 `file` 块（`nodes` 直接复用解析结果）。"""
    cached_file = document.get("file") or {}
    return {
        "file": {
            "profile": ref.profile,
            "name": ref.name,
            "kind": ref.kind,
            "version": ref.version,
            "version_label": ref.version_label,
            "sha256": sha,
            "format": cached_file.get("format") or _format_of(ref.name),
            "size": stat.st_size,
            "updated_at": _fmt_time(stat.st_mtime),
            "parser_version": parser.PARSER_VERSION,
            "node_count": len(document["nodes"]["p"]) - 1,
            "read_only": ref.kind != "current",
            "warnings": list(cached_file.get("warnings") or []),
            "expires_at": None,
        },
        "nodes": document["nodes"],
    }


def _parse(ref: FileRef, data: bytes) -> Dict[str, Any]:
    if _format_of(ref.name) == "text":
        raise AppError(
            PARSE_FAILED,
            "该格式不支持结构树，仅提供原文查看与下载",
            raw_available=True,
            unsupported=True,
        )
    try:
        result = parser.to_compact(data, ref.name)
    except AppError as exc:
        if exc.code == PARSE_FAILED:
            # 原文仍可下载：前端据此提供「下载原文」入口（§7.4）
            exc.detail["raw_available"] = True
        raise
    return {
        "file": {"format": result["format"], "warnings": result["warnings"]},
        "nodes": result["nodes"],
    }


def _memory_put(key: CacheKey, entry: _MemoryEntry, profile_id: str) -> None:
    global _MEMORY_BYTES
    limit = memory_cache_limit(profile_id)  # 锁外算：一次 listdir 不该进缓存锁
    with _CACHE_LOCK:
        previous = _MEMORY_CACHE.pop(key, None)
        if previous is not None:
            _MEMORY_BYTES -= len(previous.body)
        _MEMORY_CACHE[key] = entry
        _MEMORY_BYTES += len(entry.body)
        while _MEMORY_CACHE and (
            len(_MEMORY_CACHE) > limit or _MEMORY_BYTES > MEMORY_CACHE_MAX_BYTES
        ):
            _, evicted = _MEMORY_CACHE.popitem(last=False)
            _MEMORY_BYTES -= len(evicted.body)


def _read_source(path: str) -> Tuple[bytes, os.stat_result]:
    """读原文；若读取期间被替换（size/mtime 变化）则重读一次（§6.5 第 4 步）。"""
    stat = os.stat(path)
    data = _read_bytes(path)
    after = os.stat(path)
    if (after.st_size, after.st_mtime_ns) != (stat.st_size, stat.st_mtime_ns):
        data = _read_bytes(path)
        after = os.stat(path)
    return data, after


def _read_bytes(path: str) -> bytes:
    with open(path, "rb") as fh:
        return fh.read()


# ===================== 磁盘缓存（§6.5 第 3 步） =====================


def _disk_path(profile_id: str, sha: str) -> str:
    return os.path.join(
        storage.get_cache_root_dir(profile_id), DISK_CACHE_DIRNAME, sha + ".json.gz"
    )


def _disk_load(profile_id: str, sha: str) -> Optional[Dict[str, Any]]:
    path = _disk_path(profile_id, sha)
    if not os.path.exists(path):
        return None
    try:
        with gzip.open(path, "rt", encoding="utf-8") as fh:
            payload = json.load(fh)
    except (OSError, ValueError):
        logger.warning("文档缓存损坏，丢弃重建：%s", path)
        _remove(path)
        return None
    if (
        not isinstance(payload, dict)
        or payload.get("cache_schema") != CACHE_SCHEMA
        or payload.get("parser_version") != parser.PARSER_VERSION
    ):
        return None
    document = payload.get("document")
    if not isinstance(document, dict) or "nodes" not in document:
        return None
    return document


def _disk_store(profile_id: str, sha: str, document: Dict[str, Any]) -> None:
    path = _disk_path(profile_id, sha)
    payload = {
        "cache_schema": CACHE_SCHEMA,
        "parser_version": parser.PARSER_VERSION,
        "document": document,
    }
    try:
        blob = gzip.compress(json.dumps(payload, ensure_ascii=False).encode("utf-8"), compresslevel=6)
        fsutil.atomic_write_bytes(path, blob)
    except OSError as exc:  # 派生数据写不进去只降级，不影响请求
        logger.warning("文档缓存写入失败：%s (%s)", path, exc)


def _remove(path: str) -> None:
    try:
        os.unlink(path)
    except OSError:
        pass


# ===================== 小工具 =====================


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _fmt_time(timestamp: float) -> str:
    return datetime.fromtimestamp(timestamp).strftime("%Y-%m-%d %H:%M:%S")


def _format_of(name: str) -> str:
    ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
    if ext == "xml":
        return "xml"
    if ext == "json":
        return "json"
    if ext in TEXT_FORMATS:
        return "text"
    return "other"
