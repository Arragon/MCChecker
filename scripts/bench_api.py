"""MCChecker 读路径 API 基准（M4-05 / INH-1907；规范 §12、§16.1、T-27）。

**测的是真实请求链路**：在进程内起 `app/server/http.py::MCServer` + `build_router`
（随机端口，`http.client` 逐请求计时），handler 调的就是 `app/services/*`。脚本自己不
重写任何被测逻辑——并行实现的基准什么也证明不了。唯一在进程内替换的是
`app/services/documents` 的缓存容量常量与 `_read_bytes` 计数代理（决策 8 登记的
「模块级常量是测试可 monkeypatch 钩子」），用途只有两个：在同一份语料上比较不同的
缓存容量、把「被逐出的文件重读了几次」变成可观测数字。

**与 `scripts/bench_search.py` 的分工**（那支脚本是决策 17 的证据，保留不动）：
`bench_search.py` 只管 §12 的全局搜索一行，用 subprocess 起 `run.py`；本脚本是 §16.1
的性能门禁入口，覆盖 §12/§7.3 的全部服务端预算行（含缓存命中、冷解析、文件内筛选、
结构化对比、列表类、修订号轮询），并额外做缓存容量对比——`run.py` 子进程那种形态
没法在不改 `run.py`／不加配置项的前提下换缓存容量。搜索这一行两边同口径（合成语料、
临时数据根、冷/热对照、500 ms），数字可以互相核对。

**预算线**逐条写在表格的「预算线」列，来源是规范 §12 表格与 §7.3。D3
（`docs/MCChecker_M3_M4_PLAN.md` §2.3）已裁定：全局搜索的通过线取 §12 表格的
**500 ms**，不是 §7.6 正文的 300 ms。没有预算线的用例判定列写「仅报告」——不给自己
编一条会过的线，也不把任何计时判定改成恒真（本项目明令禁止这种假绿）。超预算就照红
报出去，退出码非零。

**语料与 H3**：默认语料是在 `tempfile.mkdtemp()` 里按 `--seed` 确定性生成的合成配置
文件，退出时整目录删除，**绝不指向仓库 `data/`**。脚本开工前后各对仓库 `data/` 拍一次
逐文件 sha256 快照，任何差异以退出码 2 失败（自带 tripwire，不靠口头声明；`data/`
不存在时这一条自己说破是空信号）。`--profile-dir` 是给手上有真实数据的人的逃生口，
风险见 `--help`。

退出码：0 全部达标；1 有用例超预算或断言不成立；2 仓库 `data/` 前后不一致（H3）；
3 环境/语料问题（服务起不来、请求非 200 等——失败绝不折算成通过）。

只依赖标准库，Python 3.9.11（H1、H2）。
"""

from __future__ import annotations

import argparse
import builtins
import hashlib
import http.client
import json
import os
import platform
import random
import shutil
import sys
import tempfile
import threading
import time
import unicodedata
import urllib.parse
from typing import Any, Callable, Dict, List, Optional, Tuple

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPO_DATA_DIR = os.path.join(ROOT, "data")
WEB_DIR = os.path.join(ROOT, "app", "web")

#: §12 表格「全局搜索」行的通过线；D3 裁定取表格值（§7.6 正文写 300 ms，不作判据）。
SEARCH_BUDGET_MS = 500.0
#: 一行 `<param_NNNNN desc="…">v</param_NNNNN>` 产生 2 个节点（元素 + 属性），
#: 另有 3 个结构节点（文件名 / CONFIG_ROOT / input）——与 `bench_search.py` 同一算法。
NODES_PER_LINE = 2
STRUCTURAL_NODES = 3
#: 历史/记录列表用例的样本规模（§12 没给这两行定预算 → 只报告）。
HISTORY_VERSIONS = 4
HISTORY_NODES = 5000
#: §12「修订号轮询：50 客户端 × 每 10 s」→ 这里连打 50 次真实请求，逐次计时。
REVISION_POLLS = 50
#: §7.3「纯内存计数」的直接检验：service 层读多少次计数。
REVISION_SNAPSHOTS = 1000

CSRF_HEADER = "X-Requested-With"
CSRF_VALUE = "MCChecker"
#: 放单文件类样本（冷解析 / 命中 / 对比 / 历史）的机型名（`device_models.add_model` 的 slug）。
FILES_PROFILE = "benchfiles"


class BenchError(Exception):
    """环境/契约问题（请求非 200、服务起不来等）。绝不折算成「通过」。"""


# ===================== 语料（合成、确定性、可复现） =====================


