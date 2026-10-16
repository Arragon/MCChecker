"""数据持久化层测试

数据目录隔离由 `tests/conftest.py` 的 `isolated_data_root` 夹具统一提供。
"""

from __future__ import annotations

import os
import threading
from datetime import datetime

import pytest


def _freeze_time(monkeypatch, module, moment: datetime) -> None:
    """把模块内的 `datetime.now()` 钉在 `moment`，让文件名断言可确定。

    归档名来自 `datetime.now()`，跨秒执行会让期望值漂移，因此涉及归档命名的
    用例一律先冻结时间。
    """

    class _FrozenDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return moment

    monkeypatch.setattr(module, "datetime", _FrozenDatetime)


class TestConfigMapping:
    def test_add_mapping(self):
        from app.core.storage import add_config_mapping, load_config_mapping
        add_config_mapping("test.xml", "http://example.com/test.xml")
        mapping = load_config_mapping()
        assert len(mapping) == 1
        assert mapping[0]["name"] == "test.xml"
        assert mapping[0]["url"] == "http://example.com/test.xml"
        assert mapping[0].get("record_url", "") == ""

    def test_add_duplicate_updates(self):
        from app.core.storage import add_config_mapping, load_config_mapping
        add_config_mapping("test.xml", "http://old.com/test.xml")
        add_config_mapping("test.xml", "http://new.com/test.xml")
        mapping = load_config_mapping()
        assert len(mapping) == 1
        assert mapping[0]["url"] == "http://new.com/test.xml"
        assert mapping[0].get("record_url", "") == ""

    def test_remove_mapping(self):
        from app.core.storage import add_config_mapping, remove_config_mapping, load_config_mapping
        add_config_mapping("test.xml", "http://example.com/test.xml")
        remove_config_mapping("test.xml")
        mapping = load_config_mapping()
        assert len(mapping) == 0

    def test_update_mapping_rename_migrates_files_and_refs(self):
        from app.core import storage

        storage.add_config_mapping("a.xml", "http://old.local/a.xml")
        storage.save_config_file("a.xml", b"v1")
        storage.save_config_file("a.xml", b"v2")

        storage.add_favorite("/root/x", "x", "1", "a.xml", "备注")
        storage.add_binding("g1", [{"file": "a.xml", "path": "/root/x"}])

        ok = storage.update_config_mapping("a.xml", "b.xml", "http://new.local/b.xml", migrate_files=True)
        assert ok is True

        mapping = storage.load_config_mapping()
        assert mapping == [{"name": "b.xml", "url": "http://new.local/b.xml", "record_url": ""}]

        assert os.path.exists(storage.get_config_path("b.xml")) is True
        assert os.path.exists(storage.get_config_path("a.xml")) is False

        assert os.path.exists(os.path.join(storage.get_archive_root_dir(), "a.xml")) is False
        assert os.path.exists(os.path.join(storage.get_archive_root_dir(), "b.xml")) is True

        favs = storage.load_favorites()
        assert favs and favs[0]["source_file"] == "b.xml"

        bindings = storage.load_bindings()
        assert bindings and bindings[0]["variables"][0]["file"] == "b.xml"

    def test_delete_mapping_only_removes_mapping(self):
        from app.core import storage

        storage.add_config_mapping("c.xml", "http://example.com/c.xml")
        storage.save_config_file("c.xml", b"v1")

        ok = storage.delete_config_mapping("c.xml", delete_files=False)
        assert ok is True
        assert storage.load_config_mapping() == []
        assert os.path.exists(storage.get_config_path("c.xml")) is True

    def test_delete_mapping_with_files_cleans_refs(self):
        from app.core import storage

        storage.add_config_mapping("d.xml", "http://example.com/d.xml")
        storage.save_config_file("d.xml", b"v1")
        storage.save_config_file("d.xml", b"v2")
        storage.add_favorite("/root/x", "x", "1", "d.xml", "备注")
        storage.add_binding("g1", [{"file": "d.xml", "path": "/root/x"}])
        storage.add_edit_remark(
            source_file="d.xml",
            node_key="0.0",
            node_path="/root/x",
            node_label="x",
            original_value="1",
            proposed_value="2",
            node_type="str",
            tree_type="xml",
            actor_ip="127.0.0.1",
            actor_name="张三",
        )

        ok = storage.delete_config_mapping("d.xml", delete_files=True)
        assert ok is True
        assert storage.load_config_mapping() == []
        assert os.path.exists(storage.get_config_path("d.xml")) is False
        assert os.path.exists(os.path.join(storage.get_archive_root_dir(), "d.xml")) is False
        assert storage.load_favorites() == []
        assert storage.load_bindings() == []
        assert storage.load_edit_remarks() == []


