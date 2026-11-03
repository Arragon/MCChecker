"""M16-42 collector 独立发布包：打包、manifest 与校验（不联网）。"""

from __future__ import annotations

import importlib.util
import json
import zipfile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "scripts" / "collector_bundle.py"


def load_module():
    spec = importlib.util.spec_from_file_location("collector_bundle_under_test", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


BUNDLE = load_module()


def make_source(tmp_path: Path) -> Path:
    root = tmp_path / "src"
    collector = root / "collector"
    collector.mkdir(parents=True)
    (collector / "main.py").write_text("print('collector')\n", encoding="utf-8")
    (collector / "requirements.lock").write_text("playwright==0.0.0\n", encoding="utf-8")
    (collector / "__pycache__").mkdir()
    (collector / "__pycache__" / "junk.pyc").write_bytes(b"\x00\x01")
    (collector / "nested").mkdir()
    (collector / "nested" / "util.py").write_text("VALUE = 1\n", encoding="utf-8")
    return root


def test_build_then_verify_ok(tmp_path):
    root = make_source(tmp_path)
    out = tmp_path / "out" / "collector.zip"
    code = BUNDLE.main(["--source-root", str(root), "--out", str(out)])
    assert code == 0
    result = BUNDLE.verify(str(out))
    assert result["ok"] is True
    assert result["members"] >= 4  # main/lock/nested/util + MANIFEST
    assert result["chromium_bundled"] is False

    with zipfile.ZipFile(out) as archive:
        manifest = json.loads(archive.read("MANIFEST.json").decode("utf-8"))
        names = archive.namelist()
    assert manifest["python"] == "3.12"
    assert manifest["entry"] == "collector/main.py"
    assert manifest["chromium"]["bundled"] is False
    assert all("__pycache__" not in name for name in names)
    assert not any(name.endswith(".pyc") for name in names)


def test_tampered_member_fails_verify(tmp_path):
    root = make_source(tmp_path)
    out = tmp_path / "collector.zip"
    assert BUNDLE.main(["--source-root", str(root), "--out", str(out)]) == 0
    with zipfile.ZipFile(out) as archive:
        members = {name: archive.read(name) for name in archive.namelist()}
    members["collector/main.py"] = b"print('tampered')\n"
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, data in members.items():
            archive.writestr(name, data)
    result = BUNDLE.verify(str(out))
    assert result["ok"] is False
    assert result["hash_mismatch"] == ["collector/main.py"]


def test_extra_member_fails_verify(tmp_path):
    root = make_source(tmp_path)
    out = tmp_path / "collector.zip"
    assert BUNDLE.main(["--source-root", str(root), "--out", str(out)]) == 0
    with zipfile.ZipFile(out, "a") as archive:
        archive.writestr("collector/backdoor.py", "print('x')\n")
    result = BUNDLE.verify(str(out))
    assert result["ok"] is False
    assert result["extra"] == ["collector/backdoor.py"]


def test_secret_marker_fails_verify(tmp_path):
    root = make_source(tmp_path)
    (root / "collector" / "leaked.py").write_text('KEY = "AKIA' + "A" * 16 + '"\n', encoding="utf-8")
    out = tmp_path / "collector.zip"
    assert BUNDLE.main(["--source-root", str(root), "--out", str(out)]) == 1
    result = BUNDLE.verify(str(out))
    assert result["ok"] is False
    assert result["secret_markers"]


def test_wheels_and_chromium_flags_recorded(tmp_path):
    root = make_source(tmp_path)
    wheels = tmp_path / "wheels"
    wheels.mkdir()
    (wheels / "playwright-0.0.0-py3-none-any.whl").write_bytes(b"wheel-bytes")
    chromium = tmp_path / "chromium"
    (chromium / "chrome-linux").mkdir(parents=True)
    (chromium / "chrome-linux" / "chrome").write_bytes(b"#!binary")

    out = tmp_path / "collector-full.zip"
    assert BUNDLE.main(
        ["--source-root", str(root), "--out", str(out), "--wheels", str(wheels), "--chromium", str(chromium)]
    ) == 0
    result = BUNDLE.verify(str(out))
    assert result["ok"] is True
    assert result["chromium_bundled"] is True
    assert result["wheels_bundled"] is True


def test_existing_output_requires_force(tmp_path):
    root = make_source(tmp_path)
    out = tmp_path / "collector.zip"
    assert BUNDLE.main(["--source-root", str(root), "--out", str(out)]) == 0
    with pytest.raises(SystemExit):
        BUNDLE.build(str(root), str(out), None, None, False)


def test_missing_collector_dir_is_rejected(tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()
    with pytest.raises(SystemExit):
        BUNDLE.build(str(empty), str(tmp_path / "x.zip"), None, None, True)
