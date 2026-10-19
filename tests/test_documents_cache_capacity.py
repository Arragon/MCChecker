"""文档内存缓存的容量规则（M4-05 / INH-1907；§6.5、§12；决策 17 / D3）。

钉住的是本卡改动涉及的六件事，每条都点名了能让它变红的坏实现：

1. 条目上限不再钉死 24，而是 `max(下限, 倍数 × 机型文件数)`（`TestEntryLimitScales` 第一条、
   `TestLimitComputation` 的算术两条）。
2. 下限仍然有效（`MEMORY_CACHE_FILES_MULTIPLIER = 0` 回到钉死语义；基准也用它复现旧配置）。
3. 字节上限是硬护栏，而且**判据取 §12 的 256 MB、不取代码常量**：一个体积就越过上限的条目必须
   留不住，条目上限同时被抬到不可能绑定（`TestByteCeiling`）。
4. 越界时走掉的是**最久未使用**那条——不是最早插入的（FIFO），也不是刚塞进来的（MRU）
   （`TestEvictionOrder`：用例显式「碰一下 A」再挤进 C。只往里塞、从不访问的夹具分不出 LRU 与 FIFO）。
5. 放大不越权：读路径对整个数据根的目录集合零增长、容量按**参数机型**算而不是按当前上下文机型算
   （`TestLimitComputation` 后两条 + `all_directories()`）。
6. 机型共用**同一把**进程内 LRU（`test_profiles_share_one_lru` 钉「同一把」，第 4 条钉「顺序」）。

搜索耗时本身由 `scripts/bench_api.py` 测量，不在这里做计时断言——计时断言在 CI 机器上必然是
恒真或恒炸，两种都无意义。`TestBenchScaleGate` 测的是基准里那条**规模闸门**（纯算术、无计时）：
它决定 §12 的规模行在这一轮能不能判，和第 3、4 条是同一个诉求——没跑到声明规模的运行不许打
PASS（M4-05 评审 Important 1）。本卡的文件面只有这一个测试文件，所以它放在这里。

缓存是**进程内全局单例**，`test_api_documents.py` 等用例会往里塞东西，所以
`TestEntryLimitScales` / `TestByteCeiling` / `TestEvictionOrder` 每个用例先 `reset_cache()`
（`TestLimitComputation` 里除新增那条外都只做容量算术，不读缓存计数，无需清）；凡是用
「缓存内字节数」当算术输入的地方都先断言条目数是 1。

**为什么需要目录集合快照**（M4-05 假绿审计）：`tests/conftest.py::isolated_data_root`
在建夹具时已经调过 `storage._ensure_dirs()`，default 的骨架目录在用例开始前就存在，所以
「调完之后那个目录还是不存在」这种断言只在**没人建过它的机型**上才有意义；而且现网
`GET /p/{pid}/files` → `storage.list_config_files()` 开头就会 `_ensure_dirs()`（M4-05 实测：
真实数据根里因此长出 `archive/`、`records/`、`cache/parse_tree/`，见 `docs/know-how.md`），
光数「参数机型的 configs」一个目录抓不到它。下面的用例一律把**整个数据根的目录集合**
拍下来比对，并把当前上下文机型换成一个从没写过的机型，两个坏实现才都会变红。
"""

from __future__ import annotations

import importlib.util
import os
from typing import List

from app.core import storage
from app.services import documents

XML = b'<?xml version="1.0"?><R a="1"><item>x</item><param_1 min="0">5</param_1></R>'

#: §12「服务端内存」行的「+LRU ≤ 256 MB」——**规范里的数字**。这里故意不是
#: `documents.MEMORY_CACHE_MAX_BYTES` 的复读：那个常量正是 `TestByteCeiling` 要监的对象，
#: 拿它当尺子就成了「护栏自己证明自己没越界」（M4-05 评审 Important 2：把常量乘 10，
#: 原先 9 条用例连同全量 933 条一起照绿）。
SPEC_LRU_MEMORY_BUDGET_BYTES = 256 * 1024 * 1024


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
        """§12「+LRU ≤ 256 MB」才是硬护栏：条目上限再宽也挡不住字节上限逐出。

        两处口径（M4-05 评审 Important 2）：
        - 条目上限被抬到 1000（6 个文件远远够不着），所以这里发生的逐出**只可能**是字节护栏干的，
          「按条目数逐出」这个坏实现拦不住本用例；
        - 上限按「单个响应体的 2.5 倍」设定，第 3 个文档一定装不下。这条测的是**机制**
          （字节数参与判定、越界就逐出）；把 `MEMORY_CACHE_MAX_BYTES` 抬高会不会放过越界的驻留，
          由 `TestByteCeiling` 用 §12 的 256 MB 当场判，不在这里靠常量自证。
        """
        pid = storage.DEFAULT_PROFILE
        monkeypatch.setattr(documents, "MEMORY_CACHE_MAX_ENTRIES", 1000)
        monkeypatch.setattr(documents, "MEMORY_CACHE_FILES_MULTIPLIER", 0)
        assert documents.memory_cache_limit(pid) == 1000  # 条目数这条路径本轮不可能绑定
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


