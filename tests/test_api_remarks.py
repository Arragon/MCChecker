"""修改建议提交与列表 API（§6.7、§7.8；M2-03）。

覆盖：定位符来自完整文档而非过滤下标、同名兄弟 locator 唯一、sha 变化 409 且文件不动、
JSON 类型检查（int/float/bool/null）与 XML 不检查、建议值与当前值相同、无值节点、
旧无 locator 记录仍可列出、四角色与 CSRF、修订号递增。
"""

from __future__ import annotations

from authz_fixtures import as_role, make_server, seed_roles, write_target

from app.core import storage
from app.services import revision

XML_NAME = "cfg.xml"
JSON_NAME = "cfg.json"

XML_DOC = (
    '<R>\n'
    '  <params>\n'
    '    <item>first</item>\n'
    '    <item>second</item>\n'
    '    <param_01 min="0" max="10">5</param_01>\n'
    '  </params>\n'
    '  <empty/>\n'
    '</R>\n'
)
XML_DOC_EDITED = XML_DOC.replace("<item>first</item>", "<item>changed</item>")
JSON_DOC = '{"n": 1, "f": 1.5, "b": true, "z": null, "s": "text"}\n'


def _doc_index(caller, pid, name):
    """取当前文档的 loc → (id, value, type) 映射（定位符以服务端文档为准）。"""
    status, _h, body = caller.json("GET", "/api/v1/p/%s/files/%s/document" % (pid, name))
    assert status == 200, body
    nodes = body["nodes"]
    return {
        loc: (nodes["id"][i], nodes["v"][i], nodes["t"][i])
        for i, loc in enumerate(nodes["loc"])
    }, body["file"]["sha256"], body["file"]["format"]


def _post(caller, pid, body):
    return caller.json("POST", "/api/v1/p/%s/remarks" % pid, body)


def _get(caller, pid, query=""):
    return caller.json("GET", "/api/v1/p/%s/remarks%s" % (pid, query))


