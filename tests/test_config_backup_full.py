"""M8-02 全域备份/恢复。"""

from __future__ import annotations

import importlib.util
import json
import os

import pytest

from app.core import config_repository as cr

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _load_backup():
    path = os.path.join(ROOT, "scripts", "config_backup.py")
    spec = importlib.util.spec_from_file_location("config_backup_full_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


backup_mod = _load_backup()


def _write(path: str, content: bytes) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as fh:
        fh.write(content)


def _make_data(root: str) -> None:
    _write(os.path.join(root, "profiles", "p1", "configs", "a.xml"), b"<R>1</R>")
    _write(os.path.join(root, "profiles", "p1", "archive", "a.xml", "v1.xml"), b"<R>1</R>")
    _write(os.path.join(root, "ip_mapping.json"), b'{"10.0.0.2": "member"}')
    _write(os.path.join(root, "admin_users.json"), b'{"admin": true}')
    _write(
        os.path.join(root, "device_models.json"),
        '[{"id": "default", "name": "默认机型"}]'.encode("utf-8"),
    )
    _write(os.path.join(root, "recovery", "old", "manifest.json"), b'{"entry": "old"}')
    repo = cr.ConfigRepository(root)
    repo.initialize()
    tx = repo.transaction({"ip": "127.0.0.1", "name": "test"}, "建类别", action="family.create")
    tx.put("family", {"name": "轴", "code": "AXIS"})
    tx.commit()


def test_full_backup_verify_and_restore(tmp_path) -> None:
    source = str(tmp_path / "data")
    backup = str(tmp_path / "backup")
    target = str(tmp_path / "restored")
    _make_data(source)
    manifest = backup_mod.backup_full(source, backup)
    assert manifest["schema"] == 2
    assert set(manifest["datasets"]) >= {"config_management", "profiles", "global", "recovery"}
    verified = backup_mod.verify_full(backup)
    assert verified["ok"] is True

    plan = backup_mod.restore_full(backup, target, force=True)
    assert plan["restored"] is True
    assert os.path.exists(os.path.join(target, "profiles", "p1", "configs", "a.xml"))
    assert os.path.exists(os.path.join(target, "config_management", "state.json"))
    assert os.path.exists(os.path.join(target, "recovery", "old", "manifest.json"))
    assert cr.ConfigRepository(target).verify()["ok"] is True

    # 同一备份重复恢复是幂等的，不再复制/写第二条审计
    again = backup_mod.restore_full(backup, target, force=True)
    assert again["already_restored"] is True


def test_full_backup_tamper_and_prune(tmp_path) -> None:
    source = str(tmp_path / "data")
    backup = str(tmp_path / "backup")
    target = str(tmp_path / "restored")
    _make_data(source)
    backup_mod.backup_full(source, backup)
    # 篡改备份中的一个文件
    victim = os.path.join(backup, "datasets", "profiles", "p1", "configs", "a.xml")
    _write(victim, b"tampered")
    assert backup_mod.verify_full(backup)["ok"] is False

    backup_mod.backup_full(source, backup, overwrite=True)
    os.makedirs(os.path.join(target, "profiles", "p1", "configs"), exist_ok=True)
    _write(os.path.join(target, "profiles", "p1", "configs", "stale.xml"), b"stale")
    plan = backup_mod.restore_full(backup, target, force=True, prune=True)
    assert plan["restored"] is True
    assert not os.path.exists(os.path.join(target, "profiles", "p1", "configs", "stale.xml"))


def test_restore_failure_rolls_back_old_target(tmp_path, monkeypatch) -> None:
    source = str(tmp_path / "data")
    backup = str(tmp_path / "backup")
    target = str(tmp_path / "restored")
    _make_data(source)
    backup_mod.backup_full(source, backup)
    os.makedirs(os.path.join(target, "profiles", "p1", "configs"), exist_ok=True)
    old_path = os.path.join(target, "profiles", "p1", "configs", "a.xml")
    _write(old_path, b"old-before-restore")

    real_copy = backup_mod.shutil.copy2

    def flaky(src, dst, *args, **kwargs):
        if str(src).startswith(backup) and str(dst) == old_path:
            raise OSError("injected restore failure")
        return real_copy(src, dst, *args, **kwargs)

    monkeypatch.setattr(backup_mod.shutil, "copy2", flaky)
    with pytest.raises(OSError):
        backup_mod.restore_full(backup, target, force=True)
    assert open(old_path, "rb").read() == b"old-before-restore"


def test_full_backup_excludes_recovery_when_asked(tmp_path) -> None:
    source = str(tmp_path / "data")
    backup = str(tmp_path / "backup")
    _make_data(source)
    manifest = backup_mod.backup_full(source, backup, include_recovery=False)
    assert "recovery" not in manifest["datasets"]
    assert manifest["include_recovery"] is False
