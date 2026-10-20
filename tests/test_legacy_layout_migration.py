"""旧布局迁移（§5.5；M4-04 / INH-1906）。

判据：复制 → 文件数 + 每文件 sha256 → 才把旧条目移入 `recovery/<ts>_legacy_layout/`；
失败只清目标副本、旧条目保留、启动继续；幂等；`nicegui_storage_secret.txt` 不动。
"""

from __future__ import annotations

import hashlib
import json
import os

from app.core import storage


def _sha(path):
    with open(path, "rb") as handle:
        return hashlib.sha256(handle.read()).hexdigest()


def _remove(path):
    if os.path.isdir(path):
        import shutil

        shutil.rmtree(path)
    elif os.path.exists(path):
        os.remove(path)


class Base:
    def setup_method(self):
        storage.ensure_profile_layout(migrate_legacy=False)
        self.base = storage.get_profile_data_dir()
        self.root = storage.DATA_DIR
        # conftest 的 `_ensure_dirs()` 会在旧根上建空的 configs/archive/cache 骨架
        # （cache 下还有 parse_tree）；它们不是本用例的旧数据，先清掉，免得被迁移成
        # 空目录干扰断言。每个用例的 tmp 根都是新的，这里只删测试脚手架。
        for name in ("configs", "archive", "cache"):
            _remove(os.path.join(self.root, name))
        for name in (
            "config_mapping.json", "favorites.json", "bindings.json", "tools.json",
            "schedule.json", "ip_mapping.json", "admin_users.json", "edit_remarks.json",
            "dltool_config.json", "configs", "archive", "cache",
        ):
            _remove(os.path.join(self.base, name))

    def target(self, name):
        return os.path.join(self.base, name)

    def legacy(self, name):
        return os.path.join(self.root, name)

    def reset_targets(self, *names):
        for name in names:
            _remove(self.target(name))

    def recovery_entries(self):
        root = storage.get_recovery_root_dir()
        if not os.path.isdir(root):
            return []
        return sorted(
            name for name in os.listdir(root) if name.endswith("_legacy_layout")
            or "_legacy_layout_" in name
        )


class TestMigration(Base):
    def test_copies_verifies_then_moves_old_entries_to_recovery(self):
        with open(self.legacy("config_mapping.json"), "w", encoding="utf-8") as handle:
            json.dump([{"name": "a.xml"}], handle)
        os.makedirs(self.legacy("configs"), exist_ok=True)
        with open(os.path.join(self.legacy("configs"), "a.xml"), "wb") as handle:
            handle.write(b"<R/>")
        with open(self.legacy("nicegui_storage_secret.txt"), "w", encoding="utf-8") as handle:
            handle.write("secret")
        self.reset_targets("config_mapping.json", "configs")

        storage.ensure_profile_layout()

        assert os.path.isfile(self.target("config_mapping.json"))
        assert os.path.isfile(os.path.join(self.target("configs"), "a.xml"))
        assert not os.path.exists(self.legacy("config_mapping.json"))
        assert not os.path.exists(self.legacy("configs"))
        assert os.path.isfile(self.legacy("nicegui_storage_secret.txt")), "secret 不许被迁移/删除"
        entries = self.recovery_entries()
        assert len(entries) == 1, entries
        manifest_path = os.path.join(
            storage.get_recovery_root_dir(), entries[0], "manifest.json"
        )
        with open(manifest_path, encoding="utf-8") as handle:
            manifest = json.load(handle)
        assert manifest["op"] == "legacy_layout", manifest
        assert manifest["profile"] == "default", manifest
        moved = {item["to"]: item for item in manifest["moved"]}
        assert set(moved) == {"config_mapping.json", "configs"}, moved
        for item in manifest["moved"]:
            base = os.path.join(storage.get_recovery_root_dir(), entries[0], item["to"])
            for rel, digest in item["files"].items():
                recovered = os.path.join(base, rel) if os.path.isdir(base) else base
                assert _sha(recovered) == digest, (item, rel)

    def test_second_run_is_idempotent(self):
        with open(self.legacy("config_mapping.json"), "w", encoding="utf-8") as handle:
            handle.write("[]")
        self.reset_targets("config_mapping.json")
        storage.ensure_profile_layout()
        before = self.recovery_entries()
        storage.ensure_profile_layout()
        assert self.recovery_entries() == before, "第二次启动不得新增恢复目录"

    def test_existing_target_is_not_overwritten(self):
        with open(self.legacy("config_mapping.json"), "w", encoding="utf-8") as handle:
            handle.write('["old"]')
        os.makedirs(self.base, exist_ok=True)
        with open(self.target("config_mapping.json"), "w", encoding="utf-8") as handle:
            handle.write('["new"]')
        storage.ensure_profile_layout()
        with open(self.target("config_mapping.json"), encoding="utf-8") as handle:
            assert handle.read() == '["new"]'
        assert os.path.exists(self.legacy("config_mapping.json")), "目标已存在时旧条目保留"
        assert self.recovery_entries() == []


class TestFailureRollback(Base):
    def test_copy_failure_keeps_old_and_removes_partial_target(self, monkeypatch):
        os.makedirs(self.legacy("configs"), exist_ok=True)
        with open(os.path.join(self.legacy("configs"), "a.xml"), "wb") as handle:
            handle.write(b"<R/>")
        self.reset_targets("configs")

        def boom(_src, dst):
            os.makedirs(dst, exist_ok=True)
            with open(os.path.join(dst, "partial.txt"), "w", encoding="utf-8") as handle:
                handle.write("half")
            raise RuntimeError("copy boom")

        monkeypatch.setattr(storage.shutil, "copytree", boom)
        storage.ensure_profile_layout()
        assert os.path.isdir(self.legacy("configs")), "旧条目必须保留"
        assert not os.path.exists(self.target("configs")), "不完整的目标副本必须删掉"
        assert self.recovery_entries() == []

    def test_hash_mismatch_keeps_old_and_removes_target(self, monkeypatch):
        with open(self.legacy("config_mapping.json"), "w", encoding="utf-8") as handle:
            handle.write("[]")
        self.reset_targets("config_mapping.json")

        def bad_copy(src, dst):
            with open(dst, "wb") as handle:
                handle.write(b"corrupt")

        monkeypatch.setattr(storage.shutil, "copy2", bad_copy)
        storage.ensure_profile_layout()
        assert os.path.isfile(self.legacy("config_mapping.json"))
        assert not os.path.exists(self.target("config_mapping.json"))
        assert self.recovery_entries() == []

    def test_manifest_failure_does_not_lose_data(self, monkeypatch):
        with open(self.legacy("config_mapping.json"), "w", encoding="utf-8") as handle:
            handle.write("[]")
        self.reset_targets("config_mapping.json")

        def boom(*_args, **_kwargs):
            raise RuntimeError("manifest boom")

        monkeypatch.setattr(storage.fsutil, "atomic_write_json", boom)
        storage.ensure_profile_layout()
        assert os.path.isfile(self.target("config_mapping.json")), "目标副本必须已就位"
        assert not os.path.exists(self.legacy("config_mapping.json")), "旧条目已移入恢复区"
        entries = self.recovery_entries()
        assert len(entries) == 1, entries
        recovered = os.path.join(
            storage.get_recovery_root_dir(), entries[0], "config_mapping.json"
        )
        assert os.path.isfile(recovered), "manifest 失败不得回滚已迁移的数据"
