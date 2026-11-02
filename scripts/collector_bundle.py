"""M16-42 collector 独立发布包：打包与校验（与核心 zip 分离，D12）。

核心发布包（`make_release.py`）只含 Python 3.9 主应用；collector 需要 Python 3.12 +
Playwright + Chromium + cryptography，必须单独分发，并带 hash manifest 供断网安装校验。

本工具只做**打包与校验**，不下载任何东西：
- 默认把 `collector/` 的源码与依赖锁打进 zip，`chromium.bundled=false` 并在 manifest 里写明
  真实浏览器/轮子必须由目标机（Windows/Linux）实机安装后回填（E4/E5，BLOCKED-EXTERNAL）。
- 给了 `--wheels DIR` / `--chromium DIR` 才把对应目录一起打包并记录 hash。

用法：
    python scripts/collector_bundle.py --out dist/collector-bundle.zip
    python scripts/collector_bundle.py --out dist/collector-bundle.zip --wheels /path/wheels --chromium /path/chromium
    python scripts/collector_bundle.py --verify-only --out dist/collector-bundle.zip
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import zipfile
from typing import Dict, Iterable, List, Optional, Sequence

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
COLLECTOR_DIR = "collector"
MANIFEST_NAME = "MANIFEST.json"
BUNDLE_VERSION = "3.1.0-collector"
FORBIDDEN_PARTS = ("__pycache__", ".venv", "node_modules", ".git", "data", "tests")
FORBIDDEN_SUFFIXES = (".pyc", ".pyo", ".log")
SECRET_MARKERS = ("BEGIN RSA PRIVATE KEY", "BEGIN OPENSSH PRIVATE KEY", "AKIA", "ghp_", "xoxb-")


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _iter_files(root: str, prefix: str) -> Iterable[str]:
    for name in sorted(os.listdir(root)):
        path = os.path.join(root, name)
        rel = prefix + "/" + name
        if os.path.isdir(path):
            for child in _iter_files(path, rel):
                yield child
        else:
            yield rel


def _should_skip(rel: str) -> bool:
    parts = rel.replace("\\", "/").split("/")
    if any(part in FORBIDDEN_PARTS for part in parts):
        return True
    return rel.endswith(FORBIDDEN_SUFFIXES)


def _scan_bytes(rel: str, data: bytes) -> Optional[str]:
    if b"\x00" in data:
        return None
    text = data.decode("utf-8", "ignore")
    for marker in SECRET_MARKERS:
        if marker in text:
            return marker
    return None


def build(source_root: str, out_path: str, wheels: Optional[str], chromium: Optional[str], force: bool) -> Dict[str, object]:
    collector_root = os.path.join(source_root, COLLECTOR_DIR)
    if not os.path.isdir(collector_root):
        raise SystemExit("找不到 collector 目录：%s" % collector_root)
    if os.path.exists(out_path) and not force:
        raise SystemExit("产物已存在（加 --force 覆盖）：%s" % out_path)

    members: Dict[str, bytes] = {}
    for rel in _iter_files(collector_root, COLLECTOR_DIR):
        if _should_skip(rel):
            continue
        with open(os.path.join(source_root, rel), "rb") as handle:
            members[rel] = handle.read()

    extras: List[str] = []
    if wheels:
        for rel in _iter_files(wheels, "wheels"):
            if _should_skip(rel):
                continue
            with open(os.path.join(wheels, rel[len("wheels/"):]), "rb") as handle:
                members[rel] = handle.read()
            extras.append(rel)
    if chromium:
        for rel in _iter_files(chromium, "chromium"):
            if _should_skip(rel):
                continue
            with open(os.path.join(chromium, rel[len("chromium/"):]), "rb") as handle:
                members[rel] = handle.read()
            extras.append(rel)

    files = [
        {"path": rel, "sha256": _sha256(data), "bytes": len(data)}
        for rel, data in sorted(members.items())
    ]
    manifest = {
        "bundle": BUNDLE_VERSION,
        "python": "3.12",
        "entry": COLLECTOR_DIR + "/main.py",
        "protocol": "mcc-collector-stdio-v1",
        "chromium": {
            "bundled": bool(chromium),
            "note": "" if chromium else "目标机实机安装后回填（E4/E5，BLOCKED-EXTERNAL）",
        },
        "wheels": {
            "bundled": bool(wheels),
            "count": len([rel for rel in extras if rel.startswith("wheels/")]),
            "note": "" if wheels else "断网安装所需的 wheel 由目标机 pip download 生成后回填",
        },
        "files": files,
    }
    payload = json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8")
    members[MANIFEST_NAME] = payload

    os.makedirs(os.path.dirname(os.path.abspath(out_path)) or ".", exist_ok=True)
    with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED) as archive:
        for rel in sorted(members):
            info = zipfile.ZipInfo(rel, date_time=(2026, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            archive.writestr(info, members[rel])
    return {"out": out_path, "members": len(members), "manifest": manifest}


def verify(out_path: str) -> Dict[str, object]:
    if not os.path.exists(out_path):
        raise SystemExit("产物不存在：%s" % out_path)
    with zipfile.ZipFile(out_path) as archive:
        names = [name for name in archive.namelist() if not name.endswith("/")]
        if MANIFEST_NAME not in names:
            raise SystemExit("缺少 %s" % MANIFEST_NAME)
        manifest = json.loads(archive.read(MANIFEST_NAME).decode("utf-8"))
        listed = {row["path"]: row for row in manifest.get("files") or []}
        actual = set(names) - {MANIFEST_NAME}
        missing = sorted(set(listed) - actual)
        extra = sorted(actual - set(listed))
        bad: List[str] = []
        secrets: List[str] = []
        for rel in sorted(actual & set(listed)):
            data = archive.read(rel)
            if _sha256(data) != listed[rel]["sha256"]:
                bad.append(rel)
            marker = _scan_bytes(rel, data)
            if marker:
                secrets.append("%s:%s" % (rel, marker))
        forbidden = sorted(
            name for name in names
            if any(part in FORBIDDEN_PARTS for part in name.split("/")) or name.endswith(FORBIDDEN_SUFFIXES)
        )
    ok = not (missing or extra or bad or secrets or forbidden)
    return {
        "ok": ok,
        "members": len(names),
        "missing": missing,
        "extra": extra,
        "hash_mismatch": bad,
        "secret_markers": secrets,
        "forbidden": forbidden,
        "chromium_bundled": bool((manifest.get("chromium") or {}).get("bundled")),
        "wheels_bundled": bool((manifest.get("wheels") or {}).get("bundled")),
    }


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="M16-42 collector 独立发布包（打包/校验）")
    parser.add_argument("--source-root", default=REPO_ROOT)
    parser.add_argument("--out", default=os.path.join(REPO_ROOT, "dist", "collector-bundle.zip"))
    parser.add_argument("--wheels", default=None, help="断网安装用的 wheel 目录（可选）")
    parser.add_argument("--chromium", default=None, help="Playwright Chromium 目录（可选）")
    parser.add_argument("--verify-only", action="store_true")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args(argv)

    if args.verify_only:
        result = verify(args.out)
        sys.stdout.write(json.dumps(result, ensure_ascii=False, sort_keys=True) + "\n")
        return 0 if result["ok"] else 1

    built = build(args.source_root, args.out, args.wheels, args.chromium, args.force)
    check = verify(args.out)
    sys.stdout.write(
        json.dumps(
            {
                "out": built["out"],
                "members": built["members"],
                "verify": check,
                "chromium_bundled": built["manifest"]["chromium"]["bundled"],
            },
            ensure_ascii=False,
            sort_keys=True,
        )
        + "\n"
    )
    return 0 if check["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
