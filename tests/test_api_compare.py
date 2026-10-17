"""结构化对比接口（§7.5、§6.6；M2-08）。

覆盖：四种旧模式（当前 vs 历史、历史 vs 历史、两侧不同文件名、记录 vs 记录）、同名
兄弟逐个条目、绑定组名只给命中的条目、`text_limit` 截断与 total、解析不了的一侧降级
为 `unsupported`、`temp` 在 M2-10 之前明确 400、版本引用校验、CSRF 硬条件、四角色、
修订号不动。
"""

from __future__ import annotations

import os

from authz_fixtures import as_role, make_server, seed_roles, write_target

from app.core import storage
from app.services import revision

FILE_A = "scenario_a.xml"
FILE_B = "scenario_b.xml"

V1 = (
    '<R>\n'
    '  <params>\n'
    '    <item>1</item>\n'
    '    <item>2</item>\n'
    '    <timeout>10</timeout>\n'
    '  </params>\n'
    '</R>\n'
)
V2 = (
    '<R>\n'
    '  <params>\n'
    '    <item>9</item>\n'
    '    <item>8</item>\n'
    '    <timeout>10</timeout>\n'
    '  </params>\n'
    '</R>\n'
)
V3 = V2.replace("<timeout>10</timeout>", "<timeout>30</timeout>")
XML_B = '<R><only>42</only></R>\n'
ITEM_1_LOC = "/R[1]/params[1]/item[1]"
ITEM_2_LOC = "/R[1]/params[1]/item[2]"


def _url(pid: str) -> str:
    return "/api/v1/p/%s/compare" % pid


def _compare(caller, pid, base, target, text_limit=None, **request_kwargs):
    payload = {"base": base, "target": target}
    if text_limit is not None:
        payload["text_limit"] = text_limit
    return caller.json("POST", _url(pid), payload, **request_kwargs)


