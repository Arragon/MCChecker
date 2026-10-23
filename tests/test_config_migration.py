"""M8-01 旧 data 预检、初始化与幂等迁移。"""

from __future__ import annotations

import importlib.util
import json
import os

import pytest

from app.core import config_repository as cr, storage
from app.services import config_catalog

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _load_cli():
    path = os.path.join(ROOT, "scripts", "inspect_config_management.py")
    spec = importlib.util.spec_from_file_location("inspect_config_management", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


icm = _load_cli()


def _write(path: str, content: bytes) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as fh:
        fh.write(content)


def _tree_snapshot(root: str):
    rows = []
    for current, dirs, files in os.walk(root):
        for name in sorted(files):
            absolute = os.path.join(current, name)
            rows.append((os.path.relpath(absolute, root), os.path.getsize(absolute)))
    return rows


def test_inspect_is_read_only_and_classifies_mixed(tmp_path) -> None:
    root = tmp_path / "data"
    _write(str(root / "configs" / "a.xml"), b"<R/>")
    _write(str(root / "profiles" / "p1" / "configs" / "b.xml"), b"<R/>")
    before = _tree_snapshot(str(root))
    result = icm.inspect(str(root))
    assert result["inventory"]["layout"] == "mixed"
    assert any(item["id"] == "p1" for item in result["profiles"])
    assert result["config_management"]["initialized"] is False
    assert _tree_snapshot(str(root)) == before


def test_initialize_backs_up_first_and_is_idempotent(tmp_path) -> None:
    root = tmp_path / "data"
    backup = tmp_path / "backup"
    _write(str(root / "profiles" / "p1" / "configs" / "a.xml"), b"<R/>")
    first = icm.initialize(str(root), str(backup))
    assert first["status"] == "initialized"
    assert os.path.exists(str(root / "config_management" / "state.json"))
    assert os.path.exists(str(backup / "manifest.json"))
    second = icm.initialize(str(root), str(backup))
    assert second["status"] == "already_initialized"


def test_migrate_pending_and_idempotent(tmp_path) -> None:
    root = str(tmp_path / "data")
    _write(os.path.join(root, "profiles", "p1", "configs", "axis.xml"), b"<R/>")
    icm.initialize(root, str(tmp_path / "backup"))
    icm._adopt_data_root(root)
    repo = cr.ConfigRepository(root)
    actor = {"ip": "127.0.0.1", "name": "test"}
    release = config_catalog.register_release(repo, actor, version_label="1.0.0", build="1")
    family = config_catalog.create_family(repo, actor, name="轴", code="AXIS_MIG")
    logical = config_catalog.create_logical_file(
        repo, actor, family_id=family["id"], role="xml_export", display_name="axis.xml"
    )
    plan = {
        "items": [
            {
                "filename": "axis.xml",
                "profile_id": "p1",
                "source_path": "profiles/p1/configs/axis.xml",
                "release_id": release["id"],
                "logical_file_id": logical["id"],
                "decision": "link",
            },
            {"filename": "unknown.xml", "profile_id": "p1", "decision": "pending"},
        ]
    }
    plan_path = str(tmp_path / "plan.json")
    with open(plan_path, "w", encoding="utf-8") as fh:
        json.dump(plan, fh)
    result = icm.migrate(root, plan_path, apply=True)
    assert result["status"] == "applied"
    linked = [row for row in result["results"] if row["status"] == "linked"]
    assert len(linked) == 1
    assert result["pending"]
    pending_path = os.path.join(root, "config_management", "imports", "pending_links.json")
    assert os.path.exists(pending_path)
    snapshots = config_catalog.list_snapshots(repo)
    assert len(snapshots) == 1
    # 同一 plan 重跑仍是同一份快照（需要显式 force-plan 覆盖收据）
    again = icm.migrate(root, plan_path, apply=True, force_plan=True)
    assert len(config_catalog.list_snapshots(repo)) == 1
    assert any(row["status"] == "linked" for row in again["results"])


def test_initialize_refuses_corrupt_state(tmp_path) -> None:
    root = tmp_path / "data"
    _write(str(root / "config_management" / "state.json"), b"{broken")
    with pytest.raises(icm.MigrationError):
        icm.initialize(str(root), str(tmp_path / "backup"))


def test_verify_reports_repository_state(tmp_path) -> None:
    root = str(tmp_path / "data")
    _write(os.path.join(root, "profiles", "p1", "configs", "a.xml"), b"<R/>")
    icm.initialize(root, str(tmp_path / "backup"))
    icm._adopt_data_root(root)
    result = icm.verify(root)
    assert result["ok"] is True
    assert result["receipt"] is None