def lines_for(nodes: int) -> int:
    return max(1, (nodes - STRUCTURAL_NODES) // NODES_PER_LINE)


def expected_nodes(nodes: int) -> int:
    return STRUCTURAL_NODES + NODES_PER_LINE * lines_for(nodes)


def make_xml(nodes: int, seed: int) -> bytes:
    """一个 `<CONFIG_ROOT><input>` 下 N 行的配置文件，节点数 = `expected_nodes(...)`。

    种子只影响 `desc` 文本：同一 `nodes` 下不同种子得到**节点数相同、内容有差异**的
    文件，正好喂对比用例（决策 17 的语料形态）。
    """
    rng = random.Random(seed)
    parts = [
        '<?xml version="1.0" encoding="UTF-8"?>\n',
        '<CONFIG_ROOT softwareVersion="BENCH">\n  <input>\n',
    ]
    for n in range(lines_for(nodes)):
        parts.append('<param_%05d desc="desc %d">%d</param_%05d>\n' % (n, rng.randrange(1000000), n % 10, n))
    parts.append("  </input>\n</CONFIG_ROOT>\n")
    return "".join(parts).encode("utf-8")


def build_corpus(
    data_dir: str, files: int, nodes: int, big_nodes: int, mid_nodes: int, seed: int
) -> Dict[str, Any]:
    """两个机型：`default` 只放搜索语料（§12 那行的规模），另一个放单文件类样本。

    分开机型不是省事：搜索用例的样本必须**恰好**是「N 文件 × M 节点」，把 30 万节点的
    大文件混进同一个 `configs/` 会让搜索行测到 34 个文件、结论一钱不值。
    """
    from app.core import device_models, storage  # noqa: E402

    search_pid = storage.DEFAULT_PROFILE
    files_pid = device_models.add_model(FILES_PROFILE)
    configs_search = os.path.join(data_dir, "profiles", search_pid, "configs")
    configs_files = os.path.join(data_dir, "profiles", files_pid, "configs")
    for directory in (configs_search, configs_files):
        os.makedirs(directory, exist_ok=True)

    sizes: Dict[str, int] = {}

    def write(directory: str, name: str, content: bytes) -> None:
        with open(os.path.join(directory, name), "wb") as fh:
            fh.write(content)
        sizes["%s/%s" % (os.path.basename(os.path.dirname(directory)), name)] = len(content)

    search_files = ["bench_%02d.xml" % index for index in range(files)]
    for index, name in enumerate(search_files):
        write(configs_search, name, make_xml(nodes, seed + index))
    big_name = "bench_big.xml"
    write(configs_files, big_name, make_xml(big_nodes, seed + 900000))
    mid_names = ["bench_mid_a.xml", "bench_mid_b.xml"]
    for index, name in enumerate(mid_names):
        write(configs_files, name, make_xml(mid_nodes, seed + 800000 + index))
    history_name = "bench_hist.xml"
    write(configs_files, history_name, make_xml(HISTORY_NODES, seed + 700000))

    profile_dir = os.path.join(data_dir, "profiles", files_pid)
    for kind in ("archive", "records"):
        target = os.path.join(profile_dir, kind, history_name)
        os.makedirs(target, exist_ok=True)
        for index in range(HISTORY_VERSIONS):
            # 版本排序按 mtime（services/history._list_versions），文件名只影响展示。
            write(target, "v%02d_2026-10-18.xml" % index, make_xml(HISTORY_NODES, seed + 700001 + index))

    # 真实部署里每个机型都有身份/映射文件（§5.1）；`/revision` 的「不读磁盘」只有把它们
    # 放进语料才测得出真相——缺文件时 `fsutil.read_json` 连 open 都不做，会假绿。
    for pid in (search_pid, files_pid):
        for name in ("ip_mapping.json", "admin_users.json", "config_mapping.json", "favorites.json"):
            path = os.path.join(data_dir, "profiles", pid, name)
            with open(path, "wb") as fh:
                fh.write(b"[]\n")

    inventory = "".join("%s:%d\n" % (key, sizes[key]) for key in sorted(sizes))
    return {
        "search_pid": search_pid,
        "files_pid": files_pid,
        "search_files": search_files,
        "search_nodes": expected_nodes(nodes),
        "big_name": big_name,
        "big_nodes": expected_nodes(big_nodes),
        "mid_names": mid_names,
        "mid_nodes": expected_nodes(mid_nodes),
        "history_name": history_name,
        "history_nodes": expected_nodes(HISTORY_NODES),
        "bytes": sum(sizes.values()),
        "digest": hashlib.sha256(inventory.encode("utf-8")).hexdigest()[:16],
        "synthetic": True,
    }


def adopt_data_root(data_dir: str) -> Dict[str, Any]:
    """`--profile-dir`：用数据根里已有的文件做语料（不生成、不删除、不新建机型）。"""
    from app.core import storage  # noqa: E402

    pid = storage.DEFAULT_PROFILE
    configs = os.path.join(data_dir, "profiles", pid, "configs")
    names = []
    if os.path.isdir(configs):
        names = sorted(entry for entry in os.listdir(configs) if os.path.isfile(os.path.join(configs, entry)))
    if len(names) < 3:
        raise BenchError("--profile-dir 的数据根里配置文件少于 3 个（找到 %d 个）：%s" % (len(names), configs))
    return {
        "search_pid": pid,
        "files_pid": pid,
        "search_files": names,
        "search_nodes": 0,  # 真实数据：节点数只有解析后才知道，主表按实测写
        "big_name": names[0],
        "big_nodes": 0,
        "mid_names": names[1:3],
        "mid_nodes": 0,
        "history_name": names[0],
        "history_nodes": 0,
        "bytes": 0,
        "digest": "真实数据（不比对内容指纹）",
        "synthetic": False,
    }


# ===================== 仓库 data/ 的 H3 tripwire =====================


def snapshot_repo_data() -> Dict[str, str]:
    """仓库 `data/` 的逐文件 sha256（键 = 相对数据根的 posix 路径）；不存在则空字典。

    与 `tests/data_guard.py` 同口径，但不 import 测试目录。数据根一级的
    `.mcchecker.lock` 是运行时产物，不计入（深层同名文件照样算）。
    """
    collected: Dict[str, str] = {}
    if not os.path.isdir(REPO_DATA_DIR):
        return collected
    for base, _dirs, names in os.walk(REPO_DATA_DIR):
        for name in names:
            path = os.path.join(base, name)
            if name == ".mcchecker.lock" and os.path.realpath(base) == os.path.realpath(REPO_DATA_DIR):
                continue
            try:
                with open(path, "rb") as fh:
                    digest = hashlib.sha256(fh.read()).hexdigest()
            except OSError:
                digest = "<读不到>"
            collected[os.path.relpath(path, REPO_DATA_DIR).replace(os.sep, "/")] = digest
    return collected


def diff_repo_data(before: Dict[str, str], after: Dict[str, str]) -> List[str]:
    problems = ["新增：%s" % key for key in sorted(set(after) - set(before))]
    problems += ["消失：%s" % key for key in sorted(set(before) - set(after))]
    problems += ["内容变化：%s" % key for key in sorted(set(before) & set(after)) if before[key] != after[key]]
    return problems


# ===================== 观测代理 =====================


class OpenCounter:
    """临时接管 `builtins.open`，记录落在数据根之内的打开动作。

    §7.3 的「不读磁盘」不该由脚本口头宣布。`gzip.open` 也走这条路（3.9.11 实测：只
    重绑 `builtins.open` 就能抓到 `.json.gz` 的打开），所以磁盘缓存重取同样计数在内。
    """

    def __init__(self, data_dir: str) -> None:
        self.root = os.path.realpath(data_dir) + os.sep
        self.paths: List[str] = []
        self._real = builtins.open
        self._on = False

    def __enter__(self) -> "OpenCounter":
        self.paths = []
        if not self._on:
            self._on = True
            builtins.open = self._spy  # type: ignore[assignment]
        return self

    def __exit__(self, *_exc: Any) -> None:
        if self._on:
            builtins.open = self._real  # type: ignore[assignment]
            self._on = False

    def _spy(self, file: Any, *args: Any, **kwargs: Any) -> Any:
        try:
            path = os.path.realpath(str(file))
        except (TypeError, ValueError):
            path = ""
        if path.startswith(self.root):
            self.paths.append(path)
        return self._real(file, *args, **kwargs)

    def rel(self, fragments: Tuple[str, ...] = (), limit: int = 3) -> str:
        """去重后的相对路径列表（只给前 `limit` 个，多了把表格撑爆）。"""
        seen: List[str] = []
        for path in self.paths:
            rel = path[len(self.root) :].replace(os.sep, "/")
            if fragments and not any(frag in rel for frag in fragments):
                continue
            if rel not in seen:
                seen.append(rel)
        if not seen:
            return "无"
        tail = "，另 %d 种" % (len(seen) - limit) if len(seen) > limit else ""
        return "%s%s" % (", ".join(seen[:limit]), tail)


class RefetchCounter:
    """数 `documents._read_bytes` 被调用几次 = 被逐出的文件重读 + 重新反序列化的次数。

    用现成钩子而不是猜：`tests/test_api_documents.py` 就是这么证明「命中不读盘」的。
    """

    def __init__(self, documents: Any) -> None:
        self.documents = documents
        self.count = 0
        self._real = documents._read_bytes
        self._on = False

    def __enter__(self) -> "RefetchCounter":
        self.count = 0
        outer = self

        def counting(path: str) -> Tuple[bytes, Any]:
            outer.count += 1
            return outer._real(path)

        if not self._on:
            self._on = True
            self.documents._read_bytes = counting
        return self

    def __exit__(self, *_exc: Any) -> None:
        if self._on:
            self.documents._read_bytes = self._real
            self._on = False


# ===================== HTTP 客户端 =====================


class Api:
    def __init__(self, port: int) -> None:
        self.port = port

    def request(
        self,
        method: str,
        path: str,
        body: Optional[bytes] = None,
        accept_encoding: str = "identity",
    ) -> Tuple[int, bytes]:
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=180)
        try:
            headers = {CSRF_HEADER: CSRF_VALUE, "Accept-Encoding": accept_encoding}
            if body is not None:
                headers["Content-Type"] = "application/json"
            conn.request(method, path, body=body, headers=headers)
            response = conn.getresponse()
            return response.status, response.read()
        finally:
            conn.close()

    def expect(self, method: str, path: str, body: Optional[bytes] = None,
               accept_encoding: str = "identity") -> Tuple[float, bytes]:
        """计时一次请求；非 200 直接终止基准（把 4xx/5xx 当样本会是假绿）。"""
        started = time.perf_counter()
        status, payload = self.request(method, path, body, accept_encoding)
        elapsed = (time.perf_counter() - started) * 1000.0
        if status != 200:
            raise BenchError(
                "%s %s → %d：%s" % (method, path, status, payload[:200].decode("utf-8", "replace"))
            )
        return elapsed, payload

    def timed(self, method: str, path: str, body: Optional[bytes] = None,
              accept_encoding: str = "identity") -> Tuple[float, Dict[str, Any]]:
        _elapsed, payload = self.expect(method, path, body, accept_encoding)
        return _elapsed, json.loads(payload.decode("utf-8"))

    def wait_ready(self, timeout: float = 30.0) -> None:
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                if self.request("GET", "/api/v1/session")[0] == 200:
                    return
            except OSError:
                pass
            time.sleep(0.05)
        raise BenchError("服务在 %.0f s 内没有就绪" % timeout)