class TestCompareApi:
    def setup_method(self):
        revision.reset()
        self.pid = seed_roles("cmp")
        write_target(self.pid, FILE_A, V1.encode("utf-8"))
        write_target(self.pid, FILE_B, XML_B.encode("utf-8"))
        storage.set_active_profile(self.pid)
        storage.save_config_file(FILE_A, V2.encode("utf-8"))
        storage.save_config_file(FILE_A, V3.encode("utf-8"))
        directory = os.path.join(storage.get_archive_root_dir(self.pid), FILE_A)
        self.archives = sorted(
            f for f in os.listdir(directory) if not f.endswith(".diff.json")
        )
        self.archive_v1, self.archive_v2 = self.archives[0], self.archives[1]

    def _ref(self, version, name=FILE_A):
        return {"file": name, "version": version}

    def test_current_vs_archive(self):
        with make_server() as server:
            status, _h, body = _compare(
                as_role(server, "member"), self.pid,
                self._ref("archive:%s" % self.archive_v2), self._ref("current"),
            )
            assert status == 200, body
            assert body["has_changes"] is True
            assert body["base"]["label"] == "历史版本 %s" % self.archive_v2
            assert body["base"]["name"] == self.archive_v2
            assert body["target"] == {"label": "当前版本", "name": FILE_A}
            ops = [(i["op"], i["loc"]) for i in body["structural"]["items"]]
            assert ops == [("modified", "/R[1]/params[1]/timeout[1]")], ops

    def test_archive_vs_archive_counts_both_siblings(self):
        """卡面验收：两个同名兄弟都变 → items 里两条 modified，不是一条。"""
        with make_server() as server:
            status, _h, body = _compare(
                as_role(server, "member"), self.pid,
                self._ref("archive:%s" % self.archive_v1),
                self._ref("archive:%s" % self.archive_v2),
            )
            assert status == 200, body
            items = body["structural"]["items"]
            assert [i["loc"] for i in items] == [ITEM_1_LOC, ITEM_2_LOC], items
            assert all(i["op"] == "modified" for i in items)
            assert body["structural"]["summary"]["modified"] == 2
            assert items[0]["old"] == "1" and items[0]["new"] == "9"

    def test_different_file_names_compare_side_by_side(self):
        """为上传对比预留：两侧文件名可以不同。"""
        with make_server() as server:
            status, _h, body = _compare(
                as_role(server, "member"), self.pid,
                self._ref("current", FILE_B), self._ref("current", FILE_A),
            )
            assert status == 200, body
            assert body["base"]["name"] == FILE_B
            summary = body["structural"]["summary"]
            # FILE_B 只有 /R[1]/only[1] 一个值节点；FILE_A 当前版本有 3 个 → added 3、removed 1
            assert summary["added"] == 3 and summary["removed"] == 1, body

    def test_record_vs_record(self):
        storage.set_active_profile(self.pid)
        first = os.path.basename(storage.save_record_file(FILE_A, V1.encode("utf-8")))
        second = os.path.basename(storage.save_record_file(FILE_A, V2.encode("utf-8")))
        with make_server() as server:
            status, _h, body = _compare(
                as_role(server, "member"), self.pid,
                self._ref("record:%s" % first), self._ref("record:%s" % second),
            )
            assert status == 200, body
            assert body["base"]["label"].startswith("修改记录")
            assert body["structural"]["summary"]["modified"] == 2

    def test_same_content_has_no_changes(self):
        with make_server() as server:
            status, _h, body = _compare(
                as_role(server, "member"), self.pid,
                self._ref("current"), self._ref("current"),
            )
            assert status == 200, body
            assert body["has_changes"] is False
            assert body["structural"]["items"] == []
            assert body["unified"]["lines"] == []

    def test_bindings_snapshot_provides_group_names(self):
        storage.set_active_profile(self.pid)
        storage.save_bindings(
            [
                {
                    "group_name": "超时组",
                    "variables": [{"file": FILE_A, "path": "%s/R/params/timeout" % FILE_A}],
                }
            ]
        )
        with make_server() as server:
            status, _h, body = _compare(
                as_role(server, "member"), self.pid,
                self._ref("current"), self._ref("archive:%s" % self.archive_v1),
            )
            assert status == 200, body
            items = body["structural"]["items"]
            timeout = [i for i in items if i["loc"].endswith("timeout[1]")][0]
            assert timeout["bound_group"] == "超时组", items
            assert all(
                i["bound_group"] is None for i in items if i is not timeout
            )

    def test_text_limit_truncates_unified_preview(self):
        with make_server() as server:
            status, _h, full = _compare(
                as_role(server, "member"), self.pid,
                self._ref("archive:%s" % self.archive_v1), self._ref("current"),
            )
            assert status == 200, full
            total = full["unified"]["total"]
            assert total > 3 and full["unified"]["truncated"] is False

            status, _h, cut = _compare(
                as_role(server, "member"), self.pid,
                self._ref("archive:%s" % self.archive_v1), self._ref("current"),
                text_limit=3,
            )
            assert cut["unified"]["lines"] == full["unified"]["lines"][:3]
            assert cut["unified"]["total"] == total
            assert cut["unified"]["truncated"] is True

    def test_unparseable_side_degrades_to_unsupported(self):
        write_target(self.pid, "broken.xml", b"<R><oops")
        with make_server() as server:
            status, _h, body = _compare(
                as_role(server, "member"), self.pid,
                {"file": "broken.xml", "version": "current"},
                self._ref("current"),
            )
            assert status == 200, body
            assert body["structural"]["type"] == "unsupported"
            assert body["structural"]["items"] == []
            assert body["structural"]["summary"]["modified"] == 0
            # 文本差异仍然算得出来（不依赖解析）
            assert body["unified"]["total"] > 0

    def test_temp_side_is_rejected_until_m2_10(self):
        with make_server() as server:
            status, _h, body = _compare(
                as_role(server, "member"), self.pid,
                {"temp": "opaque-token"}, self._ref("current"),
            )
            assert status == 400, body
            assert body["error"]["code"] == "INVALID_INPUT"
            assert "M2-10" in body["error"]["message"]

    def test_bad_references(self):
        with make_server() as server:
            caller = as_role(server, "member")
            status, _h, body = _compare(caller, self.pid, "not-an-object", self._ref("current"))
            assert status == 400, body
            status, _h, body = _compare(caller, self.pid, {}, self._ref("current"))
            assert status == 400, body
            assert body["error"]["detail"]["field"] == "base.file"
            status, _h, body = _compare(
                caller, self.pid, self._ref("archive:missing.xml"), self._ref("current")
            )
            assert status == 404, body
            status, _h, body = _compare(
                caller, self.pid, self._ref("nonsense:abc"), self._ref("current")
            )
            assert status == 400, body

    def test_write_header_required_and_revision_unchanged(self):
        with make_server() as server:
            caller = as_role(server, "member")
            before = revision.snapshot(self.pid)["parts"]
            status, _h, body = _compare(
                caller, self.pid, self._ref("current"), self._ref("current"),
                with_csrf=False,
            )
            assert status == 403, body
            status, _h, _body = _compare(
                caller, self.pid, self._ref("current"), self._ref("current")
            )
            assert revision.snapshot(self.pid)["parts"] == before

    def test_all_roles_may_compare(self):
        with make_server() as server:
            for role in ("visitor", "member", "admin", "deployer"):
                status, _h, body = _compare(
                    as_role(server, role), self.pid,
                    self._ref("current"), self._ref("current"),
                )
                assert status == 200, (role, body)