class TestConfigFileStorage:
    def test_save_and_load(self):
        from app.core.storage import save_config_file, load_config_file
        content = b"<config><key>value</key></config>"
        save_config_file("test.xml", content)
        loaded = load_config_file("test.xml")
        assert loaded == content

    def test_archive_on_overwrite(self):
        from app.core.storage import save_config_file, load_config_file, list_archived_versions
        save_config_file("test.xml", b"version1")
        save_config_file("test.xml", b"version2")
        # 旧版应被归档
        versions = list_archived_versions("test.xml")
        assert len(versions) >= 1
        # 当前版本应是新内容
        current = load_config_file("test.xml")
        assert current == b"version2"

    def test_load_nonexistent(self):
        from app.core.storage import load_config_file
        result = load_config_file("nonexistent.xml")
        assert result is None

    def test_list_config_files(self):
        from app.core.storage import save_config_file, list_config_files
        save_config_file("a.xml", b"a")
        save_config_file("b.json", b"b")
        files = list_config_files()
        assert "a.xml" in files
        assert "b.json" in files


class TestRecordStorage:
    def test_save_and_list_records(self):
        from app.core import storage

        p1 = storage.save_record_file("a.xml", b"record_v1", source_url="http://example.com/record.txt")
        assert os.path.exists(p1) is True
        versions = storage.list_record_versions("a.xml")
        assert versions
        assert versions[0]["filename"].startswith("record_")
        loaded = storage.load_record_file("a.xml", versions[0]["filename"])
        assert loaded == b"record_v1"

    def test_archive_diff_generated(self):
        from app.core.storage import save_config_file, list_archived_versions

        v1 = b"<root><a>1</a></root>"
        v2 = b"<root><a>2</a><b>3</b></root>"
        save_config_file("test.xml", v1)
        save_config_file("test.xml", v2)

        versions = list_archived_versions("test.xml")
        assert versions
        diff = versions[0].get("diff")
        assert diff is not None
        assert diff.get("has_changes") is True
        summary = diff.get("summary") or {}
        assert (summary.get("added_count", 0) + summary.get("removed_count", 0) + summary.get("modified_count", 0)) > 0

    def test_archive_diff_no_change(self):
        """diff 缓存的 `has_changes=False` 分支。

        内容完全相同的保存现在不再产生归档（`test_identical_content_not_archived`），
        因此这个分支只能直接喂给 `_write_archive_diff` 来覆盖。
        """
        from app.core import storage

        v1 = b"<root><a>1</a></root>"
        storage.save_config_file("test.xml", v1)
        storage._write_archive_diff("test.xml", "fake_archive.xml", v1, v1)

        diff = storage.load_archive_diff("test.xml", "fake_archive.xml")
        assert diff is not None
        assert diff.get("has_changes") is False


class TestFavorites:
    def test_add_favorite(self):
        from app.core.storage import add_favorite, load_favorites
        add_favorite("/root/host", "host", "localhost", "config.xml", "备注")
        favs = load_favorites()
        assert len(favs) == 1
        assert favs[0]["label"] == "host"
        assert favs[0]["note"] == "备注"

    def test_add_duplicate_updates_note(self):
        from app.core.storage import add_favorite, load_favorites
        add_favorite("/root/host", "host", "localhost", "config.xml", "旧备注")
        add_favorite("/root/host", "host", "localhost", "config.xml", "新备注")
        favs = load_favorites()
        assert len(favs) == 1
        assert favs[0]["note"] == "新备注"

    def test_remove_favorite(self):
        from app.core.storage import add_favorite, remove_favorite, load_favorites
        add_favorite("/root/host", "host", "localhost", "config.xml")
        remove_favorite("/root/host", "config.xml")
        assert len(load_favorites()) == 0

    def test_update_favorite_note(self):
        from app.core.storage import add_favorite, update_favorite_note, load_favorites
        add_favorite("/root/host", "host", "localhost", "config.xml")
        update_favorite_note("/root/host", "config.xml", "新备注")
        favs = load_favorites()
        assert favs[0]["note"] == "新备注"