def document_url(pid: str, name: str) -> str:
    return "/api/v1/p/%s/files/%s/document" % (pid, urllib.parse.quote(name, safe=""))


def file_url(pid: str, name: str, endpoint: str) -> str:
    return "/api/v1/p/%s/files/%s/%s" % (pid, urllib.parse.quote(name, safe=""), endpoint)


def search_url(pid: str, **params: str) -> str:
    return "/api/v1/p/%s/search?%s" % (pid, urllib.parse.urlencode(params))


# ===================== 用例与计时 =====================


class Case:
    """一个用例 = 一个测量函数，返回 `(计时列表, 说明, 额外失败原因)`。

    大多数用例只计一个请求（列表长度 1）；§7.3 的 P99 需要 50 个样本，那种用例自己在
    函数里循环并把每次的毫秒数都交回来。
    """

    def __init__(
        self,
        key: str,
        label: str,
        entry: str,
        sample: str,
        mode: str,
        budget_ms: Optional[float],
        budget_src: str,
        fn: Callable[[], Tuple[str, str]],
        runs: int = 5,
        cold: bool = False,
        pct: float = 0.95,
        fixed_runs: bool = False,
    ) -> None:
        self.key = key
        self.label = label
        self.entry = entry
        self.sample = sample
        self.mode = mode
        self.budget_ms = budget_ms
        self.budget_src = budget_src
        self.fn = fn
        self.runs = runs
        self.cold = cold
        self.pct = pct
        self.fixed_runs = fixed_runs  # 不受 --samples 影响（§7.3 的 P99 要 50 个样本）


