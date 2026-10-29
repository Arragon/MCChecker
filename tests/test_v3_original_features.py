"""M10-06 F01–F28 原功能回归与跨工作区数据守卫（INH-2129）。

本文件只做「原功能在多 W/P 下仍按原动作工作」的矩阵回归；细粒度边界由现有
`test_api_*.py` 与前端 100 条用例覆盖。数据守卫由 `tests/conftest.py` 的 H3 执法。

A01–A05 的落点：
- A01 壳层/速览/收藏/标签/命令面板：前端 100 条 + 本文件 favorites/document。
- A02 建议/审阅/源 SHA/新文件不覆盖源：`test_api_review.py` + 本文件 remarks/review。
- A03 搜索/比较/记录/归档下载：本文件 search + archives raw；比较与记录见
  `test_api_compare.py` / `test_api_history.py`。
- A04 DL/设置/工具：本文件 DL/schedule/tools + `test_api_dl.py` / `test_api_schedule.py`。
- A05 任务/调度捕获 W+P：`test_v3_tasks_settings_scope.py` + 本文件 50 连接。
"""

from __future__ import annotations

import hashlib
import json
from concurrent.futures import ThreadPoolExecutor

from authz_fixtures import as_role, make_server, seed_roles

FILE = "regression.xml"


def _token(wid, pid, rev):
    raw = ("%s%s%d" % (wid, pid, rev)).replace("-", "").replace("_", "")
    return "T" + raw


def _xml(token):
    return (
        '<R><marker>%s</marker><params><item>%s</item></params></R>' % (token, token)
    ).encode("utf-8")


def _sha(data):
    return hashlib.sha256(data).hexdigest()


def _headers(wid):
    return {"X-Workspace-Id": wid}


def _put(server, wid, pid, data, overwrite=False):
    url = "/api/v1/p/%s/files/%s%s" % (pid, FILE, "?overwrite=1" if overwrite else "")
    status, _h, raw = as_role(server, "admin").request(
        "PUT", url, body=data,
        headers={"X-Workspace-Id": wid, "Content-Type": "application/xml"},
    )
    assert status in (200, 201), (wid, pid, raw)
    return json.loads(raw.decode("utf-8"))


def _get(server, wid, pid, path, role="member"):
    status, _h, body = as_role(server, role).json(
        "GET", "/api/v1/p/%s%s" % (pid, path), headers=_headers(wid)
    )
    return status, body


