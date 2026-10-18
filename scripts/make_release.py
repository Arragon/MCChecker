"""MCChecker 离线发布包构建（规范 §14.3；卡片 M4-06 / INH-1908）。

只用标准库（H2）。产物 `dist/MCChecker-<版本>.zip` 要在**没有外网**的目标机上解压即用
（§16.7），所以本脚本的职责不只是「打包」，而是把 §16.7 的两个静默失败点变成构建期硬失败：

1. 名单是**白名单**：成员只来自 `RELEASE_ENTRIES`，不是把工作目录压进 zip；
   名单条目在源树里缺失就直接报错（§14.3 的「包含」与「不包含」都在这儿落实）。
2. 产物要**验收后才落地**：先写临时文件，`verify_release()` 通过后改名到位，
   磁盘上不会留下「没过验收的包」。

确定性：同一棵源树 → 同一成员集合、同一逐文件 sha256；zip 条目时间戳固定为 1980-01-01，
唯一每次不同的是 `MANIFEST.json` 里的 `built_at_utc`（它记录的就是「这次构建」）。

发布说明不在这里编：条目编号取自 `docs/MCChecker_DELIVERY_PLAN.md` §10，标题取自
`docs/MCChecker_FEATURE_CHANGES.md` 的 `### Cxx` 小节；编号在文档里找不到就失败。

3.9 兼容：`from __future__ import annotations` 置首，运行期不用 `X | Y`、`match`。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import zipfile
from datetime import datetime, timezone
from pathlib import Path, PurePath, PurePosixPath
from typing import Dict, List, Optional, Sequence, Tuple

MANIFEST_NAME = "MANIFEST.json"

#: §14.3 的「包含」名单。`app` 是目录条目（整棵展开，仍受下面两组排除规则约束）。
RELEASE_ENTRIES: Tuple[str, ...] = (
    "run.py",
    "main.py",
    "README.md",
    "start.sh",
    "start.bat",
    "docs/manual.md",
    "app",
)

#: 名单之外还要硬排除的**路径成分**（任一层命中即丢弃）。`data/` 与 `.nicegui/` 是 §14.3
#: 点名的两个；其余是这台机器上确实存在、一旦「顺手打包」就会漏出去的目录。
FORBIDDEN_COMPONENTS: Tuple[str, ...] = (
    "data",
    ".nicegui",
    ".git",
    "tests",
    "dist",
    "node_modules",
    "__pycache__",
    ".pytest_cache",
    ".mimosa",
    ".workbuddy",
    ".superpowers",
    ".claude",
    ".qoder",
    ".vscode",
)

#: 以这些前缀开头的成分同样排除（`.venv`、`.venv39`……§14.3「不包含」+ 本机虚拟环境）。
FORBIDDEN_COMPONENT_PREFIXES: Tuple[str, ...] = (".venv",)

#: 排除的后缀（`*.pyc` 见 §14.3）。
FORBIDDEN_SUFFIXES: Tuple[str, ...] = (".pyc", ".pyo", ".tmp", ".swp", ".swo", ".DS_Store")

#: §14.3 点名单独排除的前端条目（模板与浏览器内 mock 不属于生产包）。
EXCLUDED_PATHS: Tuple[str, ...] = (
    "app/web/template.html",
    "app/web/assets/js/mock",
    "app/web/tests",
)

#: §14.3「可选：把 python-3.9.11-embed-amd64.zip 解压到 runtime/python/」——
#: 由发布人员在有网环境放入，本脚本既不生成也不校验它。
OPTIONAL_RUNTIME_DIR = "runtime/python"

#: 会被浏览器直接取回的资源类型，因此需要逐文件做外链检查（§16.7）。
FRONTEND_SUFFIXES: Tuple[str, ...] = (".html", ".htm", ".js", ".mjs", ".css", ".svg")

#: 本机地址不算外网（§16.7 说的是「非本服务器的请求」）。
LOCAL_HOSTS = frozenset(
    {"localhost", "127.0.0.1", "0.0.0.0", "::1", "[::1]", "0:0:0:0:0:0:0:1"}
)

#: DELIVERY_PLAN §10：发布说明必须列出的行为变更（相对 v1.0.1）。
RELEASE_NOTE_IDS: Tuple[str, ...] = ("C09", "C12", "C13", "C20", "C25", "C26", "C27")
FEATURE_CHANGES_DOC = "docs/MCChecker_FEATURE_CHANGES.md"

#: zip 条目固定时间戳：让「同一棵源树」产出可比对的成员与哈希。
ZIP_DATE_TIME = (1980, 1, 1, 0, 0, 0)

_VERSION_RE = re.compile(r"""^__version__\s*=\s*["']([^"']+)["']""", re.M)
_FEATURE_HEADING_RE = re.compile(r"""^###\s+(C\d+)\s+(.+?)\s*$""", re.M)
_URL_RE = re.compile(r"""https?://[^\s'"()<>\];,`]+""", re.I)

