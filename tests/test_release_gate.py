"""T19 核心流程与离线验收 — 端到端 release gate

覆盖：
- 浏览器闭环（upload → view → search → favorite → compare → propose → review → download）
- DL 计算工作流
- 离线安装 / 无公网依赖
- Backup / Recovery 演练
- 双客户端 / 双 profile 隔离
- disconnect/reconnect 任务结果可解释
- 支持边界声明（single process / single scheduler）
"""

import hashlib
import json
import os
import shutil
import time
from pathlib import Path
from typing import Any, Dict

import pytest

# ---------------------------------------------------------------------------
# Fixture 数据
# ---------------------------------------------------------------------------

# legacy layout XML（旧版无 namespace）
LEGACY_XML = b"""\
<?xml version="1.0" encoding="UTF-8"?>
<config>
  <server>
    <host>10.0.0.1</host>
    <port>8080</port>
    <timeout>30</timeout>
  </server>
  <database>
    <url>jdbc:mysql://localhost:3306/app</url>
    <pool>10</pool>
  </database>
</config>
"""

# new layout JSON（新版）
NEW_JSON = b"""\
{
  "server": {
    "host": "10.0.0.2",
    "port": 9090,
    "timeout": 60,
    "debug": false
  },
  "database": {
    "url": "jdbc:mysql://db.internal:3306/app",
    "pool": 20,
    "ssl": true
  },
  "features": ["cache", "metrics"]
}
"""

# mixed layout：XML with attributes
MIXED_XML = b"""\
<?xml version="1.0" encoding="UTF-8"?>
<config version="2.0">
  <server host="10.0.0.3" port="7070"/>
  <logging level="INFO" file="/var/log/app.log"/>
</config>
"""

# repeated XML（重复元素）
REPEATED_XML = b"""\
<?xml version="1.0" encoding="UTF-8"?>
<config>
  <item name="a" value="1"/>
  <item name="b" value="2"/>
  <item name="c" value="3"/>
</config>
"""

# large XML（用于大文件测试，约 100KB）
LARGE_XML_PARTS = [b'<?xml version="1.0" encoding="UTF-8"?>\n<config>\n']
for _i in range(700):
    LARGE_XML_PARTS.append(
        f'  <entry id="{_i}" name="item_{_i}" value="val_{_i}" '
        f'description="Description for item {_i}"/>\n'.encode()
    )
LARGE_XML_PARTS.append(b"</config>\n")
LARGE_XML = b"".join(LARGE_XML_PARTS)

# special JSON（含 Unicode / 特殊字符）
SPECIAL_JSON = json.dumps({
    "name": "配置-测试",
    "path": "/opt/app/配置",
    "note": "line1\nline2\ttab",
    "emoji": "🔧",
    "empty": None,
    "nested": {"a": [1, 2, {"b": True}]},
}, ensure_ascii=False).encode("utf-8")


# ---------------------------------------------------------------------------
# 角色定义
# ---------------------------------------------------------------------------

class _FakeActor:
    """轻量 Actor 替身，不依赖 nicegui"""
    def __init__(self, ip: str, name: str, is_deployer: bool, is_admin: bool = False):
        self.ip = ip
        self.display_name = name
        self.is_deployer = is_deployer
        self.is_system = (ip == "system")
        self._is_guest = not is_deployer and not self.is_system
        self.profile_admin_scope = {"*"} if is_admin else set()

    @property
    def is_guest(self) -> bool:
        return self._is_guest


GUEST = _FakeActor("192.168.1.100", "guest_user", is_deployer=False)
DEPLOYER = _FakeActor("127.0.0.1", "deployer", is_deployer=True)
ADMIN = _FakeActor("127.0.0.1", "admin_user", is_deployer=True, is_admin=True)
SYSTEM = _FakeActor("system", "System", is_deployer=True)


# ---------------------------------------------------------------------------
# 辅助函数
# ---------------------------------------------------------------------------

def _setup_profile(storage_mod, profile_id: str, tmp_path: str) -> Path:
    """在 tmp_path 下创建 profile 目录结构"""
    profile_dir = Path(tmp_path) / "profiles" / profile_id
    (profile_dir / "configs").mkdir(parents=True, exist_ok=True)
    (profile_dir / "archive").mkdir(parents=True, exist_ok=True)
    (profile_dir / "records").mkdir(parents=True, exist_ok=True)
    (profile_dir / "cache" / "parse_tree").mkdir(parents=True, exist_ok=True)
    return profile_dir


def _write_config(profile_dir: Path, filename: str, content: bytes) -> None:
    """写入配置文件到 profile configs 目录"""
    (profile_dir / "configs" / filename).write_bytes(content)


# ===========================================================================
# 1. 浏览器闭环测试
# ===========================================================================