class TestByteCeiling:
    """字节护栏按 **§12 的 256 MB** 判，不按代码常量自证（评审 Important 2）。"""

    def setup_method(self) -> None:
        documents.reset_cache()

    def teardown_method(self) -> None:
        documents.reset_cache()

    def test_single_entry_over_the_spec_budget_is_not_retained(self, monkeypatch) -> None:
        """一个体积就越过 §12 上限的条目必须留不住——即使条目上限宽到不可能绑定。

        原先那条字节用例把上限 monkeypatch 成一个小数值，于是把
        `documents.MEMORY_CACHE_MAX_BYTES` 乘 10 谁也看不见（评审实测：单文件 9 passed、
        全量 933 passed）。这条把它换成三个能红的事实：
        ① 判据是 `SPEC_LRU_MEMORY_BUDGET_BYTES`（§12 表格「+LRU ≤ 256 MB」），不是那个常量；
        ② 条目上限抬到 1000、倍数 0 ⇒ 逐出只可能来自字节；
        ③ 体积是真实 `len(body)` 计量的 257 MB 条目，走真实的 `_memory_put` 账目。
        把常量乘 10 ⇒ 257 MB 那条被留下、驻留字节 257 MB > 256 MB ⇒ 本用例红；
        把 `_memory_put` 里的字节判定整个摘掉 ⇒ 同样红。
        `bytes(n)` 是 calloc，页没被写过就不占物理内存，用例仍是亚秒级。
        """
        monkeypatch.setattr(documents, "MEMORY_CACHE_MAX_ENTRIES", 1000)
        monkeypatch.setattr(documents, "MEMORY_CACHE_FILES_MULTIPLIER", 0)
        pid = storage.DEFAULT_PROFILE
        assert documents.memory_cache_limit(pid) == 1000
        body = bytes(SPEC_LRU_MEMORY_BUDGET_BYTES + 1024 * 1024)  # 257 MB：单条就越过上限
        assert len(body) > SPEC_LRU_MEMORY_BUDGET_BYTES

        documents._memory_put(
            ("/test/over-ceiling.xml", len(body), 1),
            documents._MemoryEntry(body=body, etag='"over"', sha256="over", node_count=0),
            pid,
        )

        info = documents.cache_info()
        assert info["bytes"] <= SPEC_LRU_MEMORY_BUDGET_BYTES  # 抬高常量 / 摘掉护栏都在这里变红
        assert info["entries"] == 0  # 条目上限是 1000，能逐出它的只有字节护栏


class TestEvictionOrder:
    """逐出顺序是 LRU（最久**未使用**），不是 FIFO（最早插入）也不是 MRU（刚塞进来的）。"""

    def setup_method(self) -> None:
        documents.reset_cache()

    def test_least_recently_used_entry_is_the_one_that_goes(self, monkeypatch) -> None:
        """塞 A、B 之后**碰一下 A**，再挤进 C：走掉的必须是 B。

        访问顺序不参与判定的夹具分不出 LRU 与 FIFO——原来那个文件里所有用例都只塞不碰，
        所以把 `_memory_put` 的 `popitem(last=False)` 改成 `last=True`（MRU）在全量 933 条上
        一样绿（评审 Important 3）。这里的三步顺序各有用途：
        - 碰 A：让「最近使用」和「最早插入」分开 ⇒ 坏实现「命中不 `move_to_end`」（退化成 FIFO）
          会在最后那句「A 还在」上变红；
        - 挤 C：坏实现 `popitem(last=True)`（MRU）会把刚塞进来的 C 自己逐掉，A、B 都还在
          ⇒ 在最后那句「B 必须读盘」上变红。
        条目上限钉成 2、倍数 0，三个小文档的字节数远够不到 256 MB ⇒ 逐出由条目数触发，
        顺序才是这一行的唯一变量。
        """
        monkeypatch.setattr(documents, "MEMORY_CACHE_MAX_ENTRIES", 2)
        monkeypatch.setattr(documents, "MEMORY_CACHE_FILES_MULTIPLIER", 0)
        pid = storage.DEFAULT_PROFILE
        names = seed_configs(pid, 3)
        documents.reset_cache()

        reads: List[str] = []
        real_read = documents._read_bytes

        def counting(path: str):
            reads.append(os.path.basename(path))
            return real_read(path)

        monkeypatch.setattr(documents, "_read_bytes", counting)

        documents.document_bytes(pid, names[0], "current")  # A 入住
        documents.document_bytes(pid, names[1], "current")  # B 入住
        assert documents.cache_info()["entries"] == 2
        reads.clear()

        documents.document_bytes(pid, names[0], "current")  # 碰一下 A：A 变成最近使用
        assert documents.cache_info()["hits"] == 1  # 这次真的走的是命中路径，`move_to_end` 才有机会生效
        assert reads == []
        reads.clear()

        documents.document_bytes(pid, names[2], "current")  # C 挤进来
        assert documents.cache_info()["entries"] == 2
        assert reads == [names[2]]  # 只有新来的 C 读了盘

        reads.clear()
        documents.document_bytes(pid, names[0], "current")  # A 必须还在（被碰过 = 最近使用）
        assert reads == []
        documents.document_bytes(pid, names[1], "current")  # B 必须已经被逐出（最久未使用）
        assert reads == [names[1]]


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


