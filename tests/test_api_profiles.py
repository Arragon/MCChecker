"""机型新增/复制/重命名/删除（M3-16 / INH-1901；规范 §7.2、§7.3、UC-41、C27）。

这是破坏性操作卡，判据落在**真实数据目录与恢复区清单**上：

1. 复制范围：`configs/` + `archive/` + 六个 JSON；不含 `cache/`、`records/`、
   `edit_remarks.json`、`ip_mapping.json`、`admin_users.json`、`update_status.json`；
   `schedule.json` 的 `interval_hours` 照抄、`last_run` 置 null。
2. 删除：默认机型 400；数据目录整个移入 `data/recovery/<ts>_delete_profile_<id>/`，
   `manifest.json` 的 `moved` 指向该目录、`files` 逐文件带 sha256；源目录与机型条目都消失。
3. 回滚：`manifest` 写入失败 ⇒ 数据目录放回原位、机型仍在、恢复条目不留半份副本。
4. 权限：POST/PATCH/DELETE 仅部署者；403 时 `device_models.json` sha 与修订号不动。
5. 修订号：注册表是全局数据，成功写递增 **default** 机型的 `settings`；失败不递增。
6. 名称/重复/非法 copy_from 都在碰盘前 400/409。
"""

from __future__ import annotations

import hashlib
import json
import os

import pytest
from authz_fixtures import as_role, make_server, seed_roles, write_target

from app.core import device_models, storage
from app.services import recovery, revision, scheduler

SOURCE_FILES = (
    "config_mapping.json",
    "favorites.json",
    "bindings.json",
    "tools.json",
    "schedule.json",
    "dltool_config.json",
)


def _registry_path():
    return os.path.join(storage.DATA_DIR, "device_models.json")


def _registry_digest():
    with open(_registry_path(), "rb") as handle:
        return hashlib.sha256(handle.read()).hexdigest()


def _default_settings_rev():
    return revision.snapshot(storage.DEFAULT_PROFILE)["parts"]["settings"]