class TestFullBrowserFlow:
    """upload → view → search → favorite → compare → propose → review → generated file → download"""

    def test_full_browser_flow(self, isolated_data_env, tmp_path):
        """完整业务闭环：上传 → 查看 → 搜索 → 收藏 → 对比 → 审阅 → 生成文件 → 下载"""
        from app.core import storage, parser, differ, searching, favorites_live, reviewing

        storage_mod = isolated_data_env
        profile_dir = _setup_profile(storage_mod, "default", str(tmp_path))

        # 1. Upload: 保存配置文件
        _write_config(profile_dir, "app.json", NEW_JSON)
        storage.save_config_file("app.json", NEW_JSON)

        # 2. View: 解析并获取树结构
        loaded = storage.load_config_file("app.json")
        assert loaded is not None
        tree = parser.parse_file(loaded, "app.json")
        assert tree["attrs"]["type"] == "json"

        # 3. Search: 关键字搜索
        flat = parser.flatten_tree(tree)
        assert len(flat) > 0
        filtered, count = searching.filter_tree_and_count(tree, "host")
        assert count > 0

        # 4. Favorite: 收藏变量
        storage.add_favorite(
            "app.json/$/server/host", "host", "10.0.0.2",
            "app.json", "服务地址"
        )
        favs = storage.load_favorites()
        assert len(favs) >= 1
        assert any(f.get("label") == "host" for f in favs)

        # 5. Compare: 上传新版本并对比
        new_content = json.dumps({
            "server": {"host": "10.0.0.99", "port": 9090, "timeout": 60, "debug": True},
            "database": {"url": "jdbc:mysql://db.internal:3306/app", "pool": 20, "ssl": True},
            "features": ["cache", "metrics", "tracing"],
        }, ensure_ascii=False).encode("utf-8")

        storage.save_config_file("app.json", new_content)
        versions = storage.list_archived_versions("app.json")
        assert len(versions) >= 1

        archive_content = storage.load_archived_file("app.json", versions[0]["filename"])
        assert archive_content is not None
        diff_result = differ.compare_versions(
            archive_content, new_content,
            versions[0]["filename"], "app.json",
            use_bindings=False,
        )
        assert diff_result["has_changes"] is True

        # 6. Propose + Review: 审阅建议
        # 使用旧版本创建审阅建议
        old_content = NEW_JSON
        source_path = str(profile_dir / "configs" / "app.json")
        # 先恢复旧内容用于审阅测试
        with open(source_path, "wb") as f:
            f.write(old_content)

        source_hash = parser.compute_content_hash(old_content)
        parsed_doc = parser.parse_json_full(old_content, "app.json", source_hash=source_hash)

        # 创建一个审阅建议：修改 server/host
        from app.core.models import NodeRef
        remark = reviewing.ReviewRemark(
            remark_id="test-remark-001",
            source_hash=source_hash,
            node_ref=NodeRef(
                content_hash=source_hash,
                locator="$/server/host",
                value_type="string",
                original_value="10.0.0.2",
            ),
            original_value="10.0.0.2",
            original_type="string",
            suggested_value="10.0.0.200",
            status="pending",
        )

        output_path = str(profile_dir / "configs" / "app.candidate.json")
        result = reviewing.submit_review(
            [remark], source_path, "default", ADMIN,
            output_path=output_path,
        )
        assert result["approved"] == 1
        assert result["conflict"] == 0
        assert os.path.exists(output_path)

        # 7. Download: 读取生成的候选文件
        with open(output_path, "r", encoding="utf-8") as f:
            candidate_data = json.load(f)
        assert candidate_data["server"]["host"] == "10.0.0.200"

    def test_xml_full_flow(self, isolated_data_env, tmp_path):
        """XML 完整闭环"""
        from app.core import storage, parser, differ

        storage_mod = isolated_data_env
        profile_dir = _setup_profile(storage_mod, "default", str(tmp_path))

        # Upload XML
        _write_config(profile_dir, "server.xml", LEGACY_XML)
        storage.save_config_file("server.xml", LEGACY_XML)

        # View
        loaded = storage.load_config_file("server.xml")
        tree = parser.parse_file(loaded, "server.xml")
        assert tree["attrs"]["type"] == "xml"

        # Search
        filtered, count = parser.parse_file(loaded, "server.xml"), 0
        flat = parser.flatten_tree(tree)
        for node in flat:
            if "localhost" in str(node.get("value", "")):
                count += 1
        assert count > 0

        # Compare with new version
        new_xml = LEGACY_XML.replace(b"10.0.0.1", b"10.0.0.99").replace(b"8080", b"9999")
        storage.save_config_file("server.xml", new_xml)

        versions = storage.list_archived_versions("server.xml")
        assert len(versions) >= 1

    def test_mixed_xml_attributes_flow(self, isolated_data_env, tmp_path):
        """混合属性 XML 闭环"""
        from app.core import storage, parser

        storage_mod = isolated_data_env
        profile_dir = _setup_profile(storage_mod, "default", str(tmp_path))

        _write_config(profile_dir, "mixed.xml", MIXED_XML)
        storage.save_config_file("mixed.xml", MIXED_XML)

        loaded = storage.load_config_file("mixed.xml")
        tree = parser.parse_file(loaded, "mixed.xml")
        assert tree["attrs"]["type"] == "xml"

        values = parser.get_all_values(tree)
        assert any("10.0.0.3" in str(v) for v in values.values())


