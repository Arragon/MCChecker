"""M16-06 旧更新源/按机型计划的 dry-run 迁移测试。

本卡只允许**读**：计划输出可以看，但旧 `config_mapping.json`/`schedule.json`、
FileResource、目录树和字节都必须原样不动；真正的执行/备份/回退归 M16-37。
"""

import hashlib
import importlib.util
import json
import os

import pytest

from app.core import storage
from app.core.errors import NOT_FOUND, AppError
from app.core.sync_models import make_source_id
from app.services import file_versions, sync_migration, workspace_catalog


def make_workspace(name="W-mig"):
    return workspace_catalog.create_workspace(name)["id"]


def add_profile(wid, name):
    return workspace_catalog.add_profile(wid, name)["id"]


def seed_profile(wid, pid, mapping, schedule=None, files=()):
    with storage.use_workspace(wid), storage.use_profile(pid):
        if files:
            for name, content in files:
                storage.save_config_file(name, content)
        storage.save_config_mapping(mapping)
        storage.save_schedule(schedule or {"enabled": False, "interval_hours": 24, "last_run": None})


def snapshot(root):
    """目录 + 文件字节的完整快照；dry-run 前后必须逐字相同。"""
    state = {}
    for current, dirs, names in os.walk(root):
        dirs.sort()
        rel_dir = os.path.relpath(current, root)
        state["dir:" + rel_dir] = None
        for name in sorted(names):
            path = os.path.join(current, name)
            with open(path, "rb") as handle:
                state[os.path.relpath(path, root)] = hashlib.sha256(handle.read()).hexdigest()
    return state


def test_plan_profile_migrates_mapping_and_existing_file_target():
    wid = make_workspace()
    seed_profile(
        wid,
        "default",
        [
            {
                "name": "A.xml",
                "url": "https://example.invalid/A.xml",
                "record_url": "https://example.invalid/records",
                "keyword": "alpha",
            }
        ],
        {"enabled": True, "interval_hours": 6},
        files=[("A.xml", b"<R><x>1</x></R>")],
    )
    with storage.use_workspace(wid), storage.use_profile("default"):
        resource = next(
            row for row in file_versions.list_resources("default") if row["name"] == "A.xml"
        )

    plan = sync_migration.plan_profile(wid, "default")

    assert plan["profile_id"] == "default"
    assert plan["old_schedule"] == {"enabled": True, "interval_hours": 6}
    assert plan["source_count"] == 1
    assert plan["warnings"] == []
    source = plan["sources"][0]
    assert source["source_id"] == make_source_id(wid, "default", "A.xml")
    assert source["workspace_id"] == wid
    assert source["profile_id"] == "default"
    assert source["name"] == "A.xml"
    assert source["kind"] == "http_file"
    assert source["resource_ref"] == "https://example.invalid/A.xml"
    assert source["record_source"] == "https://example.invalid/records"
    assert source["keyword"] == "alpha"
    assert source["target"] == {"kind": "file_archive", "file_id": resource["file_id"]}
    assert source["target_status"] == "ready"
    assert source["old_sha256"] == resource["blob_sha256"]
    assert source["schedule_mode"] == "inherit"


def test_plan_profile_warns_but_keeps_going_on_bad_entries_and_missing_file():
    wid = make_workspace()
    seed_profile(
        wid,
        "default",
        [
            "not-a-dict",
            {"name": "", "url": "https://example.invalid/x"},
            {"name": "no-file.xml", "url": "https://example.invalid/no-file.xml"},
            {"name": "no-file.xml", "url": "https://example.invalid/second"},
        ],
    )

    plan = sync_migration.plan_profile(wid, "default")

    assert plan["source_count"] == 1
    assert plan["sources"][0]["name"] == "no-file.xml"
    assert plan["sources"][0]["target"] is None
    assert plan["sources"][0]["target_status"] == "missing"
    assert plan["sources"][0]["old_sha256"] is None
    reasons = [warning["reason"] for warning in plan["warnings"]]
    assert reasons == [
        "invalid_mapping_item",
        "missing_name_or_url",
        "target_file_missing",
        "duplicate_name",
    ]


