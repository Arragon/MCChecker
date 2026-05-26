"""集成测试"""

import json
import os
import tempfile
import pytest


@pytest.fixture(autouse=True)
def setup_env(monkeypatch):
    """设置临时数据目录"""
    tmp = tempfile.mkdtemp()
    import app.core.storage as s
    monkeypatch.setattr(s, "DATA_DIR", tmp)
    monkeypatch.setattr(s, "CONFIGS_DIR", os.path.join(tmp, "configs"))
    monkeypatch.setattr(s, "ARCHIVE_DIR", os.path.join(tmp, "archive"))
    monkeypatch.setattr(s, "MAPPING_FILE", os.path.join(tmp, "config_mapping.json"))
    monkeypatch.setattr(s, "FAVORITES_FILE", os.path.join(tmp, "favorites.json"))
    monkeypatch.setattr(s, "BINDINGS_FILE", os.path.join(tmp, "bindings.json"))
    monkeypatch.setattr(s, "TOOLS_FILE", os.path.join(tmp, "tools.json"))
    monkeypatch.setattr(s, "SCHEDULE_FILE", os.path.join(tmp, "schedule.json"))
    s._ensure_dirs()
    yield
    import shutil
    shutil.rmtree(tmp, ignore_errors=True)


class TestEndToEnd:
    def test_full_workflow(self):
        """完整工作流测试：配置映射 -> 保存文件 -> 解析 -> 收藏 -> 对比"""
        from app.core import storage, parser, differ

        # 1. 创建配置映射
        storage.add_config_mapping("app.json", "http://example.com/app.json")

        # 2. 保存文件
        content_v1 = b'{"timeout": 30, "host": "localhost"}'
        storage.save_config_file("app.json", content_v1)

        # 3. 解析文件
        loaded = storage.load_config_file("app.json")
        assert loaded == content_v1
        tree = parser.parse_file(loaded, "app.json")
        assert tree["attrs"]["type"] == "json"

        # 4. 收藏变量
        values = parser.get_all_values(tree)
        storage.add_favorite("app.json/host", "host", "localhost", "app.json", "服务地址")
        favs = storage.load_favorites()
        assert len(favs) == 1

        # 5. 保存新版本（触发归档）
        content_v2 = b'{"timeout": 60, "host": "0.0.0.0"}'
        storage.save_config_file("app.json", content_v2)

        # 6. 检查归档
        versions = storage.list_archived_versions("app.json")
        assert len(versions) >= 1

        # 7. 对比版本
        archive_content = storage.load_archived_file("app.json", versions[0]["filename"])
        result = differ.compare_versions(archive_content, content_v2, versions[0]["filename"], "app.json")
        assert result["has_changes"] is True

        # 8. 全局搜索
        flat = parser.flatten_tree(tree)
        matches = [n for n in flat if "host" in (n.get("label", "") + str(n.get("value", ""))).lower()]
        assert len(matches) > 0

    def test_xml_workflow(self):
        """XML 工作流测试"""
        from app.core import storage, parser

        xml_content = b'<config><server><host>localhost</host><port>8080</port></server></config>'
        storage.save_config_file("server.xml", xml_content)

        loaded = storage.load_config_file("server.xml")
        tree = parser.parse_file(loaded, "server.xml")
        assert tree["attrs"]["type"] == "xml"

        values = parser.get_all_values(tree)
        assert any("localhost" in v for v in values.values())
        assert any("8080" in v for v in values.values())

    def test_multiple_files(self):
        """多文件管理测试"""
        from app.core import storage

        storage.save_config_file("a.xml", b"<a>1</a>")
        storage.save_config_file("b.json", b'{"b": 2}')

        files = storage.list_config_files()
        assert len(files) == 2
        assert "a.xml" in files
        assert "b.json" in files