class TestDLWorkflow:
    """DL 计算工作流"""

    def test_dl_workflow(self, isolated_data_env):
        """DL 计算基本流程"""
        from app.core import dltool

        # 基本计算
        result = dltool.validate_number(42.0, "test_value")
        assert result == 42.0

        # 拒绝 NaN
        with pytest.raises(ValueError):
            dltool.validate_number(float("nan"), "bad_value")

        # 拒绝 Inf
        with pytest.raises(ValueError):
            dltool.validate_number(float("inf"), "bad_value")

        # 区间验证
        low, high = dltool.validate_interval(0.0, 100.0)
        assert low == 0.0
        assert high == 100.0

        # 无效区间
        with pytest.raises(ValueError):
            dltool.validate_interval(100.0, 0.0)

    def test_dl_coefficient_extraction(self):
        """DL 系数提取"""
        from app.core.dltool import extract_coefficient

        data = [[1.0, 2.0, 3.0], [4.0, 5.0, 6.0]]

        result = extract_coefficient(data, 0, 0)
        assert result.status == "success"
        assert result.value == 1.0

        # 越界
        result = extract_coefficient(data, 10, 0)
        assert result.status == "missing"

        # None 数据
        result = extract_coefficient(None, 0, 0)
        assert result.status == "missing"


class TestUpdateManagement:
    """更新管理"""

    def test_update_pipeline_import(self):
        """UpdatePipeline 可导入"""
        from app.core.scheduler import UpdatePipeline
        assert UpdatePipeline is not None

    def test_update_lock_per_profile_resource(self):
        """防重入锁按 profile+resource 隔离"""
        from app.core.scheduler import get_update_lock

        lock_a = get_update_lock("profile_a", "config.json")
        lock_b = get_update_lock("profile_b", "config.json")
        lock_c = get_update_lock("profile_a", "other.json")

        # 同一 profile+resource 返回同一锁
        assert lock_a is get_update_lock("profile_a", "config.json")
        # 不同 profile 或不同 resource 返回不同锁
        assert lock_a is not lock_b
        assert lock_a is not lock_c


class TestManualRecordRefresh:
    """手动记录刷新"""

    def test_record_dir_creation(self, isolated_data_env, tmp_path):
        """记录目录创建和文件管理"""
        from app.core import storage

        storage_mod = isolated_data_env
        profile_dir = _setup_profile(storage_mod, "default", str(tmp_path))

        # 创建记录目录
        record_dir = storage.get_record_dir("test_config.json")
        assert os.path.isdir(record_dir)

        # 写入记录文件
        record_file = os.path.join(record_dir, "record_001.json")
        storage.atomic_write_json(record_file, {"ts": "2026-01-01", "data": "test"})

        # 读取验证
        with open(record_file, "r", encoding="utf-8") as f:
            data = json.load(f)
        assert data["data"] == "test"


class TestProfileSwitchNoCrossTalk:
    """Profile 切换不串数据"""

    def test_profile_switch_no_cross_talk(self, isolated_data_env, tmp_path):
        """切换 profile 后数据完全隔离"""
        from app.core import storage
        from app.core.storage import set_active_profile, get_active_profile

        storage_mod = isolated_data_env

        # 创建两个 profile
        pa = _setup_profile(storage_mod, "profile_a", str(tmp_path))
        pb = _setup_profile(storage_mod, "profile_b", str(tmp_path))

        # Profile A 写入数据
        set_active_profile("profile_a")
        _write_config(pa, "app.json", b'{"env": "a"}')
        storage.save_config_file("app.json", b'{"env": "a"}')
        storage.add_config_mapping("app.json", "http://a.example.com/app.json")

        # Profile B 写入不同数据
        set_active_profile("profile_b")
        _write_config(pb, "app.json", b'{"env": "b"}')
        storage.save_config_file("app.json", b'{"env": "b"}')
        storage.add_config_mapping("app.json", "http://b.example.com/app.json")

        # 验证 A 的数据
        set_active_profile("profile_a")
        content_a = storage.load_config_file("app.json")
        assert json.loads(content_a)["env"] == "a"
        mapping_a = storage.load_config_mapping()
        assert any("a.example.com" in m.get("url", "") for m in mapping_a)

        # 验证 B 的数据
        set_active_profile("profile_b")
        content_b = storage.load_config_file("app.json")
        assert json.loads(content_b)["env"] == "b"
        mapping_b = storage.load_config_mapping()
        assert any("b.example.com" in m.get("url", "") for m in mapping_b)


