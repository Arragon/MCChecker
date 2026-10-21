"""解析缓存测试

数据目录隔离由 `tests/conftest.py` 的 `isolated_data_root` 夹具统一提供。
"""

from __future__ import annotations

import json
import os
import time


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