#: 「引用位置」= 浏览器/JS 真的会拿去发请求的地方。判定用位置而不是用主机白名单，
#: 这样 `xmlns='http://www.w3.org/2000/svg'`（命名空间标识符，不发请求）、
#: `/^https?:\/\//i`（校验正则）、`placeholder: "http://10.20.1.15/…"`（输入框提示文字）
#: 都会被判为惰性文本，而 `<script src="https://cdn…">` 一定判为违规。
_REFERENCE_PATTERNS: Tuple[str, ...] = (
    # HTML 属性：src/href/srcset/... 的值直接是绝对地址或协议相对地址
    r"""\b(?:src|href|srcset|data|xlink:href|action|formaction|poster|codebase|manifest)\s*=\s*["']?((?:https?:)?//[^"'\s<>]+)""",
    # CSS：url(...) 与 @import
    r"""(?:url\s*\(\s*["']?|@import\s+["'])((?:https?:)?//[^"'\s)]+)""",
    # JS：把绝对地址当目标参数的调用与赋值
    r"""(?:\bfetch\s*\(|\bimportScripts\s*\(|\bnew\s+(?:Worker|EventSource|WebSocket)\s*\(|\.src\s*=|\.href\s*=)\s*["'`]?((?:https?:)?//[^"'`\s)]+)""",
    # JS：XHR 的第二个参数
    r"""\.open\s*\(\s*["'][^"'`]*["']\s*,\s*["']?((?:https?:)?//[^"'`\s)]+)""",
)
_COMPILED_REFERENCE_PATTERNS = [re.compile(p, re.I) for p in _REFERENCE_PATTERNS]


class ReleaseError(RuntimeError):
    """构建或验收失败；`str(exc)` 是给发布人员看的原因。"""


# --------------------------------------------------------------------------- 名单

def read_version(source_root: Path) -> str:
    init = source_root / "app" / "__init__.py"
    if not init.is_file():
        raise ReleaseError("找不到 %s，无法确定版本号" % init.relative_to(source_root))
    match = _VERSION_RE.search(init.read_text(encoding="utf-8"))
    if not match:
        raise ReleaseError("%s 里没有 __version__，无法确定版本号" % init)
    return match.group(1)


def is_excluded(rel: PurePosixPath) -> bool:
    """成员相对路径是否被排除（任一层成分命中，或整条路径在 §14.3 点名的排除项里）。"""
    parts = rel.parts
    for part in parts[:-1]:
        if part in FORBIDDEN_COMPONENTS or part.startswith(FORBIDDEN_COMPONENT_PREFIXES):
            return True
    last = parts[-1]
    if last in FORBIDDEN_COMPONENTS:
        return True
    if any(last.endswith(suffix) for suffix in FORBIDDEN_SUFFIXES):
        return True
    posix = rel.as_posix()
    for excluded in EXCLUDED_PATHS:
        if posix == excluded or posix.startswith(excluded + "/"):
            return True
    return False


def collect_members(source_root: Path) -> List[str]:
    """按白名单展开成员相对路径（排序、 posix 分隔符）。条目缺失即失败。"""
    source_root = source_root.resolve()
    found: List[str] = []
    for entry in RELEASE_ENTRIES:
        target = source_root / entry
        if target.is_file():
            found.append(PurePosixPath(entry).as_posix())
        elif target.is_dir():
            found.extend(_walk(source_root, PurePosixPath(entry)))
        else:
            raise ReleaseError(
                "发布名单里的条目在源目录不存在：%s（源目录 %s）" % (entry, source_root)
            )
    return sorted(found)


