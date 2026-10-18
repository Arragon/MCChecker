"""文档内存缓存的条目上限按机型文件数放大（M4-05 / INH-1907；§6.5、§12；决策 17 / D3）。

这几条只钉本卡改动的东西：条目上限不再钉死 24、下限仍可单独钉住（基准用它复现旧
配置）、字节上限仍是硬护栏、放大不越权（不建目录、不影响别的机型、跨机型共用 LRU 时
仍按字节逐出）。搜索耗时本身由 `scripts/bench_api.py` 测量，不在这里做计时断言——
计时断言在 CI 机器上必然是恒真或恒炸，两种都无意义。

缓存是**进程内全局单例**，`test_api_documents.py` 等用例会往里塞东西，所以每个用例
都自己 `reset_cache()`；凡是用「缓存内字节数」当算术输入的地方都先断言条目数是 1。
"""

from __future__ import annotations

import os
from typing import List

from app.core import storage
from app.services import documents

XML = b'<?xml version="1.0"?><R a="1"><item>x</item><param_1 min="0">5</param_1></R>'


def seed_configs(pid: str, count: int, prefix: str = "cfg") -> List[str]:
    storage.set_active_profile(pid)
    names = ["%s_%03d.xml" % (prefix, index) for index in range(count)]
    for name in names:
        storage.save_config_file(name, XML)
    return names


def loaded_body_bytes() -> int:
    """当前内存缓存里的响应体总字节；调用前必须自己确认缓存是空的。"""
    return documents.cache_info()["bytes"]


class TestEntryLimitScales:
    def setup_method(self) -> None:
        # 内存缓存是进程内全局单例，别的用例（尤其 `test_api_documents.py`）会往里塞东西。
        documents.reset_cache()

    def test_files_above_the_floor_all_stay_resident(self) -> None:
        """30 个文件 > 旧的钉死 24：按 `max(24, 2×30)` 放大后 30 项都要留在缓存里。

        钉死 24 时这里只会剩 24 项，其余 6 项每次重取都要读原文 → gunzip →
        `json.loads` → `json.dumps`（决策 17 实测的搜索超预算根因）。
        """
        pid = storage.DEFAULT_PROFILE
        names = seed_configs(pid, 30)
        for name in names:
            documents.document_bytes(pid, name, "current")

        assert documents.cache_info()["entries"] == 30

        documents.reset_cache()
        documents.document_bytes(pid, names[0], "current")
        misses_after_first = documents.cache_info()["misses"]
        documents.document_bytes(pid, names[0], "current")
        info = documents.cache_info()
        assert info["misses"] == misses_after_first  # 第二次是命中，没再读盘
        assert info["hits"] == 1

    def test_floor_still_binds_when_multiplier_pinned_to_zero(self, monkeypatch) -> None:
        """`MEMORY_CACHE_FILES_MULTIPLIER = 0` 回到旧语义：条目数钉死在下限。

        这条是「下限仍然有效」的对照——基准脚本同样用它复现决策 17 的旧配置。
        """
        monkeypatch.setattr(documents, "MEMORY_CACHE_FILES_MULTIPLIER", 0)
        monkeypatch.setattr(documents, "MEMORY_CACHE_MAX_ENTRIES", 4)
        pid = storage.DEFAULT_PROFILE
        names = seed_configs(pid, 6)
        assert documents.memory_cache_limit(pid) == 4  # 6 个文件也不放大
        for name in names:
            documents.document_bytes(pid, name, "current")
        assert documents.cache_info()["entries"] == 4

    def test_byte_cap_still_evicts_regardless_of_entry_scaling(self, monkeypatch) -> None:
        """§12「+LRU ≤ 256 MB」才是硬护栏：条目上限很宽时字节上限一撞就逐出。

        字节上限按「单个响应体的 2.5 倍」设定，所以第 3 个文档一定装不下。
        """
        pid = storage.DEFAULT_PROFILE
        names = seed_configs(pid, 6)
        documents.reset_cache()
        documents.document_bytes(pid, names[0], "current")
        assert documents.cache_info()["entries"] == 1  # 总字节此刻就等于单个体积
        single = loaded_body_bytes()
        assert single > 0
        monkeypatch.setattr(documents, "MEMORY_CACHE_MAX_BYTES", int(single * 2.5))

        for name in names[1:]:
            documents.document_bytes(pid, name, "current")

        info = documents.cache_info()
        assert info["entries"] == 2
        assert info["bytes"] <= documents.MEMORY_CACHE_MAX_BYTES


class TestLimitComputation:
    def test_shipped_floor_is_still_24(self) -> None:
        """下限本身没被本卡挪动（决策 17 只说不要钉死，没说放宽 24 这个地板）。"""
        assert documents.MEMORY_CACHE_MAX_ENTRIES == 24

    def test_more_files_always_beat_the_floor(self, monkeypatch) -> None:
        """只要机型文件数超过下限，容量就必须真的更长（倍数取 1，即 30 文件 → 30 项）。"""
        monkeypatch.setattr(documents, "MEMORY_CACHE_FILES_MULTIPLIER", 1)
        pid = storage.DEFAULT_PROFILE
        seed_configs(pid, 30)
        assert documents.memory_cache_limit(pid) > documents.MEMORY_CACHE_MAX_ENTRIES

    def test_formula_is_floor_and_file_count(self, monkeypatch) -> None:
        pid = storage.DEFAULT_PROFILE
        names = seed_configs(pid, 5)
        monkeypatch.setattr(documents, "MEMORY_CACHE_MAX_ENTRIES", 3)
        monkeypatch.setattr(documents, "MEMORY_CACHE_FILES_MULTIPLIER", 2)
        assert documents.memory_cache_limit(pid) == 10  # 2 × 5 个文件
        monkeypatch.setattr(documents, "MEMORY_CACHE_FILES_MULTIPLIER", 1)
        assert documents.memory_cache_limit(pid) == 5
        monkeypatch.setattr(documents, "MEMORY_CACHE_FILES_MULTIPLIER", 0)
        assert documents.memory_cache_limit(pid) == 3  # 放大关掉 → 只剩下限
        assert len(names) == 5

    def test_unknown_profile_falls_back_to_floor_without_creating_dirs(self, monkeypatch) -> None:
        """机型目录不存在 → 文件数按 0 算、落到下限，并且**读路径不创建目录**。"""
        monkeypatch.setattr(documents, "MEMORY_CACHE_MAX_ENTRIES", 7)
        monkeypatch.setattr(documents, "MEMORY_CACHE_FILES_MULTIPLIER", 2)
        assert documents.memory_cache_limit("ghost_model") == 7
        assert not os.path.isdir(
            os.path.join(storage.get_profile_data_dir("ghost_model"), "configs")
        )
