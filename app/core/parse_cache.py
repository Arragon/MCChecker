"""解析缓存层

基于 source content hash + algorithm version 作为 cache key，
确保内容不变时命中缓存，版本不匹配时 miss。
"""

import hashlib
import json
import logging
import os
import time
from typing import Any, Dict, Optional, Tuple

from . import parser as _parser
from .storage import atomic_write_json

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 版本常量 —— 任一变化即导致所有旧缓存失效
# ---------------------------------------------------------------------------
PARSER_VERSION = "2.0"          # 与 parser._PARSER_VERSION 对齐
CACHE_SCHEMA_VERSION = "2.0"    # 缓存 schema 自身版本


# ---------------------------------------------------------------------------
# 内部工具
# ---------------------------------------------------------------------------

def _get_cache_dir() -> str:
    from . import storage
    storage._ensure_dirs()
    return storage.get_parse_cache_dir()


def _is_archive_path(source_path: str) -> bool:
    from . import storage
    try:
        archive_root = os.path.abspath(storage.get_archive_root_dir())
        return os.path.commonpath([os.path.abspath(source_path), archive_root]) == archive_root
    except ValueError:
        return False


def _cache_base_name(source_path: str) -> str:
    """基于源文件路径的缓存文件基础名（sha1），保持同一文件复用同一缓存槽位。"""
    return hashlib.sha1(os.path.abspath(source_path).encode("utf-8")).hexdigest()


def _cache_paths(source_path: str) -> Tuple[str, str]:
    base = os.path.join(_get_cache_dir(), _cache_base_name(source_path))
    return f"{base}.meta.json", f"{base}.tree.json"


def _make_cache_key(content_hash: str, binding_revision: str = "") -> str:
    """生成 cache key：content_hash + parser version + schema version [+ binding]

    任一版本变化都会产生不同的 key，从而令旧缓存 miss。
    """
    key_parts = f"{content_hash}:{PARSER_VERSION}:{CACHE_SCHEMA_VERSION}"
    if binding_revision:
        key_parts += f":{binding_revision}"
    return hashlib.sha256(key_parts.encode()).hexdigest()[:16]


def _read_source_hash(source_path: str) -> Optional[str]:
    """读取源文件并返回 content hash；文件不存在返回 None。"""
    try:
        with open(source_path, "rb") as f:
            return _parser.compute_content_hash(f.read())
    except OSError:
        return None


# ---------------------------------------------------------------------------
# 公开 API
# ---------------------------------------------------------------------------

def invalidate(source_path: str) -> bool:
    """删除指定源文件的缓存"""
    meta_path, tree_path = _cache_paths(source_path)
    removed = False
    for p in (meta_path, tree_path):
        if os.path.exists(p):
            try:
                os.remove(p)
                removed = True
            except OSError:
                pass
    return removed