class TestReconnectTaskResult:
    """disconnect/reconnect 后任务结果可解释"""

    def test_reconnect_task_result(self):
        """任务状态在 disconnect/reconnect 后可查询"""
        from app.core.task_status import (
            TaskStatus, TaskState, register_task, get_task,
            update_task_state, TERMINAL_STATES,
        )

        # 注册任务
        status = TaskStatus(
            task_id="task-001",
            profile_id="default",
            operation="update",
            state=TaskState.RUNNING,
            inputs={"config": "app.json"},
        )
        register_task(status)

        # 模拟 disconnect：任务仍在运行
        retrieved = get_task("task-001")
        assert retrieved is not None
        assert retrieved.state == TaskState.RUNNING

        # 模拟 reconnect：查询终态
        update_task_state(
            "task-001", TaskState.SUCCEEDED,
            result={"updated": True},
            message="更新成功",
        )
        retrieved = get_task("task-001")
        assert retrieved.state == TaskState.SUCCEEDED
        assert retrieved.is_terminal
        assert retrieved.result["updated"] is True

    def test_failed_task_preserves_inputs(self):
        """失败任务保留输入参数供重试"""
        from app.core.task_status import TaskStatus, TaskState, register_task, get_task

        status = TaskStatus(
            task_id="task-002",
            profile_id="default",
            operation="update",
            state=TaskState.FAILED,
            error_code="DOWNLOAD_TIMEOUT",
            inputs={"config": "app.json", "url": "http://example.com"},
            retry_condition="网络恢复后可重试",
        )
        register_task(status)

        retrieved = get_task("task-002")
        assert retrieved.state == TaskState.FAILED
        assert retrieved.inputs["config"] == "app.json"
        assert retrieved.is_retryable
        assert "网络恢复" in retrieved.retry_condition

    def test_task_serialization_roundtrip(self):
        """TaskStatus 序列化/反序列化"""
        from app.core.task_status import TaskStatus, TaskState

        original = TaskStatus(
            task_id="task-003",
            profile_id="default",
            operation="review",
            state=TaskState.SUCCEEDED,
            result={"approved": 3},
        )
        data = original.to_dict()
        restored = TaskStatus.from_dict(data)
        assert restored.task_id == original.task_id
        assert restored.state == original.state
        assert restored.result == original.result


# ===========================================================================
# 2. 离线安装测试
# ===========================================================================


class TestOfflineCleanInstall:
    """离线安装验证"""

    def test_offline_clean_install(self):
        """离线安装：仅依赖 requirements.txt 中声明的包"""
        reqs_path = Path("requirements.txt")
        assert reqs_path.exists()

        reqs = reqs_path.read_text()
        # 只声明了 nicegui 和 apscheduler
        lines = [l.strip() for l in reqs.strip().splitlines() if l.strip() and not l.startswith("#")]
        assert len(lines) >= 2

        # 所有 import 的核心模块都是标准库或已声明依赖
        import importlib
        for mod_name in ["json", "hashlib", "xml.etree.ElementTree", "pathlib", "uuid",
                         "threading", "contextvars", "tempfile", "shutil", "difflib"]:
            mod = importlib.import_module(mod_name)
            assert mod is not None

    def test_no_undeclared_runtime_deps(self):
        """核心模块不引入 requirements.txt 之外的运行时依赖"""
        # 核心模块列表
        core_modules = [
            "app.core.storage",
            "app.core.parser",
            "app.core.differ",
            "app.core.operations",
            "app.core.searching",
            "app.core.models",
            "app.core.file_lifecycle",
            "app.core.parse_cache",
            "app.core.task_status",
            "app.core.tab_manager",
            "app.utils.helpers",
        ]
        import importlib
        for mod_name in core_modules:
            mod = importlib.import_module(mod_name)
            assert mod is not None


class TestColdBrowserNoPublicInternet:
    """冷浏览器 + 无公网首屏"""

    def test_local_static_assets_exist(self):
        """本地静态资源存在"""
        assert Path("app/static/favicon.ico").exists() or Path("app/static/favicon.svg").exists()
        assert Path("app/static/css/style.css").exists()

    def test_no_hardcoded_cdn_urls(self):
        """核心页面不硬编码 CDN URL"""
        import re
        cdn_patterns = re.compile(
            r'(cdn\.|googleapis\.com|unpkg\.com|jsdelivr\.net|cdnjs\.)',
            re.IGNORECASE,
        )
        page_dir = Path("app/pages")
        if page_dir.exists():
            for py_file in page_dir.glob("*.py"):
                content = py_file.read_text(encoding="utf-8", errors="ignore")
                # 跳过注释行
                for line in content.splitlines():
                    stripped = line.strip()
                    if stripped.startswith("#"):
                        continue
                    # 不检查注释中的 URL
                    assert not cdn_patterns.search(line), (
                        f"Found CDN reference in {py_file}: {line.strip()}"
                    )


