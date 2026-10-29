"""M15-01 旧数据 dry-run 与备份/恢复先验（INH-2156）。"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _write(path, content):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as handle:
        handle.write(content)


def _tree(root):
    out = {}
    for current, _dirs, files in os.walk(root):
        for name in sorted(files):
            path = os.path.join(current, name)
            with open(path, "rb") as handle:
                out[os.path.relpath(path, root)] = hashlib.sha256(handle.read()).hexdigest()
    return out


def _run(*args):
    return subprocess.run([sys.executable] + list(args), cwd=REPO,
                          capture_output=True, text=True)


def test_dry_run_manifest_maps_legacy_and_never_writes(tmp_path):
    data = tmp_path / "data"
    _write(str(data / "profiles/default/files/a.xml"), b"<R><A>1</A></R>")
    _write(str(data / "profiles/default/archive/a.xml/a_1.xml"), b"<R><A>0</A></R>")
    _write(str(data / "workspaces/w1/profiles/default/files/b.json"), b'{"B":1}')
    _write(str(data / "config_management/state.json"), b'{"seq":1}')
    _write(str(data / "ip_mapping.json"), b'{"10.0.0.1":"A"}')
    before = _tree(str(data))
    manifest = tmp_path / "manifest.json"
    result = _run("scripts/inspect_v3_workspace.py", "--data-dir", str(data), "--out", str(manifest))
    assert result.returncode == 0, result.stderr
    assert _tree(str(data)) == before, "dry-run 前后旧根 sha 必须相同"
    doc = json.loads(manifest.read_text())
    kinds = {row["kind"] for row in doc["entries"]}
    assert {"file", "archive", "managed_readonly", "global_identity"} <= kinds
    assert any(row["workspace_id"] == "legacy-default" for row in doc["entries"])
    assert any(row["workspace_id"] == "w1" for row in doc["entries"])
    assert doc["dry_run"] is True and doc["total"] == len(doc["entries"])


def test_backup_verify_and_restore_round_trip(tmp_path):
    data = tmp_path / "data"
    _write(str(data / "profiles/default/files/a.xml"), b"<R><A>1</A></R>")
    _write(str(data / "workspaces/w1/profiles/default/files/b.json"), b'{"B":1}')
    _write(str(data / "ip_mapping.json"), b'{"10.0.0.1":"A"}')
    backup = tmp_path / "backup"
    result = _run("scripts/backup_v3.py", "backup", "--data-dir", str(data), "--out", str(backup))
    assert result.returncode == 0, result.stderr
    verified = _run("scripts/backup_v3.py", "verify", "--backup", str(backup))
    assert verified.returncode == 0, verified.stdout + verified.stderr
    before = _tree(str(data))

    _write(str(data / "profiles/default/files/a.xml"), b"<R><A>2</A></R>")
    dry = _run("scripts/backup_v3.py", "restore", "--backup", str(backup),
               "--data-dir", str(data), "--dry-run")
    assert dry.returncode == 0 and json.loads(dry.stdout)["dry_run"] is True
    assert _tree(str(data)) != before, "dry-run 不应改写被篡改文件"
    result = _run("scripts/backup_v3.py", "restore", "--backup", str(backup),
                  "--data-dir", str(data), "--force")
    assert result.returncode == 0, result.stderr
    assert _tree(str(data)) == before, "恢复后必须回到备份 hash"

    with open(str(backup / "profiles/default/files/a.xml"), "ab") as handle:
        handle.write(b"bad")
    broken = _run("scripts/backup_v3.py", "verify", "--backup", str(backup))
    assert broken.returncode == 1
    assert json.loads(broken.stdout)["ok"] is False