class Row:
    def __init__(self, case: Case, timings: List[float], notes: List[str], failures: List[str]) -> None:
        self.case = case
        self.timings = timings
        self.notes = notes
        self.failures = failures

    @property
    def judged_ms(self) -> float:
        return percentile(self.timings, self.case.pct)

    @property
    def p50(self) -> float:
        return percentile(self.timings, 0.50)

    @property
    def p95(self) -> float:
        return percentile(self.timings, 0.95)

    @property
    def worst(self) -> float:
        return max(self.timings) if self.timings else 0.0

    @property
    def budget_text(self) -> str:
        if self.case.budget_ms is None:
            return "—（%s）" % (self.case.budget_src or "规范未定预算")
        return "≤ %.0f ms @ p%.0f（%s）" % (self.case.budget_ms, self.case.pct * 100, self.case.budget_src)

    @property
    def verdict(self) -> str:
        if self.failures:
            return "FAIL（断言不成立）"
        if self.case.budget_ms is None:
            return "仅报告"
        return "PASS" if self.judged_ms <= self.case.budget_ms else "FAIL（超预算）"

    @property
    def failed(self) -> bool:
        return self.verdict.startswith("FAIL")

    def note_text(self) -> str:
        unique: List[str] = []
        for text in [text for text in self.notes if text]:
            if text not in unique:
                unique.append(text)
        if not unique:
            return ""
        if len(unique) == len(self.notes) and len(unique) > 1:
            # 每个样本各说一件事（例如冷→热的两次）就全部列出。
            return " ｜ ".join(unique)
        head = " ｜ ".join(unique[:2])
        return head + ("（%d 个样本同类）" % len(self.notes) if len(unique) > 2 else "")


def percentile(values: List[float], fraction: float) -> float:
    ordered = sorted(values)
    if not ordered:
        return 0.0
    return ordered[min(len(ordered) - 1, int(fraction * len(ordered)))]


def run_case(case: Case, samples: int, clear_all: Callable[[], None], case_seconds: float) -> Row:
    timings: List[float] = []
    notes: List[str] = []
    failures: List[str] = []
    runs = case.runs if case.fixed_runs else max(1, min(case.runs, samples))
    index = 0
    while index < runs:
        if case.cold:
            clear_all()
        started = time.perf_counter()
        measured, note, failure = case.fn()
        wall = (time.perf_counter() - started) * 1000.0
        if not measured:
            raise BenchError("%s 没有产出任何计时样本" % case.key)
        timings += measured
        notes.append(note)
        if failure and failure not in failures:
            failures.append(failure)
        index += 1
        if index == 1 and runs > index:
            projected = wall / 1000.0 * (runs - index)
            if projected > case_seconds:
                allowed = int(case_seconds * 1000.0 / max(wall, 1e-6))
                if allowed < runs - index:
                    runs = index + allowed
                    notes.append(
                        "时间盒 %.0f s：样本数由 %d 降到 %d" % (case_seconds, index + 1 + allowed, runs)
                    )
    return Row(case, timings, notes, failures)


# ===================== 输出 =====================


def display_width(text: str) -> int:
    return sum(2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1 for ch in text)


def pad(text: str, width: int) -> str:
    return text + " " * max(0, width - display_width(text))


def render_table(headers: List[str], rows: List[List[str]], title: str) -> str:
    widths = [
        max([display_width(headers[index])] + [display_width(row[index]) for row in rows] or [0])
        for index in range(len(headers))
    ]
    out = [title, "  ".join(pad(headers[index], widths[index]) for index in range(len(headers)))]
    out.append("  ".join("-" * widths[index] for index in range(len(headers))))
    for row in rows:
        out.append("  ".join(pad(row[index], widths[index]) for index in range(len(row))).rstrip())
    return "\n".join(out)


def render_rows(rows: List[Row], title: str) -> str:
    headers = ["用例", "入口", "样本", "冷/热", "n", "p50 ms", "p95 ms", "最差 ms", "预算线", "判定"]
    table = [
        [
            row.case.key,
            row.case.entry,
            row.case.sample,
            row.case.mode,
            str(len(row.timings)),
            "%.1f" % row.p50,
            "%.1f" % row.p95,
            "%.1f" % row.worst,
            row.budget_text,
            row.verdict,
        ]
        for row in rows
    ]
    out = [render_table(headers, table, title)]
    out.append("      实测说明：")
    for row in rows:
        note = row.note_text()
        out.append("      - %-20s %s" % (row.case.key, note or row.case.label))
        for failure in row.failures:
            out.append("        断言不成立：%s" % failure)
    return "\n".join(out)


def machine_line() -> str:
    return "机器：%s / macOS %s / CPU %d 核 / Python %s" % (
        platform.machine(), platform.mac_ver()[0] or platform.system(), os.cpu_count() or 0,
        platform.python_version(),
    )


def mb(value: float) -> str:
    return "%.2f MB" % (value / 1048576)


# ===================== 用例装配 =====================


