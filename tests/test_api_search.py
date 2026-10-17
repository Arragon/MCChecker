"""全局搜索 API（§7.6；M2-05）。

覆盖：空 `q` 400、容器节点命中计 1（旧缺陷回归）、label/value/note 判定顺序、
大小写不敏感、同名兄弟各自计数、`file` 限定与 404、`limit`/`offset` 与
`truncated`、只搜当前版本、坏文件在全局扫描里跳过、四角色可读、修订号不动，
以及一次真实的计时（§12 预算）。
"""

from __future__ import annotations

import os
import time

from authz_fixtures import as_role, make_server, seed_roles, write_target

from app.core import searching, storage
from app.services import documents, favorites, revision

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
    '  <scale>scale</scale>\n'
    '</R>\n'
)
XML_B = '<R><only>42</only></R>\n'

PARAMS_ID = "root/R/params"
PARAM_ID = "root/R/params/param_01"
ITEM_ID = "root/R/params/item"
ITEM_1_LOC = "/R[1]/params[1]/item[1]"
ITEM_2_LOC = "/R[1]/params[1]/item[2]"


def _url(pid: str, query: str = "") -> str:
    return "/api/v1/p/%s/search%s" % (pid, query)


def _search(caller, pid, query):
    status, _headers, body = caller.json("GET", _url(pid, query))
    return status, body


