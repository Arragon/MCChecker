"""全局搜索基准（§12 预算 / M2-05 验收）。

样本：**30 个文件 × 约 3 万节点**，写在临时数据目录里（不入库、不留在仓库）。
每行 `<param_NNNNN desc="…">v</param_NNNNN>` 产生 2 个节点（元素 + 属性），加上
文件根、`CONFIG_ROOT`、`input` 三个结构节点，所以行数 = (目标节点数 - 3) / 2。

计时走真实 HTTP（`run.py --data-dir`），分别报告冷启动（要解析并写 doc_v2 缓存）
与预热后（文档已在内存缓存里，§12 预算的前提）的耗时，并对照 500 ms 预算。

用法：`./.venv39/bin/python scripts/bench_search.py [--files 30] [--nodes 30000]`
"""

from __future__ import annotations

import argparse
import http.client
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BUDGET_MS = 500.0
NODES_PER_LINE = 2
STRUCTURAL_NODES = 3  # 文件名 + CONFIG_ROOT + input
MEMORY_CACHE_MAX_ENTRIES = 24  # 与 services.documents 一致，用于提示逐出


def lines_for(nodes: int) -> int:
    return max(1, (nodes - STRUCTURAL_NODES) // NODES_PER_LINE)


def nodes_of(lines: int) -> int:
    return STRUCTURAL_NODES + NODES_PER_LINE * lines


def make_sample(configs_dir: str, files: int, lines: int) -> int:
    os.makedirs(configs_dir, exist_ok=True)
    inner = "".join(
        '<param_%05d desc="desc %d">%d</param_%05d>\n' % (n, n, n % 10, n)
        for n in range(lines)
    )
    body = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<CONFIG_ROOT softwareVersion="BENCH">\n'
        "  <input>\n" + inner + "  </input>\n"
        "</CONFIG_ROOT>\n"
    ).encode("utf-8")
    total = 0
    for f in range(files):
        with open(os.path.join(configs_dir, "bench_%02d.xml" % f), "wb") as fh:
            fh.write(body)
        total += len(body)
    return total


def request(port: int, path: str):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=120)
    try:
        conn.request("GET", path, headers={"X-Requested-With": "MCChecker"})
        resp = conn.getresponse()
        raw = resp.read()
        return resp.status, (json.loads(raw.decode("utf-8")) if raw else None)
    finally:
        conn.close()


def timed_search(port: int, pid: str, query: str, runs: int):
    samples = []
    payload = None
    for _ in range(runs):
        started = time.perf_counter()
        status, payload = request(port, "/api/v1/p/%s/search?%s" % (pid, query))
        samples.append((time.perf_counter() - started) * 1000.0)
        assert status == 200, (status, payload)
    return samples, payload


def percentile(values, fraction: float) -> float:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(fraction * len(ordered)))]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--files", type=int, default=30)
    ap.add_argument("--nodes", type=int, default=30000, help="每个文件的目标节点数")
    ap.add_argument("--runs", type=int, default=5)
    args = ap.parse_args()

    lines = lines_for(args.nodes)
    work = tempfile.mkdtemp(prefix="mc-bench-search-")
    data_dir = os.path.join(work, "data")
    configs = os.path.join(data_dir, "profiles", "default", "configs")
    port = 53000 + (os.getpid() % 5000)

    size = make_sample(configs, args.files, lines)
    per_file = nodes_of(lines)
    total_nodes = per_file * args.files
    print("样本：%d 文件 × %d 行 = %d 节点/文件（合计 %d 节点），原文 %.1f MB" % (
        args.files, lines, per_file, total_nodes, size / 1024 / 1024))

    server = subprocess.Popen(
        [sys.executable, os.path.join(ROOT, "run.py"), "--host", "127.0.0.1",
         "--port", str(port), "--data-dir", data_dir],
        cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    hit_query = "q=param_%05d&limit=10" % (lines - 1)
    try:
        for _ in range(120):
            try:
                if request(port, "/api/v1/session")[0] == 200:
                    break
            except OSError:
                pass
            time.sleep(0.1)
        else:
            raise SystemExit("服务未起来")

        cold, _first = timed_search(port, "default", hit_query, 1)
        warm, last = timed_search(port, "default", hit_query, args.runs)
        miss, _none = timed_search(port, "default", "q=zzzzzz&limit=10", args.runs)
    finally:
        server.terminate()
        server.wait(timeout=10)
        shutil.rmtree(work, ignore_errors=True)

    p50 = percentile(warm, 0.50)
    print("冷启动一次（含解析）：%.1f ms" % cold[0])
    print("预热后 %d 次：P50 %.1f ms / P95 %.1f ms / 最好 %.1f ms（§12 预算 %.0f ms）" % (
        args.runs, p50, percentile(warm, 0.95), min(warm), BUDGET_MS))
    print("无命中扫描：P50 %.1f ms" % percentile(miss, 0.50))
    print("单节点均摊：%.2f µs/节点（P50 %.1f ms ÷ %d 节点）" % (
        p50 * 1000 / total_nodes, p50, total_nodes))
    print("命中：total=%s 有命中的文件=%s truncated=%s" % (
        last["total"], len(last["files"]), last["truncated"]))
    if args.files > MEMORY_CACHE_MAX_ENTRIES:
        print("注：文档内存缓存上限 %d 项 < 文件数 %d，每次搜索都要重新反序列化被逐出的文件" % (
            MEMORY_CACHE_MAX_ENTRIES, args.files))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
