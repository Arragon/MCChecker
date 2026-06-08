import hashlib
import json
import logging
import os
import time
from typing import Any, Dict, Optional, Tuple

logger = logging.getLogger(__name__)


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
    return hashlib.sha1(os.path.abspath(source_path).encode("utf-8")).hexdigest()


def _cache_paths(source_path: str) -> Tuple[str, str]:
    base = os.path.join(_get_cache_dir(), _cache_base_name(source_path))
    return f"{base}.meta.json", f"{base}.tree.json"


def invalidate(source_path: str) -> bool:
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
    meta_path, tree_path = _cache_paths(source_path)
    if not os.path.exists(meta_path) or not os.path.exists(tree_path):
        return None
    if not os.path.exists(source_path):
        invalidate(source_path)
        return None
    try:
        stat = os.stat(source_path)
        with open(meta_path, "r", encoding="utf-8") as f:
            meta = json.load(f)
        if meta.get("mtime_ns") != stat.st_mtime_ns or meta.get("size") != stat.st_size:
            invalidate(source_path)
            return None
        with open(tree_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        invalidate(source_path)
        return None


def save_tree(source_path: str, tree: Dict[str, Any]) -> None:
    if not os.path.exists(source_path):
        return
    meta_path, tree_path = _cache_paths(source_path)
    try:
        stat = os.stat(source_path)
        meta = {
            "source_path": os.path.abspath(source_path),
            "mtime_ns": stat.st_mtime_ns,
            "size": stat.st_size,
            "cached_at": int(time.time()),
            "is_archive": _is_archive_path(source_path),
        }
        os.makedirs(os.path.dirname(meta_path), exist_ok=True)

        tmp_meta = f"{meta_path}.tmp"
        tmp_tree = f"{tree_path}.tmp"
        with open(tmp_tree, "w", encoding="utf-8") as f:
            json.dump(tree, f, ensure_ascii=False)
        with open(tmp_meta, "w", encoding="utf-8") as f:
            json.dump(meta, f, ensure_ascii=False)
        os.replace(tmp_tree, tree_path)
        os.replace(tmp_meta, meta_path)
    except OSError as e:
        logger.warning("保存解析缓存失败: %s", e)


def cleanup_archive_tree_cache(ttl_days: int = 7, max_entries: int = 500) -> Dict[str, int]:
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

