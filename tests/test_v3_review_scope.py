"""M10-03 remarks/审阅按工作区隔离（INH-2126）。"""

from __future__ import annotations

import hashlib

from authz_fixtures import as_role, make_server, seed_roles

FILE = "cfg.xml"
LOC_ITEM = "/R[1]/params[1]/item[1]"
LOC_PARAM = "/R[1]/params[1]/param_01[1]"

XML_W1 = b"<R><params><item>first</item><param_01>5</param_01></params></R>"
XML_W2 = b"<R><params><item>first</item><param_01>9</param_01></params></R>"


def _create(server, name):
    status, _h, body = as_role(server, "admin").json(
        "POST", "/api/v3/workspaces", {"name": name}
    )
    assert status == 201, body
    return body["workspace"]["id"]


def _put(server, wid, data):
    status, _h, body = as_role(server, "admin").request(
        "PUT", "/api/v1/p/default/files/%s" % FILE,
        body=data, headers={"X-Workspace-Id": wid, "Content-Type": "application/xml"},
    )
    assert status == 201, body


def _sha(data):
    return hashlib.sha256(data).hexdigest()


def _add(server, wid, loc, proposed, sha):
    status, _h, body = as_role(server, "admin").json(
        "POST", "/api/v1/p/default/remarks",
        {"file": FILE, "sha256": sha, "loc": loc, "proposed_value": proposed},
        headers={"X-Workspace-Id": wid},
    )
    assert status == 201, body
    return body["item"]


def _get(server, wid, path):
    status, _h, body = as_role(server, "admin").json(
        "GET", path, headers={"X-Workspace-Id": wid}
    )
    assert status == 200, body
    return body


def _submit(server, wid, decisions, request_id, sha):
    return as_role(server, "admin").json(
        "POST", "/api/v1/p/default/review/submit",
        {"request_id": request_id, "file": FILE, "source_sha": sha, "decisions": decisions},
        headers={"X-Workspace-Id": wid},
    )


def test_remarks_review_and_idempotency_are_workspace_scoped():
    seed_roles("review")
    with make_server() as server:
        w1, w2 = _create(server, "W1"), _create(server, "W2")
        _put(server, w1, XML_W1)
        _put(server, w2, XML_W2)

        item1 = _add(server, w1, LOC_ITEM, "A", _sha(XML_W1))
        item2 = _add(server, w2, LOC_ITEM, "B", _sha(XML_W2))
        assert item1["proposed_value"] == "A" and item2["proposed_value"] == "B"

        r1 = _get(server, w1, "/api/v1/p/default/remarks")["items"]
        r2 = _get(server, w2, "/api/v1/p/default/remarks")["items"]
        assert [i["id"] for i in r1] == [item1["id"]]
        assert [i["id"] for i in r2] == [item2["id"]], "同名文件的建议不得跨 W 串单"

        review1 = _get(server, w1, "/api/v1/p/default/review")
        review2 = _get(server, w2, "/api/v1/p/default/review")
        files1 = {f["file"]: f for f in review1["files"]}
        files2 = {f["file"]: f for f in review2["files"]}
        assert set(files1) == {FILE} and set(files2) == {FILE}
        assert len(files1[FILE]["nodes"]) == 1
        assert len(files2[FILE]["nodes"]) == 1
        g1 = files1[FILE]["nodes"][0]["group_key"]
        g2 = files2[FILE]["nodes"][0]["group_key"]

        # 用 W2 的 sha 在 W1 提交：必须 409，且不产生新文件、不改建议状态。
        status, _h, body = _submit(
            server, w1,
            [{"group_key": g1, "choice": item1["id"]}],
            "req-w1", _sha(XML_W2),
        )
        assert status == 409, body
        assert _get(server, w1, "/api/v1/p/default/files")["items"][0]["name"] == FILE

        # 部分提交：W1 只决定 item，param_01 保持 pending，生成文件只出现在 W1。
        _add(server, w1, LOC_PARAM, "6", _sha(XML_W1))
        status, _h, out = _submit(
            server, w1,
            [{"group_key": g1, "choice": item1["id"]}],
            "req-w1-ok", _sha(XML_W1),
        )
        assert status == 200, out
        assert out["approved"] == 1
        generated = out["generated_file"]
        pending = {
            f["file"]: f for f in _get(server, w1, "/api/v1/p/default/review")["files"]
        }[FILE]["nodes"]
        assert [n["loc"] for n in pending] == [LOC_PARAM], "未决定的分组必须保留"
        names1 = [f["name"] for f in _get(server, w1, "/api/v1/p/default/files")["items"]]
        names2 = [f["name"] for f in _get(server, w2, "/api/v1/p/default/files")["items"]]
        assert generated in names1 and generated not in names2
        assert names2 == [FILE]

        # 同一个 request_id 在两个 W 各自幂等，不是跨 W 共享回执。
        same = "req-w1-ok"
        s1, _h, again = _submit(
            server, w1,
            [{"group_key": g1, "choice": item1["id"]}],
            same, _sha(XML_W1),
        )
        assert s1 == 200 and again.get("generated_file") == generated
        s2, _h, out2 = _submit(
            server, w2,
            [{"group_key": g2, "choice": item2["id"]}],
            same, _sha(XML_W2),
        )
        assert s2 == 200 and out2["generated_file"]
        raw1 = as_role(server, "admin").request(
            "GET", "/api/v1/p/default/files/%s/raw" % generated,
            headers={"X-Workspace-Id": w1},
        )
        raw2 = as_role(server, "admin").request(
            "GET", "/api/v1/p/default/files/%s/raw" % out2["generated_file"],
            headers={"X-Workspace-Id": w2},
        )
        assert raw1[0] == 200 and b"<item>A</item>" in raw1[2] and b"B" not in raw1[2]
        assert raw2[0] == 200 and b"<item>B</item>" in raw2[2] and b"A" not in raw2[2], (
            "同一个 request_id 在两个 W 各自生成自己的文件，不得复用对方回执"
        )
        orig1 = as_role(server, "admin").request(
            "GET", "/api/v1/p/default/files/%s/raw" % FILE,
            headers={"X-Workspace-Id": w1},
        )
        orig2 = as_role(server, "admin").request(
            "GET", "/api/v1/p/default/files/%s/raw" % FILE,
            headers={"X-Workspace-Id": w2},
        )
        assert orig1[2] == XML_W1 and orig2[2] == XML_W2, "生成新文件不得覆盖原文件"

        # 管理员名单仍是全局一份。
        a1 = _get(server, w1, "/api/v1/p/default/admins")["items"]
        a2 = _get(server, w2, "/api/v1/p/default/admins")["items"]
        assert a1 == a2 and a1, "旧 admin 名单必须全局一致"