class TestSearchApi:
    def setup_method(self):
        revision.reset()
        self.pid = seed_roles("srch")
        write_target(self.pid, FILE_A, XML_A.encode("utf-8"))
        write_target(self.pid, FILE_B, XML_B.encode("utf-8"))

    # ---------- 参数校验与权限 ----------

    def test_missing_query_is_400_on_field_q(self):
        with make_server() as server:
            status, body = _search(as_role(server, "visitor"), self.pid, "")
            assert status == 400, body
            assert body["error"]["code"] == "INVALID_INPUT"
            assert body["error"]["detail"]["field"] == "q"

    def test_whitespace_only_query_is_400(self):
        with make_server() as server:
            status, body = _search(as_role(server, "visitor"), self.pid, "?q=%20%20")
            assert status == 400, body
            assert body["error"]["detail"]["field"] == "q"

    def test_all_four_roles_may_search(self):
        with make_server() as server:
            for role in ("visitor", "member", "admin", "deployer"):
                status, body = _search(as_role(server, role), self.pid, "?q=params")
                assert status == 200, (role, body)
                assert body["total"] >= 1

    def test_search_does_not_bump_revision(self):
        with make_server() as server:
            before = revision.snapshot(self.pid)["parts"]
            status, body = _search(as_role(server, "member"), self.pid, "?q=item")
            assert status == 200, body
            assert revision.snapshot(self.pid)["parts"] == before

    # ---------- 命中语义（§7.6）----------

    def test_container_node_hit_counts_one(self):
        """旧版父节点命中计 0 → 「有结果却显示未找到」；这里必须计 1。"""
        with make_server() as server:
            status, body = _search(as_role(server, "member"), self.pid, "?q=params")
            assert status == 200, body
            assert body["total"] == 1, body
            assert body["files"] == {FILE_A: 1}
            assert body["items"][0]["value"] is None
            assert body["items"][0]["match"] == "label"

    def test_value_hit_and_case_insensitive(self):
        with make_server() as server:
            status, body = _search(as_role(server, "member"), self.pid, "?q=FIRST")
            assert status == 200, body
            assert body["total"] == 1
            item = body["items"][0]
            assert item["match"] == "value"
            assert item["label"] == "item"
            assert item["value"] == "first"

    def test_label_match_wins_when_label_and_value_both_hit(self):
        """`<scale>scale</scale>`：label 与 value 都含关键词，`match` 取 label。"""
        with make_server() as server:
            status, body = _search(as_role(server, "member"), self.pid, "?q=scale")
            assert status == 200, body
            assert body["total"] == 1
            assert body["items"][0]["match"] == "label"

    def test_same_name_siblings_are_counted_separately(self):
        with make_server() as server:
            status, body = _search(as_role(server, "member"), self.pid, "?q=second")
            assert status == 200, body
            assert body["total"] == 1
            assert body["items"][0]["loc"] == ITEM_2_LOC

    def test_attribute_value_is_searchable(self):
        with make_server() as server:
            status, body = _search(as_role(server, "member"), self.pid, "?q=%E5%AE%BD%E5%BA%A6")
            assert status == 200, body
            assert body["total"] == 1
            item = body["items"][0]
            assert item["match"] == "value"
            assert item["label"] == "@description"
            assert item["type"] == "attribute"

    def test_item_fields_follow_contract(self):
        with make_server() as server:
            status, body = _search(as_role(server, "member"), self.pid, "?q=param_01")
            assert status == 200, body
            item = body["items"][0]
            assert set(item) == {
                "file", "id", "loc", "path", "label", "value", "type", "note", "match",
            }
            assert item["file"] == FILE_A
            assert item["id"] == PARAM_ID
            assert item["path"] == "R › params › param_01"
            assert item["value"] == "5"
            assert item["type"] == "element"
            assert item["note"] == ""

    def test_favorite_note_matched_by_legacy_id(self):
        favorites.add(self.pid, FILE_A, PARAM_ID)
        favorites.set_note(self.pid, FILE_A, PARAM_ID, "特注宽度")
        with make_server() as server:
            status, body = _search(as_role(server, "member"), self.pid, "?q=%E7%89%B9%E6%B3%A8")
            assert status == 200, body
            assert body["total"] == 1
            item = body["items"][0]
            assert item["match"] == "note"
            assert item["note"] == "特注宽度"

    def test_note_hit_applies_to_every_sibling_sharing_the_id(self):
        favorites.add(self.pid, FILE_A, ITEM_ID)
        favorites.set_note(self.pid, FILE_A, ITEM_ID, "shared-note")
        with make_server() as server:
            status, body = _search(as_role(server, "member"), self.pid, "?q=shared")
            assert status == 200, body
            assert body["total"] == 2
            assert {i["loc"] for i in body["items"]} == {ITEM_1_LOC, ITEM_2_LOC}
            assert {i["match"] for i in body["items"]} == {"note"}

    # ---------- 文件范围 ----------

    def test_file_param_limits_scan(self):
        with make_server() as server:
            status, body = _search(as_role(server, "member"), self.pid, "?q=R")
            assert status == 200, body
            assert set(body["files"]) == {FILE_A, FILE_B}

            status, only_b = _search(
                as_role(server, "member"), self.pid, "?q=R&file=%s" % FILE_B
            )
            assert status == 200, only_b
            assert set(only_b["files"]) == {FILE_B}
            assert all(i["file"] == FILE_B for i in only_b["items"])

    def test_unknown_file_is_404(self):
        with make_server() as server:
            status, body = _search(as_role(server, "member"), self.pid, "?q=R&file=missing.xml")
            assert status == 404, body
            assert body["error"]["code"] == "NOT_FOUND"

    def test_path_traversal_in_file_is_400(self):
        with make_server() as server:
            status, body = _search(as_role(server, "member"), self.pid, "?q=R&file=..%2Fsecret")
            assert status == 400, body
            assert body["error"]["code"] == "INVALID_INPUT"

    def test_only_current_version_is_searched(self):
        """归档目录里放一份同样内容的副本，搜索仍只命中当前版本一次。"""
        storage.set_active_profile(self.pid)
        archive_dir = os.path.join(storage.get_archive_root_dir(self.pid), FILE_A)
        os.makedirs(archive_dir, exist_ok=True)
        with open(os.path.join(archive_dir, "snapshot.xml"), "w", encoding="utf-8") as fh:
            fh.write(XML_A)
        with make_server() as server:
            status, body = _search(as_role(server, "member"), self.pid, "?q=second")
            assert status == 200, body
            assert body["total"] == 1
            assert set(body["files"]) == {FILE_A}

    def test_escaped_content_is_still_found_despite_byte_prefilter(self):
        """响应体里含 `\\n` / `"` 转义时，字节预筛必须让路（否则会漏真命中）。"""
        tricky = b'{"greeting": "line1\\nline2", "quoted": "say \\"hi\\" now"}\n'
        write_target(self.pid, "tricky.json", tricky)
        with make_server() as server:
            status, body = _search(as_role(server, "member"), self.pid, "?q=line2")
            assert status == 200, body
            assert body["files"].get("tricky.json") == 1

            status, quoted = _search(as_role(server, "member"), self.pid, "?q=hi")
            assert status == 200, quoted
            labels = {i["label"] for i in quoted["items"] if i["file"] == "tricky.json"}
            assert "quoted" in labels or "@quoted" in labels

    def test_unparsable_file_is_skipped_globally_but_422_when_named(self):
        write_target(self.pid, "broken.json", b'{"a": ')
        with make_server() as server:
            status, body = _search(as_role(server, "member"), self.pid, "?q=params")
            assert status == 200, body
            assert FILE_A in body["files"]

            status, named = _search(as_role(server, "member"), self.pid, "?q=a&file=broken.json")
            assert status == 422, named
            assert named["error"]["code"] == "PARSE_FAILED"

    # ---------- 分页 ----------

    def test_limit_offset_and_truncated(self):
        with make_server() as server:
            status, body = _search(as_role(server, "member"), self.pid, "?q=item&limit=1")
            assert status == 200, body
            assert body["total"] == 2
            assert len(body["items"]) == 1
            assert body["truncated"] is True

            status, page2 = _search(
                as_role(server, "member"), self.pid, "?q=item&limit=1&offset=1"
            )
            assert status == 200, page2
            assert page2["items"][0]["loc"] == ITEM_2_LOC
            assert page2["truncated"] is False

            status, whole = _search(
                as_role(server, "member"), self.pid, "?q=item&limit=2&offset=0"
            )
            assert status == 200, whole
            assert whole["truncated"] is False

    def test_limit_is_clamped_and_bad_values_fall_back(self):
        with make_server() as server:
            status, body = _search(as_role(server, "member"), self.pid, "?q=item&limit=99999")
            assert status == 200, body
            assert body["total"] == 2

            status, junk = _search(
                as_role(server, "member"), self.pid, "?q=item&limit=abc&offset=xyz"
            )
            assert status == 200, junk
            assert len(junk["items"]) == 2

    def test_response_echoes_query_in_original_case(self):
        with make_server() as server:
            status, body = _search(as_role(server, "member"), self.pid, "?q=It")
            assert status == 200, body
            assert body["q"] == "It"
            assert body["total"] >= 1


