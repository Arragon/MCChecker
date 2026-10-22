"""配置域最小备份/校验/恢复（M8-02a）。

它是 M6 现网试用前的安全网：必须能把 `data/config_management/` 回到"最近一次
完整提交"，且恢复失败时旧 state 仍然自洽。
"""

from __future__ import annotations

import importlib.util
import json
import os
import shutil

import pytest

from app.core import config_repository as cr

SCRIPT = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts", "config_backup.py"
)


def _load_script():
    spec = importlib.util.spec_from_file_location("config_backup_under_test", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    return module


@pytest.fixture()
def backup_mod():
    return _load_script()


def _seed(data_root: str):
    repo = cr.ConfigRepository(data_root)
    repo.initialize()
    tx = repo.transaction({"ip": "127.0.0.1", "name": "本机"}, "建族与参数")
    family = tx.put("family", {"name": "轴参数", "status": "active"})
    parameter = tx.put("parameter", {"family_id": family.id, "name": "脉冲宽度", "status": "active"})
    digest = tx.blob(b"<MachineConfig/>")
    tx.commit()
    return repo, family, parameter, digest


def test_backup_copies_domain_only(tmp_path, backup_mod) -> None:
    data_root = tmp_path / "data"
    _seed(str(data_root))
    (data_root / "profiles" / "default").mkdir(parents=True, exist_ok=True)
    (data_root / "profiles" / "default" / "records.json").write_text("{}", encoding="utf-8")

    out = tmp_path / "backup"
    manifest = backup_mod.backup(str(data_root), str(out))

    assert manifest["domain"] == "config_management"
    assert manifest["store_seq"] == 1
    paths = {item["path"] for item in manifest["files"]}
    assert "state.json" in paths
    assert all(not path.startswith("profiles/") for path in paths)
    assert any(path.startswith("objects/family/") for path in paths)
    assert any(path.startswith("blobs/") for path in paths)


def test_backup_then_verify_is_green(tmp_path, backup_mod) -> None:
    data_root = tmp_path / "data"
    _seed(str(data_root))
    out = tmp_path / "backup"
    backup_mod.backup(str(data_root), str(out))

    result = backup_mod.verify(str(out))
    assert result["ok"] is True
    assert result["problems"] == []
    assert result["manifest"]["file_count"] >= 3


def test_verify_detects_tampering(tmp_path, backup_mod) -> None:
    data_root = tmp_path / "data"
    _seed(str(data_root))
    out = tmp_path / "backup"
    manifest = backup_mod.backup(str(data_root), str(out))

    blob = next(item for item in manifest["files"] if item["path"].startswith("blobs/"))
    with open(os.path.join(str(out), blob["path"]), "wb") as fh:
        fh.write(b"tampered")

    result = backup_mod.verify(str(out))
    assert result["ok"] is False
    assert any(problem["issue"] == "哈希不符" for problem in result["problems"])


def test_restore_into_fresh_root_roundtrips(tmp_path, backup_mod) -> None:
    source = tmp_path / "source"
    repo, family, parameter, _digest = _seed(str(source))
    out = tmp_path / "backup"
    manifest = backup_mod.backup(str(source), str(out))

    target = tmp_path / "target"
    plan = backup_mod.restore(str(out), str(target), dry_run=True)
    assert plan["dry_run"] is True
    assert not (target / "config_management").exists(), "dry-run 不得写盘"

    plan = backup_mod.restore(str(out), str(target))
    assert plan["verify"]["ok"] is True
    assert plan["store_seq"] == manifest["store_seq"]

    restored = cr.ConfigRepository(str(target))
    assert restored.load_state()["seq"] == manifest["store_seq"]
    assert restored.get("family", family.id)["name"] == "轴参数"
    assert restored.get("parameter", parameter.id)["name"] == "脉冲宽度"


def test_restore_refuses_non_empty_target_without_force(tmp_path, backup_mod) -> None:
    source = tmp_path / "source"
    _seed(str(source))
    out = tmp_path / "backup"
    backup_mod.backup(str(source), str(out))

    target = tmp_path / "target"
    (target / "config_management").mkdir(parents=True)
    (target / "config_management" / "state.json").write_text("{}", encoding="utf-8")
    with pytest.raises(backup_mod.BackupError):
        backup_mod.restore(str(out), str(target))

    plan = backup_mod.restore(str(out), str(target), force=True)
    assert plan["verify"]["ok"] is True


def test_restore_refuses_corrupt_backup(tmp_path, backup_mod) -> None:
    source = tmp_path / "source"
    _seed(str(source))
    out = tmp_path / "backup"
    manifest = backup_mod.backup(str(source), str(out))
    blob = next(item for item in manifest["files"] if item["path"].startswith("blobs/"))
    with open(os.path.join(str(out), blob["path"]), "wb") as fh:
        fh.write(b"broken")

    with pytest.raises(backup_mod.BackupError):
        backup_mod.restore(str(out), str(tmp_path / "target"))


def test_backup_requires_initialized_domain(tmp_path, backup_mod) -> None:
    with pytest.raises(backup_mod.BackupError):
        backup_mod.backup(str(tmp_path / "empty"), str(tmp_path / "out"))


def test_backup_refuses_non_empty_output_without_overwrite(tmp_path, backup_mod) -> None:
    data_root = tmp_path / "data"
    _seed(str(data_root))
    out = tmp_path / "backup"
    out.mkdir()
    (out / "keep.txt").write_text("x", encoding="utf-8")
    with pytest.raises(backup_mod.BackupError):
        backup_mod.backup(str(data_root), str(out))
    manifest = backup_mod.backup(str(data_root), str(out), overwrite=True)
    assert manifest["file_count"] >= 3


def test_backup_is_self_consistent_when_writes_happened_after(tmp_path, backup_mod) -> None:
    """备份后的新写入不应改变备份；备份仍可恢复出备份时刻的状态。"""
    data_root = tmp_path / "data"
    repo, family, _parameter, _digest = _seed(str(data_root))
    out = tmp_path / "backup"
    manifest = backup_mod.backup(str(data_root), str(out))

    tx = repo.transaction({"ip": "127.0.0.1"}, "备份之后的写入")
    tx.put("family", {"name": "轴参数", "status": "retired"}, object_id=family.id)
    tx.commit()

    assert backup_mod.verify(str(out))["ok"] is True
    target = tmp_path / "target"
    backup_mod.restore(str(out), str(target))
    restored = cr.ConfigRepository(str(target))
    assert restored.load_state()["seq"] == manifest["store_seq"]
    assert restored.get("family", family.id)["status"] == "active", "恢复的是备份时刻的状态"


def test_manifest_is_json_on_disk(tmp_path, backup_mod) -> None:
    data_root = tmp_path / "data"
    _seed(str(data_root))
    out = tmp_path / "backup"
    backup_mod.backup(str(data_root), str(out))
    with open(out / "manifest.json", "r", encoding="utf-8") as fh:
        manifest = json.load(fh)
    assert manifest["schema"] == 1
    assert manifest["files"] == sorted(manifest["files"], key=lambda item: item["path"])


def test_cli_backup_and_verify(tmp_path, backup_mod) -> None:
    data_root = tmp_path / "data"
    _seed(str(data_root))
    out = tmp_path / "backup"
    assert backup_mod.main(["backup", "--data-dir", str(data_root), "--out", str(out)]) == 0
    assert backup_mod.main(["verify", "--backup", str(out)]) == 0
    shutil.rmtree(out)
    assert backup_mod.main(["verify", "--backup", str(out)]) == 2