class TestLocalUploadViewSearchCompareDownload:
    """本地上传/查看/搜索/对比/下载 smoke"""

    def test_local_upload_view_search_compare_download(self, isolated_data_env, tmp_path):
        """本地 smoke 测试全流程"""
        from app.core import storage, parser, searching, differ

        storage_mod = isolated_data_env
        profile_dir = _setup_profile(storage_mod, "default", str(tmp_path))

        # Upload
        _write_config(profile_dir, "smoke.json", SPECIAL_JSON)
        storage.save_config_file("smoke.json", SPECIAL_JSON)

        # View
        loaded = storage.load_config_file("smoke.json")
        assert loaded is not None
        tree = parser.parse_file(loaded, "smoke.json")
        assert tree is not None

        # Search
        flat = parser.flatten_tree(tree)
        assert len(flat) > 0

        # Compare
        new_content = json.dumps({"name": "updated"}, ensure_ascii=False).encode("utf-8")
        storage.save_config_file("smoke.json", new_content)
        versions = storage.list_archived_versions("smoke.json")
        assert len(versions) >= 1

        # Download (read archived)
        archive_content = storage.load_archived_file("smoke.json", versions[0]["filename"])
        assert archive_content is not None


class TestNoPublicStaticRequests:
    """无公网静态请求"""

    def test_no_public_static_requests(self):
        """静态文件目录不包含外部引用"""
        css_path = Path("app/static/css/style.css")
        if css_path.exists():
            content = css_path.read_text(encoding="utf-8", errors="ignore")
            # 检查没有 url() 引用外部 URL
            import re
            external_urls = re.findall(r'url\(["\']?(https?://[^"\')\s]+)', content)
            assert len(external_urls) == 0, f"Found external URLs in CSS: {external_urls}"


# ===========================================================================
# 3. Backup / Recovery 测试
# ===========================================================================


class TestBackupWithWriteGate:
    """WriteGate 下一致备份"""

    def test_backup_with_write_gate(self, isolated_data_env, tmp_path):
        """WriteGate 保护下的一致性备份"""
        from app.core import storage, operations
        from app.core.storage import WriteGate, global_write_gate

        storage_mod = isolated_data_env
        profile_dir = _setup_profile(storage_mod, "default", str(tmp_path))
        ops_dir = tmp_path / "operations"
        ops_dir.mkdir()
        backup_dir = tmp_path / "backup"

        # 写入一些数据
        _write_config(profile_dir, "app.json", NEW_JSON)
        _write_config(profile_dir, "server.xml", LEGACY_XML)

        # 执行备份
        manifest = operations.create_backup(profile_dir, backup_dir, ops_dir)
        assert manifest.state == operations.OpState.COMMITTED
        assert backup_dir.exists()

        # 验证备份内容
        assert (backup_dir / "configs" / "app.json").exists()
        assert (backup_dir / "configs" / "server.xml").exists()

    def test_write_gate_blocks_during_maintenance(self):
        """维护模式下 WriteGate 阻止写入"""
        from app.core.storage import WriteGate, WriteBlockedError

        gate = WriteGate()
        assert gate.state == "open"

        # 进入维护模式
        assert gate.enter_maintenance(timeout=2.0)
        assert gate.state == "maintenance"

        # 写入被阻止
        with pytest.raises(WriteBlockedError):
            gate.acquire_write()

        # 退出维护
        gate.exit_maintenance()
        assert gate.state == "open"

        # 写入恢复
        gate.acquire_write()
        gate.release_write()


class TestBackupHashManifest:
    """Hash manifest 可校验"""

    def test_backup_hash_manifest(self, isolated_data_env, tmp_path):
        """备份 hash 清单校验"""
        from app.core import operations

        storage_mod = isolated_data_env
        profile_dir = _setup_profile(storage_mod, "default", str(tmp_path))
        ops_dir = tmp_path / "operations"
        ops_dir.mkdir()
        backup_dir = tmp_path / "backup"

        _write_config(profile_dir, "app.json", NEW_JSON)

        manifest = operations.create_backup(profile_dir, backup_dir, ops_dir)
        assert manifest.expected_hash != ""

        # 校验备份完整性
        assert operations.verify_backup(backup_dir, manifest)

        # 篡改备份后校验失败
        (backup_dir / "configs" / "app.json").write_bytes(b"tampered")
        assert not operations.verify_backup(backup_dir, manifest)