def build_cases(api: Api, corpus: Dict[str, Any], ctx: Dict[str, Any]) -> List[Case]:
    """所有用例都打真实接口；`入口` 列写清楚是哪个 endpoint 或哪个 `app.*` 函数。"""
    documents = ctx["documents"]
    revision = ctx["revision"]
    opens = ctx["opens"]
    clear_all = ctx["clear_all"]
    search_pid = corpus["search_pid"]
    files_pid = corpus["files_pid"]
    search_files = corpus["search_files"]
    query = "param_00007"
    big_url = document_url(files_pid, corpus["big_name"])
    mid_url = document_url(files_pid, corpus["mid_names"][0])
    compare_body = json.dumps(
        {"base": {"file": corpus["mid_names"][0]}, "target": {"file": corpus["mid_names"][1]}}
    ).encode("utf-8")

    def warm_document(url: str, accept_encoding: str = "identity") -> Tuple[List[float], str, str]:
        api.expect("GET", url, accept_encoding=accept_encoding)  # 预热一次，不计时
        elapsed, payload = api.expect("GET", url, accept_encoding=accept_encoding)
        info = documents.cache_info()
        if accept_encoding == "gzip":
            return [elapsed], "传输体 %s（gzip；§6.5 不缓存压缩体，每次响应重压；计时不含客户端 inflate）" % mb(
                len(payload)
            ), ""
        return (
            [elapsed],
            "响应体 %s；本次驻留 %d 项 %s" % (mb(len(payload)), info["entries"], mb(info["bytes"])),
            "",
        )

    def cold_document() -> Tuple[List[float], str, str]:
        clear_all()
        elapsed, payload = api.expect("GET", big_url)
        return [elapsed], "响应体 %s；节点 %s（内存 + 磁盘缓存皆空 → 含解析与写 doc_v2）" % (
            mb(len(payload)), node_count(payload)
        ), ""

    def search_warm() -> Tuple[List[float], str, str]:
        api.expect("GET", search_url(search_pid, q=query, limit=10))  # 预热不计时
        with RefetchCounter(documents) as counter, opens:
            elapsed, payload = api.expect("GET", search_url(search_pid, q=query, limit=10))
        body = json.loads(payload.decode("utf-8"))
        info = documents.cache_info()
        return [elapsed], "命中 %d 文件 / %d 条；驻留 %d 项 %s；本次逐出重取：_read_bytes %d 次、数据根内 open %d 次（其中 configs/doc_v2：%s）" % (
            len(body["files"]), body["total"], info["entries"], mb(info["bytes"]),
            counter.count, len(opens.paths), opens.rel(("doc_v2", "/configs/")),
        ), ""

    def search_cold() -> Tuple[List[float], str, str]:
        clear_all()
        elapsed, payload = api.expect("GET", search_url(search_pid, q=query, limit=10))
        body = json.loads(payload.decode("utf-8"))
        return [elapsed], "含每个文件的首次解析与写磁盘缓存；命中 %d 文件 / %d 条" % (
            len(body["files"]), body["total"]
        ), ""

    def filter_in_file() -> Tuple[List[float], str, str]:
        url = search_url(files_pid, q=query, file=corpus["mid_names"][0], limit=10)
        api.expect("GET", url)
        elapsed, payload = api.expect("GET", url)
        body = json.loads(payload.decode("utf-8"))
        return [elapsed], "单文件扫描（%s），命中 %d 条" % (corpus["mid_names"][0], body["total"]), ""

    def compare_warm() -> Tuple[List[float], str, str]:
        api.expect("POST", "/api/v1/p/%s/compare" % files_pid, body=compare_body)
        elapsed, body = api.timed("POST", "/api/v1/p/%s/compare" % files_pid, body=compare_body)
        unified = body.get("unified") or {}
        return [elapsed], "结构摘要 %s；文本差异%s" % (
            (body.get("structural") or {}).get("summary"),
            "跳过（>20000 行，§6.6）" if unified.get("too_large") else "%s 行" % unified.get("total"),
        ), ""

    def files_list() -> Tuple[List[float], str, str]:
        elapsed, body = api.timed("GET", "/api/v1/p/%s/files" % search_pid)
        return [elapsed], "%d 个文件条目（含映射/归档/记录/待审计数）" % len(body["items"]), ""

    def history_list(endpoint: str, kind: str) -> Tuple[List[float], str, str]:
        url = file_url(files_pid, corpus["history_name"], endpoint)
        elapsed, body = api.timed("GET", url)
        items = body.get("items") or []
        with_diff = sum(1 for item in items if item.get("diff") or item.get("diff_prev"))
        return [elapsed], "%s %d 条，其中 %d 条带相邻版本结构摘要（单版本 %d 节点）" % (
            kind, len(items), with_diff, corpus["history_nodes"]
        ), ""

    def revision_poll() -> Tuple[List[float], str, str]:
        """50 次真实轮询逐次计时：§7.3 的预算是 P99，必须一个请求一个样本。"""
        url = "/api/v1/p/%s/revision" % files_pid
        api.expect("GET", url)  # 先走一遍，连接与身份解析路径热起来
        timings: List[float] = []
        with opens:
            for _ in range(REVISION_POLLS):
                elapsed, _payload = api.expect("GET", url)
                timings.append(elapsed)
            caught = len(opens.paths)
        failure = ""
        if caught:
            failure = (
                "§7.3 写明该接口「不读磁盘」，实测 %d 次轮询打开数据根内文件 %d 次（每次 %s 个，%s）。"
                "计数本体确实在内存（见 revision_snapshot_mem），读盘来自分发层的每请求机型校验与身份解析"
                "（server/http.py 的 device_models.exists + services/auth.resolve_actor），"
                "而 PLAN §10 R13 明令「身份实时读、不要加缓存」⇒ §7.3 这句与 R13 在任何 {pid} 路由上"
                "不可能同时成立。要闭合得由规范侧裁定（把 §7.3 收窄成「计数不读磁盘」，"
                "或让分发层每请求只读一次）；本卡不改分发层，只把数字报出来。"
                "P99 那一半是过的（见 p50/最差列）。"
                % (REVISION_POLLS, caught, "%.1f" % (caught / float(REVISION_POLLS)), opens.rel())
            )
        return timings, "%d 次逐次计时；数据根内 open %d 次（%s）" % (
            REVISION_POLLS, caught, opens.rel()
        ), failure

    def revision_memory() -> Tuple[List[float], str, str]:
        started = time.perf_counter()
        with opens:
            for _ in range(REVISION_SNAPSHOTS):
                revision.snapshot(files_pid)
            caught = len(opens.paths)
        total = (time.perf_counter() - started) * 1000.0
        return [total], "%d 次计数读取合计 %.2f ms（单次 %.1f µs）、数据根内 open %d 次（§7.3「纯内存计数」的直接检验）" % (
            REVISION_SNAPSHOTS, total, total * 1000.0 / REVISION_SNAPSHOTS, caught
        ), ("" if caught == 0 else "计数读取出现了 %d 次磁盘打开" % caught)

    def lru_bytes() -> Tuple[List[float], str, str]:
        clear_all()
        started = time.perf_counter()
        for name in search_files:  # 全量预热：这就是 §12 内存行要防的最坏驻留
            api.expect("GET", document_url(search_pid, name))
        elapsed = (time.perf_counter() - started) * 1000.0
        info = documents.cache_info()
        limit = documents.MEMORY_CACHE_MAX_BYTES
        return [elapsed], "全量预热 %d 文件耗时 %.0f ms，之后驻留 %d 项 / %s（字节上限 %s）" % (
            len(search_files), elapsed, info["entries"], mb(info["bytes"]), mb(limit)
        ), ("" if info["bytes"] <= limit else "驻留字节越过 §12 的「+LRU ≤ 256 MB」")

    return [
        Case("doc_cold_big", "打开大文件（冷）", "GET /files/{name}/document",
             "%s：%d 节点" % (corpus["big_name"], corpus["big_nodes"]),
             "冷", 3000.0, "§12「30 万节点冷 ≤ 3 s」", cold_document, runs=3),
        Case("doc_hit_mid", "打开 5 万节点文件（服务端缓存命中）", "GET /files/{name}/document",
             "%s：%d 节点" % (corpus["mid_names"][0], corpus["mid_nodes"]),
             "热", 300.0, "§12「5 万节点缓存命中 ≤ 300 ms」", lambda: warm_document(mid_url)),
        Case("doc_hit_mid_gzip", "同上但客户端接受 gzip（内存缓存不存压缩体）", "GET /files/{name}/document",
             "%s：%d 节点" % (corpus["mid_names"][0], corpus["mid_nodes"]),
             "热", 300.0, "§12「5 万节点缓存命中 ≤ 300 ms」", lambda: warm_document(mid_url, "gzip")),
        Case("search_warm", "全局搜索（内存缓存已预热；§12 预算的前提）", "GET /search?q=…",
             "%d 文件 × %d 节点 = %d 节点" % (
                 len(search_files), corpus["search_nodes"], len(search_files) * corpus["search_nodes"]),
             "热", SEARCH_BUDGET_MS, "§12 表格（D3：取 500 ms，不取 §7.6 的 300 ms）", search_warm),
        Case("search_cold", "全局搜索（冷：含首次解析与写缓存）", "GET /search?q=…",
             "%d 文件 × %d 节点" % (len(search_files), corpus["search_nodes"]),
             "冷", None, "§12 未给冷启动全局搜索定预算", search_cold, runs=1),
        Case("filter_in_file", "文件内筛选（服务端扫描单文件，对应 §12 前端的行）", "GET /search?file=…",
             "%s：%d 节点" % (corpus["mid_names"][0], corpus["mid_nodes"]),
             "热", 80.0, "§12「文件内筛选 5 万节点 ≤ 80 ms」（该行原指前端）", filter_in_file),
        Case("compare_2x50k", "结构化对比（两个不同文件）", "POST /compare",
             "%s × %s：各 %d 节点" % (corpus["mid_names"][0], corpus["mid_names"][1], corpus["mid_nodes"]),
             "热", 800.0, "§12「对比 2×5 万节点结构化 ≤ 800 ms」", compare_warm),
        Case("files_list", "文件列表（结构树顶层）", "GET /files",
             "%d 个文件" % len(search_files), "热", None, "§12 未定预算", files_list),
        Case("archives_cold", "历史版本列表（冷：首次计算相邻版本结构摘要）", "GET /files/{name}/archives",
             "%s × %d 个归档版本（各 %d 节点）" % (corpus["history_name"], HISTORY_VERSIONS, corpus["history_nodes"]),
             "冷", None, "§12 未定预算",
             lambda: history_list("archives", "归档"), runs=1),
        Case("archives_warm", "历史版本列表（热：摘要走 cache/diff_v2）", "GET /files/{name}/archives",
             "%s × %d 个归档版本" % (corpus["history_name"], HISTORY_VERSIONS),
             "热", None, "§12 未定预算", lambda: history_list("archives", "归档")),
        Case("records_warm", "修改记录列表（含相邻版本结构摘要）", "GET /files/{name}/records",
             "%s × %d 个记录版本（各 %d 节点）" % (corpus["history_name"], HISTORY_VERSIONS, corpus["history_nodes"]),
             "热", None, "§12 未定预算", lambda: history_list("records", "记录")),
        Case("revision_poll", "修订号轮询（真实 HTTP，逐次计时）", "GET /revision",
             "%d 次轮询" % REVISION_POLLS,
             "热", 5.0, "§7.3「极轻量、不读磁盘，P99 < 5 ms」", revision_poll,
             runs=1, fixed_runs=True, pct=0.99),
        Case("revision_snapshot_mem", "修订号计数读取本身（service 层）", "services.revision.snapshot",
             "%d 次读取" % REVISION_SNAPSHOTS,
             "热", None, "§7.3「纯内存计数」→ 判定读盘次数为 0", revision_memory, runs=1),
        Case("lru_bytes", "文档内存缓存驻留字节（§12 服务端内存行）", "services.documents.cache_info",
             "全量预热 %d 文件" % len(search_files),
             "热", None, "§12「+LRU ≤ 256 MB」→ 判定驻留不超字节上限", lru_bytes, runs=1),
    ]


