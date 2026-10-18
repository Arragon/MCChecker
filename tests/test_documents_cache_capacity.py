"""文档内存缓存的条目上限按机型文件数放大（M4-05 / INH-1907；§6.5、§12；决策 17 / D3）。

钉住的是本卡改动的四件事：条目上限不再钉死 24、下限仍可单独钉住（基准用它复现旧
配置）、字节上限仍是硬护栏、放大不越权（读路径不建目录、容量按**参数机型**算而不是
按当前上下文机型算、机型共用同一把进程内 LRU）。搜索耗时本身由 `scripts/bench_api.py`
测量，不在这里做计时断言——计时断言在 CI 机器上必然是恒真或恒炸，两种都无意义。

缓存是**进程内全局单例**，`test_api_documents.py` 等用例会往里塞东西，所以
`TestEntryLimitScales` 每个用例先 `reset_cache()`（`TestLimitComputation` 里除新增那条
外都只做容量算术，不读缓存计数，无需清）；凡是用「缓存内字节数」当算术输入的地方都先
断言条目数是 1。

**为什么需要目录集合快照**（M4-05 假绿审计）：`tests/conftest.py::isolated_data_root`
在建夹具时已经调过 `storage._ensure_dirs()`，default 的骨架目录在用例开始前就存在，所以
「调完之后那个目录还是不存在」这种断言只在**没人建过它的机型**上才有意义；而且现网
`GET /p/{pid}/files` → `storage.list_config_files()` 开头就会 `_ensure_dirs()`（M4-05 实测：
真实数据根里因此长出 `archive/`、`records/`、`cache/parse_tree/`，见 `docs/know-how.md`），
光数「参数机型的 configs」一个目录抓不到它。下面的用例一律把**整个数据根的目录集合**
拍下来比对，并把当前上下文机型换成一个从没写过的机型，两个坏实现才都会变红。
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


def all_directories() -> List[str]:
    """数据根之下的全部目录（排序后的绝对路径）。

    「读路径不建目录」只能用集合快照来断言：单个目录可以恰好被夹具或写路径提前建好，
    那时「它不存在」是空断言（详见本文件开头）。
    """
    collected: List[str] = []
    for base, dirs, _names in os.walk(storage.DATA_DIR):
        for name in dirs:
            collected.append(os.path.join(base, name))
    return sorted(collected)


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
        """机型目录不存在 → 文件数按 0 算、落到下限，并且**读路径不创建目录**。

        目录断言取整个数据根的快照而不是只看 `ghost_model/configs`：现网那个会顺手补建
        骨架的写法是 `storage._ensure_dirs()`，它建的是**当前上下文机型**的目录，只看
        参数机型那一个目录抓不到（M4-05 假绿审计里那个坏实现就是这么活下来的）。
        """
        monkeypatch.setattr(documents, "MEMORY_CACHE_MAX_ENTRIES", 7)
        monkeypatch.setattr(documents, "MEMORY_CACHE_FILES_MULTIPLIER", 2)
        before = all_directories()
        assert documents.memory_cache_limit("ghost_model") == 7
        assert all_directories() == before

    def test_limit_counts_the_argument_profile_not_the_active_one(self, monkeypatch) -> None:
        """容量数的是**参数那个机型**的 `configs/`，且当前上下文机型一个目录都不长。

        本卡的规则是「按机型内配置文件数」放大，所以「传进来的 pid 真的被用了」是规则的
        一半；另一半是读路径不建目录。两个坏实现分别对应两个曾在 M4-05 审计里活下来的变异：
        ① `memory_cache_limit` 改用 `storage.get_active_profile()`；
        ② 计数前先 `storage._ensure_dirs()`（补建上下文机型的 `configs/archive/records/
        cache/parse_tree`——`GET /p/{pid}/files` 走的就是这条路）。
        这里把上下文机型设成**从没写过的** `model_b`：夹具已经替 default 把骨架建好了，
        换成 default 时 ② 照样绿。
        """
        monkeypatch.setattr(documents, "MEMORY_CACHE_MAX_ENTRIES", 3)
        monkeypatch.setattr(documents, "MEMORY_CACHE_FILES_MULTIPLIER", 1)
        seed_configs("model_a", 5)  # 写路径：save_config_file 会建 model_a 的骨架

        storage.set_active_profile("model_b")  # model_b 只有机型 id，目录还不存在
        before = all_directories()
        assert documents.memory_cache_limit("model_a") == 5  # ① 会变成 max(3, 0)=3
        assert documents.memory_cache_limit("model_b") == 3  # 空机型落到下限
        assert all_directories() == before  # ② 在这里变红

    def test_profiles_share_one_lru(self) -> None:
        """不同机型的文档进的是**同一把**进程内 LRU（驻留数与字节都是全局账）。"""
        pid_a = "model_a"
        pid_b = "model_b"
        names_a = seed_configs(pid_a, 2)
        names_b = seed_configs(pid_b, 2)
        documents.reset_cache()

        documents.document_bytes(pid_a, names_a[0], "current")
        documents.document_bytes(pid_a, names_a[1], "current")
        assert documents.cache_info()["entries"] == 2
        documents.document_bytes(pid_b, names_b[0], "current")

        info = documents.cache_info()
        assert info["entries"] == 3  # 两个机型共用一把缓存，不是每机型一把
        assert info["bytes"] > 0