class TestBindings:
    def test_add_binding(self):
        from app.core.storage import add_binding, load_bindings
        variables = [{"file": "a.xml", "path": "/root/timeout"}, {"file": "b.xml", "path": "/root/time_limit"}]
        add_binding("timeout_group", variables)
        bindings = load_bindings()
        assert len(bindings) == 1
        assert bindings[0]["group_name"] == "timeout_group"

    def test_update_binding(self):
        from app.core.storage import add_binding, load_bindings
        add_binding("group1", [{"file": "a.xml", "path": "/x"}])
        add_binding("group1", [{"file": "b.xml", "path": "/y"}])
        bindings = load_bindings()
        assert len(bindings) == 1
        assert len(bindings[0]["variables"]) == 1
        assert bindings[0]["variables"][0]["file"] == "b.xml"

    def test_remove_binding(self):
        from app.core.storage import add_binding, remove_binding, load_bindings
        add_binding("group1", [])
        remove_binding("group1")
        assert len(load_bindings()) == 0

    def test_get_bound_paths(self):
        from app.core.storage import add_binding, get_bound_paths
        add_binding("group1", [{"file": "a.xml", "path": "/x"}, {"file": "b.xml", "path": "/y"}])
        paths = get_bound_paths()
        assert "a.xml:/x" in paths
        assert "b.xml:/y" in paths


class TestTools:
    def test_add_tool(self):
        from app.core.storage import add_tool, load_tools
        add_tool("Jenkins", "CI/CD", "http://jenkins.local")
        tools = load_tools()
        assert len(tools) == 1
        assert tools[0]["name"] == "Jenkins"

    def test_remove_tool(self):
        from app.core.storage import add_tool, remove_tool, load_tools
        add_tool("Jenkins", "CI/CD", "http://jenkins.local")
        remove_tool("Jenkins")
        assert len(load_tools()) == 0

    def test_update_tool(self):
        from app.core.storage import add_tool, update_tool, load_tools
        add_tool("Jenkins", "CI/CD", "http://old.local")
        update_tool("Jenkins", "Jenkins", "CI/CD Platform", "http://new.local")
        tools = load_tools()
        assert tools[0]["url"] == "http://new.local"
        assert tools[0]["description"] == "CI/CD Platform"


class TestSchedule:
    def test_load_default_schedule(self):
        from app.core.storage import load_schedule
        schedule = load_schedule()
        assert schedule["enabled"] is False
        assert schedule["interval_hours"] == 24

    def test_save_schedule(self):
        from app.core.storage import save_schedule, load_schedule
        save_schedule({"enabled": True, "interval_hours": 12})
        schedule = load_schedule()
        assert schedule["enabled"] is True
        assert schedule["interval_hours"] == 12


class TestIdentityTables:
    def test_ip_mapping_crud_and_resolve(self):
        from app.core import storage

        storage.upsert_ip_mapping("10.0.0.1", "张三")
        storage.upsert_ip_mapping("10.0.0.2", "李四")
        storage.upsert_ip_mapping("10.0.0.1", "张三A")

        mapping = storage.load_ip_mapping()
        assert len(mapping) == 2
        assert storage.resolve_person_by_ip("10.0.0.1") == "张三A"

        ok = storage.remove_ip_mapping("10.0.0.2")
        assert ok is True
        assert storage.resolve_person_by_ip("10.0.0.2") == ""

    def test_admin_user_crud(self):
        from app.core import storage

        storage.upsert_admin_user("张三")
        storage.upsert_admin_user("王五")
        assert storage.is_admin_user("张三") is True
        assert storage.is_admin_user("游客") is False

        ok = storage.remove_admin_user("王五")
        assert ok is True
        assert [item["name"] for item in storage.load_admin_users()] == ["张三"]


class TestEditRemarks:
    def test_add_group_and_apply_review_results(self):
        from app.core import storage

        first_id = storage.add_edit_remark(
            source_file="cfg.xml",
            node_key="0.1",
            node_path="root/a",
            node_label="a",
            original_value="1",
            proposed_value="2",
            node_type="str",
            tree_type="xml",
            actor_ip="10.0.0.1",
            actor_name="张三",
        )
        second_id = storage.add_edit_remark(
            source_file="cfg.xml",
            node_key="0.1",
            node_path="root/a",
            node_label="a",
            original_value="1",
            proposed_value="3",
            node_type="str",
            tree_type="xml",
            actor_ip="10.0.0.2",
            actor_name="李四",
        )

        remark_map = storage.build_edit_remark_map("cfg.xml")
        assert len(remark_map["0.1"]) == 2

        review_items = storage.list_review_items("cfg.xml")
        assert len(review_items) == 1
        assert len(review_items[0]["remarks"]) == 2

        result = storage.apply_review_results(
            approved_ids=[first_id],
            rejected_ids=[second_id],
            reviewer_name="管理员",
            reviewer_ip="127.0.0.1",
            generated_files={"cfg.xml": "cfg_20260613.xml"},
        )
        assert result == {"approved": 1, "rejected": 1}

        items = storage.load_edit_remarks()
        approved = next(item for item in items if item["id"] == first_id)
        rejected = next(item for item in items if item["id"] == second_id)
        assert approved["status"] == "approved"
        assert approved["generated_file"] == "cfg_20260613.xml"
        assert rejected["status"] == "rejected"


