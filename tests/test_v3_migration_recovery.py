"""M15-02 迁移执行/幂等/回退（INH-2157）。"""

from __future__ import annotations

import json
import os
import subprocess
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _write(path, content):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as handle:
        handle.write(content.encode("utf-8") if isinstance(content, str) else content)


def _run(*args):
    return subprocess.run([sys.executable] + list(args), cwd=REPO, capture_output=True, text=True)


def _setup(tmp_path):
    data = tmp_path / "data"
    _write(str(data / "profiles/default/files/a.xml"), b"<A>1</A>")
    _write(str(data / "profiles/second/files/b.xml"), b"<B>2</B>")
    _write(str(data / "workspaces/w1/profiles/default/files/c.xml"), b"<C>3</C>")
    _write(str(data / "ip_mapping.json"), b'{"10.0.0.1":"A"}')
    _write(str(data / "workspace_catalog.json"), json.dumps({"schema": 1, "workspaces": [
        {"id": "w1", "name": "W1", "default_profile_id": "default", "profiles": [{"id": "default", "name": "默认"}]}
    ]}))
    backup = tmp_path / "backup"
    assert _run("scripts/backup_v3.py", "backup", "--data-dir", str(data), "--out", str(backup)).returncode == 0
    return data, backup


def test_execute_is_idempotent_and_rollback_keeps_other_workspaces(tmp_path):
    data, backup = _setup(tmp_path)
    plan = _run("scripts/migrate_v3.py", "plan", "--data-dir", str(data))
    assert plan.returncode == 0 and json.loads(plan.stdout)["source_files"] == 2
    no_backup = _run("scripts/migrate_v3.py", "execute", "--data-dir", str(data))
    assert no_backup.returncode != 0
    first = _run("scripts/migrate_v3.py", "execute", "--data-dir", str(data), "--backup", str(backup))
    assert first.returncode == 0, first.stderr
    assert (data / "workspaces/legacy-default/profiles/default/files/a.xml").read_bytes() == b"<A>1</A>"
    assert (data / "workspaces/legacy-default/profiles/second/files/b.xml").read_bytes() == b"<B>2</B>"
    catalog = json.loads((data / "workspace_catalog.json").read_text())
    ids = {row["id"] for row in catalog["workspaces"]}
    assert {"legacy-default", "w1"} <= ids
    before_ip = (data / "ip_mapping.json").read_bytes()

    again = _run("scripts/migrate_v3.py", "execute", "--data-dir", str(data), "--backup", str(backup))
    assert again.returncode == 0 and json.loads(again.stdout)["published"] == 0, again.stdout
    _write(str(data / "profiles/default/files/new.xml"), b"<N>4</N>")
    mixed = _run("scripts/migrate_v3.py", "execute", "--data-dir", str(data), "--backup", str(backup))
    assert mixed.returncode == 0 and json.loads(mixed.stdout)["published"] == 1

    rollback = _run("scripts/migrate_v3.py", "rollback", "--data-dir", str(data), "--yes")
    assert rollback.returncode == 0, rollback.stderr
    assert not (data / "workspaces/legacy-default").exists()
    assert (data / "workspaces/w1/profiles/default/files/c.xml").read_bytes() == b"<C>3</C>"
    assert (data / "profiles/default/files/a.xml").read_bytes() == b"<A>1</A>", "回退不得删除旧根"
    catalog = json.loads((data / "workspace_catalog.json").read_text())
    assert [row["id"] for row in catalog["workspaces"]] == ["w1"]
    assert (data / "ip_mapping.json").read_bytes() == before_ip


def test_conflicting_target_aborts_without_publishing(tmp_path):
    data, backup = _setup(tmp_path)
    _write(str(data / "workspaces/legacy-default/profiles/default/files/a.xml"), b"<A>DIFFERENT</A>")
    result = _run("scripts/migrate_v3.py", "execute", "--data-dir", str(data), "--backup", str(backup))
    assert result.returncode != 0
    assert (data / "workspaces/legacy-default/profiles/default/files/a.xml").read_bytes() == b"<A>DIFFERENT</A>"
    assert not (data / "workspaces/legacy-default/profiles/second/files/b.xml").exists()
