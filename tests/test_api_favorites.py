"""收藏 API（§7.7；M2-01）。

覆盖：叶子/子树快照、重复收藏返回原项、被父级覆盖 409、实时值按旧 ID 取**先序最后一个**、
缺失标记与分组排序、备注、删除与清空、四角色、CSRF 硬条件、修订号递增。
"""

from __future__ import annotations

import os

from authz_fixtures import as_role, make_server, seed_roles, write_target

from app.core import storage
from app.services import revision

FILE_A = "scenario_a.xml"
FILE_B = "scenario_b.xml"

XML_A = (
    '<R>\n'
    '  <params>\n'
    '    <param_01 description="宽度" min="0" max="10">5</param_01>\n'
    '    <item>first</item>\n'
    '    <item>second</item>\n'
    '  </params>\n'
    '  <empty/>\n'
    '</R>\n'
)
XML_A_REVISED = XML_A.replace("<item>first</item>", "<item>alpha</item>").replace(
    "<item>second</item>", "<item>omega</item>"
)
XML_B = '<R><only>42</only></R>\n'

PARAM_ID = "root/R/params/param_01"
ITEM_ID = "root/R/params/item"
PARAMS_ID = "root/R/params"
EMPTY_ID = "root/R/empty"


def _favorites_url(pid: str) -> str:
    return "/api/v1/p/%s/favorites" % pid


def _get(caller, pid):
    status, _headers, body = caller.json("GET", _favorites_url(pid))
    assert status == 200, body
    return body["groups"]


def _post(caller, pid, file_name, node_id):
    return caller.json("POST", _favorites_url(pid), {"file": file_name, "id": node_id})


