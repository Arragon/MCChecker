"""历史版本与修改记录接口（§7.4、§6.6；M2-07）。

覆盖：归档按 mtime 倒序且与紧邻的较新版本比较、**同名兄弟都变化时 modified 为 2**、
相同内容不重复制归档、摘要写进 `cache/diff_v2/` 且不产生旧 `.diff.json`、
旧 `.diff.json` 降级为「旧统计」（`structural: false`）、修改记录倒序与 `diff_prev`、
`record_url`、绑定计数、四角色可读、修订号不动、404 与路径穿越。
"""

from __future__ import annotations

import json
import os
from typing import Tuple

from authz_fixtures import as_role, make_server, seed_roles, write_target

from app.core import storage
from app.services import history, revision

FILE_A = "scenario_a.xml"

V1 = (
    '<R>\n'
    '  <params>\n'
    '    <item>v1a</item>\n'
    '    <item>v1b</item>\n'
    '    <timeout>10</timeout>\n'
    '  </params>\n'
    '</R>\n'
)
V2 = (
    '<R>\n'
    '  <params>\n'
    '    <item>v2a</item>\n'
    '    <item>v2b</item>\n'
    '    <timeout>10</timeout>\n'
    '  </params>\n'
    '</R>\n'
)
V3 = V2.replace("<timeout>10</timeout>", "<timeout>30</timeout>")


def _archives_url(pid: str, name: str = FILE_A) -> str:
    return "/api/v1/p/%s/files/%s/archives" % (pid, name)


def _records_url(pid: str, name: str = FILE_A) -> str:
    return "/api/v1/p/%s/files/%s/records" % (pid, name)


def _touch(path: str, stamp: float) -> None:
    os.utime(path, (stamp, stamp))


def _three_versions(pid: str) -> Tuple[str, str]:
    """v1 → v2 → v3，返回两个归档文件名（`archive_v1` 里是 v1 内容）。

    mtime 刻意拉开：`scenario_a_20261017.xml` 这类短名先创建，给它较早的时间戳，
    所以倒序后 items[0] 是 v2 内容的归档、items[1] 是 v1 内容的归档。
    """
    storage.set_active_profile(pid)
    storage.save_config_file(FILE_A, V1.encode("utf-8"))
    storage.save_config_file(FILE_A, V2.encode("utf-8"))
    storage.save_config_file(FILE_A, V3.encode("utf-8"))
    directory = os.path.join(storage.get_archive_root_dir(pid), FILE_A)
    names = sorted(f for f in os.listdir(directory) if not f.endswith(".diff.json"))
    assert len(names) == 2, names
    _touch(os.path.join(directory, names[0]), 1_700_000_000)
    _touch(os.path.join(directory, names[1]), 1_700_000_600)
    return names[0], names[1]


def _item(body, filename):
    return [i for i in body["items"] if i["filename"] == filename][0]


