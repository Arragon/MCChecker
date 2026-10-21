"""mock↔后端变量绑定契约（M3-14 / INH-1899；R19、R45）。

`mock-api.js` 在这里被**执行**（`tests/utils/mock_bindings_crosscheck.js` 载入真身、
逐条跑 `MC.mock.handle`），不是被 grep。同一串请求也打真 HTTP 后端，两侧的状态码、
错误码、`detail.field` 与文案必须逐字一致；成功响应比较 `item` / `removed` 形状。

用例串行：改名冲突与「删第二次」依赖前序状态，mock 与后端各自按同一顺序重放。
"""

from __future__ import annotations

import json
import shutil
import subprocess
import urllib.parse
from pathlib import Path

import pytest
from authz_fixtures import as_role, make_server, seed_roles, write_target

REPO_ROOT = Path(__file__).resolve().parents[1]
ORACLE = REPO_ROOT / "tests" / "utils" / "mock_bindings_crosscheck.js"
NODE = shutil.which("node")

FILE_A = "a.xml"
FILE_B = "b.xml"
VARS = [
    {"file": FILE_A, "path": FILE_A + "/R/x"},
    {"file": FILE_B, "path": FILE_B + "/R/x"},
]

CASES = [
    {"name": "create 超时组", "method": "PUT", "group": "超时组", "body": {"group_name": "超时组", "variables": VARS}},
    {"name": "create 组A", "method": "PUT", "group": "组A", "body": {"group_name": "组A", "variables": VARS}},
    {"name": "create 组B", "method": "PUT", "group": "组B", "body": {"group_name": "组B", "variables": VARS}},
    {"name": "rename 组A onto 组B", "method": "PUT", "group": "组A", "body": {"group_name": "组B", "variables": VARS}},
    {"name": "rename 超时组 -> 新组", "method": "PUT", "group": "超时组", "body": {"group_name": "新组", "variables": VARS}},
    {"name": "one valid variable", "method": "PUT", "group": "单变量", "body": {"group_name": "单变量", "variables": [VARS[0], {"file": FILE_A, "path": "  "}]}},
    {"name": "empty variables", "method": "PUT", "group": "空组", "body": {"group_name": "空组", "variables": []}},
    {"name": "variables is not a list", "method": "PUT", "group": "坏形状", "body": {"group_name": "坏形状", "variables": "oops"}},
    {"name": "invalid rows are dropped", "method": "PUT", "group": "有效两行", "body": {"group_name": "有效两行", "variables": [VARS[0], {"file": FILE_A, "path": ""}, VARS[1], {"file": "missing.xml", "path": "x"}]}},
    {"name": "bad group slash", "method": "PUT", "group": "_", "body": {"group_name": "a/b", "variables": VARS}},
    {"name": "bad group percent", "method": "PUT", "group": "_", "body": {"group_name": "组%20名", "variables": VARS}},
    {"name": "percent that can be deleted", "method": "PUT", "group": "完成度%zz", "body": {"group_name": "完成度%zz", "variables": VARS}},
    {"name": "delete 组A", "method": "DELETE", "group": "组A"},
    {"name": "delete 组A again", "method": "DELETE", "group": "组A"},
    {"name": "list", "method": "GET"},
]


def _url(pid, case):
    base = "/api/v1/p/%s/bindings" % pid
    if "group" not in case:
        return base
    return base + "/" + urllib.parse.quote(case["group"], safe="")


def _mock():
    assert ORACLE.is_file(), "oracle 不见了：{}".format(ORACLE)
    proc = subprocess.run(
        [NODE, str(ORACLE)],
        input=json.dumps(CASES, ensure_ascii=False),
        capture_output=True,
        text=True,
        timeout=180,
        cwd=str(REPO_ROOT),
    )
    assert proc.returncode == 0, proc.stderr[-2000:]
    out = json.loads(proc.stdout)
    assert "error" not in out, out.get("error")
    assert out["count"] == len(CASES), out
    return {row["name"]: row for row in out["results"]}


def _error_signature(body):
    error = (body or {}).get("error") or {}
    return (
        error.get("code"),
        (error.get("detail") or {}).get("field"),
        error.get("message"),
    )


@pytest.mark.skipif(
    NODE is None,
    reason="本机没有 Node：mock↔后端变量绑定契约**未执法**（这一轮的绿证明不了 mock-api.js "
           "与 services/bindings.py 同判；换有 node 的机器重跑本文件）",
)
def test_mock_and_backend_agree_on_every_binding_case():
    mock = _mock()
    pid = seed_roles("bindmock")
    write_target(pid, FILE_A, b"<R><x>1</x></R>")
    write_target(pid, FILE_B, b"<R><x>2</x></R>")
    with make_server() as server:
        caller = as_role(server, "deployer")
        for case in CASES:
            status, _headers, body = caller.json(
                case["method"],
                _url(pid, case),
                case.get("body"),
            )
            mock_row = mock[case["name"]]
            assert status == mock_row["status"], (case["name"], status, body, mock_row)
            if status >= 400:
                assert _error_signature(body) == _error_signature(mock_row["body"]), (
                    case["name"], body, mock_row["body"]
                )
            elif case["method"] == "GET":
                assert body == mock_row["body"], (case["name"], body, mock_row["body"])
            elif case["method"] == "DELETE":
                assert body == mock_row["body"], (case["name"], body, mock_row["body"])
            else:
                assert body.get("item") == mock_row["body"].get("item"), (
                    case["name"], body, mock_row["body"]
                )