class TestSearchCompactUnit:
    """`core.searching.search_compact` 的数组扫描（不经 HTTP）。"""

    def setup_method(self):
        self.pid = seed_roles("srch_unit")

    def _nodes(self, name=FILE_A, text=XML_A):
        storage.set_active_profile(self.pid)
        storage.save_config_file(name, text.encode("utf-8"))
        return documents.load_document(self.pid, name, "current")["nodes"]

    def test_empty_keyword_returns_nothing(self):
        nodes = self._nodes()
        assert searching.search_compact(nodes, "   ") == []

    def test_file_name_node_is_never_a_hit(self):
        nodes = self._nodes()
        assert searching.search_compact(nodes, FILE_A) == []

    def test_display_path_excludes_file_name(self):
        nodes = self._nodes()
        index = nodes["loc"].index("/R[1]/params[1]/param_01[1]")
        assert searching.display_path(nodes, index) == "R › params › param_01"

    def test_display_path_for_root_element_is_itself(self):
        nodes = self._nodes()
        assert searching.display_path(nodes, nodes["loc"].index("/R[1]")) == "R"

    def test_prefilter_keeps_note_only_hits(self):
        nodes = self._nodes()
        hits = searching.search_compact(nodes, "只有备注里有", {"root/R/empty": "只有备注里有"})
        assert [h["id"] for h in hits] == ["root/R/empty"]
        assert hits[0]["match"] == "note"

    def test_no_invented_matches_across_nodes(self):
        """逐节点精确比对：只在单个 label/value/note 内匹配，跨节点的巧合子串不算命中。"""
        nodes = self._nodes()
        assert searching.search_compact(nodes, "1empty") == []
        assert searching.search_compact(nodes, "scale") != []


class TestSearchLatency:
    """§12 全局搜索预算的真实计时（样本写在临时数据目录，不入库）。"""

    def test_search_across_sample_files_stays_under_budget(self):
        files, per_file = 10, 2000
        pid = seed_roles("srch_perf")
        storage.set_active_profile(pid)
        for f in range(files):
            body = "<R>" + "".join(
                '<n id="%d">v%d</n>' % (n, n) for n in range(per_file)
            ) + "</R>"
            storage.save_config_file("bench_%02d.xml" % f, body.encode("utf-8"))

        with make_server() as server:
            caller = as_role(server, "member")
            # 先跑一次把文档读进内存缓存（§12 的预算前提是「缓存文档 + 数组扫描」）
            status, _h, warm = caller.json("GET", _url(pid, "?q=zzzzz"))
            assert status == 200, warm

            started = time.perf_counter()
            status, _h, body = caller.json("GET", _url(pid, "?q=v1999&limit=1000"))
            elapsed_ms = (time.perf_counter() - started) * 1000

        assert status == 200, body
        assert body["total"] == files
        # 10 文件 × 2000 节点：按 §12 的 30 文件 × 3 万节点 ≤ 500 ms 折算绰绰有余。
        # 这条断言只随真实测量收紧，不得改成恒真。
        assert elapsed_ms < 500, "实测 %.1f ms" % elapsed_ms