class TestFavoritesApi:
    def setup_method(self):
        self.pid = seed_roles("fav1")
        write_target(self.pid, FILE_A, XML_A.encode("utf-8"))
        write_target(self.pid, FILE_B, XML_B.encode("utf-8"))

    def test_empty_list(self):
        with make_server() as server:
            caller = as_role(server, "deployer")
            assert _get(caller, self.pid) == []

    def test_leaf_favorite_uses_server_side_label_and_value(self):
        with make_server() as server:
            caller = as_role(server, "member")
            status, _h, body = _post(caller, self.pid, FILE_A, PARAM_ID)
            assert status == 201, body
            item = body["item"]
            assert item["path"] == PARAM_ID
            assert item["label"] == "param_01"
            assert item["value"] == "5"
            assert item["source_file"] == FILE_A
            assert item["children"] == []
            groups = _get(caller, self.pid)
            assert len(groups) == 1 and groups[0]["file"] == FILE_A
            assert groups[0]["exists"] is True
            assert groups[0]["updated_at"]

    def test_container_favorite_snapshots_children(self):
        with make_server() as server:
            caller = as_role(server, "member")
            status, _h, body = _post(caller, self.pid, FILE_A, PARAMS_ID)
            assert status == 201, body
            labels = [child["label"] for child in body["item"]["children"]]
            assert labels == ["param_01", "item", "item"]
            assert body["item"]["value"] is None

    def test_childless_container_is_rejected(self):
        with make_server() as server:
            caller = as_role(server, "member")
            status, _h, body = _post(caller, self.pid, FILE_A, EMPTY_ID)
            assert status == 400, body
            assert body["error"]["code"] == "INVALID_INPUT"

    def test_duplicate_returns_original_item(self):
        with make_server() as server:
            caller = as_role(server, "member")
            first = _post(caller, self.pid, FILE_A, PARAM_ID)[2]["item"]
            status, _h, second = _post(caller, self.pid, FILE_A, PARAM_ID)
            assert status == 200, second
            assert second["item"]["path"] == first["path"]
            groups = _get(caller, self.pid)
            assert len(groups[0]["items"]) == 1

    def test_child_of_saved_parent_is_covered(self):
        with make_server() as server:
            caller = as_role(server, "member")
            assert _post(caller, self.pid, FILE_A, PARAMS_ID)[0] == 201
            status, _h, body = _post(caller, self.pid, FILE_A, PARAM_ID)
            assert status == 409, body
            assert body["error"]["code"] == "FAVORITE_COVERED"
            assert body["error"]["detail"]["parent"] == PARAMS_ID

    def test_missing_file_and_node_are_404(self):
        with make_server() as server:
            caller = as_role(server, "member")
            status, _h, body = _post(caller, self.pid, "nope.xml", PARAM_ID)
            assert status == 404 and body["error"]["code"] == "NOT_FOUND"
            status, _h, body = _post(caller, self.pid, FILE_A, "root/R/不存在")
            assert status == 404 and body["error"]["code"] == "NOT_FOUND"

    def test_live_value_uses_last_sibling_in_preorder(self):
        """§7.7：同名兄弟共享旧 ID，实时值取先序**最后一个**（与 v1 一致，mock 曾取第一个）。"""
        with make_server() as server:
            caller = as_role(server, "member")
            status, _h, body = _post(caller, self.pid, FILE_A, ITEM_ID)
            assert status == 201, body
            assert body["item"]["value"] == "second"

            write_target(self.pid, FILE_A, XML_A_REVISED.encode("utf-8"))
            groups = _get(caller, self.pid)
            item = groups[0]["items"][0]
            assert item["value"] == "omega", "实时值应取最后一个同名节点"
            assert item["saved_value"] == "second"
            assert item["missing"] is False

    def test_missing_group_sinks_and_flags_children(self):
        with make_server() as server:
            caller = as_role(server, "member")
            _post(caller, self.pid, FILE_A, PARAMS_ID)
            _post(caller, self.pid, FILE_B, "root/R/only")
            # UC-11 的「源文件已不存在」来自文件被外部删除或改名：走 App 内 DELETE 会连带
            # 清掉引用（§7 的 DELETE 语义），所以这里直接从磁盘移除文件。
            storage.set_active_profile(self.pid)
            os.remove(storage.get_config_path(FILE_B))

            groups = _get(caller, self.pid)
            assert [g["file"] for g in groups] == [FILE_A, FILE_B], "存在的文件排在前面"
            assert groups[0]["exists"] is True
            gone = groups[1]
            assert gone["exists"] is False
            assert gone["updated_at"] is None
            assert gone["items"][0]["missing"] is True
            assert gone["items"][0]["saved_value"] == "42"

    def test_patch_note_and_unknown_path(self):
        with make_server() as server:
            caller = as_role(server, "member")
            _post(caller, self.pid, FILE_A, PARAM_ID)
            status, _h, body = caller.json(
                "PATCH", _favorites_url(self.pid), {"file": FILE_A, "path": PARAM_ID, "note": "现场常用"}
            )
            assert status == 200, body
            assert body["item"]["note"] == "现场常用"
            assert _get(caller, self.pid)[0]["items"][0]["note"] == "现场常用"
            # 备注必须落在 favorites.json 上（重启进程后仍在）
            storage.set_active_profile(self.pid)
            on_disk = storage.load_favorites()
            assert on_disk[0]["note"] == "现场常用"
            status, _h, body = caller.json(
                "PATCH", _favorites_url(self.pid), {"file": FILE_A, "path": "root/R/无", "note": "x"}
            )
            assert status == 404 and body["error"]["code"] == "NOT_FOUND"

    def test_delete_one_then_clear_file(self):
        with make_server() as server:
            caller = as_role(server, "member")
            _post(caller, self.pid, FILE_A, PARAM_ID)
            _post(caller, self.pid, FILE_A, ITEM_ID)
            status, _h, body = caller.json(
                "DELETE", "%s?file=%s&path=%s" % (_favorites_url(self.pid), FILE_A, ITEM_ID)
            )
            assert status == 200 and body == {"removed": 1}, body
            assert len(_get(caller, self.pid)[0]["items"]) == 1
            status, _h, body = caller.json("DELETE", "%s?file=%s" % (_favorites_url(self.pid), FILE_A))
            assert body == {"removed": 1}, body
            assert _get(caller, self.pid) == []

    def test_missing_file_argument(self):
        with make_server() as server:
            caller = as_role(server, "member")
            status, _h, body = caller.json("DELETE", _favorites_url(self.pid))
            assert status == 400 and body["error"]["detail"]["field"] == "file"

    def test_restoring_the_file_returns_the_group(self):
        """UC-11：文件放回后回到原组（收藏数据本身未被动过）。"""
        with make_server() as server:
            caller = as_role(server, "member")
            _post(caller, self.pid, FILE_B, "root/R/only")
            storage.set_active_profile(self.pid)
            path = storage.get_config_path(FILE_B)
            backup = open(path, "rb").read()
            os.remove(path)
            gone = _get(caller, self.pid)[0]
            assert gone["exists"] is False and gone["items"][0]["missing"] is True

            with open(path, "wb") as fh:
                fh.write(backup)
            back = _get(caller, self.pid)[0]
            assert back["exists"] is True
            assert back["items"][0]["missing"] is False
            assert back["items"][0]["value"] == "42"

    def test_legacy_favorites_json_is_readable(self):
        """旧 favorites.json 字段不变：v1 写入的样本能被 GET /favorites 解析。"""
        legacy = [
            {
                "path": "root/R/params/param_01",
                "label": "param_01",
                "value": "5",
                "source_file": FILE_A,
                "note": "旧备注",
                "children": [
                    {"label": "item", "value": "first", "path": ITEM_ID, "children": []}
                ],
                "added_at": "2026-05-24 10:00:00",
            }
        ]
        storage.set_active_profile(self.pid)
        storage.save_favorites(legacy)
        with make_server() as server:
            caller = as_role(server, "member")
            groups = _get(caller, self.pid)
            assert len(groups) == 1 and groups[0]["file"] == FILE_A
            item = groups[0]["items"][0]
            assert item["note"] == "旧备注"
            assert item["saved_value"] == "5"
            assert item["value"] == "5"
            assert item["children"][0]["path"] == ITEM_ID
            # 旧快照里 item 的值是 first，实时值应取同名兄弟的最后一个（second）
            assert item["children"][0]["value"] == "second"
            assert item["children"][0]["missing"] is False

    def test_all_roles_may_write_favorites(self):
        """§4.2：`favorites_write` 对所有角色开放（含访客）。"""
        with make_server() as server:
            for role in ("visitor", "member", "admin", "deployer"):
                caller = as_role(server, role)
                status, _h, body = _post(caller, self.pid, FILE_A, PARAM_ID)
                assert status in (200, 201), (role, body)

    def test_write_without_csrf_header_is_forbidden(self):
        with make_server() as server:
            caller = as_role(server, "deployer")
            status, _h, _b = caller.request(
                "POST", _favorites_url(self.pid), body={"file": FILE_A, "id": PARAM_ID}, with_csrf=False
            )
            assert status == 403

    def test_unknown_profile_is_404(self):
        with make_server() as server:
            caller = as_role(server, "member")
            status, _h, _b = caller.json("GET", "/api/v1/p/no-such-model/favorites")
            assert status == 404


