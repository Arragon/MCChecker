"""M10-04 DL 计算/系数绑定/历史按工作区隔离（INH-2127）。"""

from __future__ import annotations

import copy

from authz_fixtures import as_role, make_server, seed_roles

SOURCE = "dl_source.xml"
XML_W1 = b"<R><PPParams><m1>2</m1></PPParams></R>"
XML_W2 = b"<R><PPParams><m1>7</m1></PPParams></R>"
BINDING = {"source_file": SOURCE, "variable_path": "root/R/PPParams", "field_map": {"m1": "k1"}}


def _create(server, name):
    status, _h, body = as_role(server, "admin").json(
        "POST", "/api/v3/workspaces", {"name": name}
    )
    assert status == 201, body
    return body["workspace"]["id"]


def _put_file(server, wid, data):
    status, _h, body = as_role(server, "admin").request(
        "PUT", "/api/v1/p/default/files/%s" % SOURCE,
        body=data, headers={"X-Workspace-Id": wid, "Content-Type": "application/xml"},
    )
    assert status == 201, body


def _get(server, wid, path):
    status, _h, body = as_role(server, "deployer").json(
        "GET", path, headers={"X-Workspace-Id": wid}
    )
    assert status == 200, body
    return body


def test_dl_state_history_and_extract_are_workspace_scoped():
    seed_roles("dlscope")
    with make_server() as server:
        w1, w2 = _create(server, "W1"), _create(server, "W2")
        _put_file(server, w1, XML_W1)
        _put_file(server, w2, XML_W2)
        deployer = as_role(server, "deployer")

        state1 = _get(server, w1, "/api/v1/p/default/dl")
        state2 = _get(server, w2, "/api/v1/p/default/dl")
        assert state1["rev"] == state2["rev"], "两 W 都还没有 DL 配置，默认 rev 相同"

        item1 = copy.deepcopy(state1["items"]["rp_energy"])
        item1["coeffs"]["k1"] = 5.0
        item1["binding"] = dict(BINDING)
        status, _h, saved1 = deployer.json(
            "PUT", "/api/v1/p/default/dl/rp_energy", item1,
            headers={"X-Workspace-Id": w1, "If-Match": state1["rev"]},
        )
        assert status == 200, saved1
        assert saved1["item"]["coeffs"]["k1"] == 5.0
        assert saved1["item"]["binding"] == BINDING
        assert saved1["rev"] != state1["rev"]

        state2b = _get(server, w2, "/api/v1/p/default/dl")
        assert state2b["rev"] == state2["rev"], "W1 保存不得改 W2 的 rev"
        assert state2b["items"]["rp_energy"]["coeffs"]["k1"] == state1["items"]["rp_energy"]["coeffs"]["k1"]
        assert state2b["items"]["rp_energy"]["binding"]["source_file"] == "", (
            "W1 的绑定不得出现在 W2"
        )

        item2 = copy.deepcopy(state2b["items"]["rp_energy"])
        item2["coeffs"]["k1"] = 9.0
        status, _h, saved2 = deployer.json(
            "PUT", "/api/v1/p/default/dl/rp_energy", item2,
            headers={"X-Workspace-Id": w2, "If-Match": state2b["rev"]},
        )
        assert status == 200, "W2 用自己仍是当前的 rev 保存，不应被 W1 的保存拖进冲突"

        status, _h, conflict = deployer.json(
            "PUT", "/api/v1/p/default/dl/rp_energy", item1,
            headers={"X-Workspace-Id": w1, "If-Match": state1["rev"]},
        )
        assert status == 409 and conflict["error"]["code"] == "REVISION_CONFLICT"
        assert _get(server, w2, "/api/v1/p/default/dl")["items"]["rp_energy"]["coeffs"]["k1"] == 9.0

        # 系数历史各记各的：两边各有自己那次 PUT 的记录，绑定内容不同。
        h1_before = _get(server, w1, "/api/v1/p/default/dl/rp_energy/coeff-history")
        h2_before = _get(server, w2, "/api/v1/p/default/dl/rp_energy/coeff-history")
        assert len(h1_before["entries"]) == 1 and len(h2_before["entries"]) == 1
        assert h1_before["entries"][0]["binding"]["source_file"] == SOURCE
        assert h2_before["entries"][0]["binding"]["source_file"] == ""
        current1 = _get(server, w1, "/api/v1/p/default/dl")
        body = {
            "coeffs": dict(current1["items"]["rp_energy"]["coeffs"]),
            "note": "W1 现场",
            "x_min": current1["items"]["rp_energy"]["x_min"],
            "x_max": current1["items"]["rp_energy"]["x_max"],
            "y_min": current1["items"]["rp_energy"]["y_min"],
            "y_max": current1["items"]["rp_energy"]["y_max"],
            "binding": BINDING,
        }
        body["coeffs"]["k1"] = 6.0
        status, _h, hist1 = deployer.json(
            "POST", "/api/v1/p/default/dl/rp_energy/coeffs", body,
            headers={"X-Workspace-Id": w1, "If-Match": current1["rev"]},
        )
        assert status == 200, hist1
        assert len(_get(server, w1, "/api/v1/p/default/dl/rp_energy/coeff-history")["entries"]) == 2
        assert len(_get(server, w2, "/api/v1/p/default/dl/rp_energy/coeff-history")["entries"]) == 1

        # 同一个 binding（文件/路径相同）在 W1/W2 解析到各自的源文件。
        for wid, expect in ((w1, 2.0), (w2, 7.0)):
            status, _h, ext = deployer.json(
                "POST", "/api/v1/p/default/dl/rp_energy/extract",
                {"binding": BINDING}, headers={"X-Workspace-Id": wid},
            )
            assert status == 200, ext
            assert ext["coeffs"]["k1"] == expect, "绑定不得跨 W 解析到另一份源文件"