# ==========================================================================
# M0-06 / INH-1863：原子写、机型锁与 §5.3 命名
# ==========================================================================


class TestArchiveNaming:
    def test_two_saves_in_same_second_create_distinct_archives(self, monkeypatch):
        """同一秒内两次不同内容的保存必须留下两个不同的归档（§5.3）。

        旧实现只有“日级”归档名撞车时才退化到秒级，同秒的第二次保存会直接
        覆盖前一个归档——历史版本就此丢失。
        """
        from app.core import storage

        _freeze_time(monkeypatch, storage, datetime(2026, 5, 24, 14, 30, 12))

        storage.save_config_file("cfg.xml", b"v1")
        storage.save_config_file("cfg.xml", b"v2")
        third = storage.save_config_file_detailed("cfg.xml", b"v3")

        names = [v["filename"] for v in storage.list_archived_versions("cfg.xml")]
        assert len(names) == 2, names
        assert len(set(names)) == 2, names

        # 当前文件是最后一次写入的内容，两个归档都真实存在
        assert storage.load_config_file("cfg.xml") == b"v3"
        assert third.archived in names
        for name in names:
            assert os.path.exists(storage.get_archived_path("cfg.xml", name))
            assert os.path.getsize(storage.get_archived_path("cfg.xml", name)) > 0

    def test_archive_name_conflict_appends_counter(self, monkeypatch):
        """两个首选归档名都被占用时，依次追加 `_2`（§5.3）。

        同时断言占用者的内容没有被覆盖——旧实现是「存在就直接写过去」。
        """
        from app.core import storage

        moment = datetime(2026, 5, 24, 14, 30, 12)
        _freeze_time(monkeypatch, storage, moment)

        storage.save_config_file("cfg.xml", b"v1")
        archive_dir = storage.get_archive_dir("cfg.xml")
        occupied = [
            "cfg_%s.xml" % moment.strftime("%Y%m%d"),
            "cfg_%s.xml" % moment.strftime("%Y%m%d_%H%M%S"),
        ]
        for name in occupied:
            with open(os.path.join(archive_dir, name), "wb") as fh:
                fh.write(b"occupied")

        result = storage.save_config_file_detailed("cfg.xml", b"v2")
        assert result.archived is not None
        assert result.archived.endswith("_2.xml"), result.archived
        assert result.archived not in occupied

        for name in occupied:
            with open(os.path.join(archive_dir, name), "rb") as fh:
                assert fh.read() == b"occupied"

    def test_identical_content_not_archived(self):
        """内容与当前文件完全一致：不归档、不写（§5.4 第 2 步）。"""
        from app.core import storage

        v1 = b"<root><a>1</a></root>"
        first = storage.save_config_file_detailed("cfg.xml", v1)
        assert first.unchanged is False
        assert first.archived is None

        second = storage.save_config_file_detailed("cfg.xml", v1)
        assert second.unchanged is True
        assert second.archived is None
        assert storage.load_config_file("cfg.xml") == v1
        assert storage.list_archived_versions("cfg.xml") == []

    def test_save_config_file_returns_path_for_backward_compat(self):
        """旧签名仍然返回当前文件路径，未迁移的调用方不受影响。"""
        from app.core import storage

        path = storage.save_config_file("cfg.xml", b"v1")
        assert path == storage.get_config_path("cfg.xml")
        assert isinstance(path, str)

    def test_record_files_never_overwrite_each_other(self, monkeypatch):
        """修改记录按 §5.3 命名，同名时追加 `_2`，不会互相覆盖。"""
        from app.core import storage

        moment = datetime(2026, 5, 24, 14, 30, 12)
        _freeze_time(monkeypatch, storage, moment)

        path = storage.save_record_file("a.xml", b"first", source_url="http://example.com/record.txt")
        occupied = os.path.basename(path)
        with open(path, "wb") as fh:
            fh.write(b"occupied")

        second = storage.save_record_file("a.xml", b"second", source_url="http://example.com/record.txt")
        assert os.path.basename(second) != occupied
        assert second.endswith("_2.txt"), second

        with open(path, "rb") as fh:
            assert fh.read() == b"occupied"
        with open(second, "rb") as fh:
            assert fh.read() == b"second"