def _rel_of(path: Path, base: Path) -> PurePosixPath:
    """相对源目录的路径，统一成 posix 分隔符（zip 成员名与清单都用它）。"""
    return PurePosixPath(PurePath(path).relative_to(base).as_posix())


def _walk(source_root: Path, entry: PurePosixPath) -> List[str]:
    found: List[str] = []
    base = source_root / entry.as_posix()
    for dirpath, dirnames, filenames in os.walk(str(base), followlinks=False):
        current = entry / _rel_of(Path(dirpath), base)
        # 就地剪枝：被排除的目录连读都不读，避免 `__pycache__`/`data` 里的东西被带进来。
        dirnames[:] = sorted(
            name
            for name in dirnames
            if not is_excluded(current / name)
        )
        for name in sorted(filenames):
            rel = current / name
            if is_excluded(rel):
                continue
            path = Path(dirpath) / name
            if path.is_symlink():
                raise ReleaseError(
                    "发布名单内不允许符号链接（会把本机路径带进包里）：%s" % rel.as_posix()
                )
            found.append(rel.as_posix())
    return found


def collect_release_notes(source_root: Path) -> List[str]:
    doc = source_root / FEATURE_CHANGES_DOC
    if not doc.is_file():
        raise ReleaseError("发布说明缺来源：%s 不存在" % FEATURE_CHANGES_DOC)
    headings: Dict[str, str] = {}
    for cid, title in _FEATURE_HEADING_RE.findall(doc.read_text(encoding="utf-8")):
        headings.setdefault(cid, title)
    notes: List[str] = []
    missing: List[str] = []
    for cid in RELEASE_NOTE_IDS:
        title = headings.get(cid)
        if title is None:
            missing.append(cid)
        else:
            notes.append("%s %s" % (cid, title))
    if missing:
        raise ReleaseError(
            "%s 里找不到这些发布说明条目的标题：%s（编号取自 DELIVERY_PLAN §10，不允许在这里编）"
            % (FEATURE_CHANGES_DOC, "、".join(missing))
        )
    return notes


# ------------------------------------------------------------------- 外链与泄漏检查

def _host_of(url: str) -> str:
    body = url[2:] if url.startswith("//") else url.split("://", 1)[1]
    for sep in ("?", "#", "/"):
        body = body.split(sep, 1)[0]
    return body.rsplit("@", 1)[-1].split(":", 1)[0].strip("[]").lower()


def reference_spans(text: str) -> List[Tuple[int, int]]:
    """所有「引用位置」上 URL 的字符区间。"""
    spans: List[Tuple[int, int]] = []
    for pattern in _COMPILED_REFERENCE_PATTERNS:
        for match in pattern.finditer(text):
            spans.append((match.start(1), match.end(1)))
    return spans


def scan_external_references(rel_path: str, text: str) -> Tuple[List[str], List[Tuple[int, str]]]:
    """返回 (违规说明, 惰性出现项 [(行号, URL)])。

    违规 = URL 出现在引用位置且主机不是本机；惰性项 = 只作为文本出现的 URL
    （命名空间、校验正则、输入框提示），逐条列出来作为 §16.7 的证据，不当成失败。
    """
    violations: List[str] = []
    spans = reference_spans(text)
    for start, _end in spans:
        url = _match_at(text, start)
        if url is None:
            continue
        host = _host_of(url)
        if host and host not in LOCAL_HOSTS:
            line = text.count("\n", 0, start) + 1
            violations.append(
                "%s:%d 引用了非本机主机 %s（%s）" % (rel_path, line, host, url)
            )
    inert: List[Tuple[int, str]] = []
    for match in _URL_RE.finditer(text):
        if any(start == match.start() for start, _ in spans):
            continue
        url = _read_url(text, match.start())
        if _host_of(url) in LOCAL_HOSTS:
            continue
        inert.append((text.count("\n", 0, match.start()) + 1, url))
    return violations, inert


