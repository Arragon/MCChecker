"""mock↔后端外部工具契约（M3-15 / INH-1900；R19、R45）。

`mock-api.js` 在这里被**执行**（`tests/utils/mock_tools_crosscheck.js` 载入真身、逐条跑
`MC.mock.handle`），不是被 grep。同一串请求也打真 HTTP 后端，两侧的状态码、错误码、
`detail.field` 与文案必须逐字一致；成功响应比较 `item` / `removed` / 列表项（忽略
`added_at` 的具体时间值，只要求存在）。

用例串行：改名冲突与「删第二次」依赖前序状态，mock 与后端各自按同一顺序重放。
"""

from __future__ import annotations

import json
import shutil
import subprocess
import urllib.parse
from pathlib import Path

import pytest
from authz_fixtures import as_role, make_server, seed_roles

REPO_ROOT = Path(__file__).resolve().parents[1]
ORACLE = REPO_ROOT / "tests" / "utils" / "mock_tools_crosscheck.js"
NODE = shutil.which("node")

NAME = "构建工具"
URL = "https://example.com/build?x=1"

CASES = [
    {"name": "create 构建工具", "method": "PUT", "pathName": NAME, "body": {"name": NAME, "description": "生成固件的脚本", "url": URL}},
    {"name": "create 另一个", "method": "PUT", "pathName": "另一个", "body": {"name": "另一个", "description": "", "url": "http://127.0.0.1:8080/a"}},
    {"name": "rename 构建工具 onto 另一个", "method": "PUT", "pathName": NAME, "body": {"name": "另一个", "description": "", "url": URL}},
    {"name": "bad name slash", "method": "PUT", "pathName": "_", "body": {"name": "a/b", "description": "", "url": URL}},
    {"name": "bad name percent", "method": "PUT", "pathName": "_", "body": {"name": "工具%20名", "description": "", "url": URL}},
    {"name": "javascript url", "method": "PUT", "pathName": "_", "body": {"name": "坏链", "description": "", "url": "javascript:alert(1)"}},
    {"name": "ftp url", "method": "PUT", "pathName": "_", "body": {"name": "坏链", "description": "", "url": "ftp://example.com/x"}},
    {"name": "no scheme", "method": "PUT", "pathName": "_", "body": {"name": "坏链", "description": "", "url": "example.com/x"}},
    {"name": "http without host", "method": "PUT", "pathName": "_", "body": {"name": "坏链", "description": "", "url": "http://"}},
    {"name": "bad port", "method": "PUT", "pathName": "_", "body": {"name": "坏链", "description": "", "url": "http://example.com:99999"}},
    {"name": "percent host", "method": "PUT", "pathName": "_", "body": {"name": "坏链", "description": "", "url": "http://exa%6dple.com/x"}},
    {"name": "userinfo", "method": "PUT", "pathName": "_", "body": {"name": "坏链", "description": "", "url": "http://user@example.com/x"}},
    {"name": "too long description", "method": "PUT", "pathName": "_", "body": {"name": "长说明", "description": "a" * 101, "url": URL}},
    {"name": "update with empty description", "method": "PUT", "pathName": NAME, "body": {"name": NAME, "description": "", "url": "https://example.com/v2"}},
    {"name": "delete 构建工具", "method": "DELETE", "pathName": NAME},
    {"name": "delete 构建工具 again", "method": "DELETE", "pathName": NAME},
    {"name": "list", "method": "GET"},
]


def _url(pid, case):
    base = "/api/v1/p/%s/tools" % pid
    if "pathName" not in case:
        return base
    return base + "/" + urllib.parse.quote(case["pathName"], safe="")


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


def _tool(row):
    return {
        "name": row.get("name"),
        "description": row.get("description"),
        "url": row.get("url"),
    }


@pytest.mark.skipif(
    NODE is None,
    reason="本机没有 Node：mock↔后端外部工具契约**未执法**（这一轮的绿证明不了 mock-api.js "
           "与 services/tools.py 同判；换有 node 的机器重跑本文件）",
)
def test_mock_and_backend_agree_on_every_tool_case():
    mock = _mock()
    pid = seed_roles("toolmock")
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
                backend_items = [_tool(row) for row in body["items"]]
                mock_items = [_tool(row) for row in mock_row["body"]["items"]]
                assert backend_items == mock_items, (backend_items, mock_items)
                assert all(row.get("added_at") for row in body["items"]), body
            elif case["method"] == "DELETE":
                assert body == mock_row["body"], (case["name"], body, mock_row["body"])
            else:
                assert _tool(body["item"]) == _tool(mock_row["body"]["item"]), (
                    case["name"], body, mock_row["body"]
                )
                assert body["item"].get("added_at"), body
