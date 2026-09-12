"""操作模块多工况测试

覆盖：manifest CRUD、profile 迁移、配置改名、quarantine 删除恢复、
备份恢复、恢复扫描等边界场景。
"""

import json
import os
import pytest
import tempfile
import shutil
from pathlib import Path

from app.core.operations import (
    OpState,
    OperationManifest,
    create_manifest,
    save_manifest,
    load_manifest,
    list_manifests,
    find_pending_manifests,
    migrate_profile,
    retry_migration,
    rename_config,
    delete_to_quarantine,
    restore_from_quarantine,
    create_backup,
    verify_backup,
    restore_backup,
    scan_and_recover,
    _hash_file,
    _hash_directory,
    _hash_file_manifest,
)


# ---- Manifest CRUD ----

class TestManifestCRUD:
    def test_create_manifest(self):
        manifest = create_manifest("migration", "/src", "/dst")
        assert manifest.type == "migration"
        assert manifest.source == "/src"
        assert manifest.target == "/dst"
        assert manifest.state == OpState.PREPARED
        assert manifest.operation_id

    def test_save_and_load_manifest(self, tmp_path):
        ops_dir = tmp_path / "ops"
        ops_dir.mkdir()
        manifest = create_manifest("test", "/a", "/b")
        save_manifest(manifest, ops_dir)

        loaded = load_manifest(ops_dir, manifest.operation_id)
        assert loaded is not None
        assert loaded.operation_id == manifest.operation_id
        assert loaded.type == "test"

    def test_load_nonexistent_manifest(self, tmp_path):
        ops_dir = tmp_path / "ops"
        ops_dir.mkdir()
        loaded = load_manifest(ops_dir, "nonexistent_id")
        assert loaded is None

    def test_list_manifests(self, tmp_path):
        ops_dir = tmp_path / "ops"
        ops_dir.mkdir()
        m1 = create_manifest("test1", "/a", "/b")
        m2 = create_manifest("test2", "/c", "/d")
        save_manifest(m1, ops_dir)
        save_manifest(m2, ops_dir)

        manifests = list_manifests(ops_dir)
        assert len(manifests) == 2

    def test_find_pending_manifests(self, tmp_path):
        ops_dir = tmp_path / "ops"
        ops_dir.mkdir()
        m1 = create_manifest("test", "/a", "/b")
        m1.state = OpState.PREPARED
        save_manifest(m1, ops_dir)

        m2 = create_manifest("test", "/c", "/d")
        m2.state = OpState.COMMITTED
        save_manifest(m2, ops_dir)

        pending = find_pending_manifests(ops_dir)
        assert len(pending) == 1
        assert pending[0].operation_id == m1.operation_id


# ---- Hash 工具 ----

class TestHashTools:
    def test_hash_file(self, tmp_path):
        file_path = tmp_path / "test.txt"
        file_path.write_text("test content")
        hash1 = _hash_file(file_path)
        assert len(hash1) == 64  # SHA-256

    def test_hash_file_deterministic(self, tmp_path):
        file_path = tmp_path / "test.txt"
        file_path.write_text("test content")
        hash1 = _hash_file(file_path)
        hash2 = _hash_file(file_path)
        assert hash1 == hash2

    def test_hash_directory(self, tmp_path):
        (tmp_path / "a.txt").write_text("a")
        (tmp_path / "b.txt").write_text("b")
        hash1 = _hash_directory(tmp_path)
        assert len(hash1) == 64

    def test_hash_directory_deterministic(self, tmp_path):
        (tmp_path / "a.txt").write_text("a")
        hash1 = _hash_directory(tmp_path)
        hash2 = _hash_directory(tmp_path)
        assert hash1 == hash2

    def test_hash_directory_changes_with_content(self, tmp_path):
        (tmp_path / "a.txt").write_text("a")
        hash1 = _hash_directory(tmp_path)
        (tmp_path / "a.txt").write_text("b")
        hash2 = _hash_directory(tmp_path)
        assert hash1 != hash2

    def test_hash_file_manifest(self, tmp_path):
        (tmp_path / "a.txt").write_text("a")
        (tmp_path / "b.txt").write_text("b")
        manifest = _hash_file_manifest(tmp_path)
        assert "a.txt" in manifest
        assert "b.txt" in manifest
        assert len(manifest["a.txt"]) == 64


# ---- Profile 迁移 ----

class TestProfileMigration:
    def test_migrate_profile_success(self, tmp_path):
        src = tmp_path / "src"
        src.mkdir()
        (src / "config.json").write_text('{"test": true}')

        dst = tmp_path / "dst"
        ops = tmp_path / "ops"
        ops.mkdir()

        manifest = migrate_profile(src, dst, ops)
        assert manifest.state == OpState.COMMITTED
        assert dst.exists()
        assert (dst / "config.json").exists()

    def test_migrate_profile_idempotent(self, tmp_path):
        src = tmp_path / "src"
        src.mkdir()
        (src / "config.json").write_text('{"test": true}')

        dst = tmp_path / "dst"
        ops = tmp_path / "ops"
        ops.mkdir()

        m1 = migrate_profile(src, dst, ops)
        m2 = migrate_profile(src, dst, ops)
        assert m1.state == OpState.COMMITTED
        assert m2.state == OpState.COMMITTED

    def test_migrate_profile_source_not_found(self, tmp_path):
        src = tmp_path / "nonexistent"
        dst = tmp_path / "dst"
        ops = tmp_path / "ops"
        ops.mkdir()

        with pytest.raises(FileNotFoundError):
            migrate_profile(src, dst, ops)

    def test_retry_migration_committed(self, tmp_path):
        ops = tmp_path / "ops"
        ops.mkdir()
        manifest = create_manifest("migration", "/src", "/dst")
        manifest.state = OpState.COMMITTED
        save_manifest(manifest, ops)

        result = retry_migration(manifest, ops)
        assert result.state == OpState.COMMITTED