def node_count(payload: bytes) -> Any:
    try:
        return json.loads(payload.decode("utf-8"))["file"]["node_count"]
    except (ValueError, KeyError, UnicodeDecodeError):
        return "?"


# ===================== 缓存容量对比 =====================


def sweep_caps(
    api: Api, corpus: Dict[str, Any], ctx: Dict[str, Any], specs: List[str], samples: int
) -> List[List[str]]:
    """同一份语料、同一条查询，只改容量钩子；每个配置都重跑预热 + 计时。"""
    documents = ctx["documents"]
    opens = ctx["opens"]
    defaults = ctx["cap_defaults"]
    url = search_url(corpus["search_pid"], q="param_00007", limit=10)
    rows: List[List[str]] = []
    for spec in specs:
        documents.MEMORY_CACHE_MAX_ENTRIES = defaults["entries"]
        documents.MEMORY_CACHE_FILES_MULTIPLIER = defaults["multiplier"]
        if spec == "auto":
            label = "auto＝代码默认 max(%d, %d×机型文件数)" % (
                defaults["entries"], defaults["multiplier"]
            )
        else:
            try:
                fixed = int(spec)
            except ValueError:
                raise BenchError("--caps 只接受数字或 auto，收到 %r" % spec)
            documents.MEMORY_CACHE_MAX_ENTRIES = fixed
            documents.MEMORY_CACHE_FILES_MULTIPLIER = 0  # 关掉按文件数放大 → 容量钉死在 fixed
            label = "%d（钉死条目数，倍数=0）" % fixed
        documents.reset_cache()
        api.expect("GET", url)  # 预热一次不计时（与主表同协议）
        timings: List[float] = []
        refetch: List[int] = []
        disk_opens: List[int] = []
        for index in range(max(1, samples)):
            with RefetchCounter(documents) as counter, opens:
                elapsed, _payload = api.expect("GET", url)
                refetch.append(counter.count)
                disk_opens.append(len(opens.paths))
            timings.append(elapsed)
            if index == 0 and samples > 1 and timings[0] / 1000.0 * (samples - 1) > ctx["case_seconds"]:
                break
        info = documents.cache_info()
        limit_now = documents.memory_cache_limit(corpus["search_pid"])
        rows.append([
            label,
            "上限 %d 项" % limit_now,
            str(len(timings)),
            "%.1f" % percentile(timings, 0.50),
            "%.1f" % percentile(timings, 0.95),
            "%.1f" % max(timings),
            "%.1f" % (sum(refetch) / len(refetch)),
            "%.1f" % (sum(disk_opens) / len(disk_opens)),
            "%d / %.0f MB" % (info["entries"], info["bytes"] / 1048576),
            "≤ %.0f ms" % SEARCH_BUDGET_MS,
            "PASS" if percentile(timings, 0.95) <= SEARCH_BUDGET_MS else "FAIL（超预算）",
        ])
    documents.MEMORY_CACHE_MAX_ENTRIES = defaults["entries"]
    documents.MEMORY_CACHE_FILES_MULTIPLIER = defaults["multiplier"]
    return rows


