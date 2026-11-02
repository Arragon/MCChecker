"""M16-38 secret 扫描器：规则、模式、掩码与 allowlist 双向校验。"""

from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "scripts" / "scan_secrets.py"

GIT_ENV = {
    "GIT_AUTHOR_NAME": "scan",
    "GIT_AUTHOR_EMAIL": "scan@example.invalid",
    "GIT_COMMITTER_NAME": "scan",
    "GIT_COMMITTER_EMAIL": "scan@example.invalid",
}


def load_module():
    spec = importlib.util.spec_from_file_location("scan_secrets_under_test", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


SCAN = load_module()


def git(root: Path, *args: str) -> str:
    env = dict(os.environ, **GIT_ENV)
    proc = subprocess.run(
        ["git"] + list(args),
        cwd=str(root),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr.decode("utf-8", "replace")
    return proc.stdout.decode("utf-8", "replace")


def make_repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    git(root, "init", "-q")
    git(root, "config", "user.email", "scan@example.invalid")
    git(root, "config", "user.name", "scan")
    return root


def commit_file(root: Path, name: str, content: str, message: str = "add") -> None:
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    git(root, "add", name)
    git(root, "commit", "-q", "-m", message)


def run_scan(root: Path, *args: str, allowlist: Path = None) -> tuple:
    argv = ["--root", str(root)]
    if allowlist is not None:
        argv += ["--allowlist", str(allowlist)]
    argv += list(args)
    out = io.StringIO()
    err = io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = SCAN.main(argv)
    return code, out.getvalue()


def test_tree_mode_finds_aws_key_and_masks_it(tmp_path):
    root = make_repo(tmp_path)
    secret = "AKIA" + "QWERTYUIOPASDFGH"
    commit_file(root, "conf.py", 'KEY = "%s"\n' % secret)
    code, out = run_scan(root, "--tree", allowlist=tmp_path / "none.json")
    assert code == 1
    assert "conf.py:1" in out
    assert "aws-access-key" in out
    assert secret not in out, "输出不得包含完整 secret"
    assert secret[:4] in out and secret[-4:] in out


def test_clean_repo_exits_zero(tmp_path):
    root = make_repo(tmp_path)
    commit_file(root, "app.py", "print('hello')\n")
    code, out = run_scan(root, "--tree", allowlist=tmp_path / "none.json")
    assert code == 0
    assert "命中 0 条" in out


def test_placeholder_values_are_ignored(tmp_path):
    root = make_repo(tmp_path)
    commit_file(root, "conf.py", 'password = "FAKE-PASSWORD-XYZ"\ntoken = "changeme-please"\n')
    code, _out = run_scan(root, "--tree", allowlist=tmp_path / "none.json")
    assert code == 0


def test_allowlist_suppresses_and_reports_stale_entries(tmp_path):
    root = make_repo(tmp_path)
    commit_file(root, "conf.py", 'password = "real-looking-pass"\n')
    allowlist = tmp_path / "allow.json"
    allowlist.write_text(
        json.dumps(
            {
                "entries": [
                    {"path": "conf.py", "rule": "generic-assignment", "line": 1, "reason": "合成样例"},
                    {"path": "other.py", "rule": "generic-assignment", "line": 9, "reason": "已删除代码"},
                ]
            }
        ),
        encoding="utf-8",
    )
    code, out = run_scan(root, "--tree", allowlist=allowlist)
    assert code == 0
    assert "放行 1 条" in out
    assert "1 条未命中" in out

    code, _out = run_scan(root, "--tree", "--strict-allowlist", allowlist=allowlist)
    assert code == 1, "开启 strict 后失效的 allowlist 条目要让扫描失败"


def test_allowlist_requires_reason(tmp_path):
    root = make_repo(tmp_path)
    commit_file(root, "conf.py", "print(1)\n")
    allowlist = tmp_path / "allow.json"
    allowlist.write_text(json.dumps([{"path": "conf.py", "rule": "jwt"}]), encoding="utf-8")
    with pytest.raises(SystemExit) as exc:
        run_scan(root, "--tree", allowlist=allowlist)
    assert exc.value.code == 2


def test_staged_mode_only_sees_index(tmp_path):
    root = make_repo(tmp_path)
    commit_file(root, "app.py", "print(1)\n")
    (root / "new.py").write_text('token = "ghp_' + "a" * 24 + '"\n', encoding="utf-8")
    git(root, "add", "new.py")
    code, out = run_scan(root, "--staged", allowlist=tmp_path / "none.json")
    assert code == 1
    assert "new.py:1" in out and "github-token" in out
    assert "app.py" not in out, "暂存区模式只看新增行"


def test_range_mode_reports_added_lines_only(tmp_path):
    root = make_repo(tmp_path)
    commit_file(root, "app.py", "print(1)\n")
    first = git(root, "rev-parse", "HEAD").strip()
    commit_file(root, "app.py", 'print(1)\nJWT = "eyJabcdefgh.eyJijklmnop.qrstuvwxyz"\n', "add jwt")
    code, out = run_scan(root, "--range", "%s..HEAD" % first, allowlist=tmp_path / "none.json")
    assert code == 1
    assert "app.py:2" in out and "jwt" in out


def test_all_history_finds_secret_in_deleted_file(tmp_path):
    root = make_repo(tmp_path)
    secret = "AKIA" + "ZXCVBNMLKJHGFDSA"
    commit_file(root, "old.py", 'KEY = "%s"\n' % secret)
    git(root, "rm", "-q", "old.py")
    git(root, "commit", "-q", "-m", "remove")

    code, _out = run_scan(root, "--tree", allowlist=tmp_path / "none.json")
    assert code == 0, "当前树已无该文件"

    code, out = run_scan(root, "--all-history", allowlist=tmp_path / "none.json")
    assert code == 1
    assert "old.py" in out and "aws-access-key" in out
    assert secret not in out


def test_all_history_dedupes_identical_hits(tmp_path):
    root = make_repo(tmp_path)
    content = 'KEY = "AKIA' + "QWERTYUIOPASDFGH" + '"\n'
    commit_file(root, "a.py", content)
    commit_file(root, "a.py", content + "\n", "touch")
    code, out = run_scan(root, "--all-history", allowlist=tmp_path / "none.json")
    assert code == 1
    assert out.count("aws-access-key") == 1, "同一 path/rule/evidence 只报一次"


def test_not_a_git_repository_exits_two(tmp_path):
    plain = tmp_path / "plain"
    plain.mkdir()
    (plain / "x.txt").write_text("hello", encoding="utf-8")
    code, _out = run_scan(plain, "--tree", allowlist=tmp_path / "none.json")
    assert code == 2


def test_high_entropy_ignores_identifiers_and_hashes(tmp_path):
    root = make_repo(tmp_path)
    body = (
        "def test_workspace_context_http_root_helper():\n"
        "    digest = 'a' * 64\n"
        "    return digest\n"
    )
    commit_file(root, "noise.py", body)
    code, out = run_scan(root, "--tree", allowlist=tmp_path / "none.json")
    assert code == 0, out


def test_json_output_is_machine_readable(tmp_path):
    root = make_repo(tmp_path)
    commit_file(root, "conf.py", 'password = "super-secret-value"\n')
    code, out = run_scan(root, "--tree", "--json", allowlist=tmp_path / "none.json")
    assert code == 1
    payload = json.loads(out)
    assert payload["mode"] == "tree"
    assert payload["hits"][0]["path"] == "conf.py"
    assert payload["hits"][0]["rule"] == "generic-assignment"
    assert "super-secret-value" not in out


def test_repository_itself_is_clean():
    """仓库当前树与全历史都必须干净（allowlist 之外没有命中）。"""
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        code = SCAN.main(["--root", str(REPO_ROOT), "--tree"])
    assert code == 0, out.getvalue()
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        code = SCAN.main(["--root", str(REPO_ROOT), "--all-history"])
    assert code == 0, out.getvalue()


def test_scan_does_not_modify_repository(tmp_path):
    root = make_repo(tmp_path)
    commit_file(root, "app.py", "print(1)\n")
    before = git(root, "status", "--porcelain")
    run_scan(root, "--tree", allowlist=tmp_path / "none.json")
    assert git(root, "status", "--porcelain") == before
