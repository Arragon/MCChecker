"""数据持久化层测试"""

import json
import os
import pytest


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
        from app.core.storage import save_config_file, list_archived_versions

        v1 = b"<root><a>1</a></root>"
        save_config_file("test.xml", v1)
        save_config_file("test.xml", v1)

        versions = list_archived_versions("test.xml")
        assert versions
        diff = versions[0].get("diff")
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