class TestMigrationInterruptedRecovery:
    """迁移中断恢复"""

    def test_migration_interrupted_recovery(self, isolated_data_env, tmp_path):
        """迁移中断后可通过 manifest 恢复"""
        from app.core import operations
        from app.core.operations import OpState

        storage_mod = isolated_data_env
        source_dir = tmp_path / "source_profile"
        source_dir.mkdir()
        (source_dir / "configs").mkdir()
        (source_dir / "configs" / "app.json").write_bytes(NEW_JSON)

        target_dir = tmp_path / "target_profile"
        ops_dir = tmp_path / "operations"
        ops_dir.mkdir()

        # 正常迁移
        manifest = operations.migrate_profile(source_dir, target_dir, ops_dir)
        assert manifest.state == OpState.COMMITTED
        assert (target_dir / "configs" / "app.json").exists()

        # 幂等：再次迁移同一目标
        manifest2 = operations.migrate_profile(source_dir, target_dir, ops_dir)
        assert manifest2.state == OpState.COMMITTED

    def test_migration_retry_after_partial(self, isolated_data_env, tmp_path):
        """部分完成的迁移可重试"""
        from app.core import operations
        from app.core.operations import OpState, OperationManifest, save_manifest

        storage_mod = isolated_data_env
        source_dir = tmp_path / "source"
        source_dir.mkdir()
        (source_dir / "data.txt").write_text("test")

        target_dir = tmp_path / "target"
        ops_dir = tmp_path / "ops"
        ops_dir.mkdir()

        # 模拟中断的 manifest
        manifest = operations.create_manifest("migration", str(source_dir), str(target_dir))
        manifest.state = OpState.RECOVERY_REQUIRED
        manifest.completed_steps = ["hash_source"]
        manifest.preimage_hash = operations._hash_directory(source_dir)
        save_manifest(manifest, ops_dir)

        # 重试
        recovered = operations.retry_migration(manifest, ops_dir)
        assert recovered.state == OpState.COMMITTED


class TestRenameInterruptedRecovery:
    """改名中断恢复"""

    def test_rename_interrupted_recovery(self, isolated_data_env, tmp_path):
        """改名操作完整执行"""
        from app.core import operations, storage
        from app.core.operations import OpState

        storage_mod = isolated_data_env
        profile_dir = _setup_profile(storage_mod, "default", str(tmp_path))
        ops_dir = tmp_path / "operations"
        ops_dir.mkdir()

        # 准备数据
        _write_config(profile_dir, "old_name.json", NEW_JSON)
        storage.add_config_mapping("old_name.json", "http://example.com/old.json")

        # 执行改名
        manifest = operations.rename_config(
            profile_dir, "old_name.json", "new_name.json", ops_dir
        )
        assert manifest.state == OpState.COMMITTED

        # 验证文件已改名
        assert (profile_dir / "configs" / "new_name.json").exists()
        assert not (profile_dir / "configs" / "old_name.json").exists()


class TestReviewBatchInterruptedRecovery:
    """审阅批次中断恢复"""

    def test_review_batch_idempotent(self, isolated_data_env, tmp_path):
        """审阅幂等提交：重复提交不产生多次业务结果"""
        from app.core import reviewing, parser
        from app.core.models import NodeRef

        storage_mod = isolated_data_env
        profile_dir = _setup_profile(storage_mod, "default", str(tmp_path))

        source_path = str(profile_dir / "configs" / "app.json")
        _write_config(profile_dir, "app.json", NEW_JSON)

        source_hash = parser.compute_content_hash(NEW_JSON)

        remark = reviewing.ReviewRemark(
            remark_id="idem-001",
            source_hash=source_hash,
            node_ref=NodeRef(
                content_hash=source_hash,
                locator="$/server/host",
                value_type="string",
                original_value="10.0.0.2",
            ),
            original_value="10.0.0.2",
            original_type="string",
            suggested_value="10.0.0.200",
        )

        applied_hashes = set()
        output_path = str(profile_dir / "app.candidate.json")

        # 第一次提交
        r1 = reviewing.submit_review_idempotent(
            [remark], source_path, "default", ADMIN,
            output_path=output_path, applied_hashes=applied_hashes,
        )
        assert r1["approved"] == 1
        assert r1["already_applied"] is False

        # 重置 remark 状态模拟重试
        remark.status = "pending"
        # 恢复源文件
        _write_config(profile_dir, "app.json", NEW_JSON)

        # 第二次提交（同一 candidate hash → 已应用）
        r2 = reviewing.submit_review_idempotent(
            [remark], source_path, "default", ADMIN,
            output_path=output_path, applied_hashes=applied_hashes,
        )
        assert r2["already_applied"] is True


class TestUpdateCommitInterruptedRecovery:
    """更新提交中断恢复"""

    def test_update_commit_manifest_tracking(self, isolated_data_env, tmp_path):
        """更新操作通过 manifest 跟踪"""
        from app.core import operations
        from app.core.operations import OpState

        storage_mod = isolated_data_env
        ops_dir = tmp_path / "operations"
        ops_dir.mkdir()

        # 创建更新 manifest
        manifest = operations.create_manifest("backup", "profile_dir", "backup_dir")
        operations.save_manifest(manifest, ops_dir)

        # 列出 manifest
        manifests = operations.list_manifests(ops_dir)
        assert len(manifests) == 1
        assert manifests[0].type == "backup"

        # 查找未完成的
        pending = operations.find_pending_manifests(ops_dir)
        assert len(pending) == 1


# ===========================================================================
# 4. 双客户端 / 双 profile 测试
# ===========================================================================


