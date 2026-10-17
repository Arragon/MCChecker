"""删除文件进入恢复区（§5.6、§7.4、T-15；M2-12）。

覆盖：只有管理员能删（部署者不在管理员名单同样 403，§4.2 脚注 1）、原文**移入**
`data/recovery/` 且逐文件校验 sha、`archive/` 与 `records/` 一起走、映射/收藏/绑定/建议
被清理并原样写进 manifest、修订号只按真正改动的分量递增、中途失败整体回滚不留半删除，
以及 UC-11「恢复后收藏回到原组」的 API 层等价验证。

卡面与 §7.4 在这里有一处冲突（见 PR 说明与交付文档第 8 节决策 19）：§7.4 要求删除时
**移除收藏**，而 UC-11 描述「删除后收藏组变为源文件不存在」——收藏被移除就不可能留在
「源文件不存在」组里。本文件按 §7.4 实现移除，并验证 manifest 足以把收藏原样放回、
放回后重新按源文件成组。
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import urllib.parse
from datetime import datetime

from authz_fixtures import ADMIN_NAME, as_role, make_server, seed_roles, sha256_of, write_target

from app.core import storage
from app.services import recovery, revision

FILE_A = "scenario_a.xml"
FILE_B = "scenario_b.xml"
URL_A = "http://10.20.1.15/cfg/scenario_a.xml"

XML_V1 = "<R><a>1</a><b>2</b></R>\n"
XML_V2 = "<R><a>2</a><b>2</b></R>\n"
XML_B = "<R><c>3</c></R>\n"
RECORD = "<html>修改记录</html>"


def _url(pid: str, name: str) -> str:
    return "/api/v1/p/%s/files/%s" % (pid, urllib.parse.quote(name))


def _entries() -> list:
    root = storage.get_recovery_root_dir()
    return sorted(os.listdir(root)) if os.path.isdir(root) else []


def _entry_path(name: str) -> str:
    return os.path.join(storage.get_recovery_root_dir(), name)


def _manifest(entry: str) -> dict:
    with open(os.path.join(_entry_path(entry), "manifest.json"), "rb") as fh:
        return json.loads(fh.read().decode("utf-8"))


def _read(path: str) -> bytes:
    with open(path, "rb") as fh:
        return fh.read()


def _tree(path: str) -> dict:
    """目录下全部普通文件：`{文件名: 字节}`（用于「移过去的内容和原来一模一样」）。"""
    if os.path.isfile(path):
        return {os.path.basename(path): _read(path)}
    return {
        name: _read(os.path.join(path, name))
        for name in sorted(os.listdir(path))
        if os.path.isfile(os.path.join(path, name))
    }


def _archive_dir(name: str) -> str:
    return os.path.join(storage.get_archive_root_dir(), name)


def _records_dir(name: str) -> str:
    return os.path.join(storage.get_records_root_dir(), name)


def _seed_full_workspace(pid: str) -> None:
    """造一份「有归档、有记录、四种引用都指着 FILE_A」的机型数据。"""
    storage.set_active_profile(pid)
    write_target(pid, FILE_A, XML_V1.encode("utf-8"))
    storage.save_config_file_detailed(FILE_A, XML_V2.encode("utf-8"))  # 覆盖 → 留下一个归档版本
    storage.save_record_file(FILE_A, RECORD.encode("utf-8"))
    write_target(pid, FILE_B, XML_B.encode("utf-8"))
    storage.add_config_mapping(FILE_A, URL_A)
    # 收藏与绑定按**旧节点 ID** 引用（§6.2：根 `root`，元素 `父ID/tag`）。
    storage.add_favorite("root/R/a", "a", "2", FILE_A)
    storage.add_favorite("root/R/b", "b", "2", FILE_A)
    storage.add_edit_remark(
        source_file=FILE_A,
        node_key="root/R/a",
        node_path="/R/a",
        node_label="a",
        original_value="2",
        proposed_value="3",
        node_type="element",
        tree_type="xml",
        actor_ip="10.0.0.2",
        actor_name="成员甲",
        locator="/R[1]/a[1]",
        schema_version=2,
    )
    storage.add_binding("组甲", [{"file": FILE_A, "path": "root/R/a"}, {"file": FILE_B, "path": "root/R/c"}])
    storage.add_binding("组乙", [{"file": FILE_A, "path": "root/R/b"}])


class TestPermission:
    def setup_method(self):
        revision.reset()
        self.pid = seed_roles("delperm")
        _seed_full_workspace(self.pid)
        self.sha_before = sha256_of(self.pid, FILE_A)

    def _snapshot(self):
        storage.set_active_profile(self.pid)
        return (
            sorted(_tree(_archive_dir(FILE_A))),
            sorted(_tree(_records_dir(FILE_A))),
            len(storage.load_favorites()),
            len(storage.load_edit_remarks()),
            len(storage.load_config_mapping()),
            len(storage.load_bindings()),
        )

    def test_non_admin_roles_are_rejected_and_nothing_moves(self):
        snapshot = self._snapshot()
        for role in ("visitor", "member", "deployer"):
            with make_server() as server:
                status, _h, body = as_role(server, role).request("DELETE", _url(self.pid, FILE_A))
                assert status == 403, (role, body)
                assert json.loads(body)["error"]["detail"]["action"] == "delete_file"
        assert sha256_of(self.pid, FILE_A) == self.sha_before
        assert self._snapshot() == snapshot
        assert _entries() == []

    def test_deployer_admin_can_delete(self):
        admin_pid = seed_roles("dellocal", local_admin=True)
        storage.set_active_profile(admin_pid)
        write_target(admin_pid, FILE_A, XML_V1.encode("utf-8"))
        with make_server() as server:
            status, _h, body = as_role(server, "deployer_admin").request(
                "DELETE", _url(admin_pid, FILE_A)
            )
            assert status == 200, body
        assert not os.path.isfile(storage.get_config_path(FILE_A))

    def test_write_without_csrf_header_is_rejected(self):
        with make_server() as server:
            status, _h, body = as_role(server, "admin").request(
                "DELETE", _url(self.pid, FILE_A), with_csrf=False
            )
            assert status == 403, body
        assert sha256_of(self.pid, FILE_A) == self.sha_before


class TestRecoveryMove:
    def setup_method(self):
        revision.reset()
        self.pid = seed_roles("delmove")
        _seed_full_workspace(self.pid)

    def test_current_archive_and_records_all_move_into_one_entry(self):
        storage.set_active_profile(self.pid)
        before = {
            "configs": _tree(storage.get_config_path(FILE_A)),
            "archive": _tree(_archive_dir(FILE_A)),
            "records": _tree(_records_dir(FILE_A)),
        }
        assert before["archive"], "夹具应该留下至少一个归档版本"
        with make_server() as server:
            status, _h, body = as_role(server, "admin").request("DELETE", _url(self.pid, FILE_A))
            assert status == 200, body
        payload = json.loads(body)
        assert set(payload) == {"deleted", "removed", "recovery"}
        assert payload["deleted"] is True
        entry = payload["recovery"]
        assert entry in _entries()

        # 原位置三样都没了，而且读路径不会把目录顺手建回来。
        assert not os.path.exists(storage.get_config_path(FILE_A))
        assert not os.path.exists(_archive_dir(FILE_A))
        assert not os.path.exists(_records_dir(FILE_A))

        # 恢复区里内容逐字节相同，连 `.diff.json` 边车也一起走。
        for rel, files in before.items():
            moved = _tree(os.path.join(_entry_path(entry), rel, FILE_A if rel != "configs" else ""))
            assert moved == files, rel

    def test_entry_name_carries_time_op_profile_and_file(self):
        with make_server() as server:
            _status, _h, body = as_role(server, "admin").request("DELETE", _url(self.pid, FILE_A))
        entry = json.loads(body)["recovery"]
        # 名字是 `<时间>_<op>_<机型>_<文件>`，时间本身含一个下划线。
        stamp, rest = entry.split("_" + recovery.OP_DELETE_FILE + "_", 1)
        datetime.strptime(stamp, "%Y%m%d_%H%M%S")
        assert rest.startswith(self.pid + "_")
        assert entry.endswith(FILE_A)

    def test_other_files_are_untouched(self):
        with make_server() as server:
            status, _h, body = as_role(server, "admin").request("DELETE", _url(self.pid, FILE_A))
            assert status == 200, body
        assert _read(storage.get_config_path(FILE_B)) == XML_B.encode("utf-8")

    def test_deleted_file_disappears_from_listing_and_document(self):
        with make_server() as server:
            caller = as_role(server, "admin")
            status, _h, body = caller.request("DELETE", _url(self.pid, FILE_A))
            assert status == 200, body
            _s, _h, listing = caller.json("GET", "/api/v1/p/%s/files" % self.pid)
            assert [item["name"] for item in listing["items"]] == [FILE_B]
            status, _h, body = caller.json("GET", _url(self.pid, FILE_A) + "/document")
            assert status == 404, body

    def test_counts_match_the_ui_and_pre_delete_state(self):
        with make_server() as server:
            caller = as_role(server, "admin")
            _s, _h, listing = caller.json("GET", "/api/v1/p/%s/files" % self.pid)
            row = [item for item in listing["items"] if item["name"] == FILE_A][0]
            status, _h, body = caller.request("DELETE", _url(self.pid, FILE_A))
            assert status == 200, body
        removed = json.loads(body)["removed"]
        # archives/records 与 `GET /files` 同口径（归档数不含 `.diff.json` 边车）。
        assert removed["archives"] == row["archive_count"] == 1
        assert removed["records"] == row["record_count"] == 1
        assert removed["favorites"] == 2
        assert removed["remarks"] == 1

    def test_missing_file_is_404_and_creates_no_entry(self):
        with make_server() as server:
            status, _h, body = as_role(server, "admin").request(
                "DELETE", _url(self.pid, "ghost.xml")
            )
            assert status == 404, body
        assert _entries() == []

    def test_illegal_names_are_rejected_before_any_move(self):
        with make_server() as server:
            caller = as_role(server, "admin")
            for bad in ("a:b.xml", "CON.xml", "x" * 200 + ".xml"):
                status, _h, body = caller.request("DELETE", _url(self.pid, bad))
                assert status == 400, (bad, status, body)
                assert json.loads(body)["error"]["code"] == "INVALID_INPUT"
        assert _entries() == []
        assert os.path.isfile(storage.get_config_path(FILE_A))

    def test_repeated_delete_of_the_same_name_keeps_both_entries(self):
        with make_server() as server:
            first = json.loads(
                as_role(server, "admin").request("DELETE", _url(self.pid, FILE_A))[2]
            )["recovery"]
        storage.set_active_profile(self.pid)
        write_target(self.pid, FILE_A, XML_V1.encode("utf-8"))
        with make_server() as server:
            second = json.loads(
                as_role(server, "admin").request("DELETE", _url(self.pid, FILE_A))[2]
            )["recovery"]
        # 同一秒内删掉同名文件两次也要各留一份：第二次的副本不能盖掉第一次。
        assert first != second
        contents = {first: XML_V2.encode("utf-8"), second: XML_V1.encode("utf-8")}
        for entry, expected in contents.items():
            assert _read(os.path.join(_entry_path(entry), "configs", FILE_A)) == expected


class TestManifest:
    def setup_method(self):
        revision.reset()
        self.pid = seed_roles("delmanifest")
        _seed_full_workspace(self.pid)
        storage.set_active_profile(self.pid)
        self.favorites = storage.load_favorites()
        self.remarks = storage.load_edit_remarks()
        self.mapping = storage.load_config_mapping()

    def test_manifest_shape_matches_spec_5_6(self):
        with make_server() as server:
            entry = json.loads(
                as_role(server, "admin").request("DELETE", _url(self.pid, FILE_A))[2]
            )["recovery"]
        manifest = _manifest(entry)
        assert set(manifest) == {
            "op",
            "profile",
            "target",
            "created_at",
            "actor",
            "moved",
            "removed_refs",
        }
        assert manifest["op"] == "delete_file"
        assert manifest["profile"] == self.pid
        assert manifest["target"] == FILE_A
        assert set(manifest["actor"]) == {"ip", "name"}
        assert manifest["actor"]["ip"] == "10.0.0.3"
        assert manifest["actor"]["name"] == ADMIN_NAME
        moved = {item["to"]: item["from"] for item in manifest["moved"]}
        assert set(moved) == {
            "configs/%s" % FILE_A,
            "archive/%s" % FILE_A,
            "records/%s" % FILE_A,
        }
        # `from` 必须是相对数据根的可用路径：符号链接的临时数据根（macOS /var）上
        # 只 realpath 一侧会算出../../../../ 开头的废路径。
        base = os.path.join("profiles", self.pid)
        assert moved["configs/%s" % FILE_A] == "%s/configs/%s" % (base, FILE_A)
        assert moved["archive/%s" % FILE_A] == "%s/archive/%s" % (base, FILE_A)
        assert moved["records/%s" % FILE_A] == "%s/records/%s" % (base, FILE_A)

    def test_removed_refs_are_exactly_what_was_there(self):
        with make_server() as server:
            entry = json.loads(
                as_role(server, "admin").request("DELETE", _url(self.pid, FILE_A))[2]
            )["recovery"]
        refs = _manifest(entry)["removed_refs"]
        assert refs["favorites"] == self.favorites
        assert refs["remarks"] == self.remarks
        assert refs["mapping"] == self.mapping
        # 绑定按「组 → 被摘掉的变量」记录，恢复时能放回原组。
        assert [item["group_name"] for item in refs["bindings"]] == ["组甲", "组乙"]
        assert refs["bindings"][0]["variables"] == [{"file": FILE_A, "path": "root/R/a"}]
        assert refs["bindings"][1]["variables"] == [{"file": FILE_A, "path": "root/R/b"}]

    def test_absent_artifacts_are_not_recorded_as_moved(self):
        with make_server() as server:
            payload = json.loads(
                as_role(server, "admin").request("DELETE", _url(self.pid, FILE_B))[2]
            )
        manifest = _manifest(payload["recovery"])
        assert [item["to"] for item in manifest["moved"]] == ["configs/%s" % FILE_B]
        assert manifest["removed_refs"]["mapping"] == []
        assert payload["removed"] == {"archives": 0, "records": 0, "remarks": 0, "favorites": 0}


class TestReferenceCleanup:
    def setup_method(self):
        revision.reset()
        self.pid = seed_roles("delrefs")
        _seed_full_workspace(self.pid)

    def test_mapping_favorites_remarks_are_gone(self):
        with make_server() as server:
            assert as_role(server, "admin").request("DELETE", _url(self.pid, FILE_A))[0] == 200
        storage.set_active_profile(self.pid)
        assert storage.load_config_mapping() == []
        assert storage.load_favorites() == []
        assert storage.load_edit_remarks() == []

    def test_binding_loses_only_that_file_and_empty_group_is_dropped(self):
        with make_server() as server:
            assert as_role(server, "admin").request("DELETE", _url(self.pid, FILE_A))[0] == 200
        storage.set_active_profile(self.pid)
        assert storage.load_bindings() == [
            {"group_name": "组甲", "variables": [{"file": FILE_B, "path": "root/R/c"}]}
        ]

    def test_revision_bumps_only_the_parts_that_changed(self):
        with make_server() as server:
            caller = as_role(server, "admin")
            before = caller.json("GET", "/api/v1/p/%s/revision" % self.pid)[2]["parts"]
            assert caller.request("DELETE", _url(self.pid, FILE_A))[0] == 200
            after = caller.json("GET", "/api/v1/p/%s/revision" % self.pid)[2]["parts"]
        # 文件、收藏、建议、绑定四份权威数据都动了 → 四个分量各 +1，tasks 不动。
        assert after["files"] - before["files"] == 1
        assert after["favorites"] - before["favorites"] == 1
        assert after["remarks"] - before["remarks"] == 1
        assert after["settings"] - before["settings"] == 1
        assert after["tasks"] == before["tasks"]


class TestBareDelete:
    def setup_method(self):
        revision.reset()
        self.pid = seed_roles("delbare")
        write_target(self.pid, FILE_A, XML_V1.encode("utf-8"))

    def test_only_files_part_bumps_and_no_refs_removed(self):
        with make_server() as server:
            caller = as_role(server, "admin")
            before = caller.json("GET", "/api/v1/p/%s/revision" % self.pid)[2]["parts"]
            payload = json.loads(caller.request("DELETE", _url(self.pid, FILE_A))[2])
            after = caller.json("GET", "/api/v1/p/%s/revision" % self.pid)[2]["parts"]
        assert payload["removed"] == {"archives": 0, "records": 0, "remarks": 0, "favorites": 0}
        assert after["files"] == before["files"] + 1
        assert after["favorites"] == before["favorites"]
        assert after["remarks"] == before["remarks"]
        assert after["settings"] == before["settings"]

    def test_unparsable_file_is_still_deletable(self):
        """恢复区只做字节搬运，不解析——坏文件也要能删掉（否则只能登服务器手删）。"""
        storage.set_active_profile(self.pid)
        storage.save_config_file("broken.xml", b"<R><oops")
        with make_server() as server:
            status, _h, body = as_role(server, "admin").request(
                "DELETE", _url(self.pid, "broken.xml")
            )
            assert status == 200, body


class TestRollback:
    """§5.6 的顺序：先移动到恢复区并校验，再改 JSON 引用——中途失败必须整体退回。"""

    def setup_method(self):
        revision.reset()
        self.pid = seed_roles("delrollback")
        _seed_full_workspace(self.pid)

    def test_failure_after_the_moves_restores_everything(self, monkeypatch):
        storage.set_active_profile(self.pid)
        originals = (
            _read(storage.get_config_path(FILE_A)),
            _tree(_archive_dir(FILE_A)),
            _tree(_records_dir(FILE_A)),
            storage.load_favorites(),
            storage.load_bindings(),
            storage.load_edit_remarks(),
            storage.load_config_mapping(),
        )
        revision_before = revision.snapshot(self.pid)

        def boom(_data):
            raise RuntimeError("绑定写不下去")

        monkeypatch.setattr(storage, "save_bindings", boom)
        with make_server() as server:
            status, _h, body = as_role(server, "admin").request("DELETE", _url(self.pid, FILE_A))
            assert status == 500, body
            assert json.loads(body)["error"]["code"] == "STORAGE_FAILURE"

        # 权威数据回到删除前：文件字节、两个目录、四份 JSON 都没动，修订号也没涨。
        assert _read(storage.get_config_path(FILE_A)) == originals[0]
        assert _tree(_archive_dir(FILE_A)) == originals[1]
        assert _tree(_records_dir(FILE_A)) == originals[2]
        assert storage.load_favorites() == originals[3]
        assert storage.load_bindings() == originals[4]
        assert storage.load_edit_remarks() == originals[5]
        assert storage.load_config_mapping() == originals[6]
        assert revision.snapshot(self.pid) == revision_before

        # 恢复区保留，但里面不能留下半份副本，也没写 manifest（那等于宣告删除成功）。
        entries = _entries()
        assert len(entries) == 1
        entry = _entry_path(entries[0])
        assert not os.path.exists(os.path.join(entry, "manifest.json"))
        for rel in ("configs", "archive", "records"):
            assert not os.path.exists(os.path.join(entry, rel, FILE_A))


class TestUC11FavoritesRegroup:
    """UC-11 的 API 层等价验证：manifest 里的收藏放回后，重新按源文件成组。"""

    def setup_method(self):
        revision.reset()
        self.pid = seed_roles("deluc11")
        _seed_full_workspace(self.pid)

    def test_group_disappears_then_returns_to_its_original_file(self):
        with make_server() as server:
            caller = as_role(server, "admin")
            groups = caller.json("GET", "/api/v1/p/%s/favorites" % self.pid)[2]["groups"]
            assert [g["file"] for g in groups] == [FILE_A]
            payload = json.loads(caller.request("DELETE", _url(self.pid, FILE_A))[2])
            groups = caller.json("GET", "/api/v1/p/%s/favorites" % self.pid)[2]["groups"]
            # §7.4：v2 的删除会**移除**收藏，所以组不是「源文件不存在」，而是整个没了。
            assert groups == []
        entry = payload["recovery"]
        manifest = _manifest(entry)
        assert len(manifest["removed_refs"]["favorites"]) == 2

        # §14.6 的手工恢复：原文从恢复区放回 configs/，收藏按 manifest 原样放回。
        shutil.move(
            os.path.join(_entry_path(entry), "configs", FILE_A),
            storage.get_config_path(FILE_A),
        )
        storage.save_favorites(manifest["removed_refs"]["favorites"])
        with make_server() as server:
            groups = as_role(server, "member").json("GET", "/api/v1/p/%s/favorites" % self.pid)[2][
                "groups"
            ]
        assert [g["file"] for g in groups] == [FILE_A]
        restored = groups[0]
        assert restored["exists"] is True
        assert [item["path"] for item in restored["items"]] == ["root/R/a", "root/R/b"]
        assert [item["missing"] for item in restored["items"]] == [False, False]

    def test_missing_source_file_groups_as_not_existing(self):
        """对照：速览的「源文件已不存在」来自 `exists=false`，走的是另一条路径。"""
        storage.set_active_profile(self.pid)
        os.remove(storage.get_config_path(FILE_A))
        with make_server() as server:
            groups = as_role(server, "member").json("GET", "/api/v1/p/%s/favorites" % self.pid)[2][
                "groups"
            ]
        assert [g["exists"] for g in groups] == [False]


class TestServiceContract:
    """service 层单独可测（不经 HTTP），并且不动 `storage.delete_config_file` 的旧语义。"""

    def setup_method(self):
        revision.reset()
        self.pid = seed_roles("delsvc")
        _seed_full_workspace(self.pid)

    def test_legacy_hard_delete_still_hard_deletes(self):
        """v1 页面与既有测试依赖旧硬删语义：直接消失，不进恢复区。"""
        storage.set_active_profile(self.pid)
        assert storage.delete_config_file(FILE_A) is True
        assert not os.path.exists(storage.get_config_path(FILE_A))
        assert _entries() == []

    def test_service_returns_the_same_payload_as_the_endpoint(self):
        storage.set_active_profile(self.pid)
        payload = recovery.delete_file(self.pid, FILE_A, ip="10.0.0.3", actor_name=ADMIN_NAME)
        assert payload["deleted"] is True
        assert payload["recovery"] in _entries()
