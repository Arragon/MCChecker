"""离线发布包测试（M4-06 / INH-1908；规范 §14.3、§16.7）。

全部在 `tmp_path` 里造一棵**假源树**（不碰仓库 `data/`，不起服务、不联网、不 sleep）：
名单集合用测试里手写的字面量比对，所以「多打一个文件」和「少打一个文件」都会红。
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
import zipfile
from pathlib import Path
from typing import Dict, List

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import make_release as mr  # noqa: E402  （脚本不在包里，只能按路径导入）

# 假源树里**应当**进入产物的成员，手写字面量：与 RELEASE_ENTRIES 的展开结果一一对应。
EXPECTED_MEMBERS = {
    "README.md",
    "app/__init__.py",
    "app/config.py",
    "app/core/__init__.py",
    "app/core/storage.py",
    "app/web/assets/css/base.css",
    "app/web/assets/js/app.js",
    "app/web/index.html",
    "docs/manual.md",
    "main.py",
    "run.py",
    "start.bat",
    "start.sh",
}

# 应当被排除、且这台机器上确实存在的条目（.mimosa/.workbuddy/.superpowers 是草稿目录）。
EXCLUDED_SOURCE_PATHS = [
    "data/x.xml",
    "data/profiles/p1/files/a.xml",
    ".venv39/pyvenv.cfg",
    ".venv/bin/python",
    "tests/test_decoy.py",
    ".git/config",
    "app/__pycache__/config.cpython-39.pyc",
    "app/__pycache__/x.py",
    "app/core/__pycache__/storage.cpython-39.pyc",
    "app/web/template.html",
    "app/web/assets/js/mock/mock-api.js",
    "app/web/tests/index.html",
    "app/static/favicon.png.pyc",
    ".nicegui/storage-user-1.json",
    ".superpowers/secret",
    ".mimosa/secret",
    ".workbuddy/secret",
    ".pytest_cache/v/cache/lastfailed",
    ".claude/settings.json",
    "dist/MCChecker-old.zip",
    "requirements.txt",
    "docs/MCChecker_DELIVERY_PLAN.md",
    "app/data/leak.xml",  # 即便 data 出现在 app 下面也不许进包
]

FC_HEADINGS = "".join(
    "### %s 变更标题 %s\n- **新方案**：说明。\n\n" % (cid, cid)
    for cid in mr.RELEASE_NOTE_IDS
)

CLEAN_INDEX_HTML = """<!doctype html>
<html lang="zh-CN">
  <head>
    <meta charset="utf-8" />
    <link rel="icon" href="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg'/%3E" />
    <link rel="stylesheet" href="assets/css/base.css" />
  </head>
  <body>
    <div id="app"></div>
    <script src="config.js"></script>
    <script src="assets/js/app.js"></script>
  </body>