def _match_at(text: str, start: int) -> Optional[str]:
    """从引用位置抓出完整 URL 文本（引用模式捕获到的那段）。"""
    if _URL_RE.match(text, start) is not None or text.startswith("//", start):
        return _read_url(text, start)
    return None


def _read_url(text: str, start: int) -> str:
    end = start
    stop = set(" \t\r\n'\"()<>,;`]")
    while end < len(text) and text[end] not in stop:
        end += 1
    return text[start:end]


def frontend_scan(members: Dict[str, bytes]) -> Tuple[List[str], Dict[str, List[Tuple[int, str]]]]:
    violations: List[str] = []
    inert: Dict[str, List[Tuple[int, str]]] = {}
    for rel, data in sorted(members.items()):
        if not rel.lower().endswith(FRONTEND_SUFFIXES):
            continue
        text = data.decode("utf-8", errors="replace")
        hits, skipped = scan_external_references(rel, text)
        violations.extend(hits)
        if skipped:
            inert[rel] = skipped
    return violations, inert


def find_leaked_paths(members: Dict[str, bytes], forbidden: Sequence[str]) -> List[str]:
    """任一成员（含清单）里出现本机绝对路径 → 失败。二进制也照样查（按字节）。"""
    needles = [(lit, lit.encode("utf-8")) for lit in forbidden if lit]
    leaks: List[str] = []
    for rel, data in sorted(members.items()):
        for literal, blob in needles:
            if blob in data:
                leaks.append("%s 里含本机绝对路径 %s" % (rel, literal))
    return leaks


def _forbidden_literals(source_root: Path, zip_parent: Path) -> List[str]:
    """构建机上不该出现在包里的绝对路径。

    `/var` 与 `/private/var` 这类软链在 macOS 上是同一个目录的两种写法，所以实参与
    `resolve()` 后的形态都要查；测试里传的就是未解析的 tmp 路径。
    """
    candidates = [source_root, source_root.resolve(), Path.home(), zip_parent, Path(zip_parent).resolve()]
    seen: Dict[str, None] = {}
    for path in candidates:
        for text in (str(path), path.as_posix()):
            if text and text not in ("/", "."):
                seen[text] = None
    return sorted(seen)


# ------------------------------------------------------------------------- 打包

def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def read_member_bytes(source_root: Path, members: Sequence[str]) -> Dict[str, bytes]:
    blob: Dict[str, bytes] = {}
    for rel in members:
        path = source_root / rel
        if not path.is_file():
            raise ReleaseError("成员在展开后消失了：%s" % rel)
        blob[rel] = path.read_bytes()
    return blob


def _member_mode(rel: str, path: Path) -> int:
    """入口脚本要能被双击/直接执行，权限位写进 zip（Linux/macOS 解压后可 `./start.sh`）。"""
    if rel in ("start.sh", "start.bat"):
        return 0o755
    mode = path.stat().st_mode & 0o777
    return mode or 0o644


