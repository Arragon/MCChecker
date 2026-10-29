"""V3 大参数性能复跑脚本（INH-2149）。

用法：
    .venv39/bin/python tests/performance_v3.py [--json out.json] [--keep]

它在临时数据根里启动真实 HTTP server，用 50,000 标量 JSON 与 3,200 标量 XML
实测：计划创建、按页读取、20 并发 p95、原子提交；输出机器与预算对照。
不触碰仓库 `data/`，也不改旧 F01–F28 基线。
"""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import os
import platform
import shutil
import statistics
import sys
import tempfile
import time
from typing import Any, Dict, List

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tests"))

from app.core import storage  # noqa: E402

DATA_SUBDIRS = ("profiles", "workspaces", "configs", "archive", "records", "cache",
                "temporary", "recovery", "logs")


def _prepare_root() -> str:
    root = tempfile.mkdtemp(prefix="mcc-perf-")
    storage.configure_data_dir(root)
    for sub in DATA_SUBDIRS:
        os.makedirs(os.path.join(root, sub), exist_ok=True)
    os.makedirs(os.path.join(root, "profiles", "default"), exist_ok=True)
    return root


def _timed(call):
    start = time.perf_counter()
    result = call()
    return (time.perf_counter() - start) * 1000.0, result


def _json_fixture(count: int) -> bytes:
    body = {"Params": {"P%05d" % index: index for index in range(count)}}
    return json.dumps(body).encode("utf-8")