class TestArchivesApi:
    def setup_method(self):
        revision.reset()
        self.pid = seed_roles("hist")
        self.archive_v1, self.archive_v2 = _three_versions(self.pid)

    def _get(self, caller, url):
        status, _headers, body = caller.json("GET", url)
        return status, body

    def test_current_block_matches_disk(self):
        with make_server() as server:
            status, body = self._get(as_role(server, "member"), _archives_url(self.pid))
            assert status == 200, body
            current = body["current"]
            assert current["name"] == FILE_A
            assert current["size"] == os.path.getsize(storage.get_config_path(FILE_A))
            assert set(current) == {"name", "updated_at", "size", "sha256"}

    def test_items_sorted_by_mtime_desc_and_compared_to_next_newer(self):
        with make_server() as server:
            status, body = self._get(as_role(server, "member"), _archives_url(self.pid))
            assert status == 200, body
            items = body["items"]
            assert [i["date"] for i in items] == sorted(
                [i["date"] for i in items], reverse=True
            )
            # 最新的归档与「当前版本」比，较早的归档与「紧邻的较新归档」比
            assert items[0]["compared_to"] == FILE_A
            assert items[1]["compared_to"] == items[0]["filename"]

    def test_same_name_siblings_are_counted_separately(self):
        """卡面点名的一条：两个同名兄弟都变化时 modified 是 2，不是 1（C11）。"""
        with make_server() as server:
            status, body = self._get(as_role(server, "member"), _archives_url(self.pid))
            assert status == 200, body
            diff = _item(body, self.archive_v1)["diff"]
            assert diff["structural"] is True
            assert diff["summary"]["modified"] == 2, diff
            assert diff["summary"]["added"] == 0
            assert diff["summary"]["removed"] == 0

    def test_each_archive_compares_with_its_immediately_newer_neighbour(self):
        with make_server() as server:
            status, body = self._get(as_role(server, "member"), _archives_url(self.pid))
            assert status == 200, body
            newer = _item(body, self.archive_v2)
            older = _item(body, self.archive_v1)
            assert newer["compared_to"] == FILE_A
            assert older["compared_to"] == self.archive_v2
            # v2 → v3 只改了 timeout
            assert newer["diff"]["summary"]["modified"] == 1, newer
            # v1 → v2 改了两个 item，timeout 不变
            assert older["diff"]["summary"]["modified"] == 2, older

    def test_diff_summary_cached_under_diff_v2_and_no_legacy_file_written(self):
        directory = os.path.join(
            storage.get_cache_root_dir(self.pid), "diff_v2"
        )
        before = set(os.listdir(directory)) if os.path.isdir(directory) else set()
        archive_dir = os.path.join(storage.get_archive_root_dir(self.pid), FILE_A)
        legacy_before = {f for f in os.listdir(archive_dir) if f.endswith(".diff.json")}

        with make_server() as server:
            caller = as_role(server, "member")
            status, first = self._get(caller, _archives_url(self.pid))
            assert status == 200, first
        after = set(os.listdir(directory))
        assert after - before, "首次列表应写入 diff_v2 摘要缓存"

        with make_server() as server:
            status, second = self._get(as_role(server, "member"), _archives_url(self.pid))
        assert second["items"][0]["diff"] == first["items"][0]["diff"]

        legacy_after = {f for f in os.listdir(archive_dir) if f.endswith(".diff.json")}
        assert legacy_after == legacy_before, "本卡不得再产生旧 .diff.json"

    def test_identical_content_reports_no_changes_without_new_archive(self):
        """内容与较新版本相同：`has_changes` 为 False，且本卡只读，不会再造归档。"""
        storage.set_active_profile(self.pid)
        directory = os.path.join(storage.get_archive_root_dir(self.pid), FILE_A)
        before = set(os.listdir(directory))
        target = os.path.join(directory, "same_copy.xml")
        with open(target, "wb") as fh:
            fh.write(storage.load_config_file(FILE_A))
        _touch(target, 1_800_000_000)

        with make_server() as server:
            status, body = self._get(as_role(server, "member"), _archives_url(self.pid))
            assert status == 200, body
            newest = body["items"][0]
            assert newest["filename"] == "same_copy.xml"
            assert newest["diff"]["has_changes"] is False
            assert newest["diff"]["summary"]["modified"] == 0
        assert set(os.listdir(directory)) - before == {"same_copy.xml"}

    def test_listing_does_not_create_archive_directories(self):
        other = os.path.join(storage.get_archive_root_dir(self.pid), "never_seen.xml")
        assert not os.path.isdir(other)
        with make_server() as server:
            caller = as_role(server, "member")
            status, _h, _body = caller.json("GET", _archives_url(self.pid, "never_seen.xml"))
            assert status == 404
        assert not os.path.isdir(other), "读历史列表不该创建目录"

    def test_legacy_diff_json_is_reported_as_unstructured_old_stats(self):
        """旧 `.diff.json` 里没有可确认的绑定版本，只能当「旧统计」显示。"""
        storage.set_active_profile(self.pid)
        # 当前版本换成解析不了的字节 → 现在也算不出结构化差异 → 才允许退回旧缓存
        storage.save_config_file(FILE_A, b"<R><broken")
        directory = os.path.join(storage.get_archive_root_dir(self.pid), FILE_A)
        files = [f for f in os.listdir(directory) if not f.endswith(".diff.json")]
        newest = max(files, key=lambda f: os.path.getmtime(os.path.join(directory, f)))
        with open(os.path.join(directory, newest + ".diff.json"), "w", encoding="utf-8") as fh:
            json.dump(
                {
                    "has_changes": True,
                    "summary": {
                        "added_count": 3,
                        "removed_count": 2,
                        "modified_count": 1,
                        "bound_count": 4,
                    },
                },
                fh,
            )
        with make_server() as server:
            status, body = self._get(as_role(server, "member"), _archives_url(self.pid))
            assert status == 200, body
            assert body["items"][0]["filename"] == newest
            diff = body["items"][0]["diff"]
            assert diff["structural"] is False
            assert diff["summary"] == {
                "added": 3,
                "removed": 2,
                "modified": 1,
                "bound": 4,
            }

    def test_diff_is_null_when_nothing_can_be_computed(self):
        """两侧算不出来、也没有旧缓存时给 `null`，不能拿一串 0 冒充「没有差异」（§7.4）。"""
        storage.set_active_profile(self.pid)
        directory = os.path.join(storage.get_archive_root_dir(self.pid), FILE_A)
        # 手工放两个归档（不经 save_config_file，所以没有旧 .diff.json 可依）
        plain = os.path.join(directory, "plain_old.xml")
        broken = os.path.join(directory, "broken_new.xml")
        with open(plain, "wb") as fh:
            fh.write(V1.encode("utf-8"))
        with open(broken, "wb") as fh:
            fh.write(b"<R><oops")
        _touch(plain, 1_700_000_800)
        _touch(broken, 1_700_000_900)
        with make_server() as server:
            status, body = self._get(as_role(server, "member"), _archives_url(self.pid))
            assert status == 200, body
            # plain_old 的较新邻居解析不了 → 只能 null
            assert _item(body, "plain_old.xml")["diff"] is None
            # broken_new 与当前版本比较：两侧都解析不了 → 也是 null（它没有旧缓存）
            assert _item(body, "broken_new.xml")["diff"] is None
            assert _item(body, self.archive_v2)["diff"]["structural"] is True

    def test_bound_count_only_covers_changed_entries(self):
        storage.set_active_profile(self.pid)
        storage.save_bindings(
            [
                {
                    "group_name": "g",
                    "variables": [
                        {"file": FILE_A, "path": "%s/R/params/item" % FILE_A},
                        {"file": FILE_A, "path": "%s/R/params/timeout" % FILE_A},
                    ],
                }
            ]
        )
        with make_server() as server:
            status, body = self._get(as_role(server, "member"), _archives_url(self.pid))
            assert status == 200, body
            changed = _item(body, self.archive_v1)["diff"]
            # 两个 item 兄弟变了 → bound 2；timeout 没变 → 不计（旧版会把没变化的也算进去）
            assert changed["summary"]["modified"] == 2
            assert changed["summary"]["bound"] == 2, changed
            newer_pair = _item(body, self.archive_v2)["diff"]
            # v2 → v3 变的正是被绑定的 timeout → modified 1、bound 1
            assert newer_pair["summary"]["modified"] == 1
            assert newer_pair["summary"]["bound"] == 1, newer_pair

    def test_missing_file_is_404_and_traversal_is_rejected(self):
        with make_server() as server:
            caller = as_role(server, "member")
            status, body = self._get(caller, _archives_url(self.pid, "nope.xml"))
            assert status == 404, body
            # `../` 编码在路径里：路由层不匹配（404），服务层自己也会拒（400）
            status, escaped = self._get(
                caller, "/api/v1/p/%s/files/..%%2Fsecret/archives" % self.pid
            )
            assert status in (400, 404), escaped
        from app.core.errors import AppError

        storage.set_active_profile(self.pid)
        try:
            history.archives(self.pid, "../secret")
        except AppError as exc:
            assert exc.http_status == 400
        else:
            raise AssertionError("路径穿越必须被拒绝")

    def test_all_roles_may_read_and_revision_unchanged(self):
        with make_server() as server:
            before = revision.snapshot(self.pid)["parts"]
            for role in ("visitor", "member", "admin", "deployer"):
                caller = as_role(server, role)
                status, _h, body = caller.json("GET", _archives_url(self.pid))
                assert status == 200, (role, body)
                status, _h, rec = caller.json("GET", _records_url(self.pid))
                assert status == 200, (role, rec)
            assert revision.snapshot(self.pid)["parts"] == before


