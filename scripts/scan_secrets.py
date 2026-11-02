"""M16-38 统一 secret 扫描：工作区 / 暂存区 / 提交区间 / 全历史。

只在本地读 Git 与文件，不外联、不写入仓库；输出一律掩码，**绝不打印完整
secret**。用于把「发布包/日志/备份无 secret」补齐到「Git 也没有」这一条。

用法：

    python scripts/scan_secrets.py                 # 等价 --tree：git ls-files 的工作区内容
    python scripts/scan_secrets.py --staged        # 只看已暂存的新增行（提交前钩子）
    python scripts/scan_secrets.py --range origin/feat/mc-v3.1..HEAD
    python scripts/scan_secrets.py --all-history   # 全部分支的提交内容（慢）
    python scripts/scan_secrets.py --json          # 机器可读，供发布门汇总

退出码：0 干净；1 有命中；2 环境/参数错误（不是 git 仓库、区间非法、allowlist 损坏）。
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import subprocess  # nosec B404 - 只调用 git，参数全部走列表形式，不经 shell
import sys
import threading
from typing import Dict, Iterable, Iterator, List, Optional, Sequence, Tuple

DEFAULT_ALLOWLIST = os.path.join(os.path.dirname(os.path.abspath(__file__)), "secret_scan_allowlist.json")

#: 合成值：测试/文档里的占位串不算泄露（大小写不敏感）。
PLACEHOLDER_HINTS = (
    "fake",
    "dummy",
    "example",
    "sample",
    "placeholder",
    "changeme",
    "redacted",
    "your-",
    "your_",
    "<",
    ">",
    "...",
    "***",
    "xxx",
)

#: 目录/文件后缀：与 make_release 的排除口径保持一致，避免扫运行时数据与虚拟环境。
EXCLUDE_PREFIXES = (
    "data/",
    "dist/",
    ".git/",
    ".venv",
    "venv/",
    "node_modules/",
    "__pycache__/",
    ".qoder/",
    "docs/evidence/",
)
EXCLUDE_SUFFIXES = (
    ".pyc",
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".webp",
    ".ico",
    ".zip",
    ".gz",
    ".pdf",
    ".woff",
    ".woff2",
    ".ttf",
)
MAX_BYTES = 2 * 1024 * 1024

RULES: Tuple[Tuple[str, "re.Pattern[str]"], ...] = (
    ("private-key", re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |DSA |PGP )?PRIVATE KEY-----")),
    ("aws-access-key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("github-token", re.compile(r"\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{20,}\b")),
    ("github-pat", re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}\b")),
    ("slack-token", re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b")),
    ("llm-api-key", re.compile(r"\bsk-[A-Za-z0-9]{20,}\b")),
    ("jwt", re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b")),
    (
        "generic-assignment",
        re.compile(
            r"(?i)\b(?:password|passwd|pwd|secret|token|api[_-]?key|authorization)\b"
            r"\s*[:=]\s*[\"']([^\"'\s]{8,})[\"']"
        ),
    ),
)

ENTROPY_TOKEN = re.compile(r"[A-Za-z0-9+/=]{32,}")
ENTROPY_MIN = 3.6


def _log(message: str) -> None:
    sys.stderr.write(message + "\n")


def _git(root: str, args: Sequence[str], binary: bool = False) -> bytes:
    try:
        proc = subprocess.run(  # nosec B603 - 固定 git 子命令 + 列表参数
            ["git"] + list(args),
            cwd=root,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
    except FileNotFoundError:
        raise SystemExit(_fail("找不到 git 可执行文件"))
    if proc.returncode != 0:
        raise SystemExit(_fail("git 失败：" + " ".join(args) + "\n" + proc.stderr.decode("utf-8", "replace")[:400]))
    return proc.stdout


def _fail(message: str) -> int:
    _log("scan_secrets: " + message)
    return 2


def excluded(path: str) -> bool:
    lowered = path.replace("\\", "/").lstrip("./")
    for prefix in EXCLUDE_PREFIXES:
        if lowered.startswith(prefix):
            return True
    return lowered.endswith(EXCLUDE_SUFFIXES)


def shannon(text: str) -> float:
    if not text:
        return 0.0
    counts: Dict[str, int] = {}
    for ch in text:
        counts[ch] = counts.get(ch, 0) + 1
    total = float(len(text))
    return -sum((n / total) * math.log(n / total, 2) for n in counts.values())


def looks_like_placeholder(value: str) -> bool:
    lowered = value.casefold()
    return any(hint in lowered for hint in PLACEHOLDER_HINTS)


def is_high_entropy(token: str) -> bool:
    """凭据形态：长度够、大小写+数字混排、不是纯哈希。

    snake_case 标识符、路径、常量名都不会同时满足「含大写 + 含小写 + 含数字
    且不含 `_`/`-`/`/`」，因此这里对代码噪声的容忍度比裸熵阈值高得多。
    """
    if len(token) < 32:
        return False
    if any(ch in token for ch in ("_", "-", "/")):
        return False
    if not any(ch.isdigit() for ch in token):
        return False
    if not any(ch.isupper() for ch in token):
        return False
    if not any(ch.islower() for ch in token):
        return False
    # 纯十六进制多为内容哈希（sha256/md5），不是凭据。
    if re.fullmatch(r"[0-9a-fA-F]+", token):
        return False
    return shannon(token) >= ENTROPY_MIN


def mask(value: str) -> str:
    if len(value) <= 8:
        return "*" * len(value)
    return value[:4] + "*" * max(4, len(value) - 8) + value[-4:]


def scan_text(text: str) -> Iterator[Tuple[int, str, str]]:
    """返回 (行号, 规则, 掩码后的证据)。"""
    for lineno, line in enumerate(text.splitlines(), start=1):
        if len(line) > 4000:
            line = line[:4000]
        for rule, pattern in RULES:
            for match in pattern.finditer(line):
                raw = match.group(1) if match.groups() else match.group(0)
                if looks_like_placeholder(raw):
                    continue
                yield lineno, rule, mask(raw)
        for token in ENTROPY_TOKEN.findall(line):
            if looks_like_placeholder(token):
                continue
            if is_high_entropy(token):
                yield lineno, "high-entropy", mask(token)


def read_blob(path: str) -> Optional[str]:
    try:
        if os.path.getsize(path) > MAX_BYTES:
            return None
        with open(path, "rb") as handle:
            raw = handle.read()
    except (OSError, ValueError):
        return None
    if b"\x00" in raw:
        return None
    return raw.decode("utf-8", "ignore")


def iter_tree(root: str) -> Iterator[Tuple[str, str]]:
    out = _git(root, ["ls-files", "-z"])
    for raw in out.split(b"\x00"):
        if not raw:
            continue
        path = raw.decode("utf-8", "replace")
        if excluded(path):
            continue
        text = read_blob(os.path.join(root, path))
        if text is None:
            continue
        yield path, text


def _parse_diff(diff: str, default_path: str = "") -> Iterator[Tuple[str, int, str]]:
    path = default_path
    lineno = 0
    for line in diff.splitlines():
        if line.startswith("+++ b/"):
            path = line[6:]
            continue
        if line.startswith("@@"):
            match = re.search(r"\+(\d+)", line)
            lineno = int(match.group(1)) - 1 if match else 0
            continue
        if line.startswith("+") and not line.startswith("+++"):
            lineno += 1
            yield path, lineno, line[1:]
        elif line.startswith(" ") or line == "":
            lineno += 1


def iter_diff(root: str, range_spec: Optional[str], staged: bool) -> Iterator[Tuple[str, int, str, str]]:
    args = ["diff", "-U0", "--no-color", "--no-ext-diff"]
    if staged:
        args.append("--cached")
    if range_spec:
        args.append(range_spec)
    diff = _git(root, args).decode("utf-8", "ignore")
    for path, lineno, text in _parse_diff(diff):
        if excluded(path):
            continue
        yield "", path, lineno, text


def _read_batch(root: str, shas: Sequence[str]) -> Iterator[Tuple[str, bytes]]:
    """流式读 `git cat-file --batch`：避免每个 blob 起一个子进程。

    写入必须放到独立线程：Windows 的管道缓冲区远小于 macOS/Linux，一次性写几百个
    sha 会与 git 的 stdout 同时塞满 → 双向阻塞（CI 上表现为 pytest 永久挂起）。
    """
    proc = subprocess.Popen(  # nosec B603 - 固定 git 子命令
        ["git", "cat-file", "--batch"],
        cwd=root,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
    )
    assert proc.stdin is not None and proc.stdout is not None
    payload = ("\n".join(shas) + "\n").encode("utf-8")

    def _feed() -> None:
        try:
            proc.stdin.write(payload)
            proc.stdin.flush()
        except (BrokenPipeError, OSError):
            pass
        finally:
            try:
                proc.stdin.close()
            except (BrokenPipeError, OSError):
                pass

    writer = threading.Thread(target=_feed, name="scan-secrets-feed", daemon=True)
    writer.start()
    try:
        for _sha in shas:
            header = proc.stdout.readline().decode("utf-8", "replace").strip()
            if not header:
                break
            parts = header.split()
            if len(parts) < 3:
                continue
            kind = parts[1]
            try:
                size = int(parts[2])
            except ValueError:
                break
            # 无论什么类型都必须把内容读干净，否则后续 header 会错位。
            if size > MAX_BYTES or kind != "blob":
                remaining = size
                while remaining > 0:
                    chunk = proc.stdout.read(min(65536, remaining))
                    if not chunk:
                        break
                    remaining -= len(chunk)
                proc.stdout.read(1)
                continue
            data = proc.stdout.read(size)
            proc.stdout.read(1)
            yield parts[0], data
    finally:
        writer.join(timeout=5)
        try:
            proc.wait(timeout=30)
        except subprocess.TimeoutExpired:
            proc.kill()


def iter_history(root: str) -> Iterator[Tuple[str, str, int, str]]:
    """按 blob 扫全历史：首次出现路径 + blob 短 id。

    历史里的行号会随版本漂移，因此这里只给「文件 + blob」定位，行号取该
    blob 内的真实行号。
    """
    out = _git(root, ["rev-list", "--objects", "--all"]).decode("utf-8", "ignore")
    first_path: Dict[str, str] = {}
    order: List[str] = []
    for line in out.splitlines():
        parts = line.split(" ", 1)
        if len(parts) != 2:
            continue
        sha, path = parts
        if path in first_path.values() or sha in first_path:
            continue
        if excluded(path):
            continue
        first_path[sha] = path
        order.append(sha)
    for start in range(0, len(order), 200):
        chunk = order[start : start + 200]
        for sha, data in _read_batch(root, chunk):
            if b"\x00" in data:
                continue
            text = data.decode("utf-8", "ignore")
            lines = text.splitlines()
            path = first_path.get(sha, "")
            for lineno, _rule, _evidence in scan_text(text):
                if 0 < lineno <= len(lines):
                    yield sha[:7], path, lineno, lines[lineno - 1]


def collect(mode: str, root: str, range_spec: Optional[str]) -> List[Dict[str, object]]:
    hits: List[Dict[str, object]] = []
    if mode == "tree":
        for path, text in iter_tree(root):
            for lineno, rule, evidence in scan_text(text):
                hits.append({"path": path, "line": lineno, "rule": rule, "commit": None, "evidence": evidence})
    elif mode in ("staged", "range"):
        for commit, path, lineno, text in iter_diff(root, range_spec, mode == "staged"):
            for _number, rule, evidence in scan_text(text):
                hits.append({"path": path, "line": lineno, "rule": rule, "commit": commit or None, "evidence": evidence})
    elif mode == "all-history":
        seen = set()
        for blob, path, lineno, text in iter_history(root):
            for _number, rule, evidence in scan_text(text):
                key = (path, rule, evidence)
                if key in seen:
                    continue
                seen.add(key)
                hits.append({"path": path, "line": lineno, "rule": rule, "commit": blob, "evidence": evidence})
    return hits


def load_allowlist(path: str) -> List[Dict[str, object]]:
    if not os.path.exists(path):
        return []
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError) as exc:
        raise SystemExit(_fail("allowlist 无法解析：%s" % exc))
    entries = data.get("entries") if isinstance(data, dict) else data
    if not isinstance(entries, list):
        raise SystemExit(_fail("allowlist 必须是数组或 {\"entries\": [...]}"))
    cleaned = []
    for entry in entries:
        if not isinstance(entry, dict) or not entry.get("path") or not entry.get("rule"):
            raise SystemExit(_fail("allowlist 条目缺少 path/rule"))
        if not entry.get("reason"):
            raise SystemExit(_fail("allowlist 条目必须写 reason（便于复核）"))
        cleaned.append(entry)
    return cleaned


def _matches(entry: Dict[str, object], hit: Dict[str, object]) -> bool:
    if entry.get("path") != hit.get("path") or entry.get("rule") != hit.get("rule"):
        return False
    line = entry.get("line")
    # 历史命中的行号随版本漂移，只按 path+rule 放行（当前树仍按行号严格匹配）。
    if line is not None and not hit.get("commit") and int(line) != int(hit.get("line") or 0):
        return False
    commit = entry.get("commit")
    if commit and str(commit) != str(hit.get("commit") or ""):
        return False
    return True


def split_allowlist(
    hits: Sequence[Dict[str, object]], entries: Sequence[Dict[str, object]]
) -> Tuple[List[Dict[str, object]], List[Dict[str, object]], List[Dict[str, object]]]:
    """返回 (未放行命中, 被放行的命中, 失效的 allowlist 条目)。"""
    remaining: List[Dict[str, object]] = []
    allowed: List[Dict[str, object]] = []
    used = set()
    for hit in hits:
        match = None
        for index, entry in enumerate(entries):
            if _matches(entry, hit):
                match = index
                break
        if match is None:
            remaining.append(hit)
        else:
            used.add(match)
            allowed.append(hit)
    stale = [entry for index, entry in enumerate(entries) if index not in used]
    return remaining, allowed, stale


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="M16-38 secret 扫描（只读、掩码输出）")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--tree", action="store_true", help="扫描工作区受版本控制的文件（默认）")
    group.add_argument("--staged", action="store_true", help="只扫描已暂存的新增行")
    group.add_argument("--range", dest="range_spec", help="扫描提交区间，例如 A..B")
    group.add_argument("--all-history", action="store_true", help="扫描全部分支的提交内容")
    parser.add_argument("--root", default=".", help="仓库根（默认当前目录）")
    parser.add_argument("--allowlist", default=DEFAULT_ALLOWLIST, help="allowlist JSON 路径")
    parser.add_argument("--json", action="store_true", help="输出 JSON")
    parser.add_argument("--strict-allowlist", action="store_true", help="allowlist 条目未命中即失败")
    args = parser.parse_args(argv)

    root = os.path.abspath(args.root)
    if not os.path.isdir(os.path.join(root, ".git")) and not os.path.exists(os.path.join(root, ".git")):
        return _fail("不是 git 仓库：%s" % root)

    if args.staged:
        mode = "staged"
    elif args.range_spec:
        mode = "range"
    elif args.all_history:
        mode = "all-history"
    else:
        mode = "tree"

    hits = collect(mode, root, args.range_spec)
    entries = load_allowlist(args.allowlist)
    remaining, allowed, stale = split_allowlist(hits, entries)

    if args.json:
        sys.stdout.write(
            json.dumps(
                {
                    "mode": mode,
                    "hits": remaining,
                    "allowed": allowed,
                    "stale_allowlist": stale,
                },
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
            + "\n"
        )
    else:
        for hit in remaining:
            location = "%s:%s" % (hit["path"], hit["line"])
            if hit.get("commit"):
                location = "%s@%s" % (location, hit["commit"])
            sys.stdout.write("%s rule=%s evidence=%s\n" % (location, hit["rule"], hit["evidence"]))
        if allowed:
            sys.stdout.write("已按 allowlist 放行 %d 条（见 %s）\n" % (len(allowed), args.allowlist))
        if stale:
            sys.stdout.write("allowlist 有 %d 条未命中（可能是已修好的豁免）\n" % len(stale))
        sys.stdout.write("扫描完成：mode=%s 命中 %d 条，放行 %d 条\n" % (mode, len(remaining), len(allowed)))

    if remaining:
        return 1
    if stale and args.strict_allowlist:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
