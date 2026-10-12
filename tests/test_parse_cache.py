import json
import os
import time
from unittest import mock

import pytest


class TestParseCache:
    def test_save_and_load_tree(self):
        from app.core import storage, parser, parse_cache

        content = b'{"a": 1, "b": [2, 3]}'
        storage.save_config_file("test.json", content)
        source_path = storage.get_config_path("test.json")

        tree = parser.parse_file(content, "test.json")
        parse_cache.save_tree(source_path, tree)

        cached = parse_cache.load_tree(source_path)
        assert cached == tree

    def test_cache_invalidated_on_file_change(self):
        from app.core import storage, parser, parse_cache

        content = b'{"a": 1}'
        storage.save_config_file("test.json", content)
        source_path = storage.get_config_path("test.json")

        tree = parser.parse_file(content, "test.json")
        parse_cache.save_tree(source_path, tree)

        with open(source_path, "wb") as f:
            f.write(b'{"a": 2}')
        os.utime(source_path, (time.time() + 10, time.time() + 10))

        cached = parse_cache.load_tree(source_path)
        assert cached is None

    def test_cleanup_archive_cache_ttl(self):
        from app.core import storage, parser, parse_cache

        archive_dir = storage.get_archive_dir("test.xml")
        archive_path = os.path.join(archive_dir, "test_20260101.xml")
        content = b"<config><a>1</a></config>"
        with open(archive_path, "wb") as f:
            f.write(content)

        tree = parser.parse_file(content, "test_20260101.xml")
        parse_cache.save_tree(archive_path, tree)

        meta_path, _ = parse_cache._cache_paths(archive_path)
        with open(meta_path, "r", encoding="utf-8") as f:
            meta = json.load(f)
        meta["cached_at"] = int(time.time()) - 86400 * 9
        with open(meta_path, "w", encoding="utf-8") as f:
            json.dump(meta, f, ensure_ascii=False)

        parse_cache.cleanup_archive_tree_cache(ttl_days=7, max_entries=500)
        assert parse_cache.load_tree(archive_path) is None

    def test_cleanup_archive_cache_max_entries(self):
        from app.core import storage, parser, parse_cache

        archive_dir = storage.get_archive_dir("test.xml")

        a1 = os.path.join(archive_dir, "test_20260101.xml")
        a2 = os.path.join(archive_dir, "test_20260102.xml")
        content = b"<config><a>1</a></config>"
        for p in (a1, a2):
            with open(p, "wb") as f:
                f.write(content)

        t1 = parser.parse_file(content, "test_20260101.xml")
        t2 = parser.parse_file(content, "test_20260102.xml")
        parse_cache.save_tree(a1, t1)
        parse_cache.save_tree(a2, t2)

        m1, _ = parse_cache._cache_paths(a1)
        m2, _ = parse_cache._cache_paths(a2)
        with open(m1, "r", encoding="utf-8") as f:
            meta1 = json.load(f)
        with open(m2, "r", encoding="utf-8") as f:
            meta2 = json.load(f)
        meta1["cached_at"] = int(time.time()) - 200
        meta2["cached_at"] = int(time.time()) - 100
        with open(m1, "w", encoding="utf-8") as f:
            json.dump(meta1, f, ensure_ascii=False)
        with open(m2, "w", encoding="utf-8") as f:
            json.dump(meta2, f, ensure_ascii=False)

        parse_cache.cleanup_archive_tree_cache(ttl_days=999, max_entries=1)

        remaining = [p for p in (a1, a2) if parse_cache.load_tree(p) is not None]
        assert len(remaining) == 1
        assert remaining[0] == a2