class TestConcurrentWrites:
    def test_twenty_threads_adding_favorites_keep_all_entries(self):
        """20 个线程各添加 1 条收藏，最终必须恰好 20 条（§5.4）。

        旧实现的 load 与 save 之间没有任何锁，后写的线程会覆盖先写的结果，
        最终条数随机少于 20。
        """
        from app.core import storage

        errors = []
        barrier = threading.Barrier(20)

        def worker(index):
            try:
                barrier.wait(timeout=10)
                storage.add_favorite(f"/root/v{index}", f"v{index}", str(index), "cfg.xml")
            except BaseException as exc:  # pragma: no cover - 失败时由断言暴露
                errors.append(exc)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(20)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(30)

        assert errors == []
        favorites = storage.load_favorites()
        assert len(favorites) == 20
        assert len({fav["path"] for fav in favorites}) == 20

    def test_twenty_threads_saving_config_file_keep_all_archives(self):
        """20 个线程各保存一版不同内容：19 个归档 + 当前文件，无互相覆盖。"""
        from app.core import storage

        errors = []
        barrier = threading.Barrier(20)

        def worker(index):
            try:
                barrier.wait(timeout=10)
                storage.save_config_file("cfg.xml", b"v%d" % index)
            except BaseException as exc:  # pragma: no cover
                errors.append(exc)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(20)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(30)

        assert errors == []
        names = [v["filename"] for v in storage.list_archived_versions("cfg.xml")]
        assert len(names) == 19, names
        assert len(set(names)) == 19, names


class TestCorruptDataProtection:
    def test_corrupt_favorites_blocks_write_and_keeps_bytes(self):
        """`favorites.json` 损坏后，读改写必须失败且文件字节保持不变（§5.4）。

        旧实现把损坏文件读成空列表，下一次保存直接把权威数据清空。
        """
        from app.core import storage
        from app.core.errors import CORRUPT_DATA, CorruptDataError

        storage.add_favorite("/root/x", "x", "1", "cfg.xml")
        path = storage.get_favorites_file()
        with open(path, "rb") as fh:
            healthy_bytes = fh.read()
        assert healthy_bytes  # 前置条件：确实写过一条收藏

        corrupt_bytes = b"{ this is not valid json"
        with open(path, "wb") as fh:
            fh.write(corrupt_bytes)

        with pytest.raises(CorruptDataError) as excinfo:
            storage.add_favorite("/root/y", "y", "2", "cfg.xml")
        assert excinfo.value.code == CORRUPT_DATA
        assert excinfo.value.path == path

        # 写入被拒：损坏现场原样保留。旧实现会把损坏文件读成空列表，这一步
        # 之后文件就变成只含 y 的新列表，x 的记录永久丢失。
        with open(path, "rb") as fh:
            assert fh.read() == corrupt_bytes

        # 读取同样必须报错，而不是悄悄返回空列表
        with pytest.raises(CorruptDataError):
            storage.load_favorites()

    def test_corrupt_archive_diff_cache_degrades_to_missing(self):
        """归档 diff 是派生数据，损坏时按缺失处理，不阻断列出归档。"""
        from app.core import storage

        storage.save_config_file("cfg.xml", b"v1")
        storage.save_config_file("cfg.xml", b"v2")
        versions = storage.list_archived_versions("cfg.xml")
        assert len(versions) == 1

        diff_path = storage._get_archive_diff_path("cfg.xml", versions[0]["filename"])
        with open(diff_path, "wb") as fh:
            fh.write(b"not json at all")

        assert storage.load_archive_diff("cfg.xml", versions[0]["filename"]) is None
        assert storage.list_archived_versions("cfg.xml")[0]["diff"] is None


class TestMaintenanceGate:
    def test_writes_rejected_during_maintenance(self):
        """维护态期间写入被拒，读取不受影响（§3.5）。"""
        from app.core import storage
        from app.core.errors import MAINTENANCE, AppError
        from app.core.locks import write_gate

        storage.add_favorite("/root/x", "x", "1", "cfg.xml")

        with write_gate.maintenance(timeout=5):
            with pytest.raises(AppError) as excinfo:
                storage.add_favorite("/root/y", "y", "2", "cfg.xml")
            assert excinfo.value.code == MAINTENANCE

            # 读取不经过闸门
            assert len(storage.load_favorites()) == 1

        storage.add_favorite("/root/z", "z", "3", "cfg.xml")
        assert len(storage.load_favorites()) == 2