</html>
"""


@pytest.fixture()
def source_tree(tmp_path: Path) -> Path:
    """一棵最小但完整的假源树：白名单条目 + 全部排除项 + 发布说明来源文档。"""
    root = tmp_path / "src"
    files: Dict[str, str] = {
        "README.md": "# MCChecker\n\n内网工具，默认端口 50002。\n",
        "run.py": '"""入口。"""\nfrom __future__ import annotations\n',
        "main.py": "from run import main\n",
        "start.sh": "#!/bin/sh\necho start\n",
        "start.bat": "@echo off\r\nrem start\r\n",
        "app/__init__.py": '__version__ = "2.0.0"\n',
        "app/config.py": "DEFAULT_PORT = 50002  # 端口说明里有 http://127.0.0.1:50002 例子\n",
        "app/core/__init__.py": "",
        "app/core/storage.py": "def load():\n    return {}\n",
        "app/web/index.html": CLEAN_INDEX_HTML,
        "app/web/assets/css/base.css": "body { font-family: sans-serif; }\n",
        "app/web/assets/js/app.js": 'h("input", {placeholder: "http://10.20.1.15/cfg.xml"});\n',
        "docs/manual.md": "# 使用手册\n\n启动：`python run.py`。\n",
        "docs/MCChecker_FEATURE_CHANGES.md": "# 变更\n\n" + FC_HEADINGS,
    }
    for rel, text in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    for rel in EXCLUDED_SOURCE_PATHS:
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("不该进包的内容\n", encoding="utf-8")
    return root


def build(root: Path, out_dir: Path, name: str = "MCChecker-2.0.0.zip", force: bool = False) -> Path:
    zip_path = out_dir / name
    mr.build_release(root, zip_path, force=force)
    assert zip_path.is_file()
    return zip_path


def content_members(zip_path: Path) -> List[str]:
    with zipfile.ZipFile(str(zip_path)) as archive:
        return [n for n in archive.namelist() if n != mr.MANIFEST_NAME]


# --------------------------------------------------------------------- 成员集合

def test_member_set_is_exactly_the_inclusion_list(source_tree: Path, tmp_path: Path) -> None:
    """集合相等：多加一个文件、漏打一个文件，都会在这里变红。"""
    zip_path = build(source_tree, tmp_path / "out")
    assert set(content_members(zip_path)) == EXPECTED_MEMBERS


def test_decoys_never_become_members(source_tree: Path, tmp_path: Path) -> None:
    """ planted 的 data/、.venv、tests、.git、缓存、草稿目录都不得出现在产物里。 """
    zip_path = build(source_tree, tmp_path / "out")
    names = set(content_members(zip_path))
    for rel in EXCLUDED_SOURCE_PATHS:
        assert rel not in names, "%s 不该进包却进了" % rel
    assert "data/x.xml" not in names
    assert ".superpowers/secret" not in names
    for name in names:
        assert "data" not in Path(name).parts, name
        assert "__pycache__" not in Path(name).parts, name
        assert not Path(name).name.endswith(".pyc"), name
        assert ".venv" not in name, name


def test_missing_whitelisted_entry_fails_loudly(source_tree: Path, tmp_path: Path) -> None:
    """白名单不是「工作目录有什么就压什么」：条目缺失必须报错，不能静默少打。"""
    (source_tree / "run.py").unlink()
    with pytest.raises(mr.ReleaseError, match="run.py"):
        build(source_tree, tmp_path / "out")


def test_refuses_overwrite_until_force(source_tree: Path, tmp_path: Path) -> None:
    out = tmp_path / "out"
    zip_path = build(source_tree, out)
    digest = zip_path.read_bytes()
    with pytest.raises(mr.ReleaseError, match="已存在"):
        build(source_tree, out)
    assert zip_path.read_bytes() == digest  # 失败没动原产物
    build(source_tree, out, force=True)
    assert zip_path.is_file()


# --------------------------------------------------------------------- 清单完整性

def test_manifest_sha256_matches_extracted_bytes(source_tree: Path, tmp_path: Path) -> None:
    """把产物解压出来逐个数 sha256 复算（含文本与二进制两种形态），与清单对照。"""
    (source_tree / "app" / "static").mkdir(parents=True, exist_ok=True)
    png_bytes = bytes(range(256)) + "中文".encode("utf-8")
    (source_tree / "app" / "static" / "favicon.png").write_bytes(png_bytes)
    zip_path = build(source_tree, tmp_path / "out")

    with zipfile.ZipFile(str(zip_path)) as archive:
        manifest = json.loads(archive.read(mr.MANIFEST_NAME).decode("utf-8"))
        entry = {item["path"]: item for item in manifest["files"]}
        extracted = {}
        archive.extractall(str(tmp_path / "extract"))
    for rel in ("app/static/favicon.png", "app/web/index.html"):
        on_disk = (tmp_path / "extract" / rel).read_bytes()
        assert entry[rel]["sha256"] == hashlib.sha256(on_disk).hexdigest()
        assert entry[rel]["bytes"] == len(on_disk)
        extracted[rel] = on_disk
    assert extracted["app/static/favicon.png"] == png_bytes
    assert manifest["version"] == "2.0.0"
    assert manifest["release_notes"] == [
        "%s 变更标题 %s" % (cid, cid) for cid in mr.RELEASE_NOTE_IDS
    ]


def test_verifier_detects_tampered_member(source_tree: Path, tmp_path: Path) -> None:
    """目标机离线自证完整性的依据：清单哈希与内容不一致时必须判失败。"""
    zip_path = build(source_tree, tmp_path / "out")
    broken = tmp_path / "tampered.zip"
    with zipfile.ZipFile(str(broken), "w") as out_archive:
        with zipfile.ZipFile(str(zip_path)) as good:
            for info in good.infolist():
                data = good.read(info.filename)
                if info.filename == "app/core/storage.py":
                    data = data + b"# tampered\n"
                out_archive.writestr(info.filename, data)
    with pytest.raises(mr.ReleaseError, match="哈希"):
        mr.verify_release(broken, source_root=source_tree)


def test_release_notes_ids_must_exist_in_the_doc(source_tree: Path, tmp_path: Path) -> None:
    """发布说明取自文档：编号在 FEATURE_CHANGES 里找不到就失败，脚本自己不许编。"""
    (source_tree / "docs" / "MCChecker_FEATURE_CHANGES.md").write_text(
        "# 变更\n\n### C09 只有这一条\n", encoding="utf-8"
    )
    with pytest.raises(mr.ReleaseError, match="C12"):
        build(source_tree, tmp_path / "out")


def test_manifest_paths_are_relative_and_free_of_machine_paths(source_tree: Path, tmp_path: Path) -> None:
    zip_path = build(source_tree, tmp_path / "out")
    with zipfile.ZipFile(str(zip_path)) as archive:
        manifest_text = archive.read(mr.MANIFEST_NAME).decode("utf-8")
    assert str(source_tree) not in manifest_text
    assert str(Path.home()) not in manifest_text
    for item in json.loads(manifest_text)["files"]:
        assert not os.path.isabs(item["path"])


def test_verifier_rejects_baked_absolute_path(source_tree: Path, tmp_path: Path) -> None:
    """负向对照：源文件里带进本机绝对路径（例如生成时写死了构建目录），验收必须失败。"""
    config = source_tree / "app" / "config.py"
    config.write_text(
        'DATA_HINT = r"%s"\n' % source_tree,
        encoding="utf-8",
    )
    out = tmp_path / "out"
    zip_path = out / "MCChecker-2.0.0.zip"
    with pytest.raises(mr.ReleaseError, match="绝对路径"):
        mr.build_release(source_tree, zip_path)
    assert not zip_path.exists(), "带本机路径的包不许落地"


def test_verifier_rejects_member_not_on_the_list(source_tree: Path, tmp_path: Path) -> None:
    """产物里多出一个名单外的成员（比如 data/x.xml），验收必须失败。"""
    zip_path = build(source_tree, tmp_path / "out")
    broken = tmp_path / "broken2.zip"
    with zipfile.ZipFile(str(broken), "w") as out_archive:
        with zipfile.ZipFile(str(zip_path)) as good:
            for info in good.infolist():
                out_archive.writestr(info.filename, good.read(info.filename))
            out_archive.writestr("data/x.xml", b"<x/>")
    with pytest.raises(mr.ReleaseError, match="不一致"):
        mr.verify_release(broken, source_root=source_tree)


# ------------------------------------------------------------------- 外链（负向对照）

CDN_SNIPPET = '<script src="https://cdn.example/x.js"></script>'


def test_external_reference_is_rejected_then_green(source_tree: Path, tmp_path: Path) -> None:
    """§16.7 的负向对照：HTML 里加一行 CDN，构建必须失败且不留产物；去掉后恢复绿。"""
    index = source_tree / "app" / "web" / "index.html"
    original = index.read_text(encoding="utf-8")

    index.write_text(original.replace("</body>", "    %s\n  </body>" % CDN_SNIPPET), encoding="utf-8")
    out = tmp_path / "out"
    zip_path = out / "MCChecker-2.0.0.zip"
    with pytest.raises(mr.ReleaseError, match="cdn.example"):
        mr.build_release(source_tree, zip_path)
    assert not zip_path.exists(), "验收没过的包不许落地"

    index.write_text(original, encoding="utf-8")
    assert build(source_tree, out).is_file()  # 去掉外链就恢复通过


@pytest.mark.parametrize(
    "snippet, host",
    [
        ('<link rel="stylesheet" href="https://fonts.example/a.css">', "fonts.example"),
        ('<img src="//img.example/x.png">', "img.example"),
        ('a { background: url("http://bg.example/a.png"); }', "bg.example"),
        ('@import url("https://imp.example/a.css");', "imp.example"),
        ('fetch("https://api.example/v1/x")', "api.example"),
        ('el.src = "https://el.example/a.js";', "el.example"),
        ("xhr.open('GET', 'https://xhr.example/a')", "xhr.example"),
        ("importScripts('https://w.example/a.js')", "w.example"),
        ('new EventSource("https://sse.example/stream")', "sse.example"),
    ],
)
def test_reference_positions_are_all_caught(source_tree: Path, tmp_path: Path, snippet: str, host: str) -> None:
    """各类「浏览器会真去取」的写法都要判为违规（HTML 属性、CSS url/@import、JS fetch/赋值/XHR/Worker/SSE）。"""
    index = source_tree / "app" / "web" / "index.html"
    original = index.read_text(encoding="utf-8")
    index.write_text(original + "\n" + snippet + "\n", encoding="utf-8")
    with pytest.raises(mr.ReleaseError, match=re.escape(host)):
        build(source_tree, tmp_path / "out")


def test_inert_text_urls_are_reported_not_rejected(source_tree: Path, tmp_path: Path) -> None:
    """命名空间 URI、校验正则、输入框提示文字都不该让构建失败，但要被逐条列出来当证据。"""
    js = source_tree / "app" / "web" / "assets" / "js" / "app.js"
    js.write_text(
        'var NS = "http://www.w3.org/2000/svg";\n'
        'if (!/^https?:\\/\\//i.test(url)) return fail(400, "需以 http:// 开头");\n'
        'var e = h("input", {placeholder: "http://10.20.1.15/cfg/config.xml"});\n'
        'document.body.appendChild(el)\n',
        encoding="utf-8",
    )
    zip_path = build(source_tree, tmp_path / "out")  # 不失败
    report = mr.verify_release(zip_path, source_root=source_tree)
    inert = report["inert_references"]["app/web/assets/js/app.js"]
    urls = [url for _line, url in inert]
    assert any("www.w3.org" in u for u in urls), urls
    assert any("10.20.1.15" in u for u in urls), urls
    assert report["violations"] == []


def test_localhost_reference_is_not_a_violation(source_tree: Path, tmp_path: Path) -> None:
    index = source_tree / "app" / "web" / "index.html"
    index.write_text(
        index.read_text(encoding="utf-8") + '<script src="http://127.0.0.1:50002/a.js"></script>\n',
        encoding="utf-8",
    )
    assert build(source_tree, tmp_path / "out").is_file()


# --------------------------------------------------------------------- 入口脚本

def run_start_sh(root: Path, env: Dict[str, str], extra_args: List[str] = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["/bin/sh", str(root / "start.sh")] + (extra_args or []),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        universal_newlines=True,
        env=env,
        timeout=30,
    )


def start_sh_env(tmp_path: Path, path_dirs: List[str]) -> Dict[str, str]:
    """干净环境：PATH 只剩给定目录，且不带 MCHECKER_PYTHON（否则会被当成指定解释器）。"""
    env = {"PATH": os.pathsep.join(path_dirs), "HOME": str(tmp_path / "home")}
    for var in ("MCHECKER_PYTHON", "MCHECKER_PORT", "MCHECKER_DATA_DIR"):
        env.pop(var, None)
        os.environ.pop(var, None)
    return env


def test_start_sh_fails_when_no_interpreter(tmp_path: Path) -> None:
    """这台机器上没有 3.9+ 解释器时：非 0 退出 + 能看懂的一句话，且不起服务、不建 data/。"""
    empty = tmp_path / "bin"
    empty.mkdir()
    root = tmp_path / "release"
    root.mkdir()
    (root / "run.py").write_text("raise SystemExit(0)\n", encoding="utf-8")
    for name in ("start.sh", "start.bat"):
        (root / name).write_text((REPO_ROOT / name).read_text(encoding="utf-8"), encoding="utf-8")
    (root / "start.sh").chmod(0o755)

    proc = run_start_sh(root, start_sh_env(tmp_path, [str(empty)]))
    assert proc.returncode != 0
    assert "Python" in proc.stderr and "3.9" in proc.stderr, proc.stderr
    assert not (root / "data").exists(), "start.sh 不许提前创建 data/"


def test_start_sh_fails_when_interpreter_is_too_old(tmp_path: Path) -> None:
    """模拟 3.8 解释器（版本检查返回 1）：报「版本过低」而不是「没找到」。"""
    fake = tmp_path / "fakebin"
    fake.mkdir()
    old = fake / "python3"
    old.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
    old.chmod(0o755)
    root = tmp_path / "release"
    root.mkdir()
    (root / "run.py").write_text("raise SystemExit(0)\n", encoding="utf-8")
    (root / "start.sh").write_text((REPO_ROOT / "start.sh").read_text(encoding="utf-8"), encoding="utf-8")
    (root / "start.sh").chmod(0o755)

    proc = run_start_sh(root, start_sh_env(tmp_path, [str(fake)]))
    assert proc.returncode != 0
    assert "低于 3.9" in proc.stderr, proc.stderr


def test_start_sh_reports_url_and_launches_packaged_app(tmp_path: Path) -> None:
    """用源码目录跑一遍正常路径：打印访问地址、并把 run.py 的 stdout 透出来。"""
    root = tmp_path / "release"
    root.mkdir()
    (root / "run.py").write_text(
        "import sys\nprint('listening on http://0.0.0.0:%s' % (sys.argv[2] if len(sys.argv) > 2 else '50002'))\n",
        encoding="utf-8",
    )
    (root / "start.sh").write_text((REPO_ROOT / "start.sh").read_text(encoding="utf-8"), encoding="utf-8")
    (root / "start.sh").chmod(0o755)
    env = start_sh_env(tmp_path, [str(Path(sys.executable).parent)])
    env["MCHECKER_PYTHON"] = sys.executable

    proc = run_start_sh(root, env, extra_args=["--port", "50599"])
    assert proc.returncode == 0, proc.stderr
    assert "http://127.0.0.1:50599/" in proc.stdout, proc.stdout
    assert "listening on http://0.0.0.0:50599" in proc.stdout, proc.stdout


def test_start_sh_needs_run_py(tmp_path: Path) -> None:
    """包不完整（没有 run.py）时也要给一句能看懂的话，而不是 ImportError。"""
    root = tmp_path / "release"
    root.mkdir()
    (root / "start.sh").write_text((REPO_ROOT / "start.sh").read_text(encoding="utf-8"), encoding="utf-8")
    (root / "start.sh").chmod(0o755)
    proc = run_start_sh(root, start_sh_env(tmp_path, [str(tmp_path / "empty-bin")]))
    assert proc.returncode != 0
    assert "run.py" in proc.stderr, proc.stderr


def run_cli(args: List[str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(REPO_ROOT / "scripts" / "make_release.py")] + args,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        universal_newlines=True,
        timeout=60,
    )


def test_cli_prints_what_it_wrote_and_guards_overwrite(source_tree: Path, tmp_path: Path) -> None:
    """命令行这条路径：打印写明的产物、覆盖要 --force、--verify-only 能单独复验。"""
    out = tmp_path / "out" / "MCChecker-2.0.0.zip"
    common = ["--source-root", str(source_tree), "--out", str(out)]

    first = run_cli(common)
    assert first.returncode == 0, first.stderr
    assert "已生成发布包" in first.stdout and str(out) in first.stdout, first.stdout
    assert "成员 13 个" in first.stdout, first.stdout
    assert "外部主机引用（会被浏览器取回的）：0 个" in first.stdout, first.stdout

    refused = run_cli(common)
    assert refused.returncode == 1 and "已存在" in refused.stderr, refused.stderr

    assert run_cli(common + ["--force"]).returncode == 0
    verified = run_cli(common + ["--verify-only"])
    assert verified.returncode == 0 and "验收通过" in verified.stdout, verified.stdout

    index = source_tree / "app" / "web" / "index.html"
    index.write_text(
        index.read_text(encoding="utf-8") + '<script src="https://cdn.example/x.js"></script>\n',
        encoding="utf-8",
    )
    failed = run_cli(common + ["--force"])
    assert failed.returncode == 1 and "cdn.example" in failed.stderr, failed.stderr
    # 验收不过不许把已有产物换掉：留下的仍是上一份可离线运行的包
    assert run_cli(common + ["--verify-only"]).returncode == 0
    rejected = run_cli(common + ["--force"])
    assert rejected.returncode == 1, rejected.stdout
    assert "cdn.example" in rejected.stderr, rejected.stderr


# ------------------------------------------------------------------ 真实仓库冒烟

def test_real_repo_artifact_is_offline_clean(tmp_path: Path) -> None:
    """对本仓真源树跑一遍：验收通过、前端外链引用为 0、app/web 的生产文件都在包里。"""
    zip_path = build(REPO_ROOT, tmp_path / "out")
    names = set(content_members(zip_path))
    report = mr.verify_release(zip_path, source_root=REPO_ROOT)
    assert report["violations"] == []
    assert "app/web/index.html" in names
    assert "app/server/http.py" in names
    assert "docs/manual.md" in names
    assert not any(n.startswith("app/web/assets/js/mock/") for n in names)
    assert not any(n.startswith("app/web/tests/") for n in names)
    assert "app/web/template.html" not in names
    assert len(names) > 50


def test_real_repo_zip_is_deterministic_apart_from_timestamp(tmp_path: Path) -> None:
    """同一棵源树构建两次：成员与逐文件 sha256 完全一致（built_at_utc 是唯一变量）。"""
    out = tmp_path / "out"
    first = build(REPO_ROOT, out, name="a.zip")
    second = build(REPO_ROOT, out, name="b.zip")
    with zipfile.ZipFile(str(first)) as fa, zipfile.ZipFile(str(second)) as sa:
        assert fa.namelist() == sa.namelist()
        assert [i.date_time for i in fa.infolist()] == [i.date_time for i in sa.infolist()]
        for name in fa.namelist():
            if name != mr.MANIFEST_NAME:
                assert fa.read(name) == sa.read(name)
        manifest_a = json.loads(fa.read(mr.MANIFEST_NAME).decode("utf-8"))
        manifest_b = json.loads(sa.read(mr.MANIFEST_NAME).decode("utf-8"))
    assert manifest_a["files"] == manifest_b["files"]