class TestCacheConsistency:
    """T10: parse/diff cache 使用 source content hash + algorithm version 作为 key"""

    def test_same_mtime_size_different_bytes_miss(self):
        """same mtime/size but different bytes → cache miss"""
        from app.core import storage, parser, parse_cache

        content_a = b'{"x": 1}'
        content_b = b'{"y": 1}'
        assert len(content_a) == len(content_b)  # same size

        storage.save_config_file("consist.json", content_a)
        source_path = storage.get_config_path("consist.json")

        tree = parser.parse_file(content_a, "consist.json")
        parse_cache.save_tree(source_path, tree)
        assert parse_cache.load_tree(source_path) is not None

        # overwrite with different content of same length, keep mtime
        stat = os.stat(source_path)
        with open(source_path, "wb") as f:
            f.write(content_b)
        os.utime(source_path, (stat.st_atime, stat.st_mtime))

        # different bytes → different hash → miss
        assert parse_cache.load_tree(source_path) is None

    def test_parser_version_change_invalidates(self):
        """parser version 改变 → cache miss"""
        from app.core import storage, parser, parse_cache

        content = b'{"v": 1}'
        storage.save_config_file("pver.json", content)
        source_path = storage.get_config_path("pver.json")

        tree = parser.parse_file(content, "pver.json")
        parse_cache.save_tree(source_path, tree)
        assert parse_cache.load_tree(source_path) is not None

        # 篡改 meta 中的 parser_version 模拟版本升级
        meta_path, _ = parse_cache._cache_paths(source_path)
        with open(meta_path, "r", encoding="utf-8") as f:
            meta = json.load(f)
        meta["parser_version"] = "99.0"
        meta["cache_key"] = "stale"
        with open(meta_path, "w", encoding="utf-8") as f:
            json.dump(meta, f)

        assert parse_cache.load_tree(source_path) is None

    def test_schema_version_change_invalidates(self):
        """cache schema version 改变 → cache miss"""
        from app.core import storage, parser, parse_cache

        content = b'{"s": 1}'
        storage.save_config_file("sver.json", content)
        source_path = storage.get_config_path("sver.json")

        tree = parser.parse_file(content, "sver.json")
        parse_cache.save_tree(source_path, tree)
        assert parse_cache.load_tree(source_path) is not None

        meta_path, _ = parse_cache._cache_paths(source_path)
        with open(meta_path, "r", encoding="utf-8") as f:
            meta = json.load(f)
        meta["cache_schema_version"] = "99.0"
        meta["cache_key"] = "stale"
        with open(meta_path, "w", encoding="utf-8") as f:
            json.dump(meta, f)

        assert parse_cache.load_tree(source_path) is None

    def test_corrupt_tree_returns_none_deletes_cache(self):
        """half / corrupt cache → 返回 None，删除缓存但不碰源文件"""
        from app.core import storage, parser, parse_cache

        content = b'{"c": 1}'
        storage.save_config_file("corrupt.json", content)
        source_path = storage.get_config_path("corrupt.json")

        tree = parser.parse_file(content, "corrupt.json")
        parse_cache.save_tree(source_path, tree)

        # 损坏 tree 文件
        _, tree_path = parse_cache._cache_paths(source_path)
        with open(tree_path, "w", encoding="utf-8") as f:
            f.write("{not valid json")

        assert parse_cache.load_tree(source_path) is None
        # 源文件仍在
        assert os.path.exists(source_path)
        # 缓存已被清理
        meta_path, _ = parse_cache._cache_paths(source_path)
        assert not os.path.exists(meta_path)
        assert not os.path.exists(tree_path)

    def test_clear_all_removes_cache_not_source(self):
        """clear_all 清空缓存后业务正常，不碰 raw 文件"""
        from app.core import storage, parser, parse_cache

        content = b'{"clear": 1}'
        storage.save_config_file("clearme.json", content)
        source_path = storage.get_config_path("clearme.json")

        tree = parser.parse_file(content, "clearme.json")
        parse_cache.save_tree(source_path, tree)
        assert parse_cache.load_tree(source_path) == tree

        stats = parse_cache.clear_all()
        assert stats["removed"] > 0

        # 缓存已空
        assert parse_cache.load_tree(source_path) is None
        # 源文件仍在
        assert os.path.exists(source_path)
        with open(source_path, "rb") as f:
            assert f.read() == content

        # 重新缓存仍可命中
        parse_cache.save_tree(source_path, tree)
        assert parse_cache.load_tree(source_path) == tree

    def test_cleanup_does_not_touch_raw_archive(self):
        """cleanup / clear_all 不碰 raw archive / current"""
        from app.core import storage, parser, parse_cache

        content = b"<root><z>1</z></root>"
        storage.save_config_file("noclobber.xml", content)
        source_path = storage.get_config_path("noclobber.xml")

        archive_dir = storage.get_archive_dir("noclobber.xml")
        archive_path = os.path.join(archive_dir, "noclobber_20260101.xml")
        with open(archive_path, "wb") as f:
            f.write(content)

        tree = parser.parse_file(content, "noclobber_20260101.xml")
        parse_cache.save_tree(archive_path, tree)
        parse_cache.save_tree(source_path, tree)

        parse_cache.clear_all()
        parse_cache.cleanup_archive_tree_cache(ttl_days=0, max_entries=0)

        # raw 文件完好
        assert os.path.exists(source_path)
        assert os.path.exists(archive_path)

    def test_old_meta_without_cache_key_miss(self):
        """旧格式 meta（无 cache_key 字段）→ miss"""
        from app.core import storage, parser, parse_cache

        content = b'{"old": 1}'
        storage.save_config_file("oldmeta.json", content)
        source_path = storage.get_config_path("oldmeta.json")

        tree = parser.parse_file(content, "oldmeta.json")
        parse_cache.save_tree(source_path, tree)

        # 回退 meta 为旧格式（mtime/size based）
        meta_path, _ = parse_cache._cache_paths(source_path)
        stat = os.stat(source_path)
        old_meta = {
            "source_path": os.path.abspath(source_path),
            "mtime_ns": stat.st_mtime_ns,
            "size": stat.st_size,
            "cached_at": int(time.time()),
            "is_archive": False,
        }
        with open(meta_path, "w", encoding="utf-8") as f:
            json.dump(old_meta, f)

        # 旧格式没有 cache_key → miss
        assert parse_cache.load_tree(source_path) is None

    def test_make_cache_key_deterministic(self):
        """相同输入产生相同 key，不同版本产生不同 key"""
        from app.core.parse_cache import _make_cache_key

        h = "abc123"
        key1 = _make_cache_key(h)
        key2 = _make_cache_key(h)
        assert key1 == key2
        assert len(key1) == 16

        # binding_revision 不同 → key 不同
        key3 = _make_cache_key(h, "rev1")
        key4 = _make_cache_key(h, "rev2")
        assert key3 != key4