def write_zip(zip_path: Path, members: Dict[str, bytes], manifest: Dict[str, object], modes: Dict[str, int]) -> None:
    with zipfile.ZipFile(str(zip_path), "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for rel in sorted(members):
            info = zipfile.ZipInfo(rel, date_time=ZIP_DATE_TIME)
            info.compress_type = zipfile.ZIP_DEFLATED
            # UNIX 头 + 权限位：start.sh 解压后才能直接 `./start.sh`（§14.2 双击入口）。
            info.create_system = 3
            info.external_attr = modes[rel] << 16
            archive.writestr(info, members[rel])
        info = zipfile.ZipInfo(MANIFEST_NAME, date_time=ZIP_DATE_TIME)
        info.create_system = 3
        info.external_attr = 0o644 << 16
        archive.writestr(info, (json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8"))


def build_release(
    source_root: Path,
    out_path: Optional[Path] = None,
    force: bool = False,
) -> Dict[str, object]:
    """构建 + 验收，返回给 CLI 打印的结果。验收不过不落最终文件。"""
    source_root = source_root.resolve()
    version = read_version(source_root)
    target = out_path if out_path is not None else source_root / "dist" / (
        "MCChecker-%s.zip" % version
    )
    target = Path(target)
    if target.exists() and not force:
        raise ReleaseError(
            "产物已存在：%s（要覆盖就加 --force）" % target
        )
    target.parent.mkdir(parents=True, exist_ok=True)

    names = collect_members(source_root)
    blob = read_member_bytes(source_root, names)
    modes = {rel: _member_mode(rel, source_root / rel) for rel in names}
    manifest: Dict[str, object] = {
        "product": "MCChecker",
        "version": version,
        "built_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "generator": "scripts/make_release.py",
        "release_notes_source": FEATURE_CHANGES_DOC,
        "release_notes": collect_release_notes(source_root),
        "files": [
            {"path": rel, "sha256": _sha256(blob[rel]), "bytes": len(blob[rel])}
            for rel in sorted(names)
        ],
    }

    temp = target.parent / (target.name + ".part")
    if temp.exists():
        temp.unlink()
    try:
        write_zip(temp, blob, manifest, modes)
        report = verify_release(temp, source_root=source_root, version=version)
        if target.exists():
            target.unlink()
        os.replace(str(temp), str(target))
    finally:
        if temp.exists():
            temp.unlink()

    return {
        "zip": target,
        "version": version,
        "file_count": len(names),
        "zip_bytes": target.stat().st_size,
        "zip_sha256": _sha256(target.read_bytes()),
        "inert_references": report["inert_references"],
        "release_notes": manifest["release_notes"],
    }


# ------------------------------------------------------------------------- 验收

def verify_release(
    zip_path: Path,
    source_root: Path,
    version: Optional[str] = None,
) -> Dict[str, object]:
    """重开产物逐条核对；任何一条不过就 `ReleaseError`。"""
    zip_path = Path(zip_path)
    source_root = source_root.resolve()
    if not zipfile.is_zipfile(str(zip_path)):
        raise ReleaseError("产物不是合法的 zip：%s" % zip_path)

    expected = collect_members(source_root)

    with zipfile.ZipFile(str(zip_path)) as archive:
        bad = archive.testzip()
        if bad is not None:
            raise ReleaseError("zip 解压校验失败：%s" % bad)
        names = [info.filename for info in archive.infolist()]
        blob = {name: archive.read(name) for name in names}

    # 1) 成员集合必须与名单**完全一致**（多的少的都算失败）。
    extra = sorted(set(names) - set(expected) - {MANIFEST_NAME})
    missing = sorted(set(expected) - set(names))
    if extra or missing:
        raise ReleaseError(
            "产物成员与发布名单不一致。多出：%s；缺少：%s"
            % ("、".join(extra) or "无", "、".join(missing) or "无")
        )

    # 2) 名字里不许有被排除的成分（data/、.venv、tests/、.git、缓存、本机草稿目录）。
    for name in names:
        if is_excluded(PurePosixPath(name)) or name.startswith("data/") or name == "data":
            raise ReleaseError("产物里出现了被排除的路径：%s" % name)

    # 3) 清单结构 + 逐文件 sha256 复核（目标机可以离线照这个自证完整性）。
    if MANIFEST_NAME not in blob:
        raise ReleaseError("产物缺少 %s" % MANIFEST_NAME)
    try:
        manifest = json.loads(blob[MANIFEST_NAME].decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as exc:
        raise ReleaseError("%s 解析失败：%s" % (MANIFEST_NAME, exc))
    if version is not None and manifest.get("version") != version:
        raise ReleaseError(
            "%s 版本 %r 与 app/__init__.py 的 %r 不一致" % (MANIFEST_NAME, manifest.get("version"), version)
        )
    listed = manifest.get("files")
    if not isinstance(listed, list) or not listed:
        raise ReleaseError("%s 里的 files 不是非空列表" % MANIFEST_NAME)
    seen = set()
    for item in listed:
        if not isinstance(item, dict) or "path" not in item or "sha256" not in item:
            raise ReleaseError("%s 里有不完整的文件条目：%r" % (MANIFEST_NAME, item))
        path = item["path"]
        if path not in blob:
            raise ReleaseError("清单登记了产物里不存在的文件：%s" % path)
        digest = _sha256(blob[path])
        if digest != item["sha256"]:
            raise ReleaseError(
                "清单哈希与实际内容不符：%s 清单 %s… 实际 %s…"
                % (path, str(item["sha256"])[:12], digest[:12])
            )
        if int(item.get("bytes", -1)) != len(blob[path]):
            raise ReleaseError("清单字节数与实际不符：%s" % path)
        if path in seen:
            raise ReleaseError("清单里 %s 出现多次" % path)
        seen.add(path)
    if seen != set(expected):
        raise ReleaseError("清单覆盖的文件集合与成员不一致")

    # 4) 本机绝对路径不许留在任何成员（含清单）里。
    leaks = find_leaked_paths(blob, _forbidden_literals(source_root, Path(zip_path).parent))
    if leaks:
        raise ReleaseError("产物里泄漏了本机绝对路径：\n  " + "\n  ".join(leaks))

    # 5) 前端文件的外链引用（§16.7 的真正失败模式）。
    violations, inert = frontend_scan(blob)
    if violations:
        raise ReleaseError(
            "打包产物里存在会被浏览器取回的外部主机引用（§16.7 离线会失败）：\n  "
            + "\n  ".join(violations)
        )

    return {
        "members": sorted(names),
        "manifest": manifest,
        "inert_references": inert,
        "violations": violations,
    }


# -------------------------------------------------------------------------- CLI

def _print_result(result: Dict[str, object], stream=sys.stdout) -> None:
    print("已生成发布包：%s" % result["zip"], file=stream)
    print("  版本 %s，成员 %d 个，产物 %d 字节（含 %s）"
          % (result["version"], result["file_count"], result["zip_bytes"], MANIFEST_NAME), file=stream)
    print("  产物 sha256：%s" % result["zip_sha256"], file=stream)
    inert = result["inert_references"] or {}
    total = sum(len(v) for v in inert.values())
    print(
        "  外部主机引用（会被浏览器取回的）：0 个；仅作为文本出现的 URL %d 处，分布在 %d 个前端文件"
        % (total, len(inert)),
        file=stream,
    )
    for rel, hits in sorted(inert.items()):
        for line, url in hits:
            print("    惰性文本 %s:%d %s" % (rel, line, url), file=stream)
    print("  发布说明（取自 %s）：" % FEATURE_CHANGES_DOC, file=stream)
    for note in result["release_notes"]:
        print("    %s" % note, file=stream)


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="make_release.py",
        description="构建 MCChecker 离线发布包（规范 §14.3；M4-06 / INH-1908）",
    )
    parser.add_argument(
        "--source-root",
        default=None,
        help="源目录（默认本仓库根目录）",
    )
    parser.add_argument(
        "--out",
        default=None,
        help="产物 zip 路径（默认 <源目录>/dist/MCChecker-<版本>.zip）",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="允许覆盖已存在的产物",
    )
    parser.add_argument(
        "--verify-only",
        action="store_true",
        help="不构建，只按同一套规则验收已有产物",
    )
    args = parser.parse_args(list(sys.argv[1:]) if argv is None else list(argv))

    source_root = Path(args.source_root) if args.source_root else Path(__file__).resolve().parents[1]
    if not source_root.is_dir():
        print("失败：源目录不存在 %s" % source_root, file=sys.stderr)
        return 2

    try:
        if args.verify_only:
            if not args.out:
                print("失败：--verify-only 需要 --out 指定要验收的产物", file=sys.stderr)
                return 2
            report = verify_release(Path(args.out), source_root=source_root)
            print("验收通过：%s（成员 %d 个，外部主机引用 0 个）"
                  % (args.out, len(report["members"]) - 1))
            return 0
        result = build_release(
            source_root=source_root,
            out_path=Path(args.out) if args.out else None,
            force=args.force,
        )
    except ReleaseError as exc:
        print("发布包构建失败：%s" % exc, file=sys.stderr)
        return 1
    _print_result(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