def test_plan_workspace_keeps_profiles_apart_and_is_deterministic():
    wid = make_workspace()
    second = add_profile(wid, "第二机型")
    seed_profile(
        wid,
        "default",
        [{"name": "A.xml", "url": "https://example.invalid/a"}],
        {"enabled": True, "interval_hours": 6},
        files=[("A.xml", b"<R><x>1</x></R>")],
    )
    seed_profile(
        wid,
        second,
        [{"name": "B.xml", "url": "https://example.invalid/b"}],
        {"enabled": False, "interval_hours": 48},
        files=[("B.xml", b"<R><x>2</x></R>")],
    )

    plan = sync_migration.plan_workspace(wid, ["default", second])
    again = sync_migration.plan_workspace(wid, ["default", second])

    assert plan["schema"] == 1
    assert plan["workspace_id"] == wid
    assert plan["profile_ids"] == ["default", second]
    assert plan["writes"] == 0
    assert plan["policy_overrides"] == {
        "default": {"enabled": True, "interval_hours": 6},
        second: {"enabled": False, "interval_hours": 48},
    }
    assert [source["name"] for source in plan["sources"]] == ["A.xml", "B.xml"]
    assert [entry["profile_id"] for entry in plan["profile_plans"]] == ["default", second]
    assert plan["plan_id"].startswith("mig-")
    assert len(plan["plan_id"]) == len("mig-") + 16
    assert plan["plan_id"] == again["plan_id"]

    # 不传 profile 列表时按归属枚举，顺序与 catalog 一致。
    all_profiles = sync_migration.plan_workspace(wid)
    assert all_profiles["profile_ids"] == ["default", second]


def test_plan_workspace_isolated_between_workspaces_and_rejects_foreign_profile():
    w1 = make_workspace("W1-mig")
    w2 = make_workspace("W2-mig")
    for wid in (w1, w2):
        seed_profile(
            wid,
            "default",
            [{"name": "A.xml", "url": "https://example.invalid/a"}],
            files=[("A.xml", b"<R><x>" + wid.encode() + b"</x></R>")],
        )

    one = sync_migration.plan_workspace(w1)
    two = sync_migration.plan_workspace(w2)

    assert one["sources"][0]["source_id"] != two["sources"][0]["source_id"]
    assert one["sources"][0]["target"]["file_id"] != two["sources"][0]["target"]["file_id"]
    assert one["plan_id"] != two["plan_id"]

    other = add_profile(w2, "仅W2")
    with pytest.raises(AppError) as exc:
        sync_migration.plan_workspace(w1, [other])
    assert exc.value.code == NOT_FOUND


def test_plan_is_zero_write_on_data_tree():
    wid = make_workspace()
    second = add_profile(wid, "第二机型")
    seed_profile(
        wid,
        "default",
        [{"name": "A.xml", "url": "https://example.invalid/a"}],
        {"enabled": True, "interval_hours": 12},
        files=[("A.xml", b"<R><x>1</x></R>")],
    )
    seed_profile(wid, second, [{"name": "gone.xml", "url": "https://example.invalid/g"}])
    before = snapshot(storage.DATA_DIR)

    plan = sync_migration.plan_workspace(wid)

    assert plan["writes"] == 0
    assert snapshot(storage.DATA_DIR) == before
    with storage.use_workspace(wid), storage.use_profile("default"):
        assert storage.load_config_mapping() == [
            {"name": "A.xml", "url": "https://example.invalid/a"}
        ]
        assert storage.load_schedule()["interval_hours"] == 12
    assert not os.path.exists(os.path.join(storage.DATA_DIR, "workspaces", wid, "sync"))


def test_cli_plan_prints_json_without_writing(capsys):
    wid = make_workspace()
    seed_profile(
        wid,
        "default",
        [{"name": "A.xml", "url": "https://example.invalid/a"}],
        {"enabled": True, "interval_hours": 6},
        files=[("A.xml", b"<R><x>1</x></R>")],
    )
    before = snapshot(storage.DATA_DIR)
    path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "scripts", "sync_migrate.py")
    spec = importlib.util.spec_from_file_location("sync_migrate_cli", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    assert module.main(["plan", "--workspace", wid]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["workspace_id"] == wid
    assert payload["writes"] == 0
    assert [source["name"] for source in payload["sources"]] == ["A.xml"]
    assert snapshot(storage.DATA_DIR) == before
