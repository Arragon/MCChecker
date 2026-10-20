"""恢复区 --dry-run（M4-09 / INH-1911；§14.6）。"""

from __future__ import annotations

import hashlib
import json
import os

import run
from app.config import load_settings


def _entry(tmp_path):
    entry = tmp_path / "recovery" / "20261020_120000_delete_file_p_a.xml"
    (entry / "configs").mkdir(parents=True)
    (entry / "configs" / "a.xml").write_text("<R/>", encoding="utf-8")
    manifest = {
        "op": "delete_file",
        "profile": "p",
        "target": "a.xml",
        "moved": [{"from": "profiles/p/configs/a.xml", "to": "configs/a.xml"}],
        "removed_refs": {"favorites": [{"file": "a.xml", "path": "root"}]},
    }
    (entry / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return entry


def _tree(root):
    if not os.path.isdir(root):
        return None
    out = {}
    for base, dirs, names in os.walk(root):
        for name in names:
            path = os.path.join(base, name)
            with open(path, "rb") as handle:
                out[os.path.relpath(path, root)] = hashlib.sha256(handle.read()).hexdigest()
    return out


def test_prints_plan_and_does_not_touch_authoritative_data(tmp_path, capsys):
    entry = _entry(tmp_path)
    data_dir = tmp_path / "restore-data"
    before = _tree(str(data_dir))
    code = run.main(["--restore", str(entry), "--dry-run", "--data-dir", str(data_dir)])
    out = capsys.readouterr()
    assert code == 0, out.err
    assert "恢复计划（dry-run" in out.out
    assert str(entry / "configs" / "a.xml") in out.out
    assert str(data_dir / "profiles" / "p" / "configs" / "a.xml") in out.out
    assert "removed_refs.favorites" in out.out
    assert _tree(str(data_dir)) == before, "dry-run 不得创建/修改数据根"
    assert not data_dir.exists(), "dry-run 连 data 根目录都不该创建"


def test_missing_entry_is_nonzero(tmp_path, capsys):
    code = run.main(["--restore", str(tmp_path / "nope"), "--dry-run", "--data-dir", str(tmp_path / "restore-data")])
    err = capsys.readouterr().err
    assert code == 2 and "恢复条目不存在" in err, err


def test_without_dry_run_is_refused(tmp_path, capsys):
    entry = _entry(tmp_path)
    code = run.main(["--restore", str(entry), "--data-dir", str(tmp_path / "restore-data")])
    err = capsys.readouterr().err
    assert code == 2 and "--dry-run" in err, err
    assert not (tmp_path / "restore-data" / "profiles").exists()


def test_settings_parse_restore_flags(tmp_path):
    entry = _entry(tmp_path)
    settings = load_settings(["--restore", str(entry), "--dry-run", "--data-dir", str(tmp_path / "restore-data")])
    assert settings.restore_dir == str(entry)
    assert settings.dry_run is True


def test_missing_manifest_is_nonzero(tmp_path, capsys):
    entry = tmp_path / "empty-entry"
    entry.mkdir()
    code = run.main(["--restore", str(entry), "--dry-run", "--data-dir", str(tmp_path / "restore-data")])
    err = capsys.readouterr().err
    assert code == 2 and "manifest.json" in err, err