def test_original_feature_matrix_across_two_workspaces_and_two_profiles():
    seed_roles("p1")
    with make_server() as server:
        admin = as_role(server, "admin")
        w1 = admin.json("POST", "/api/v3/workspaces", {"name": "Reg W1"})[2]["workspace"]["id"]
        w2 = admin.json("POST", "/api/v3/workspaces", {"name": "Reg W2"})[2]["workspace"]["id"]
        second = admin.json(
            "POST", "/api/v3/workspaces/%s/profiles" % w1, {"name": "Second"}
        )[2]["profile"]["id"]
        second2 = admin.json(
            "POST", "/api/v3/workspaces/%s/profiles" % w2, {"name": "Second"}
        )[2]["profile"]["id"]
        assert second == second2 == "second"
        scopes = [
            ("default", "default"),
            ("default", "p1"),
            (w1, "default"),
            (w1, second),
            (w2, "default"),
            (w2, second),
        ]

        for wid, pid in scopes:
            first = _token(wid, pid, 1)
            second_token = _token(wid, pid, 2)
            data1, data2 = _xml(first), _xml(second_token)

            body = _put(server, wid, pid, data1)
            assert body.get("archived") is None

            status, listing = _get(server, wid, pid, "/files", "visitor")
            assert status == 200 and [f["name"] for f in listing["items"]] == [FILE]

            status, doc = _get(server, wid, pid, "/files/%s/document" % FILE)
            assert status == 200 and first in doc["nodes"]["v"], (wid, pid, doc)

            status, _h, raw = as_role(server, "visitor").request(
                "GET", "/api/v1/p/%s/files/%s/raw" % (pid, FILE), headers=_headers(wid)
            )
            assert status == 200 and raw == data1

            status, _h, fav = as_role(server, "member").json(
                "POST", "/api/v1/p/%s/favorites" % pid,
                {"file": FILE, "id": "root/R/params/item"}, headers=_headers(wid),
            )
            assert status == 201 and fav["item"]["value"] == first, (wid, pid, fav)
            status, overview = _get(server, wid, pid, "/favorites")
            assert status == 200 and overview["groups"][0]["items"][0]["value"] == first

            status, _h, remark = as_role(server, "member").json(
                "POST", "/api/v1/p/%s/remarks" % pid,
                {"file": FILE, "sha256": _sha(data1), "loc": "/R[1]/params[1]/item[1]",
                 "proposed_value": first + "X"},
                headers=_headers(wid),
            )
            assert status == 201, (wid, pid, remark)
            status, review = _get(server, wid, pid, "/review", "admin")
            assert status == 200
            files = {f["file"]: f for f in review["files"]}
            assert files[FILE]["nodes"][0]["current_value"] == first

            overwritten = _put(server, wid, pid, data2, overwrite=True)
            assert overwritten["archived"], (wid, pid, overwritten)
            status, archives = _get(server, wid, pid, "/files/%s/archives" % FILE)
            assert status == 200 and archives["items"], (wid, pid, archives)
            old_name = archives["items"][0]["filename"]
            status, _h, old_raw = as_role(server, "visitor").request(
                "GET",
                "/api/v1/p/%s/files/%s/raw?version=archive:%s" % (pid, FILE, old_name),
                headers=_headers(wid),
            )
            assert status == 200 and old_raw == data1, "归档后仍能下载旧版本"

            status, found = _get(server, wid, pid, "/search?q=%s" % second_token)
            assert status == 200 and found["total"] >= 2, (wid, pid, found)
            assert all(hit["file"] == FILE for hit in found["items"])
            status, missing = _get(server, wid, pid, "/search?q=NO_SUCH_TOKEN_ANYWHERE")
            assert status == 200 and missing["total"] == 0

            status, dl = _get(server, wid, pid, "/dl")
            assert status == 200 and set(dl["items"]) == {
                "pp_energy", "rp_energy", "rp_width", "ppz"
            }
            status, schedule = _get(server, wid, pid, "/schedule")
            assert status == 200 and set(schedule) == {
                "enabled", "interval_hours", "last_run", "next_run_at"
            }
            status, tools = _get(server, wid, pid, "/tools")
            assert status == 200 and tools["items"] == []

        # 每个作用域最后读回自己的第二版，确认没有写串。
        for wid, pid in scopes:
            status, _h, raw = as_role(server, "visitor").request(
                "GET", "/api/v1/p/%s/files/%s/raw" % (pid, FILE), headers=_headers(wid)
            )
            assert status == 200 and raw == _xml(_token(wid, pid, 2)), (wid, pid)


def test_fifty_concurrent_clients_keep_their_workspace_scope():
    seed_roles("p1")
    with make_server() as server:
        admin = as_role(server, "admin")
        w1 = admin.json("POST", "/api/v3/workspaces", {"name": "Many W1"})[2]["workspace"]["id"]
        w2 = admin.json("POST", "/api/v3/workspaces", {"name": "Many W2"})[2]["workspace"]["id"]
        scopes = [("default", "default"), ("default", "p1"), (w1, "default"), (w2, "default")]
        for wid, pid in scopes:
            _put(server, wid, pid, _xml(_token(wid, pid, 1)))

        def call(i):
            wid, pid = scopes[i % len(scopes)]
            status, body = _get(server, wid, pid, "/files", "visitor")
            assert status == 200, (wid, pid, body)
            assert [f["name"] for f in body["items"]] == [FILE]
            return wid, pid

        with ThreadPoolExecutor(max_workers=50) as pool:
            results = list(pool.map(call, range(50)))
        assert len(results) == 50
        assert set(results) == set(scopes)