class TestRemarkSubmit:
    def setup_method(self):
        revision.reset()
        self.pid = seed_roles("rm1")
        write_target(self.pid, XML_NAME, XML_DOC.encode("utf-8"))
        write_target(self.pid, JSON_NAME, JSON_DOC.encode("utf-8"))

    def test_locator_points_at_the_same_node_in_the_full_document(self):
        with make_server() as server:
            caller = as_role(server, "member")
            index, sha, fmt = _doc_index(caller, self.pid, XML_NAME)
            loc = "/R[1]/params[1]/param_01[1]"
            status, _h, body = _post(caller, self.pid, {
                "file": XML_NAME, "sha256": sha, "loc": loc, "proposed_value": "6"
            })
            assert status == 201, body
            item = body["item"]
            assert item["locator"] == loc
            assert item["node_path"] == index[loc][0], "node_path 必须写完整文档里的旧 ID"
            assert item["original_value"] == index[loc][1]
            assert item["proposed_value"] == "6"
            assert item["node_key"] == "" and item["schema_version"] == 2
            assert item["source_sha256"] == sha
            assert item["tree_type"] == fmt
            assert item["status"] == "pending"

    def test_same_name_siblings_get_different_locators(self):
        """v1 的根因：同名兄弟共享旧 ID，必须用 locator 区分。"""
        with make_server() as server:
            caller = as_role(server, "member")
            index, sha, _fmt = _doc_index(caller, self.pid, XML_NAME)
            first, second = "/R[1]/params[1]/item[1]", "/R[1]/params[1]/item[2]"
            a = _post(caller, self.pid, {"file": XML_NAME, "sha256": sha, "loc": first, "proposed_value": "A"})[2]["item"]
            b = _post(caller, self.pid, {"file": XML_NAME, "sha256": sha, "loc": second, "proposed_value": "B"})[2]["item"]
            assert a["locator"] != b["locator"]
            assert a["node_path"] == b["node_path"] == index[first][0]
            assert (a["original_value"], b["original_value"]) == ("first", "second")
            items = _get(caller, self.pid, "?file=%s" % XML_NAME)[2]["items"]
            assert sorted(i["proposed_value"] for i in items) == ["A", "B"]

    def test_stale_sha_is_rejected_and_file_untouched(self):
        with make_server() as server:
            caller = as_role(server, "member")
            index, sha, _fmt = _doc_index(caller, self.pid, XML_NAME)
            loc = "/R[1]/params[1]/param_01[1]"
            _post(caller, self.pid, {"file": XML_NAME, "sha256": sha, "loc": loc, "proposed_value": "6"})
            write_target(self.pid, XML_NAME, XML_DOC_EDITED.encode("utf-8"))
            status, _h, body = _post(caller, self.pid, {"file": XML_NAME, "sha256": sha, "loc": loc, "proposed_value": "7"})
            assert status == 409 and body["error"]["code"] == "SOURCE_CHANGED", body
            with storage.use_profile(self.pid):
                with open(storage.get_config_path(XML_NAME), "rb") as fh:
                    assert fh.read() == XML_DOC_EDITED.encode("utf-8"), "被拒的提交不得改动文件"

    def test_same_value_and_empty_value_rejected_with_field(self):
        with make_server() as server:
            caller = as_role(server, "member")
            index, sha, _fmt = _doc_index(caller, self.pid, XML_NAME)
            loc = "/R[1]/params[1]/param_01[1]"
            status, _h, body = _post(caller, self.pid, {"file": XML_NAME, "sha256": sha, "loc": loc, "proposed_value": "5"})
            assert status == 400 and body["error"]["detail"]["field"] == "proposed_value"
            status, _h, body = _post(caller, self.pid, {"file": XML_NAME, "sha256": sha, "loc": loc, "proposed_value": "  "})
            assert status == 400 and body["error"]["detail"]["field"] == "proposed_value"

    def test_valueless_node_rejected(self):
        with make_server() as server:
            caller = as_role(server, "member")
            _index, sha, _fmt = _doc_index(caller, self.pid, XML_NAME)
            status, _h, body = _post(caller, self.pid, {"file": XML_NAME, "sha256": sha, "loc": "/R[1]/empty[1]", "proposed_value": "x"})
            assert status == 400 and body["error"]["detail"]["field"] == "loc"
            status, _h, body = _post(caller, self.pid, {"file": XML_NAME, "sha256": sha, "loc": "/R[1]/nope[9]", "proposed_value": "x"})
            assert status == 400 and body["error"]["detail"]["field"] == "loc"

    def test_json_type_checks(self):
        cases = [
            ("/n", "abc", 400),      # int ← 非数字
            ("/n", "-42", 201),      # int ← 负整数
            ("/f", "xyz", 400),      # float ← 非数字
            ("/f", "2.5", 201),
            ("/b", "yes", 400),      # bool ← 非 true/false
            ("/b", "false", 201),
            ("/z", "0", 400),        # NoneType ← 只允许 null
            ("/z", "null", 400),     # 唯一合法值恰好等于当前值，被 §6.7 第 1 步拦下（规范疑问已在 PR 记录）
            ("/s", "任意文本", 201),  # str 不限制
        ]
        with make_server() as server:
            caller = as_role(server, "member")
            _index, sha, fmt = _doc_index(caller, self.pid, JSON_NAME)
            assert fmt == "json"
            for i, (loc, value, expected) in enumerate(cases):
                status, _h, body = _post(caller, self.pid, {"file": JSON_NAME, "sha256": sha, "loc": loc, "proposed_value": value})
                assert status == expected, (loc, value, expected, body)
                if expected == 400:
                    assert body["error"]["detail"]["field"] == "proposed_value", (loc, body)

    def test_xml_skips_type_check(self):
        """§6.7：XML 不做类型检查（属性值本质是字符串）。"""
        with make_server() as server:
            caller = as_role(server, "member")
            index, sha, fmt = _doc_index(caller, self.pid, XML_NAME)
            assert fmt == "xml"
            attr_loc = "/R[1]/params[1]/param_01[1]/@min"
            assert attr_loc in index
            status, _h, body = _post(caller, self.pid, {"file": XML_NAME, "sha256": sha, "loc": attr_loc, "proposed_value": "不是数字"})
            assert status == 201, body
            assert body["item"]["node_type"] == "attribute"

    def test_all_roles_may_submit(self):
        with make_server() as server:
            caller = as_role(server, "member")
            _index, sha, _fmt = _doc_index(caller, self.pid, XML_NAME)
            loc = "/R[1]/params[1]/param_01[1]"
            for i, role in enumerate(("visitor", "member", "admin", "deployer")):
                status, _h, body = _post(as_role(server, role), self.pid, {"file": XML_NAME, "sha256": sha, "loc": loc, "proposed_value": str(6 + i)})
                assert status == 201, (role, body)

    def test_actor_recorded_and_unregistered_name_falls_back_to_ip(self):
        with make_server() as server:
            caller = as_role(server, "member")
            _index, sha, _fmt = _doc_index(caller, self.pid, JSON_NAME)
            item = _post(caller, self.pid, {"file": JSON_NAME, "sha256": sha, "loc": "/n", "proposed_value": "9"})[2]["item"]
            assert item["actor_ip"] == "10.0.0.2" and item["actor_name"] == "成员甲"
            visitor = _post(as_role(server, "visitor"), self.pid, {"file": JSON_NAME, "sha256": sha, "loc": "/f", "proposed_value": "9"})[2]["item"]
            assert visitor["actor_name"] == "" and visitor["actor_display"] == "10.0.0.1" == visitor["actor_ip"]

    def test_write_without_csrf_header_is_forbidden(self):
        with make_server() as server:
            caller = as_role(server, "member")
            _index, sha, _fmt = _doc_index(caller, self.pid, XML_NAME)
            status, _h, _b = caller.request(
                "POST", "/api/v1/p/%s/remarks" % self.pid,
                body={"file": XML_NAME, "sha256": sha, "loc": "/R[1]/params[1]/param_01[1]", "proposed_value": "6"},
                with_csrf=False,
            )
            assert status == 403

    def test_missing_arguments_and_unknown_file(self):
        with make_server() as server:
            caller = as_role(server, "member")
            assert _post(caller, self.pid, {"loc": "/a", "proposed_value": "1"})[0] == 400
            assert _post(caller, self.pid, {"file": XML_NAME, "proposed_value": "1"})[0] == 400
            status, _h, body = _post(caller, self.pid, {"file": "nope.xml", "loc": "/a", "proposed_value": "1"})
            assert status == 404 and body["error"]["code"] == "NOT_FOUND"

    def test_revision_bumps_only_on_success(self):
        with make_server() as server:
            caller = as_role(server, "member")
            _index, sha, _fmt = _doc_index(caller, self.pid, XML_NAME)
            loc = "/R[1]/params[1]/param_01[1]"
            base = caller.json("GET", "/api/v1/p/%s/revision" % self.pid)[2]["parts"]["remarks"]
            _post(caller, self.pid, {"file": XML_NAME, "sha256": sha, "loc": loc, "proposed_value": "6"})
            assert caller.json("GET", "/api/v1/p/%s/revision" % self.pid)[2]["parts"]["remarks"] == base + 1
            _post(caller, self.pid, {"file": XML_NAME, "sha256": sha, "loc": loc, "proposed_value": "5"})  # 与当前值相同 → 400
            _get(caller, self.pid)
            assert caller.json("GET", "/api/v1/p/%s/revision" % self.pid)[2]["parts"]["remarks"] == base + 1

    def test_unknown_profile_is_404(self):
        with make_server() as server:
            caller = as_role(server, "member")
            assert caller.json("GET", "/api/v1/p/no-such-model/remarks")[0] == 404
            assert _post(caller, "no-such-model", {"file": XML_NAME, "loc": "/a", "proposed_value": "1"})[0] == 404