def load_tree(source_path: str) -> Optional[Dict[str, Any]]:
    """加载解析缓存

    验证逻辑：
    1. 源文件必须存在
    2. 读取源文件内容计算 content_hash
    3. meta 中的 cache_key 必须与当前 hash + 版本匹配
    4. tree 文件必须可读
    任何一步失败 → miss（删除旧缓存），返回 None
    """
    meta_path, tree_path = _cache_paths(source_path)
    if not os.path.exists(meta_path) or not os.path.exists(tree_path):
        return None
    if not os.path.exists(source_path):
        invalidate(source_path)
        return None

    # 计算当前内容的 hash
    content_hash = _read_source_hash(source_path)
    if content_hash is None:
        invalidate(source_path)
        return None

    expected_key = _make_cache_key(content_hash)

    try:
        with open(meta_path, "r", encoding="utf-8") as f:
            meta = json.load(f)
    except (OSError, json.JSONDecodeError):
        invalidate(source_path)
        return None

    # 版本 & hash 校验
    if meta.get("cache_key") != expected_key:
        invalidate(source_path)
        return None

    try:
        with open(tree_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        # tree 损坏 —— 只删缓存，不碰源文件
        invalidate(source_path)
        return None


def save_tree(source_path: str, tree: Dict[str, Any]) -> None:
    """保存解析结果到缓存

    使用 content hash + version 作为 cache key，
    通过 atomic_write_json 保证写入原子性。
    """
    if not os.path.exists(source_path):
        return

    content_hash = _read_source_hash(source_path)
    if content_hash is None:
        return

    cache_key = _make_cache_key(content_hash)
    meta_path, tree_path = _cache_paths(source_path)

    meta = {
        "source_path": os.path.abspath(source_path),
        "cache_key": cache_key,
        "content_hash": content_hash,
        "parser_version": PARSER_VERSION,
        "cache_schema_version": CACHE_SCHEMA_VERSION,
        "cached_at": int(time.time()),
        "is_archive": _is_archive_path(source_path),
    }

    try:
        os.makedirs(os.path.dirname(meta_path), exist_ok=True)
        # 先写 tree（大文件），再写 meta（小文件）
        # 如果 tree 写入失败，meta 不更新，旧缓存仍会被 hash 校验淘汰
        atomic_write_json(tree_path, tree)
        atomic_write_json(meta_path, meta)
    except OSError as e:
        logger.warning("保存解析缓存失败: %s", e)


def clear_all() -> Dict[str, int]:
    """清空所有解析缓存（不触碰 raw archive / current 文件）"""
    cache_dir = _get_cache_dir()
    stats = {"scanned": 0, "removed": 0}
    try:
        entries = os.listdir(cache_dir)
    except OSError:
        return stats

    for fname in entries:
        fpath = os.path.join(cache_dir, fname)
        if not os.path.isfile(fpath):
            continue
        if fname.endswith((".meta.json", ".tree.json")):
            stats["scanned"] += 1
            try:
                os.remove(fpath)
                stats["removed"] += 1
            except OSError:
                pass
    return stats


def cleanup_archive_tree_cache(ttl_days: int = 7, max_entries: int = 500) -> Dict[str, int]:
    """清理归档文件的解析缓存

    仅删除 cache 文件，不触碰 raw archive / current。
    """
    cache_dir = _get_cache_dir()
    now = int(time.time())
    ttl_seconds = max(ttl_days, 0) * 86400

    stats = {"scanned": 0, "removed": 0, "kept": 0}
    kept: list[tuple[int, str]] = []

    try:
        files = [f for f in os.listdir(cache_dir) if f.endswith(".meta.json")]
    except OSError:
        return stats

    for fname in files:
        meta_path = os.path.join(cache_dir, fname)
        base = meta_path[:-len(".meta.json")]
        tree_path = base + ".tree.json"
        stats["scanned"] += 1

        try:
            with open(meta_path, "r", encoding="utf-8") as f:
                meta = json.load(f)
        except (OSError, json.JSONDecodeError):
            for p in (meta_path, tree_path):
                if os.path.exists(p):
                    try:
                        os.remove(p)
                        stats["removed"] += 1
                    except OSError:
                        pass
            continue

        if not meta.get("is_archive", False):
            stats["kept"] += 1
            continue

        source_path = meta.get("source_path") or ""
        cached_at = int(meta.get("cached_at") or 0)

        should_remove = False
        if not source_path or not os.path.exists(source_path):
            should_remove = True
        elif ttl_seconds and (now - cached_at) > ttl_seconds:
            should_remove = True

        if should_remove:
            for p in (meta_path, tree_path):
                if os.path.exists(p):
                    try:
                        os.remove(p)
                        stats["removed"] += 1
                    except OSError:
                        pass
        else:
            kept.append((cached_at, base))

    if max_entries >= 0 and len(kept) > max_entries:
        kept.sort(key=lambda x: x[0])
        for _, base in kept[: len(kept) - max_entries]:
            meta_path = base + ".meta.json"
            tree_path = base + ".tree.json"
            for p in (meta_path, tree_path):
                if os.path.exists(p):
                    try:
                        os.remove(p)
                        stats["removed"] += 1
                    except OSError:
                        pass

    stats["kept"] = max(stats["scanned"] - stats["removed"], 0)
    return stats
