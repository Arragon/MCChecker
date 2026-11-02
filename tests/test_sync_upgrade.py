"""M16-37 旧来源/机型计划无损升级、备份、恢复测试。

验收口径：旧文件/记录/归档逐字节不变、dry-run 零写入、重复执行不重复、
部分失败可回滚/重试、备份不复制 secret。所有写入都在 pytest 临时数据根内。
"""

import hashlib
import importlib.util
import json
import os

import pytest

from app.core import storage
from app.core.errors import INVALID_INPUT, REVISION_CONFLICT, STORAGE_FAILURE, AppError
from app.services import (
    sync_paths,
    sync_policy,
    sync_sources,
    sync_upgrade,
    workspace_catalog,
)

PACKAGE_RE = r"^pkg_.*\.zip$"


def make_workspace(name):
    return workspace_catalog.create_workspace(name)["id"]


def add_profile(wid, name):
    return workspace_catalog.add_profile(wid, name)["id"]


def write_bytes(path, content):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as handle:
        handle.write(content)


def seed_profile(wid, pid, mapping, schedule, files=(), records=(), archives=()):
    with storage.use_workspace(wid), storage.use_profile(pid):
        for name, content in files:
            storage.save_config_file(name, content)
        for name, filename, content in records:
            write_bytes(os.path.join(storage.get_records_root_dir(pid), name, filename), content)
        for name, filename, content in archives:
            write_bytes(os.path.join(storage.get_archive_root_dir(pid), name, filename), content)
        storage.save_config_mapping(mapping)
        storage.save_schedule(schedule)


def legacy_roots(wid):
    if wid == "default":
        return [storage.PROFILES_DIR]
    return [os.path.join(storage.WORKSPACES_DIR, wid)]


def snapshot_legacy(wid):
    """旧 configs/records/archive + 机型文件；排除 sync/cache/logs/locks。"""
    skip_dirs = {"sync", "cache", "logs", "locks", "__pycache__"}
    state = {}
    for root in legacy_roots(wid):
        for current, dirs, names in os.walk(root):
            dirs[:] = sorted(name for name in dirs if name not in skip_dirs)
            for name in sorted(names):
                path = os.path.join(current, name)
                if os.path.islink(path):
                    continue
                with open(path, "rb") as handle:
                    state[os.path.relpath(path, storage.DATA_DIR)] = hashlib.sha256(
                        handle.read()
                    ).hexdigest()
    return state


def snapshot_data():
    """整个数据根（含目录）的快照，用于证明 dry-run 零写入。"""
    state = {}
    for current, dirs, names in os.walk(storage.DATA_DIR):
        dirs.sort()
        state["dir:" + os.path.relpath(current, storage.DATA_DIR)] = None
        for name in sorted(names):
            path = os.path.join(current, name)
            with open(path, "rb") as handle:
                state[os.path.relpath(path, storage.DATA_DIR)] = hashlib.sha256(
                    handle.read()
                ).hexdigest()
    return state


def sources_of(wid, pid, status=None):
    return sync_sources.list_sources(wid, profile_id=pid, status=status, limit=200)["items"]