# ---- 配置改名 ----

class TestConfigRename:
    def test_rename_config(self, tmp_path):
        profile = tmp_path / "profile"
        profile.mkdir()
        configs = profile / "configs"
        configs.mkdir()
        (configs / "old.xml").write_text("<config/>")

        ops = tmp_path / "ops"
        ops.mkdir()

        manifest = rename_config(profile, "old.xml", "new.xml", ops)
        assert manifest.state == OpState.COMMITTED
        assert (configs / "new.xml").exists()
        assert not (configs / "old.xml").exists()

    def test_rename_config_updates_mapping(self, tmp_path):
        profile = tmp_path / "profile"
        profile.mkdir()
        configs = profile / "configs"
        configs.mkdir()
        (configs / "old.xml").write_text("<config/>")

        mapping_file = profile / "config_mapping.json"
        with open(mapping_file, "w") as f:
            json.dump([{"name": "old.xml", "url": "http://example.com"}], f)

        ops = tmp_path / "ops"
        ops.mkdir()

        rename_config(profile, "old.xml", "new.xml", ops)

        with open(mapping_file) as f:
            mapping = json.load(f)
        assert mapping[0]["name"] == "new.xml"


# ---- Quarantine 删除和恢复 ----

class TestQuarantine:
    def test_delete_to_quarantine(self, tmp_path):
        target = tmp_path / "target.txt"
        target.write_text("test")

        quarantine = tmp_path / "quarantine"
        ops = tmp_path / "ops"
        ops.mkdir()

        manifest = delete_to_quarantine(target, quarantine, ops)
        assert manifest.state == OpState.COMMITTED
        assert not target.exists()
        assert quarantine.exists()

    def test_delete_nonexistent_raises(self, tmp_path):
        target = tmp_path / "nonexistent.txt"
        quarantine = tmp_path / "quarantine"
        ops = tmp_path / "ops"
        ops.mkdir()

        with pytest.raises(FileNotFoundError):
            delete_to_quarantine(target, quarantine, ops)

    def test_restore_from_quarantine(self, tmp_path):
        target = tmp_path / "target.txt"
        target.write_text("test")

        quarantine = tmp_path / "quarantine"
        ops = tmp_path / "ops"
        ops.mkdir()

        manifest = delete_to_quarantine(target, quarantine, ops)

        restore_path = tmp_path / "restored.txt"
        restore_manifest = restore_from_quarantine(manifest, restore_path, ops)
        assert restore_manifest.state == OpState.COMMITTED
        assert restore_path.exists()
        assert restore_path.read_text() == "test"


# ---- 备份和恢复 ----

class TestBackupRestore:
    def test_create_backup(self, tmp_path):
        profile = tmp_path / "profile"
        profile.mkdir()
        (profile / "config.json").write_text('{"test": true}')

        backup = tmp_path / "backup"
        ops = tmp_path / "ops"
        ops.mkdir()

        manifest = create_backup(profile, backup, ops)
        assert manifest.state == OpState.COMMITTED
        assert backup.exists()
        assert (backup / "config.json").exists()

    def test_verify_backup(self, tmp_path):
        profile = tmp_path / "profile"
        profile.mkdir()
        (profile / "config.json").write_text('{"test": true}')

        backup = tmp_path / "backup"
        ops = tmp_path / "ops"
        ops.mkdir()

        manifest = create_backup(profile, backup, ops)
        assert verify_backup(backup, manifest)

    def test_restore_backup(self, tmp_path):
        profile = tmp_path / "profile"
        profile.mkdir()
        (profile / "config.json").write_text('{"test": true}')

        backup = tmp_path / "backup"
        target = tmp_path / "restored"
        ops = tmp_path / "ops"
        ops.mkdir()

        manifest = create_backup(profile, backup, ops)
        restore_manifest = restore_backup(backup, target, ops)
        assert restore_manifest.state == OpState.COMMITTED
        assert target.exists()
        assert (target / "config.json").exists()

    def test_backup_nonexistent_raises(self, tmp_path):
        profile = tmp_path / "nonexistent"
        backup = tmp_path / "backup"
        ops = tmp_path / "ops"
        ops.mkdir()

        with pytest.raises(FileNotFoundError):
            create_backup(profile, backup, ops)


# ---- 恢复扫描 ----

class TestRecoveryScan:
    def test_scan_empty_ops_dir(self, tmp_path):
        ops = tmp_path / "ops"
        ops.mkdir()
        results = scan_and_recover(ops)
        assert len(results) == 0

    def test_scan_with_pending_manifest(self, tmp_path):
        ops = tmp_path / "ops"
        ops.mkdir()
        manifest = create_manifest("test", "/a", "/b")
        manifest.state = OpState.PREPARED
        save_manifest(manifest, ops)

        results = scan_and_recover(ops)
        assert len(results) == 1
        assert results[0]["action"] == "needs_manual_review"