class TestRevisionEndpoint:
    """§7.3 + §7.1：写成功后递增对应分量；读接口不递增。"""

    def setup_method(self):
        revision.reset()  # 计数是进程内状态，跨用例不会自动归零
        self.pid = seed_roles("favrev")
        write_target(self.pid, FILE_A, XML_A.encode("utf-8"))

    def _rev(self, caller):
        status, _h, body = caller.json("GET", "/api/v1/p/%s/revision" % self.pid)
        assert status == 200, body
        return body

    def test_starts_at_one_and_bumps_only_favorites(self):
        with make_server() as server:
            caller = as_role(server, "member")
            before = self._rev(caller)
            assert before["rev"] == "1.1.1.1.1"
            assert before["parts"]["favorites"] == 1

            _post(caller, self.pid, FILE_A, PARAM_ID)
            after = self._rev(caller)
            assert after["parts"]["favorites"] == 2
            assert after["parts"]["files"] == 1
            assert after["rev"] == "1.2.1.1.1"

    def test_reads_do_not_bump_but_deletes_do(self):
        with make_server() as server:
            caller = as_role(server, "member")
            _post(caller, self.pid, FILE_A, PARAM_ID)
            _get(caller, self.pid)
            self._rev(caller)
            assert self._rev(caller)["parts"]["favorites"] == 2
            caller.json("DELETE", "%s?file=%s" % (_favorites_url(self.pid), FILE_A))
            assert self._rev(caller)["parts"]["favorites"] == 3

    def test_failed_writes_do_not_bump(self):
        """§7.1 只在写成功后递增：409/400/404/403 与「删了 0 条」都不该动计数。"""
        with make_server() as server:
            caller = as_role(server, "member")
            base = self._rev(caller)["parts"]["favorites"]
            _post(caller, self.pid, FILE_A, PARAMS_ID)
            assert self._rev(caller)["parts"]["favorites"] == base + 1

            assert _post(caller, self.pid, FILE_A, PARAM_ID)[0] == 409
            assert _post(caller, self.pid, FILE_A, EMPTY_ID)[0] == 400
            assert _post(caller, self.pid, "nope.xml", PARAM_ID)[0] == 404
            caller.request(
                "POST", _favorites_url(self.pid), body={"file": FILE_A, "id": ITEM_ID}, with_csrf=False
            )
            caller.json("DELETE", "%s?file=%s&path=root%%2FR%%2Fmissing" % (_favorites_url(self.pid), FILE_A))
            assert self._rev(caller)["parts"]["favorites"] == base + 1

    def test_unknown_profile_is_404(self):
        with make_server() as server:
            caller = as_role(server, "member")
            status, _h, _b = caller.json("GET", "/api/v1/p/no-such-model/revision")
            assert status == 404