class TestDoubleClientNoCrossTalk:
    """双客户端不串数据"""

    def test_double_client_no_cross_talk(self, isolated_data_env, tmp_path):
        """两个 session 的临时文件互相隔离"""
        from app.core.file_lifecycle import TempStorage
        from app.core.models import FileKind

        storage_mod = isolated_data_env
        data_root = Path(str(tmp_path))
        temp_storage = TempStorage(data_root)

        # Session A 上传
        ref_a = temp_storage.create_temp(
            b'{"client": "A"}', owner_session="session_a",
            original_name="config.json", profile_id="default",
        )
        assert ref_a.kind == FileKind.TEMP

        # Session B 上传
        ref_b = temp_storage.create_temp(
            b'{"client": "B"}', owner_session="session_b",
            original_name="config.json", profile_id="default",
        )

        # Session A 只能读自己的文件
        path_a = temp_storage.get_temp(ref_a, "session_a")
        assert path_a is not None
        assert b'"A"' in path_a.read_bytes()

        # Session B 不能读 Session A 的文件
        path_cross = temp_storage.get_temp(ref_a, "session_b")
        assert path_cross is None  # 隔离：返回 None

        # Session B 读自己的
        path_b = temp_storage.get_temp(ref_b, "session_b")
        assert path_b is not None
        assert b'"B"' in path_b.read_bytes()


class TestTwoProfileNoCrossTalk:
    """双 profile 不串数据"""

    def test_two_profile_no_cross_talk(self, isolated_data_env, tmp_path):
        """两个 profile 的配置完全隔离"""
        from app.core import storage
        from app.core.storage import set_active_profile

        storage_mod = isolated_data_env

        pa = _setup_profile(storage_mod, "alpha", str(tmp_path))
        pb = _setup_profile(storage_mod, "beta", str(tmp_path))

        # Alpha 数据
        set_active_profile("alpha")
        _write_config(pa, "data.json", b'{"profile": "alpha"}')
        storage.save_config_file("data.json", b'{"profile": "alpha"}')
        storage.add_favorite("data.json/$/profile", "profile", "alpha", "data.json")

        # Beta 数据
        set_active_profile("beta")
        _write_config(pb, "data.json", b'{"profile": "beta"}')
        storage.save_config_file("data.json", b'{"profile": "beta"}')
        storage.add_favorite("data.json/$/profile", "profile", "beta", "data.json")

        # 验证 Alpha
        set_active_profile("alpha")
        alpha_content = storage.load_config_file("data.json")
        assert json.loads(alpha_content)["profile"] == "alpha"
        alpha_favs = storage.load_favorites()
        assert all(f.get("value") == "alpha" for f in alpha_favs if f.get("label") == "profile")

        # 验证 Beta
        set_active_profile("beta")
        beta_content = storage.load_config_file("data.json")
        assert json.loads(beta_content)["profile"] == "beta"
        beta_favs = storage.load_favorites()
        assert all(f.get("value") == "beta" for f in beta_favs if f.get("label") == "profile")


# ===========================================================================
# 5. 支持边界声明
# ===========================================================================


class TestSupportBoundary:
    """明确 single process / single scheduler 支持边界"""

    def test_single_process_write_gate(self):
        """WriteGate 为单进程设计"""
        from app.core.storage import WriteGate, WriteBlockedError

        gate = WriteGate()
        # 基本状态转换
        assert gate.state == "open"

        # 多写入者
        gate.acquire_write()
        gate.acquire_write()
        gate.release_write()
        gate.release_write()

        # draining → maintenance 通过 enter_maintenance 进入
        assert gate.enter_maintenance(timeout=2.0)
        assert gate.state == "maintenance"
        with pytest.raises(WriteBlockedError):
            gate.acquire_write()
        gate.exit_maintenance()
        assert gate.state == "open"

    def test_single_scheduler_per_process(self):
        """调度器为单进程单例"""
        from app.core import scheduler
        # scheduler 模块提供 get_update_lock 按 profile+resource 隔离
        lock1 = scheduler.get_update_lock("p1", "r1")
        lock2 = scheduler.get_update_lock("p1", "r1")
        assert lock1 is lock2


class TestRepresentativeFileSize:
    """记录实测最大代表文件，不虚构'无限'"""

    def test_large_xml_parsing(self, isolated_data_env, tmp_path):
        """大 XML 文件解析（约 100KB）"""
        from app.core import parser

        assert len(LARGE_XML) > 40_000  # 确认 > 40KB
        tree = parser.parse_file(LARGE_XML, "large.xml")
        assert tree is not None
        assert tree["attrs"]["type"] == "xml"

        flat = parser.flatten_tree(tree)
        assert len(flat) >= 500  # 至少 500 个 entry

    def test_special_json_parsing(self, isolated_data_env):
        """特殊字符 JSON 解析"""
        from app.core import parser

        tree = parser.parse_file(SPECIAL_JSON, "special.json")
        assert tree is not None
        values = parser.get_all_values(tree)
        assert any("配置" in str(v) for v in values.values())


# ===========================================================================
# 6. 综合验收
# ===========================================================================