def load_bench_module():
    """按文件路径加载 `scripts/bench_api.py`（它不是包，也没有入口可 import）。

    顶层只 import 标准库、`app.*` 都在函数里、`main()` 有 `__name__` 守卫，所以 import
    这个模块本身没有副作用（不建语料、不起服务）。与 `test_data_guard_enforcement.py`
    加载真实 conftest 同一个路子。
    """
    scripts_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts")
    path = os.path.join(scripts_dir, "bench_api.py")
    spec = importlib.util.spec_from_file_location("bench_api_under_test", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TestBenchScaleGate:
    """基准的规模闸门：§12 的规模行只有**实测规模够得上那一行**才准判（评审 Important 1）。

    闸门原先看的是 `corpus["synthetic"]`，于是被文档写明的 `--files 3 --nodes 500`（1,497 个
    节点）能在「30 文件 × 3 万节点 ≤ 500 ms」那一行打 PASS。正反两个方向都要钉：小语料判不够，
    默认那轮（30 × 29999）必须仍然判够——反过来，把默认那轮也判成不够的闸门等于「永远不判」，
    那是另一种假绿。
    """

    def test_small_synthetic_corpus_cannot_satisfy_the_30x30k_row(self) -> None:
        bench = load_bench_module()
        declared = (bench.DECLARED_SEARCH_FILES, bench.DECLARED_SEARCH_NODES)
        assert declared == (30, 30000)  # §12 表格「全局搜索」行声明的规模

        # 复现评审那条命令的实测规模：3 文件 × 499 节点 = 1,497 节点。
        small_files, small_nodes = 3, bench.expected_nodes(500)
        assert small_nodes == 499 and small_files * small_nodes == 1497
        assert not bench.row_scale_met(small_files, small_nodes, *declared)

        # 默认那轮必须仍然够得上（`lines_for()` 的整数行只凑得出 29999，那一档取整误差是允许的）。
        default_nodes = bench.expected_nodes(30000)
        assert default_nodes == 29999
        assert bench.row_scale_met(30, default_nodes, *declared)
        assert bench.row_scale_met(1, bench.expected_nodes(bench.DECLARED_COLD_NODES),
                                   1, bench.DECLARED_COLD_NODES)  # 30 万冷那一行
        assert bench.row_scale_met(2, bench.expected_nodes(bench.DECLARED_MID_NODES),
                                   2, bench.DECLARED_MID_NODES)  # 2×5 万对比那一行

        # 只满足其中一个维度也不算满足：文件数够但每文件小一档、每文件够但文件数少。
        assert not bench.row_scale_met(30, bench.expected_nodes(20000), *declared)
        assert not bench.row_scale_met(5, default_nodes, *declared)
        # 节点数压根没量过（`--profile-dir` 的现成文件）⇒ 一律不够，不许拿它冒充那一行。
        assert not bench.row_scale_met(30, 0, *declared)

    def test_tolerance_is_one_generation_step_not_a_whole_tier(self) -> None:
        """容差只覆盖 `lines_for()` 那一档取整（5 个节点），不是随便放宽的门槛。

        写成字面量是故意的：把容差调大（比如 20%）会让 `--nodes 25000` 也在 3 万那一行打 PASS，
        那正是本条要防的东西。
        """
        bench = load_bench_module()
        assert bench.SCALE_NODE_TOLERANCE == 5
        assert bench.row_scale_met(30, 30000 - 5, 30, 30000)
        assert not bench.row_scale_met(30, 30000 - 6, 30, 30000)