def _xml_fixture(count: int) -> bytes:
    parts = ["<R><G>"]
    for index in range(count):
        parts.append("<P%05d>%d</P%05d>" % (index, index, index))
    parts.append("</G></R>")
    return "".join(parts).encode("utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--json", default="")
    parser.add_argument("--keep", action="store_true")
    args = parser.parse_args()

    root = _prepare_root()
    from authz_fixtures import as_role, make_server, seed_roles

    report: Dict[str, Any] = {
        "machine": {
            "platform": platform.platform(),
            "python": platform.python_version(),
            "cpu_count": os.cpu_count(),
        },
        "fixtures": {},
        "timings_ms": {},
        "p95_ms": {},
        "budgets_ms": {"preview_3200": 3000, "first_page_50k": 500, "commit_3200": 5000},
        "pass": {},
    }
    try:
        seed_roles("perf")
        with make_server() as server:
            admin = as_role(server, "admin")
            wid = admin.json("POST", "/api/v3/workspaces", {"name": "Perf"})[2]["workspace"]["id"]
            cid = admin.json(
                "POST", "/api/v3/workspaces/%s/profiles/default/configs" % wid,
                {"code": "MC-001", "name": "性能"},
            )[2]["config"]["config_id"]

            big = _json_fixture(50000)
            report["fixtures"]["json_50k_bytes"] = len(big)
            admin.request(
                "PUT", "/api/v1/p/default/files/perf50k.json", body=big,
                headers={"X-Workspace-Id": wid, "Content-Type": "application/json"},
            )
            sel = admin.json(
                "GET", "/api/v3/workspaces/%s/profiles/default/file-selector?q=perf50k.json" % wid
            )[2]["items"][0]
            elapsed, response = _timed(lambda: admin.json(
                "POST",
                "/api/v3/workspaces/%s/profiles/default/configs/%s/extraction-plans" % (wid, cid),
                {"version_id": sel["version_id"]},
            ))
            assert response[0] == 201, response
            plan = response[2]["plan"]
            report["timings_ms"]["plan_50k"] = round(elapsed, 1)
            report["fixtures"]["json_50k_rows"] = plan["counts"]["total"]

            elapsed, page = _timed(lambda: admin.json(
                "GET",
                "/api/v3/workspaces/%s/profiles/default/configs/%s/extraction-plans/%s/rows?offset=0&limit=50"
                % (wid, cid, plan["plan_id"]),
            ))
            assert page[0] == 200 and len(page[2]["items"]) == 50, page
            report["timings_ms"]["first_page_50k"] = round(elapsed, 1)

            def one_request() -> float:
                start = time.perf_counter()
                status, _h, body = admin.json(
                    "GET",
                    "/api/v3/workspaces/%s/profiles/default/configs/%s/extraction-plans/%s/rows?offset=1200&limit=100"
                    % (wid, cid, plan["plan_id"]),
                )
                assert status == 200, body
                return (time.perf_counter() - start) * 1000.0

            with concurrent.futures.ThreadPoolExecutor(max_workers=20) as pool:
                samples = list(pool.map(lambda _: one_request(), range(100)))
            samples.sort()
            report["p95_ms"]["rows_20_clients"] = round(samples[int(len(samples) * 0.95) - 1], 1)
            report["p95_ms"]["rows_median"] = round(statistics.median(samples), 1)

            elapsed, committed = _timed(lambda: admin.json(
                "POST",
                "/api/v3/workspaces/%s/profiles/default/configs/%s/extraction-plans/%s/commit"
                % (wid, cid, plan["plan_id"]),
                {"request_id": "perf-50k"},
            ))
            assert committed[0] == 200, committed
            report["timings_ms"]["commit_50k"] = round(elapsed, 1)
            report["timings_ms"]["commit_50k_created"] = committed[2]["result"]["created"]
            elapsed, params50k = _timed(lambda: admin.json(
                "GET",
                "/api/v3/workspaces/%s/profiles/default/configs/%s/parameters?offset=0&limit=100"
                % (wid, cid),
            ))
            assert params50k[0] == 200 and len(params50k[2]["items"]) == 100, params50k[0]
            report["timings_ms"]["parameters_50k_first_page"] = round(elapsed, 1)
            release = admin.json(
                "POST",
                "/api/v3/workspaces/%s/profiles/default/configs/%s/releases" % (wid, cid),
                {"version": "690"},
            )[2]["release"]["release_id"]
            admin.json(
                "POST",
                "/api/v3/workspaces/%s/profiles/default/configs/%s/drafts/d0-%s/freeze"
                % (wid, cid, release),
                {"request_id": "perf-freeze"},
            )
            elapsed, overview = _timed(lambda: admin.json(
                "GET",
                "/api/v3/workspaces/%s/profiles/default/configs/%s/releases/%s/overview?offset=0&limit=100"
                % (wid, cid, release),
            ))
            assert overview[0] == 200 and len(overview[2]["rows"]) == 100, overview[0]
            report["timings_ms"]["overview_50k_page"] = round(elapsed, 1)

            cid2 = admin.json(
                "POST", "/api/v3/workspaces/%s/profiles/default/configs" % wid,
                {"code": "MC-002", "name": "性能 3200"},
            )[2]["config"]["config_id"]
            xml = _xml_fixture(3200)
            report["fixtures"]["xml_3200_bytes"] = len(xml)
            admin.request(
                "PUT", "/api/v1/p/default/files/perf3200.xml", body=xml,
                headers={"X-Workspace-Id": wid, "Content-Type": "application/xml"},
            )
            sel2 = admin.json(
                "GET", "/api/v3/workspaces/%s/profiles/default/file-selector?q=perf3200.xml" % wid
            )[2]["items"][0]
            elapsed, plan2 = _timed(lambda: admin.json(
                "POST",
                "/api/v3/workspaces/%s/profiles/default/configs/%s/extraction-plans" % (wid, cid2),
                {"version_id": sel2["version_id"]},
            ))
            assert plan2[0] == 201, plan2
            report["timings_ms"]["preview_3200"] = round(elapsed, 1)
            report["fixtures"]["xml_3200_rows"] = plan2[2]["plan"]["counts"]["total"]
            elapsed, committed2 = _timed(lambda: admin.json(
                "POST",
                "/api/v3/workspaces/%s/profiles/default/configs/%s/extraction-plans/%s/commit"
                % (wid, cid2, plan2[2]["plan"]["plan_id"]),
                {"request_id": "perf-3200"},
            ))
            assert committed2[0] == 200, committed2
            report["timings_ms"]["commit_3200"] = round(elapsed, 1)
            report["timings_ms"]["commit_3200_created"] = committed2[2]["result"]["created"]

        report["pass"] = {
            "preview_3200": report["timings_ms"]["preview_3200"] <= report["budgets_ms"]["preview_3200"],
            "first_page_50k": report["timings_ms"]["first_page_50k"] <= report["budgets_ms"]["first_page_50k"],
            "commit_3200": report["timings_ms"]["commit_3200"] <= report["budgets_ms"]["commit_3200"],
        }
    finally:
        if not args.keep:
            shutil.rmtree(root, ignore_errors=True)

    text = json.dumps(report, ensure_ascii=False, indent=2)
    print(text)
    if args.json:
        with open(args.json, "w", encoding="utf-8") as handle:
            handle.write(text + "\n")
    return 0 if all(report.get("pass", {}).values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