class TestRemarkListCompatibility:
    def setup_method(self):
        self.pid = seed_roles("rm2")
        write_target(self.pid, XML_NAME, XML_DOC.encode("utf-8"))

    def test_legacy_records_without_locator_are_still_listed(self):
        """旧 edit_remarks.json 记录（没有 locator 等新键）仍可列出。"""
        legacy = {
            "id": "deadbeef",
            "source_file": XML_NAME,
            "node_key": "root/R/params/param_01",
            "node_path": "root/R/params/param_01",
            "node_label": "param_01",
            "original_value": "5",
            "proposed_value": "8",
            "node_type": "element",
            "tree_type": "xml",
            "actor_ip": "10.0.0.9",
            "actor_name": "旧记录人",
            "actor_display": "旧记录人",
            "status": "pending",
            "reviewed_by": "",
            "reviewer_ip": "",
            "reviewed_at": "",
            "generated_file": "",
            "created_at": "2026-05-24 10:00:00",
        }
        storage.set_active_profile(self.pid)
        storage.save_edit_remarks([legacy])
        with make_server() as server:
            caller = as_role(server, "member")
            items = _get(caller, self.pid, "?file=%s&status=pending" % XML_NAME)[2]["items"]
            assert len(items) == 1
            item = items[0]
            assert "locator" not in item and item["node_key"] == "root/R/params/param_01"
            assert item["proposed_value"] == "8" and item["actor_display"] == "旧记录人"

    def test_filters_by_file_and_status(self):
        storage.set_active_profile(self.pid)
        storage.add_edit_remark(
            source_file=XML_NAME, node_key="k1", node_path="root/R/params/param_01", node_label="param_01",
            original_value="5", proposed_value="6", node_type="element", tree_type="xml",
            actor_ip="10.0.0.2", actor_name="成员甲",
        )
        with make_server() as server:
            caller = as_role(server, "member")
            assert len(_get(caller, self.pid)[2]["items"]) == 1
            assert _get(caller, self.pid, "?file=other.xml")[2]["items"] == []
            assert _get(caller, self.pid, "?status=approved")[2]["items"] == []
            assert len(_get(caller, self.pid, "?status=pending")[2]["items"]) == 1

    def test_pending_count_shows_up_in_file_list(self):
        """侧栏与审阅徽标的数据源：`/files` 的 `pending_remarks`。"""
        storage.set_active_profile(self.pid)
        storage.add_edit_remark(
            source_file=XML_NAME, node_key="k1", node_path="root/R/params/param_01", node_label="param_01",
            original_value="5", proposed_value="6", node_type="element", tree_type="xml",
            actor_ip="10.0.0.2", actor_name="成员甲",
        )
        with make_server() as server:
            caller = as_role(server, "member")
            items = caller.json("GET", "/api/v1/p/%s/files" % self.pid)[2]["items"]
            row = [f for f in items if f["name"] == XML_NAME][0]
            assert row["pending_remarks"] == 1