# ===================== 主流程 =====================


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="bench_api.py",
        description=(
            "MCChecker 读路径 API 基准（M4-05 / INH-1907、规范 §12、T-27）：在进程内起真实"
            " HTTP 服务（app/server + app/services），对语料逐请求计时，按 §12/§7.3 的预算线"
            "打印 PASS/FAIL；超预算退出码非零。"
        ),
        epilog=(
            "语料与风险\n"
            "  默认语料：tempfile.mkdtemp() 里按 --seed 确定性生成的合成配置文件"
            "（默认 30 文件 × 3 万节点 = 决策 17 的场景），退出时整目录删除，不读也不写仓库"
            " data/。脚本开工前后各对仓库 data/ 拍一次逐文件 sha256 快照，任何差异以退出码 2"
            " 失败。本机没有 data/ 时这一条自己说破是空信号。\n"
            "  --profile-dir 的风险（后果自负）：把真实数据根指给基准之后，基准会**读**其中"
            "每个配置文件（搜索/筛选/对比/列表都会全量扫描），并往数据根里**写**派生缓存"
            "（profiles/<pid>/cache/doc_v2、cache/diff_v2）。读接口不会改动 configs/archive/"
            "records 里的权威文件，但磁盘占用、读放大与耗时都是真的；本脚本实测 100 文件 ×"
            " 3 万节点的机型会把内存驻留顶到 256 MB 上限（71 项 / 254 MB），真实语料更大时"
            "进程内存就是这个量级。请先备份、先确认剩余空间与可接受的运行时长，并且只指向"
            "仓库之外的目录——指向仓库内部（含 data/）一律拒绝（H3）。合成语料已足够复现"
            "决策 17 的场景。\n"
            "预算线：§12 表格 + §7.3；没有预算线的用例判定列写「仅报告」，不参与退出码。"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--files", type=int, default=30, help="搜索语料的文件数（默认 30 = 决策 17 场景）")
    parser.add_argument("--nodes", type=int, default=30000, help="搜索语料每文件目标节点数（默认 30000）")
    parser.add_argument("--big-nodes", dest="big_nodes", type=int, default=300000,
                        help="冷加载大文件目标节点数（默认 300000，§12 冷行）")
    parser.add_argument("--mid-nodes", dest="mid_nodes", type=int, default=50000,
                        help="命中/筛选/对比用的中等文件目标节点数（默认 50000，§12 两行）")
    parser.add_argument("--samples", type=int, default=5, help="每个用例的计时样本数上限（默认 5）")
    parser.add_argument("--caps", default="24,auto,64",
                        help="缓存容量对比扫描，逗号分隔；数字=钉死条目数，auto=代码默认策略"
                             "（默认 24,auto,64）")
    parser.add_argument("--seed", type=int, default=20261018, help="语料种子（默认 20261018，可复现）")
    parser.add_argument("--profile-dir", dest="profile_dir", default="",
                        help="逃生口：改用这个数据根（布局同 data/）而不是合成语料；风险见上文")
    parser.add_argument("--keep-temp", dest="keep_temp", action="store_true",
                        help="保留临时语料目录（调试用；默认退出即删）")
    parser.add_argument("--case-seconds", dest="case_seconds", type=float, default=60.0,
                        help="单个用例的时间盒，预估超时就不再补样本并在输出里写明（默认 60 s）")
    args = parser.parse_args(argv)

    print(machine_line())
    before = snapshot_repo_data()
    print("H3 tripwire（开工前）：仓库 data/ %s" % (
        "已快照 %d 个文件" % len(before) if before
        else "不存在或为空 → 本机没有可比对的权威数据，本 tripwire 是空信号，不是「数据完好」"
             "的证据（与 pytest 的 H3 守卫同一口径）"))

    work: Optional[str] = None
    if args.profile_dir:
        data_dir = os.path.realpath(os.path.expanduser(args.profile_dir))
        if data_dir == os.path.realpath(REPO_DATA_DIR) or data_dir.startswith(ROOT + os.sep):
            print("拒绝：--profile-dir 落在仓库之内（%s）。基准会往数据根写派生缓存，仓库" % data_dir,
                  file=sys.stderr)
            print("data/ 是权威数据（H3）；要用真实数据请先复制到仓库之外。", file=sys.stderr)
            return 3
    else:
        work = os.path.realpath(tempfile.mkdtemp(prefix="mcbench-"))
        data_dir = os.path.realpath(os.path.join(work, "data"))
        os.makedirs(data_dir, exist_ok=True)

    # 数据根必须在 import 任何「import 期派生路径」的模块之前切换（决策 1 / know-how 第 7 节）。
    sys.path.insert(0, ROOT)
    from app.core import storage  # noqa: E402

    storage.configure_data_dir(data_dir)
    if os.path.realpath(storage.DATA_DIR) != data_dir or data_dir.startswith(ROOT + os.sep):
        print("拒绝：数据根不是预期的目录（storage.DATA_DIR=%s，期望 %s）" % (
            storage.DATA_DIR, data_dir), file=sys.stderr)
        if work:
            shutil.rmtree(work, ignore_errors=True)
        return 3
    print("数据根：%s（%s）" % (
        data_dir,
        "--profile-dir 指定的真实数据，脚本不会删除它" if args.profile_dir
        else "合成语料 · 临时目录，退出即删"))

    from app.config import Settings  # noqa: E402
    from app.server import http as http_layer  # noqa: E402
    from app.server.router import build_router  # noqa: E402
    from app.services import documents, revision  # noqa: E402

    server: Optional[Any] = None
    thread: Optional[threading.Thread] = None
    code = 3
    try:
        if args.profile_dir:
            corpus = adopt_data_root(data_dir)
            print("语料：沿用数据根里 %d 个现有配置文件（不生成、不删除；机型 %s）" % (
                len(corpus["search_files"]), corpus["search_pid"]))
        else:
            corpus = build_corpus(
                data_dir, args.files, args.nodes, args.big_nodes, args.mid_nodes, args.seed
            )
            print("语料：合成 %d 文件 × %d 节点（决策 17 场景，机型 %s）+ 机型 %s 里 %d 节点大文件 1 个、"
                  "%d 节点中等文件 2 个、%d 节点历史文件 1 个及其归档/记录各 %d 个；"
                  "原文合计 %.1f MB；种子 %d；内容指纹 %s" % (
                      len(corpus["search_files"]), corpus["search_nodes"], corpus["search_pid"],
                      corpus["files_pid"], corpus["big_nodes"], corpus["mid_nodes"],
                      corpus["history_nodes"], HISTORY_VERSIONS, corpus["bytes"] / 1048576,
                      args.seed, corpus["digest"]))

        settings = Settings(host="127.0.0.1", port=0, data_dir=data_dir, web_dir=WEB_DIR, log_level="WARNING")
        server = http_layer.MCServer(settings, build_router(settings))
        thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
        thread.start()
        api = Api(server.server_port)
        api.wait_ready()
        print("服务：真实 MCServer @ 127.0.0.1:%d（http.client 逐请求计时）" % server.server_port)

        def clear_all() -> None:
            documents.reset_cache()
            revision.reset()
            for pid in (corpus["search_pid"], corpus["files_pid"]):
                cache_root = storage.get_cache_root_dir(pid)
                for name in ("doc_v2", "diff_v2"):
                    target = os.path.join(cache_root, name)
                    if os.path.isdir(target):
                        shutil.rmtree(target)

        ctx: Dict[str, Any] = {
            "documents": documents,
            "revision": revision,
            "opens": OpenCounter(data_dir),
            "clear_all": clear_all,
            "case_seconds": args.case_seconds,
            "cap_defaults": {
                "entries": documents.MEMORY_CACHE_MAX_ENTRIES,
                "multiplier": getattr(documents, "MEMORY_CACHE_FILES_MULTIPLIER", 0),
            },
        }
        rows = [run_case(case, args.samples, clear_all, args.case_seconds) for case in build_cases(api, corpus, ctx)]
        print()
        print(render_rows(rows, "【主表 · 真实 HTTP 请求 · 判定用 p95（§7.3 那行用 p99）· 计时含环回传输，不含前端解析】"))

        specs = [item.strip() for item in args.caps.split(",") if item.strip()]
        sweep_rows = sweep_caps(api, corpus, ctx, specs, args.samples)
        print()
        print(render_table(
            ["容量配置", "条目上限", "n", "p50 ms", "p95 ms", "最差 ms", "重取/次", "open/次", "驻留 项/MB", "预算线", "判定"],
            sweep_rows,
            "【缓存容量对比 · 同一语料同一条查询，只改 documents 的容量钩子；实验表，不参与退出码】",
        ))
        print("      「重取/次」= 一次搜索里 `documents._read_bytes` 的调用数（逐出后重新读原文 + 反序列化），")
        print("      它是决策 17 那条 731 ms 的机制本体；`open/次` 另含磁盘缓存 `doc_v2/*.json.gz` 的打开。")

        failed = [row for row in rows if row.failed]
        after = snapshot_repo_data()
        problems = diff_repo_data(before, after)
        print()
        if problems:
            print("H3 tripwire 触发：仓库 data/ 与开工前不一致 → %s" % "；".join(problems), file=sys.stderr)
            code = 2
        elif failed:
            print("H3 tripwire（收工后）：仓库 data/ %s" % (
                "前后 %d 个文件逐一比对一致" % len(after) if after
                else "本机不存在 data/（空信号，见开工前那条说明）"))
            print("%d 个用例判定为 FAIL：%s" % (len(failed), ", ".join(row.case.key for row in failed)),
                  file=sys.stderr)
            code = 1
        else:
            print("H3 tripwire（收工后）：仓库 data/ %s" % (
                "前后 %d 个文件逐一比对一致" % len(after) if after
                else "本机不存在 data/（空信号，见开工前那条说明）"))
            print("全部有预算线/断言的用例达标。")
            code = 0
    except BenchError as exc:
        print("基准无法进行：%s" % exc, file=sys.stderr)
        code = 3
    finally:
        if server is not None:
            try:
                server.shutdown()
                server.server_close()
            except Exception:  # 收尾异常不该盖掉已测出的结论
                pass
        if thread is not None:
            thread.join(timeout=10)
        if work is not None:
            if args.keep_temp:
                print("临时语料目录保留（--keep-temp）：%s" % work)
            else:
                shutil.rmtree(work, ignore_errors=True)
                print("临时语料目录已删除：%s" % work)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