def load_cli():
    path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "scripts", "sync_upgrade.py")
    spec = importlib.util.spec_from_file_location("sync_upgrade_cli", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def seed_standard(wid, pid, interval=6, content=b"<R><x>1</x></R>"):
    seed_profile(
        wid,
        pid,
        [
            {
                "name": "Package.xml",
                "url": "https://example.invalid/Package.xml",
                "record_url": "https://example.invalid/records",
                "keyword": PACKAGE_RE,
            }
        ],
        {"enabled": True, "interval_hours": interval, "last_run": None},
        files=[("Package.xml", content)],
        records=[("Package.xml", "record_20260101_000000.xml", b"<R><old/></R>")],
        archives=[("Package.xml", "Package_20250101.xml", b"<R><older/></R>")],
    )


def test_two_workspaces_same_profile_name_are_lossless_and_keep_schedules(tmp_path):
    w1 = make_workspace("W1-upgrade")
    w2 = make_workspace("W2-upgrade")
    p1 = add_profile(w1, "LineA")
    p2 = add_profile(w2, "LineA")
    assert p1 == p2 == "linea"

    seed_standard(w1, p1, interval=6, content=b"<R><x>one</x></R>")
    seed_standard(w2, p2, interval=48, content=b"<R><x>two</x></R>")
    before1 = snapshot_legacy(w1)
    before2 = snapshot_legacy(w2)
    assert before1 != before2

    plan1 = sync_upgrade.plan(w1)
    plan2 = sync_upgrade.plan(w2)
    assert plan1["writes"] == 0 and plan2["writes"] == 0
    assert plan1["policy_overrides"][p1]["interval_hours"] == 6
    assert plan2["policy_overrides"][p2]["interval_hours"] == 48
    assert plan1["plan_id"] != plan2["plan_id"]

    first = sync_upgrade.execute(w1, plan1, str(tmp_path / "b1"))
    second = sync_upgrade.execute(w2, plan2, str(tmp_path / "b2"))

    assert snapshot_legacy(w1) == before1
    assert snapshot_legacy(w2) == before2
    assert [row["name"] for row in sources_of(w1, p1)] == ["Package.xml"]
    assert [row["name"] for row in sources_of(w2, p2)] == ["Package.xml"]
    assert sources_of(w1, p1)[0]["keyword"] == PACKAGE_RE
    assert sources_of(w2, p2)[0]["keyword"] == PACKAGE_RE
    assert sources_of(w1, p1)[0]["source_id"] != sources_of(w2, p2)[0]["source_id"]
    assert sources_of(w1, p1)[0]["target"]["file_id"]
    assert sources_of(w2, p2)[0]["target"]["file_id"]

    policy1 = sync_policy.get_policy(w1)
    policy2 = sync_policy.get_policy(w2)
    assert policy1["profile_overrides"][p1] == {"enabled": True, "interval_hours": 6}
    assert policy2["profile_overrides"][p2] == {"enabled": True, "interval_hours": 48}
    assert first["sources_created"] and second["sources_created"]

    # 旧版本数据仍可读：映射、计划、当前文件、记录、归档。
    with storage.use_workspace(w1), storage.use_profile(p1):
        assert storage.load_config_mapping()[0]["keyword"] == PACKAGE_RE
        assert storage.load_schedule()["interval_hours"] == 6
        assert storage.load_config_file("Package.xml") == b"<R><x>one</x></R>"
        record_path = os.path.join(
            storage.get_records_root_dir(p1), "Package.xml", "record_20260101_000000.xml"
        )
        archive_path = os.path.join(
            storage.get_archive_root_dir(p1), "Package.xml", "Package_20250101.xml"
        )
    assert open(record_path, "rb").read() == b"<R><old/></R>"
    assert open(archive_path, "rb").read() == b"<R><older/></R>"


def test_dry_run_writes_zero_bytes_including_cli(capsys):
    wid = make_workspace("W-dry")
    pid = add_profile(wid, "LineA")
    seed_standard(wid, pid)
    before = snapshot_data()
    backup_dir = os.path.join(storage.DATA_DIR, "..", "dry-backup")

    plan = sync_upgrade.plan(wid)
    assert plan["writes"] == 0
    assert plan["plan_id"].startswith("mig-")
    assert not os.path.exists(backup_dir)
    assert snapshot_data() == before

    cli = load_cli()
    assert cli.main(["plan", "--workspace", wid]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["writes"] == 0
    assert [row["name"] for row in payload["sources"]] == ["Package.xml"]
    assert snapshot_data() == before


def test_repeated_execute_does_not_duplicate_sources_or_rewrite_policy():
    wid = make_workspace("W-repeat")
    pid = add_profile(wid, "LineA")
    seed_standard(wid, pid)
    plan = sync_upgrade.plan(wid)
    backup_dir = os.path.join(storage.DATA_DIR, "..", "repeat-backup")

    first = sync_upgrade.execute(wid, plan, backup_dir)
    policy_after_first = sync_policy.get_policy(wid)
    source_ids = [row["source_id"] for row in sources_of(wid, pid)]
    assert len(first["sources_created"]) == 1

    second = sync_upgrade.execute(wid, plan, backup_dir)
    assert second["sources_created"] == []
    assert second["writes"] == 0
    assert second["backup_reused"] is True
    assert [row["source_id"] for row in sources_of(wid, pid)] == source_ids
    assert sync_policy.get_policy(wid) == policy_after_first
    assert policy_after_first["revision"] == 1


def test_partial_failure_rolls_back_sync_and_can_be_retried(monkeypatch):
    wid = make_workspace("W-partial")
    pid = add_profile(wid, "LineA")
    seed_profile(
        wid,
        pid,
        [
            {"name": "A.xml", "url": "https://example.invalid/A.xml"},
            {"name": "B.xml", "url": "https://example.invalid/B.xml"},
        ],
        {"enabled": True, "interval_hours": 12, "last_run": None},
        files=[("A.xml", b"<R><a/></R>"), ("B.xml", b"<R><b/></R>")],
    )
    before = snapshot_legacy(wid)
    plan = sync_upgrade.plan(wid)
    backup_dir = os.path.join(storage.DATA_DIR, "..", "partial-backup")

    original = sync_sources.create_source
    state = {"calls": 0}

    def flaky(workspace_id, payload):
        state["calls"] += 1
        if state["calls"] == 2:
            raise AppError(STORAGE_FAILURE, "simulated partial write")
        return original(workspace_id, payload)

    monkeypatch.setattr(sync_sources, "create_source", flaky)
    with pytest.raises(AppError) as exc:
        sync_upgrade.execute(wid, plan, backup_dir)
    assert exc.value.code == STORAGE_FAILURE

    assert sources_of(wid, pid) == []
    assert not os.path.exists(sync_paths.sync_file(wid, "sources.json"))
    assert not os.path.exists(sync_paths.sync_file(wid, "policy.json"))
    assert sync_policy.get_policy(wid)["revision"] == 0
    assert snapshot_legacy(wid) == before

    state["calls"] = -10 ** 9
    result = sync_upgrade.execute(wid, plan, backup_dir)
    assert len(result["sources_created"]) == 2
    assert [row["name"] for row in sources_of(wid, pid)] == ["A.xml", "B.xml"]
    assert snapshot_legacy(wid) == before
    with storage.use_workspace(wid), storage.use_profile(pid):
        assert storage.load_config_file("A.xml") == b"<R><a/></R>"
        assert storage.load_config_file("B.xml") == b"<R><b/></R>"


def test_public_rollback_restores_recorded_paths_and_removes_new_sync_files():
    wid = make_workspace("W-rollback")
    pid = add_profile(wid, "LineA")
    seed_standard(wid, pid)
    before = snapshot_legacy(wid)
    plan = sync_upgrade.plan(wid)
    backup_dir = os.path.join(storage.DATA_DIR, "..", "rollback-backup")
    sync_upgrade.execute(wid, plan, backup_dir)
    assert sources_of(wid, pid)

    with storage.use_workspace(wid), storage.use_profile(pid):
        config_path = storage.get_config_path("Package.xml")
        record_path = os.path.join(
            storage.get_records_root_dir(pid), "Package.xml", "record_20260101_000000.xml"
        )
    write_bytes(config_path, b"<R><tampered/></R>")
    os.unlink(record_path)
    extra_path = os.path.join(
        storage.WORKSPACES_DIR, wid, "profiles", pid, "configs", "Extra.xml"
    )
    write_bytes(extra_path, b"<R><new/></R>")

    result = sync_upgrade.rollback(wid, backup_dir)
    assert result["verified"] is True
    assert os.path.relpath(config_path, storage.DATA_DIR).replace(os.sep, "/") in result["restored"]
    assert os.path.exists(record_path)
    assert open(config_path, "rb").read() == b"<R><x>1</x></R>"
    assert sources_of(wid, pid) == []
    assert not os.path.exists(sync_paths.sync_file(wid, "sources.json"))
    assert not os.path.exists(sync_paths.sync_file(wid, "policy.json"))
    # manifest 之外的新文件不属于回退范围，原样保留。
    assert open(extra_path, "rb").read() == b"<R><new/></R>"
    after = snapshot_legacy(wid)
    extra_rel = os.path.relpath(extra_path, storage.DATA_DIR)
    after.pop(extra_rel)
    assert after == before


def test_backup_skips_secret_files_and_magic(tmp_path):
    wid = make_workspace("W-secret")
    pid = add_profile(wid, "LineA")
    seed_standard(wid, pid)
    profile_root = workspace_catalog.profile_root(wid, pid)
    write_bytes(os.path.join(profile_root, "planted.enc"), b"ciphertext")
    write_bytes(os.path.join(profile_root, "planted.key"), b"private-key")
    write_bytes(os.path.join(profile_root, "magic.xml"), b"<R>M16SEC1-secret</R>")
    backup_dir = str(tmp_path / "secret-backup")

    manifest = sync_upgrade.backup(wid, backup_dir)
    skipped = {row["path"] for row in manifest["skipped_secrets"]}
    assert any(path.endswith("planted.enc") for path in skipped)
    assert any(path.endswith("planted.key") for path in skipped)
    assert any(path.endswith("magic.xml") for path in skipped)
    assert manifest["file_count"] >= 1

    for row in manifest["files"]:
        assert not os.path.isabs(row["path"])
        assert ".." not in row["path"].split("/")
        assert row["sha256"]
        assert row["size"] >= 0
        assert row["timestamp"]
    for current, _dirs, names in os.walk(backup_dir):
        for name in names:
            if name == sync_upgrade.BACKUP_MANIFEST:
                continue
            assert not name.lower().endswith((".enc", ".key"))
            with open(os.path.join(current, name), "rb") as handle:
                assert b"M16SEC1" not in handle.read()

    with open(os.path.join(backup_dir, sync_upgrade.BACKUP_MANIFEST), "r", encoding="utf-8") as handle:
        raw = handle.read()
    assert "M16SEC1" not in raw
    assert sync_upgrade.verify(wid, backup_dir)["ok"] is True
    assert sources_of(wid, pid) == []


def test_stale_plan_is_refused_before_backup_or_write():
    wid = make_workspace("W-stale")
    pid = add_profile(wid, "LineA")
    seed_standard(wid, pid)
    plan = sync_upgrade.plan(wid)
    backup_dir = os.path.join(storage.DATA_DIR, "..", "stale-backup")

    with storage.use_workspace(wid), storage.use_profile(pid):
        storage.save_config_mapping(
            [{"name": "Changed.xml", "url": "https://example.invalid/Changed.xml"}]
        )

    with pytest.raises(AppError) as exc:
        sync_upgrade.execute(wid, plan, backup_dir)
    assert exc.value.code == REVISION_CONFLICT
    assert not os.path.exists(backup_dir)
    assert not os.path.exists(sync_paths.sync_root(wid))
    assert sources_of(wid, pid) == []


def test_targetless_draft_is_warned_and_never_fabricated():
    wid = make_workspace("W-targetless")
    pid = add_profile(wid, "LineA")
    seed_profile(
        wid,
        pid,
        [{"name": "gone.xml", "url": "https://example.invalid/gone.xml"}],
        {"enabled": False, "interval_hours": 24, "last_run": None},
    )
    before = snapshot_legacy(wid)
    plan = sync_upgrade.plan(wid)
    assert plan["sources"][0]["target"] is None
    assert any(row["reason"] == "target_file_missing" for row in plan["warnings"])

    result = sync_upgrade.execute(wid, plan, os.path.join(storage.DATA_DIR, "..", "targetless"))
    assert result["sources_created"] == []
    assert any(row["reason"] == "target_missing" for row in result["sources_skipped"])
    assert sources_of(wid, pid) == []
    assert snapshot_legacy(wid) == before


def test_verify_reports_missing_and_changed_files_and_rollback_refuses_tamper():
    wid = make_workspace("W-tamper")
    pid = add_profile(wid, "LineA")
    seed_standard(wid, pid)
    plan = sync_upgrade.plan(wid)
    backup_dir = os.path.join(storage.DATA_DIR, "..", "tamper-backup")
    sync_upgrade.execute(wid, plan, backup_dir)

    manifest_path = os.path.join(backup_dir, sync_upgrade.BACKUP_MANIFEST)
    with open(manifest_path, "r", encoding="utf-8") as handle:
        manifest = json.load(handle)
    victim = next(row for row in manifest["files"] if row["path"].endswith("config_mapping.json"))
    victim_path = os.path.join(backup_dir, *victim["path"].split("/"))
    write_bytes(victim_path, b"[]")

    report = sync_upgrade.verify(wid, backup_dir)
    assert report["ok"] is False
    assert any(row["path"] == victim["path"] and row["status"] == "changed" for row in report["problems"])
    with pytest.raises(AppError) as exc:
        sync_upgrade.rollback(wid, backup_dir)
    assert exc.value.code == INVALID_INPUT
    assert sources_of(wid, pid)

    os.unlink(victim_path)
    report = sync_upgrade.verify(wid, backup_dir)
    assert report["ok"] is False
    assert any(row["path"] == victim["path"] and row["status"] == "missing" for row in report["problems"])


def test_cli_execute_verify_rollback_are_json_and_failure_is_nonzero(capsys):
    wid = make_workspace("W-cli")
    pid = add_profile(wid, "LineA")
    seed_standard(wid, pid)
    backup_dir = os.path.join(storage.DATA_DIR, "..", "cli-backup")
    cli = load_cli()

    assert cli.main(["execute", "--workspace", wid, "--backup-dir", backup_dir]) == 0
    executed = json.loads(capsys.readouterr().out)
    assert len(executed["sources_created"]) == 1
    assert "M16SEC1" not in json.dumps(executed)

    assert cli.main(["verify", "--workspace", wid, "--backup-dir", backup_dir]) == 0
    verified = json.loads(capsys.readouterr().out)
    assert verified["ok"] is True

    assert cli.main(["rollback", "--workspace", wid, "--backup-dir", backup_dir]) == 0
    rolled = json.loads(capsys.readouterr().out)
    assert rolled["verified"] is True
    assert sources_of(wid, pid) == []

    os.unlink(os.path.join(backup_dir, sync_upgrade.BACKUP_MANIFEST))
    assert cli.main(["verify", "--workspace", wid, "--backup-dir", backup_dir]) == 1
    failed = json.loads(capsys.readouterr().out)
    assert failed["ok"] is False