def _seed_source(pid):
    base = storage.get_profile_data_dir(pid)
    os.makedirs(os.path.join(base, "configs"), exist_ok=True)
    os.makedirs(os.path.join(base, "archive", "a.xml"), exist_ok=True)
    os.makedirs(os.path.join(base, "cache"), exist_ok=True)
    os.makedirs(os.path.join(base, "records", "a.xml"), exist_ok=True)
    write_target(pid, "a.xml", b"<R><x>1</x></R>")
    with open(os.path.join(base, "archive", "a.xml", "a_20260101.xml"), "wb") as handle:
        handle.write(b"<R><x>0</x></R>")
    with open(os.path.join(base, "cache", "keep.txt"), "w", encoding="utf-8") as handle:
        handle.write("cache")
    with open(os.path.join(base, "records", "a.xml", "record_1.xml"), "w", encoding="utf-8") as handle:
        handle.write("<R/>")
    contents = {
        "config_mapping.json": [],
        "favorites.json": [],
        "bindings.json": [],
        "tools.json": [],
        "schedule.json": {"enabled": True, "interval_hours": 6, "last_run": {"at": "2026-10-20 10:00:00"}},
        "dltool_config.json": {"pp_energy": {"coeffs": {}}},
        "edit_remarks.json": [{"source_file": "a.xml"}],
        "ip_mapping.json": [{"ip": "10.0.0.9", "name": "甲"}],
        "admin_users.json": [{"name": "甲"}],
        "update_status.json": {"a.xml": {"status": "success"}},
    }
    for name, payload in contents.items():
        with open(os.path.join(base, name), "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False)


def _create(caller, name, copy_from=None):
    return caller.json("POST", "/api/v1/profiles", {"name": name, "copy_from": copy_from})


class TestCreateAndCopy:
    def setup_method(self):
        revision.reset()
        self.pid = seed_roles("prof-src")
        _seed_source(self.pid)

    def test_create_empty_profile(self):
        with make_server() as server:
            status, _h, body = _create(as_role(server, "deployer"), "空机型")
        assert status == 201, body
        profile = body["profile"]
        assert profile["name"] == "空机型", body
        assert device_models.exists(profile["id"]), profile
        assert _default_settings_rev() == 2

    def test_copy_scope_and_schedule_reset(self):
        with make_server() as server:
            status, _h, body = _create(
                as_role(server, "deployer"), "复制机型", self.pid
            )
        assert status == 201, body
        dest = body["profile"]["id"]
        base = storage.get_profile_data_dir(dest)
        for name in SOURCE_FILES:
            assert os.path.exists(os.path.join(base, name)), name
        assert os.path.isfile(os.path.join(base, "configs", "a.xml"))
        assert os.path.isdir(os.path.join(base, "archive", "a.xml"))
        for excluded in ("cache", "records", "edit_remarks.json", "ip_mapping.json", "admin_users.json", "update_status.json"):
            assert not os.path.exists(os.path.join(base, excluded)), excluded
        with open(os.path.join(base, "schedule.json"), encoding="utf-8") as handle:
            schedule = json.load(handle)
        assert schedule["interval_hours"] == 6, schedule
        assert schedule["enabled"] is True, schedule
        assert schedule["last_run"] is None, schedule

    def test_duplicate_name_is_a_conflict(self):
        with make_server() as server:
            caller = as_role(server, "deployer")
            assert _create(caller, "重复名")[0] == 201
            sha_before = _registry_digest()
            rev_before = _default_settings_rev()
            status, _h, body = _create(caller, "重复名")
            assert status == 409, body
            assert body["error"]["code"] == "FILE_EXISTS", body
            assert body["error"]["detail"]["field"] == "name", body
            assert _registry_digest() == sha_before
            assert _default_settings_rev() == rev_before

    @pytest.mark.parametrize("name", ["", "   ", "a" * 101, "机\x01型"])
    def test_bad_name_is_rejected(self, name):
        with make_server() as server:
            status, _h, body = _create(as_role(server, "deployer"), name)
        assert status == 400, (name, body)
        assert body["error"]["detail"]["field"] == "name", (name, body)

    def test_unknown_copy_from_is_rejected(self):
        with make_server() as server:
            status, _h, body = _create(
                as_role(server, "deployer"), "坏复制", "does-not-exist"
            )
        assert status == 400, body
        assert body["error"]["detail"]["field"] == "copy_from", body


class TestPermissions:
    def setup_method(self):
        revision.reset()
        self.pid = seed_roles("prof-perm")
        _seed_source(self.pid)

    @pytest.mark.parametrize("role", ["visitor", "member", "admin"])
    def test_non_deployers_cannot_write(self, role):
        sha_before = _registry_digest()
        rev_before = _default_settings_rev()
        with make_server() as server:
            caller = as_role(server, role)
            status, _h, body = _create(caller, "非法创建")
            assert status == 403, (role, body)
            status, _h, body = caller.json(
                "PATCH", "/api/v1/profiles/%s" % self.pid, {"name": "非法改名"}
            )
            assert status == 403, (role, body)
            status, _h, body = caller.json(
                "DELETE", "/api/v1/profiles/%s" % self.pid
            )
            assert status == 403, (role, body)
        assert _registry_digest() == sha_before
        assert _default_settings_rev() == rev_before


class TestRename:
    def setup_method(self):
        revision.reset()
        self.pid = seed_roles("prof-rename")

    def test_rename_updates_registry_and_bumps_default(self):
        rev_before = _default_settings_rev()
        with make_server() as server:
            status, _h, body = as_role(server, "deployer").json(
                "PATCH", "/api/v1/profiles/%s" % self.pid, {"name": "新名字"}
            )
        assert status == 200, body
        assert body["profile"]["name"] == "新名字", body
        assert device_models.get_model_name(self.pid) == "新名字"
        assert _default_settings_rev() == rev_before + 1

    def test_duplicate_rename_is_a_conflict(self):
        with make_server() as server:
            caller = as_role(server, "deployer")
            assert _create(caller, "另一个")[0] == 201
            sha_before = _registry_digest()
            rev_before = _default_settings_rev()
            status, _h, body = caller.json(
                "PATCH", "/api/v1/profiles/%s" % self.pid, {"name": "另一个"}
            )
            assert status == 409, body
            assert body["error"]["code"] == "FILE_EXISTS", body
            assert _registry_digest() == sha_before
            assert _default_settings_rev() == rev_before

    def test_unknown_profile_is_404(self):
        with make_server() as server:
            status, _h, _body = as_role(server, "deployer").json(
                "PATCH", "/api/v1/profiles/nope", {"name": "x"}
            )
        assert status == 404


class TestDelete:
    def setup_method(self):
        revision.reset()
        self.pid = seed_roles("prof-del")
        _seed_source(self.pid)

    def test_default_cannot_be_deleted(self):
        sha_before = _registry_digest()
        with make_server() as server:
            status, _h, body = as_role(server, "deployer").json(
                "DELETE", "/api/v1/profiles/default"
            )
        assert status == 400, body
        assert body["error"]["code"] == "INVALID_INPUT", body
        assert body["error"]["detail"]["field"] == "id", body
        assert _registry_digest() == sha_before

    def test_delete_moves_the_whole_directory_into_recovery(self):
        rev_before = _default_settings_rev()
        source_base = storage.get_profile_data_dir(self.pid)
        with make_server() as server:
            status, _h, body = as_role(server, "deployer").json(
                "DELETE", "/api/v1/profiles/%s" % self.pid
            )
        assert status == 200, body
        assert body["deleted"] is True, body
        entry = body["recovery"]
        entry_path = os.path.join(storage.get_recovery_root_dir(), entry)
        assert os.path.isdir(entry_path), entry_path
        assert not os.path.exists(source_base), "源数据目录必须整个移走"
        assert not device_models.exists(self.pid), "机型条目必须移除"
        assert _default_settings_rev() == rev_before + 1

        with open(os.path.join(entry_path, "manifest.json"), encoding="utf-8") as handle:
            manifest = json.load(handle)
        assert manifest["op"] == "delete_profile", manifest
        assert manifest["profile"] == self.pid, manifest
        assert len(manifest["moved"]) == 1, manifest
        assert manifest["moved"][0]["to"] == "profiles/%s" % self.pid, manifest
        assert manifest["files"], manifest
        for item in manifest["files"]:
            recovered = os.path.join(entry_path, "profiles", self.pid, item["path"])
            with open(recovered, "rb") as handle:
                assert hashlib.sha256(handle.read()).hexdigest() == item["sha256"], item

    def test_manifest_failure_rolls_back_data_and_registry(self):
        source_base = storage.get_profile_data_dir(self.pid)
        original = recovery.write_manifest

        def boom(*_args, **_kwargs):
            raise RuntimeError("manifest boom")

        recovery.write_manifest = boom
        try:
            with make_server() as server:
                status, _h, body = as_role(server, "deployer").json(
                    "DELETE", "/api/v1/profiles/%s" % self.pid
                )
        finally:
            recovery.write_manifest = original
        assert status == 500, body
        assert os.path.isdir(os.path.join(source_base, "configs")), "数据目录必须放回"
        assert device_models.exists(self.pid), "机型条目必须保留"
        leftovers = [
            name
            for name in os.listdir(storage.get_recovery_root_dir())
            if "_delete_profile_%s" % self.pid in name
        ]
        assert leftovers == [], leftovers

    def test_partial_source_removal_keeps_the_complete_recovery_copy(self):
        """评审 Critical：`_remove_source` 半删后抛错时，副本是唯一完整数据，绝不能清条目。"""
        source_base = storage.get_profile_data_dir(self.pid)
        original = recovery._remove_source

        def partial(source):
            os.remove(os.path.join(source, "configs", "a.xml"))
            raise RuntimeError("partial remove")

        recovery._remove_source = partial
        try:
            with make_server() as server:
                status, _h, body = as_role(server, "deployer").json(
                    "DELETE", "/api/v1/profiles/%s" % self.pid
                )
        finally:
            recovery._remove_source = original
        assert status == 500, body
        assert os.path.isdir(source_base), "残源可以留在原处，但数据不能丢"
        entries = [
            name
            for name in os.listdir(storage.get_recovery_root_dir())
            if "_delete_profile_%s" % self.pid in name
        ]
        assert len(entries) == 1, entries
        recovered = os.path.join(
            storage.get_recovery_root_dir(), entries[0], "profiles", self.pid,
            "configs", "a.xml",
        )
        assert os.path.isfile(recovered), "被半删掉的文件必须仍在恢复区副本里"
        assert device_models.exists(self.pid), "注册表还没动，机型必须还在"

    def test_create_copy_failure_rolls_back_registry_and_partial_directory(self):
        original = storage.copy_profile_data

        def boom(_src, _dst):
            raise RuntimeError("copy boom")

        storage.copy_profile_data = boom
        try:
            with make_server() as server:
                status, _h, body = _create(
                    as_role(server, "deployer"), "复制失败", self.pid
                )
        finally:
            storage.copy_profile_data = original
        assert status == 500, body
        assert not any(
            model.get("name") == "复制失败" for model in device_models.load_models()
        ), "复制失败不能留下已注册机型"
        leftovers = [
            name
            for name in os.listdir(storage.PROFILES_DIR)
            if name not in {m["id"] for m in device_models.load_models()}
        ]
        assert leftovers == [], leftovers

    def test_registry_remove_failure_restores_the_data_directory(self):
        original = device_models.remove_model

        def boom(*_args, **_kwargs):
            raise RuntimeError("registry boom")

        device_models.remove_model = boom
        source_base = storage.get_profile_data_dir(self.pid)
        try:
            with make_server() as server:
                status, _h, body = as_role(server, "deployer").json(
                    "DELETE", "/api/v1/profiles/%s" % self.pid
                )
        finally:
            device_models.remove_model = original
        assert status == 500, body
        assert device_models.exists(self.pid), "注册表写失败时机型必须还在"
        assert os.path.isfile(os.path.join(source_base, "configs", "a.xml")), "数据必须放回"
        entries = [
            name
            for name in os.listdir(storage.get_recovery_root_dir())
            if "_delete_profile_%s" % self.pid in name
        ]
        assert entries == [], entries

    def test_unknown_profile_is_404(self):
        with make_server() as server:
            status, _h, _body = as_role(server, "deployer").json(
                "DELETE", "/api/v1/profiles/nope"
            )
        assert status == 404


class TestSchedulerReload:
    def setup_method(self):
        revision.reset()
        self.pid = seed_roles("prof-sched")

    def test_create_and_delete_reload_the_scheduler(self):
        calls = []
        fake = type("FakeScheduler", (), {"reload": lambda self: calls.append(1)})()
        original = scheduler.get_scheduler
        scheduler.get_scheduler = lambda: fake
        try:
            with make_server() as server:
                caller = as_role(server, "deployer")
                status, _h, body = _create(caller, "调度机型")
                assert status == 201, body
                status, _h, body = caller.json(
                    "DELETE", "/api/v1/profiles/%s" % body["profile"]["id"]
                )
                assert status == 200, body
        finally:
            scheduler.get_scheduler = original
        assert len(calls) == 2, calls