class TestRecordsApi:
    def setup_method(self):
        revision.reset()
        self.pid = seed_roles("recs")
        write_target(self.pid, FILE_A, V3.encode("utf-8"))
        storage.set_active_profile(self.pid)
        storage.save_record_file(FILE_A, V1.encode("utf-8"))
        storage.save_record_file(FILE_A, V2.encode("utf-8"))
        _touch(storage.get_record_path(FILE_A, storage.list_record_versions(FILE_A)[-1]["filename"]), 1_700_000_000)

    def _get(self, caller, url):
        status, _headers, body = caller.json("GET", url)
        return status, body

    def test_items_descending_with_diff_prev(self):
        with make_server() as server:
            status, body = self._get(as_role(server, "member"), _records_url(self.pid))
            assert status == 200, body
            items = body["items"]
            assert [i["date"] for i in items] == sorted(
                [i["date"] for i in items], reverse=True
            )
            assert items[0]["diff_prev"]["summary"]["modified"] == 2
            # 最早的一条没有「上一条」
            assert items[-1]["diff_prev"] is None

    def test_record_url_comes_from_mapping(self):
        storage.set_active_profile(self.pid)
        storage.add_config_mapping(FILE_A, "http://example.invalid/source")
        storage.update_config_record_url(FILE_A, "http://example.invalid/changes")
        with make_server() as server:
            status, body = self._get(as_role(server, "member"), _records_url(self.pid))
            assert status == 200, body
            assert body["record_url"] == "http://example.invalid/changes"

    def test_record_url_empty_when_unmapped(self):
        with make_server() as server:
            status, body = self._get(as_role(server, "member"), _records_url(self.pid))
            assert status == 200, body
            assert body["record_url"] == ""

    def test_missing_file_is_404(self):
        with make_server() as server:
            status, body = self._get(
                as_role(server, "member"), _records_url(self.pid, "nope.xml")
            )
            assert status == 404, body


class TestSummarizeDocuments:
    """`core.differ.summarize_documents` 的 loc 键计数（不经 HTTP）。"""

    def _nodes(self, text: str):
        from app.core import parser

        return parser.to_compact(text.encode("utf-8"), FILE_A)["nodes"]

    def test_identical_documents_report_zero(self):
        nodes = self._nodes(V1)
        summary = differ_summary(nodes, nodes)
        assert summary["summary"] == {
            "added": 0,
            "removed": 0,
            "modified": 0,
            "bound": 0,
        }

    def test_added_and_removed_use_loc_keys(self):
        base = self._nodes(V1)
        target = self._nodes(V1.replace("<timeout>10</timeout>", ""))
        summary = differ_summary(base, target)
        assert summary["summary"]["removed"] == 1
        assert summary["summary"]["modified"] == 0


def differ_summary(base_nodes, target_nodes):
    from app.core import differ

    return differ.summarize_documents(base_nodes, target_nodes, FILE_A, FILE_A, [])
