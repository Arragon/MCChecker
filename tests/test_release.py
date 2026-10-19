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
# 名单上每一条惰性放行的路径都必须在这棵树里（`find_stale_inert_entries()` 会因为
# 「条目指向的文件不在产物里」直接判红，僵尸条目不许攒着）。
EXPECTED_MEMBERS = {
    "README.md",
    "app/__init__.py",
    "app/config.py",
    "app/core/__init__.py",
    "app/core/downloader.py",
    "app/core/reviewing.py",
    "app/core/storage.py",
    "app/static/favicon.svg",
    "app/web/assets/css/base.css",
    "app/web/assets/js/app.js",
    "app/web/assets/js/ui/icons.js",
    "app/web/assets/js/views/importer.js",
    "app/web/assets/js/views/settings.js",
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
        "run.py": '"""入口。"""\nfrom __future__ import annotations\n\nprint("listening on http://%s:%d")\n',
        "main.py": "from run import main\n",
        "start.sh": "#!/bin/sh\necho start\n",
        "start.bat": "@echo off\r\nrem start\r\n",
        "app/__init__.py": '__version__ = "2.0.0"\n',
        "app/config.py": "DEFAULT_PORT = 50002  # 端口说明里有 http://127.0.0.1:50002 例子\n",
        "app/core/__init__.py": "",
        "app/core/storage.py": "def load():\n    return {}\n",
        # 名单上剩下四条惰性放行的宿主文件。形状照真实源码抄（反引号/引号收尾，
        # token 才不会一路吞进后面的中文），这样假树也覆盖满 `INERT_URL_LITERALS`。
        "app/core/downloader.py": (
            '"""日志与消息只出现 `scheme://host/path`（§11.5），query 一律抹掉。\n'
            '   脱敏目标是形如 `scheme://...` 的片段。\n"""\n'
            'MASKED = "scheme://host/path"\n'
        ),
        "app/core/reviewing.py": (
            "#  命名空间 URI 天然带 `/`（`{http://…}tag`），按 `/` 切会切错。\n"
        ),
        "app/web/index.html": CLEAN_INDEX_HTML,
        "app/web/assets/css/base.css": "body { font-family: sans-serif; }\n",
        # 只有 `INERT_URL_LITERALS` 上逐条列过的 (路径, 地址) 才是惰性的；这台假树里放的
        # 就是名单上的全部条目，未列过的一律由下面的负向用例证明会红。
        "app/web/assets/js/app.js": 'h("input", {placeholder: "http://127.0.0.1:50002/cfg.xml"});\n',
        "app/web/assets/js/ui/icons.js": 'var NS = "http://www.w3.org/2000/svg";\n',
        "app/web/assets/js/views/importer.js": (
            'var urlInput = h("input.input", { type: "url", placeholder: "http://10.20.1.15/cfg/config.xml" });\n'
        ),
        "app/web/assets/js/views/settings.js": (
            'var url = h("input.input", { type: "url", placeholder: "http://…" });\n'
        ),
        "app/static/favicon.svg": (
            "<svg xmlns=\"http://www.w3.org/2000/svg\" viewBox=\"0 0 16 16\"></svg>\n"
        ),
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
    """白名单不是「工作目录有什么就压什么」：条目缺失必须**在这一步**报错，不能静默少打。

    匹配钉在「名单缺失」这句本身，而不是 `run.py` 这个文件名：产物成员集合的核对也会抱怨
    `run.py` 少了（`产物成员与发布名单不一致…缺少：run.py`），只匹配文件名的话，把
    `collect_members()` 里那条 raise 删掉、让它一路跑到验收，这条用例照样绿——
    那样「缺失即失败」就不再被执法了（M4-06 复审 Minor 2 实测就是这个红不起来的机制）。
    """
    (source_tree / "run.py").unlink()
    with pytest.raises(mr.ReleaseError, match="发布名单里的条目在源目录不存在") as exc:
        build(source_tree, tmp_path / "out")
    assert "run.py" in str(exc.value)


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


def test_inert_urls_are_accepted_only_because_they_are_listed(source_tree: Path, tmp_path: Path) -> None:
    """惰性项不是「看着像文本」，而是「被逐条列进名单」：列过的照常绿并被点名出来。"""
    js = source_tree / "app" / "web" / "assets" / "js" / "ui" / "icons.js"
    js.write_text(
        'var NS = "http://www.w3.org/2000/svg";\n'
        'if (!/^https?:\\/\\//i.test(url)) return fail(400, "需以 http:// 开头");\n'
        'document.body.appendChild(el)\n',
        encoding="utf-8",
    )
    zip_path = build(source_tree, tmp_path / "out")  # 名单上的 (路径, 地址) ⇒ 不失败
    report = mr.verify_release(zip_path, source_root=source_tree)
    inert = report["inert_references"]["app/web/assets/js/ui/icons.js"]
    urls = [url for _line, url in inert]
    assert urls == ["http://www.w3.org/2000/svg"], urls  # 校验正则里没有主机，不进气泡
    assert report["violations"] == []


def test_same_url_in_an_unlisted_file_is_a_violation(source_tree: Path, tmp_path: Path) -> None:
    """放行名单按 (路径, 地址) 生效：同一个命名空间 URI 换个文件就不再是惰性的。"""
    js = source_tree / "app" / "web" / "assets" / "js" / "app.js"
    js.write_text('var NS = "http://www.w3.org/2000/svg";\n', encoding="utf-8")
    out = tmp_path / "out"
    zip_path = out / "MCChecker-2.0.0.zip"
    with pytest.raises(mr.ReleaseError, match=re.escape("www.w3.org")):
        mr.build_release(source_tree, zip_path)
    assert not zip_path.exists()


def test_stale_inert_entry_fails_the_build(source_tree: Path, tmp_path: Path) -> None:
    """名单里的地址在产物里找不到 ⇒ 名单已经脱节，必须红（不许一边失效一边放行）。"""
    svg = source_tree / "app" / "static" / "favicon.svg"
    svg.write_text("<svg viewBox='0 0 16 16'></svg>\n", encoding="utf-8")
    with pytest.raises(mr.ReleaseError, match="已经找不到"):
        build(source_tree, tmp_path / "out")


def test_inert_entry_for_a_file_that_left_the_artifact_fails(source_tree: Path, tmp_path: Path) -> None:
    """僵尸条目同样必须红：条目指的文件不再进产物，放行就悬在半空。

    第二轮复审实测旧行为是**静默绿**（把宿主文件删掉，构建 exit 0），与「陈旧条目一定失败」
    的说法正好相反；这里把另一个方向也钉上。
    """
    (source_tree / "app" / "static" / "favicon.svg").unlink()
    out = tmp_path / "out"
    zip_path = out / "MCChecker-2.0.0.zip"
    with pytest.raises(mr.ReleaseError, match="已经不在产物里"):
        mr.build_release(source_tree, zip_path)
    assert not zip_path.exists(), "验收没过的包不许落地"


def test_backslash_and_markdown_rules_do_not_reject_legitimate_text(
    source_tree: Path, tmp_path: Path
) -> None:
    """精确性对照：两条新规则都不是「看见 `\\` 或 `//` 就杀」。

    反斜杠规则照 WHATWG 只作用在会联网的那五个特殊 scheme（http/https/ftp/ws/wss）上，所以 Windows
    路径与 `file:` 本机路径
    照旧放行；Markdown 规则只认真引用语法（`](` / `]:` 后面紧跟 `//`），所以站内链接、
    相对链接、锚点、整除与行注释都不受影响。这一条红了就说明闸门开始误杀正文了。
    """
    manual = source_tree / "docs" / "manual.md"
    manual.write_text(
        manual.read_text(encoding="utf-8")
        + "\n".join(
            [
                r"配置放在 `C:\Users\mc\cfg.xml`，另有机型目录 `D:\data`。",
                r"历史写法：`file:///d:/Project/数据/x.xml` 与 `file:\d:\Project\x.xml`。",
                "内链：[说明](/manual/ch2)、![图标](images/a.png)、[锚点](#ch2)、[相对](../docs/a.md)、[空格](</manual/a b>)。",
                "代码里的整除 `a // b`，行注释 `// 说明`，正则 `/^https?:\\/\\//i`。",
                "",
            ]
        ),
        encoding="utf-8",
    )
    zip_path = build(source_tree, tmp_path / "out")
    names = set(content_members(zip_path))
    assert "docs/manual.md" in names  # 真的带着这正文出了包，不是没打包所以绿


@pytest.mark.parametrize(
    "rel, snippet, host",
    [
        # Important 1 复审点名的五种形状：过去全都能带着产物出门（exit 0），现在每种都红。
        (
            "app/web/assets/js/core/api.js",
            'var MC_BASE = "https://telemetry.example.com"; fetch(MC_BASE + "/ping");',
            "telemetry.example.com",
        ),
        (
            "app/web/index.html",
            '<img srcset="/local.png 1x, https://evil.example.com/remote.png 2x">',
            "evil.example.com",
        ),
        (
            "app/web/index.html",
            '<meta http-equiv="refresh" content="0;url=https://evil.example.com/x">',
            "evil.example.com",
        ),
        (
            "run.py",
            'urllib.request.urlopen("https://telemetry.example.com/report")',
            "telemetry.example.com",
        ),
        (
            "docs/manual.md",
            '<img src="https://docs.example.com/diagram.png">',
            "docs.example.com",
        ),
        # 同一批语法漏洞的协议相对形态（不带 scheme，所以老的位置模式也看不见第二个候选）。
        (
            "app/web/index.html",
            '<img srcset="/local.png 1x, //evil.example.com/remote.png 2x">',
            "evil.example.com",
        ),
        (
            "app/web/assets/js/app.js",
            'var MC_BASE = "//telemetry.example.com"; fetch(MC_BASE + "/ping");',
            "telemetry.example.com",
        ),
        # 藏法：主机段里塞换行——WHATWG URL 解析会先把它删掉，所以这里也必须跟着删。
        (
            "app/web/index.html",
            '<img src="http://evil.example.co\nm/x">',
            "evil.example.com",
        ),
        # IPv6 字面量：按 `:` 切会把 `[2001:db8::1]` 切成空串，等于给外链放行。
        ("app/web/index.html", '<img src="http://[2001:db8::1]/x">', "2001:db8::1"),
        # 不是前端的文件也在射程内：Markdown 与 shell 里的地址一样红。
        ("start.sh", 'printf "see https://telemetry.example.com/x\\n"', "telemetry.example.com"),
        # 第二轮复审 Important 1：反斜杠权威段。node 24 实测
        # `new URL("https:\\evil.example.com\\a.js").host` 就是 `evil.example.com`
        # ——浏览器真会去取，而旧扫描只找 `scheme://`，于是这条整绿着出了 zip。
        ("app/web/index.html", r"<img src='https:\evil.example.com\a.js'>", "evil.example.com"),
        ("app/web/index.html", r'<a href="https:\\evil.example.com\x.js">', "evil.example.com"),
        # 同一族里 WHATWG 还认的两种写法（special authority ignore slashes）。
        ("app/web/index.html", '<a href="https:/evil.example.com/x.js">', "evil.example.com"),
        ("app/web/index.html", '<a href="https:///evil.example.com/x.js">', "evil.example.com"),
        # 第二轮复审 Important 2：不带 scheme 又不在引号里的 Markdown 引用目标，
        # 旧的两条捕网（scheme 前缀、引号后 `//`）都够不着，位置网又不认 `](`。
        ("docs/manual.md", "[说明](//evil.example.com/manual)", "evil.example.com"),
        ("docs/manual.md", "![截图](//evil.example.com/a.png)", "evil.example.com"),
        ("docs/manual.md", "[说明](<//evil.example.com/a b>)", "evil.example.com"),
        ("docs/manual.md", "[ref]: //evil.example.com/manual", "evil.example.com"),
        ("README.md", "详细说明见 [手册](//evil.example.com/manual)。", "evil.example.com"),
        # 反斜杠写法躲过的不只是 HTML：真放到前端脚本与文档字符串里同样红。
        ("app/web/assets/js/app.js", r'var BASE = "https:\evil.example.com"; ', "evil.example.com"),
    ],
)
def test_planted_shapes_fail_the_build(
    source_tree: Path, tmp_path: Path, rel: str, snippet: str, host: str
) -> None:
    """倒过来判定的执法证明：把地址放进任何一种写法/任何一种文件，构建红且不落地。"""
    target = source_tree / rel
    if not target.is_file():  # 假树里没有的真实条目（例如 core/api.js）照样进包，直接造出来
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("", encoding="utf-8")
    target.write_text(target.read_text(encoding="utf-8") + "\n" + snippet + "\n", encoding="utf-8")
    out = tmp_path / "out"
    zip_path = out / "MCChecker-2.0.0.zip"
    with pytest.raises(mr.ReleaseError, match=re.escape(host)):
        mr.build_release(source_tree, zip_path)
    assert not zip_path.exists(), "验收没过的包不许落地"
    assert not list(out.glob("*.part")), "临时文件不许留下"


def test_ipv6_loopback_still_local(source_tree: Path, tmp_path: Path) -> None:
    """回环放行不含 IPv6 之外的口子：`[::1]` 是本机，仍然可以出现。"""
    index = source_tree / "app" / "web" / "index.html"
    index.write_text(
        index.read_text(encoding="utf-8") + '<img src="http://[::1]:50002/x.png">\n',
        encoding="utf-8",
    )
    assert build(source_tree, tmp_path / "out").is_file()


def test_nonlocal_ipv6_literal_is_rejected(source_tree: Path, tmp_path: Path) -> None:
    index = source_tree / "app" / "web" / "index.html"
    index.write_text(
        index.read_text(encoding="utf-8") + '<img src="http://[2606:4700::6810::8422]/x">\n',
        encoding="utf-8",
    )
    with pytest.raises(mr.ReleaseError, match="2606"):
        build(source_tree, tmp_path / "out")


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
    """干净环境：`env=` 是给子进程的全部世界（PATH + HOME），不带 MCHECKER_PYTHON。

    以前这里还顺手 `os.environ.pop(...)`——那是白改：子进程根本不看父进程的环境，
    却把 pytest 自己的进程环境永久改坏了（后面的用例、包括 `run_cli` 那条继承父环境的，
    都在一个被摘掉变量的环境里跑）。要断干净就把话说到子环境上，别动进程全局（复审 Minor 3）。
    """
    return {"PATH": os.pathsep.join(path_dirs), "HOME": str(tmp_path / "home")}


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


def start_sh_release_root(tmp_path: Path, run_py_text: str) -> Path:
    """一个最小发布目录：真 start.sh + 一个只打印、不起服务的 run.py。"""
    root = tmp_path / "release"
    root.mkdir()
    (root / "run.py").write_text(run_py_text, encoding="utf-8")
    (root / "start.sh").write_text((REPO_ROOT / "start.sh").read_text(encoding="utf-8"), encoding="utf-8")
    (root / "start.sh").chmod(0o755)
    return root


def printed_data_root(stdout: str) -> str:
    """start.sh 打印出来的那一个数据根（运维就是照着它备份 §14.3 说的「整个 data/」）。"""
    match = re.search(r"数据根 (.+)$", stdout, re.M)
    assert match is not None, stdout
    return match.group(1).strip()


def test_start_sh_prints_the_data_root_run_py_uses(tmp_path: Path) -> None:
    """`--data-dir` 传了以后，屏幕上报的必须是实际用的那一个（复审 Minor 1）。

    旧实现无论怎么传都印 `<发布目录>/data`，而进程日志里是另一个目录——§14.3 的升级说明是
    「把整个 data/ 复制走」，报错目录等于把人引去备份错的根。
    """
    chosen = tmp_path / "operator-data"
    root = start_sh_release_root(tmp_path, "import sys\nprint('argv=%s' % sys.argv[1:])\n")
    env = start_sh_env(tmp_path, [str(Path(sys.executable).parent)])
    env["MCHECKER_PYTHON"] = sys.executable

    proc = run_start_sh(root, env, extra_args=["--data-dir", str(chosen)])
    assert proc.returncode == 0, proc.stderr
    assert printed_data_root(proc.stdout) == str(chosen), proc.stdout
    assert "argv=['--data-dir', %r]" % str(chosen) in proc.stdout, proc.stdout  # 参数原样交给 run.py

    proc = run_start_sh(root, env, extra_args=["--data-dir=%s" % chosen])
    assert printed_data_root(proc.stdout) == str(chosen), proc.stdout

    default_proc = run_start_sh(root, env)
    assert printed_data_root(default_proc.stdout) == os.path.realpath(str(root / "data"))


def test_start_sh_data_root_matches_config_resolution(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """同一份 `--data-dir` / 环境变量，start.sh 打印的与 run.py 解析出的必须是一个值。"""
    from app import config as app_config  # 只跑解析，不碰 storage（§7 那条红线在这儿不适用）

    chosen = tmp_path / "operator-data"
    from_env = tmp_path / "env-data"
    root = start_sh_release_root(tmp_path, "print('ok')\n")
    env = start_sh_env(tmp_path, [str(Path(sys.executable).parent)])
    env["MCHECKER_PYTHON"] = sys.executable

    monkeypatch.delenv("MCHECKER_DATA_DIR", raising=False)
    settings = app_config.load_settings(argv=["--data-dir", str(chosen)])
    proc = run_start_sh(root, env, extra_args=["--data-dir", str(chosen)])
    assert proc.returncode == 0, proc.stderr
    assert printed_data_root(proc.stdout) == settings.data_dir == str(chosen)

    env["MCHECKER_DATA_DIR"] = str(from_env)
    monkeypatch.setenv("MCHECKER_DATA_DIR", str(from_env))
    settings = app_config.load_settings(argv=[])
    proc = run_start_sh(root, env)
    assert proc.returncode == 0, proc.stderr
    assert printed_data_root(proc.stdout) == settings.data_dir == str(from_env), proc.stdout

    proc = run_start_sh(root, env, extra_args=["--data-dir", str(chosen)])  # 命令行压过环境变量
    assert printed_data_root(proc.stdout) == str(chosen), proc.stdout


def test_start_sh_uses_the_documented_runtime_python_dir(tmp_path: Path) -> None:
    """内嵌运行时按 §14.3 放在 `runtime/python/`，和 start.bat 同一个目录（复审 Minor 4）。

    旧 start.sh 找的是 `runtime/bin/`——那既是文档里不存在的目录，也让「随包分发解释器」
    在类 Unix 上其实是哑的。这里两个目录都摆上假解释器，选中的必须是文档那一个。
    """
    root = start_sh_release_root(tmp_path, "print('started')\n")
    for rel in ("runtime/python/bin/python3", "runtime/bin/python3"):
        stub = root / rel
        stub.parent.mkdir(parents=True, exist_ok=True)
        stub.write_text('#!/bin/sh\nif [ "$1" = "-c" ]; then exit 0; fi\necho "RUNTIME=$0 ARGS=$*"\n', encoding="utf-8")
        stub.chmod(0o755)
    env = start_sh_env(tmp_path, [str(tmp_path / "empty-bin")])  # PATH 里没有 python，MCHECKER_PYTHON 也不给
    proc = run_start_sh(root, env)
    assert proc.returncode == 0, proc.stderr
    assert str(root / "runtime/python/bin/python3") in proc.stdout, proc.stdout
    assert "runtime/bin/python3" not in proc.stdout, proc.stdout
    assert "ARGS=run.py" in proc.stdout, proc.stdout  # 被选中的那个真的接到了 run.py


def test_start_sh_library_mode_exposes_the_resolvers(tmp_path: Path) -> None:
    """`MCHECKER_START_LIB=0` 不是摆设（复审 Minor 5）：source 进来只取函数，不 exec run.py。

    这一条同时也是端口/数据根两个解析函数的执法用例——它们只在 start.sh 内部被调用，
    光跑整脚本只能看见打印结果，看不见 `--port=` / `--data-dir=` 这两种写法的解析。
    """
    env = start_sh_env(tmp_path, [str(Path(sys.executable).parent)])
    env["MCHECKER_START_LIB"] = "0"
    env["MCHECKER_DATA_DIR"] = str(tmp_path / "from-env")
    script = (
        ". '%s'; printf '%%s|%%s|%%s|%%s\\n' "
        '"$(mcc_resolve_port --port 50701)" "$(mcc_resolve_port --port=50702)" '
        '"$(mcc_resolve_data_dir --data-dir %s)" "$(mcc_resolve_data_dir)"'
        % (REPO_ROOT / "start.sh", tmp_path / "arg-data")
    )
    proc = subprocess.run(
        ["/bin/sh", "-c", script],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        universal_newlines=True,
        env=env,
        timeout=30,
    )
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == "50701|50702|%s|%s" % (
        tmp_path / "arg-data",
        tmp_path / "from-env",
    ), proc.stdout


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
    assert "成员 19 个" in first.stdout, first.stdout
    assert "外部主机引用：0 个" in first.stdout, first.stdout
    # 放行的每一条都要点名，且报的是「名单有几条」——只看 0 个证明不了闸门在执法。
    assert "已放行 app/static/favicon.svg" in first.stdout, first.stdout

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

def test_inert_allowlist_is_a_list_of_pairs_not_a_directory_rule() -> None:
    """放行名单的结构检查：`(路径, 地址, 理由)` 一条一个理由，键不许重复。

    目录级/后缀级的口子（「整个 app/pages 都放行」「非前端文件不看」）就是复审 Important 1
    出事的方式，这里把它钉成结构：条目必须指到具体文件、必须带地址、必须带理由，键不许撞车。
    """
    keys = [(rel, url) for rel, url, _why in mr.INERT_URL_LITERALS]
    assert keys, "一条放行都没有的话，这条用例就成了空断言"
    assert len(keys) == len(set(keys)), "同一路径+地址列了两遍，第二条永远是死的"
    for rel, url, why in mr.INERT_URL_LITERALS:
        assert not rel.endswith("/") and "." in rel.rsplit("/", 1)[-1], rel  # 指文件，不指目录
        assert "://" in url or url.startswith("//"), (rel, url)
        assert why.strip(), (rel, url)


def test_real_repo_artifact_is_offline_clean(tmp_path: Path) -> None:
    """对本仓真源树跑一遍：验收通过、外部主机引用为 0、app/web 的生产文件都在包里。"""
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


def test_real_repo_every_listed_inert_entry_is_actually_present(tmp_path: Path) -> None:
    """真产物里：放行名单的每条都必须被闸门看见（没看见就是脱节），且不能多出一条没列的。"""
    zip_path = build(REPO_ROOT, tmp_path / "out")
    with zipfile.ZipFile(str(zip_path)) as archive:
        blob = {n: archive.read(n) for n in archive.namelist() if n != mr.MANIFEST_NAME}
    listed = {(rel, url) for rel, url, _why in mr.INERT_URL_LITERALS}
    seen = set()
    for rel, data in blob.items():
        for _line, url in mr.url_literals(mr._as_text(data)):
            host = mr._host_of(url)
            if not host or host in mr.LOCAL_HOSTS:
                continue
            seen.add((rel, url))
    assert seen <= listed, "出现了没被逐条放行的地址：%s" % sorted(seen - listed)
    assert not mr.find_stale_inert_entries(blob), "名单里有产物已经不再包含的条目"


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