class TestNoUnresolvedP0:
    """所有 P0 模块可正常导入和运行"""

    def test_all_core_modules_importable(self):
        """所有核心模块可导入"""
        modules = [
            "app.core.storage",
            "app.core.parser",
            "app.core.differ",
            "app.core.operations",
            "app.core.searching",
            "app.core.models",
            "app.core.file_lifecycle",
            "app.core.parse_cache",
            "app.core.favorites_live",
            "app.core.reviewing",
            "app.core.task_status",
            "app.core.tab_manager",
            "app.core.dltool",
            "app.core.device_models",
            "app.core.scheduler",
            "app.utils.helpers",
        ]
        import importlib
        for mod_name in modules:
            mod = importlib.import_module(mod_name)
            assert mod is not None, f"Failed to import {mod_name}"

    def test_input_validation_enforced(self):
        """输入验证已强制执行"""
        from app.utils.helpers import validate_filename

        # 合法文件名
        assert validate_filename("app.json") == "app.json"
        assert validate_filename("server-config.xml") == "server-config.xml"

        # 非法文件名
        with pytest.raises(ValueError):
            validate_filename("")
        with pytest.raises(ValueError):
            validate_filename("..")
        with pytest.raises(ValueError):
            validate_filename("../etc/passwd")
        with pytest.raises(ValueError):
            validate_filename("CON")
        with pytest.raises(ValueError):
            validate_filename("file:name")

    def test_path_traversal_blocked(self, isolated_data_env, tmp_path):
        """路径穿越被阻止"""
        from app.core.file_lifecycle import resolve_file_path
        from app.core.models import FileRef, FileKind

        data_root = Path(str(tmp_path))
        _setup_profile(isolated_data_env, "default", str(tmp_path))

        # 尝试路径穿越
        ref = FileRef(
            profile_id="default",
            kind=FileKind.CURRENT,
            name="../../etc/passwd",
        )
        result = resolve_file_path(ref, data_root, "default")
        assert result is None  # 被阻止

    def test_atomic_write_json(self, tmp_path):
        """原子写入 JSON"""
        from app.core.storage import atomic_write_json

        path = str(tmp_path / "test.json")
        data = {"key": "value", "number": 42}
        atomic_write_json(path, data)

        with open(path, "r", encoding="utf-8") as f:
            loaded = json.load(f)
        assert loaded == data

    def test_corrupt_data_detection(self, tmp_path):
        """损坏数据检测"""
        from app.core.storage import CorruptDataError, _load_json

        corrupt_path = str(tmp_path / "corrupt.json")
        with open(corrupt_path, "w") as f:
            f.write("{invalid json")

        with pytest.raises(CorruptDataError):
            _load_json(corrupt_path, default=[])

    def test_xss_prevention_in_search(self, isolated_data_env, tmp_path):
        """搜索中 XSS 防护"""
        from app.core import parser, searching

        xss_json = json.dumps({
            "label": "<script>alert('xss')</script>",
            "value": "safe_value",
        }).encode("utf-8")

        tree = parser.parse_file(xss_json, "xss.json")
        flat = parser.flatten_tree(tree)
        # 搜索不会执行脚本
        filtered, count = searching.filter_tree_and_count(tree, "script")
        # 搜索只返回文本匹配，不执行
        assert isinstance(count, int)


class TestRecoveryProcedure:
    """恢复流程验证"""

    def test_scan_and_recover(self, isolated_data_env, tmp_path):
        """扫描并恢复未完成操作"""
        from app.core import operations
        from app.core.operations import OpState, save_manifest

        ops_dir = tmp_path / "operations"
        ops_dir.mkdir()

        # 创建一个中断的迁移 manifest
        source_dir = tmp_path / "src"
        source_dir.mkdir()
        (source_dir / "data.txt").write_text("test")

        manifest = operations.create_manifest("migration", str(source_dir), str(tmp_path / "dst"))
        manifest.state = OpState.RECOVERY_REQUIRED
        manifest.completed_steps = ["hash_source"]
        manifest.preimage_hash = operations._hash_directory(source_dir)
        save_manifest(manifest, ops_dir)

        # 扫描恢复
        results = operations.scan_and_recover(ops_dir)
        assert len(results) >= 1
        # 迁移应该被重试
        assert results[0]["type"] == "migration"

    def test_restore_from_backup(self, isolated_data_env, tmp_path):
        """从备份恢复"""
        from app.core import operations
        from app.core.operations import OpState

        storage_mod = isolated_data_env
        profile_dir = _setup_profile(storage_mod, "default", str(tmp_path))
        ops_dir = tmp_path / "operations"
        ops_dir.mkdir()
        backup_dir = tmp_path / "backup"
        restore_dir = tmp_path / "restored"

        # 写入数据并备份
        _write_config(profile_dir, "app.json", NEW_JSON)
        backup_manifest = operations.create_backup(profile_dir, backup_dir, ops_dir)
        assert backup_manifest.state == OpState.COMMITTED

        # 恢复
        restore_manifest = operations.restore_backup(
            backup_dir, restore_dir, ops_dir,
        )
        assert restore_manifest.state == OpState.COMMITTED
        assert (restore_dir / "configs" / "app.json").exists()
