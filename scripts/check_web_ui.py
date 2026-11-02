"""M16-40 前端单测页的真实浏览器验收：只认 `<title>ALL PASSED (n)</title>`。

为什么不用 `python -m http.server`：HTTP/1.0 的单线程服务在 116+ 用例下抖动
（见 `app/web/tests/tests.js` 顶部注释）。这里用 `ThreadingHTTPServer` + HTTP/1.1
keep-alive，并且只把 `ALL PASSED` / `n FAILED / m` 当作有效结论，其他标题一律重跑。

生产服务屏蔽 `/tests/`（`app/server/http.py` 的 BLOCKED_PREFIXES），所以必须另起
静态服务，不能拿 `run.py` 当宿主。
"""

from __future__ import annotations

import argparse
import functools
import http.server
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from typing import Optional, Tuple

CHROME_CANDIDATES = (
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
    "/usr/bin/google-chrome",
    "/usr/bin/chromium",
)
TITLE_RE = re.compile(r"<title>([^<]*)</title>", re.IGNORECASE)
VALID = re.compile(r"^(?:ALL PASSED \(\d+\)|\d+ FAILED / \d+)$")


def find_chrome(explicit: Optional[str] = None) -> Optional[str]:
    for path in ([explicit] if explicit else []) + [os.environ.get("CHROME_BIN")] + list(CHROME_CANDIDATES):
        if path and os.path.exists(path):
            return path
    return shutil.which("google-chrome") or shutil.which("chromium")


class Handler(http.server.SimpleHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):  # 静音，避免污染结果
        return


def serve(directory: str, host: str, port: int):
    handler = functools.partial(Handler, directory=directory)
    httpd = http.server.ThreadingHTTPServer((host, port), handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    return httpd, thread


def dump_title(chrome: str, url: str, budget: int) -> str:
    proc = subprocess.run(
        [
            chrome,
            "--headless=new",
            "--disable-gpu",
            "--no-sandbox",
            "--disable-extensions",
            "--user-data-dir=%s" % _profile_dir(),
            "--virtual-time-budget=%d" % budget,
            "--dump-dom",
            url,
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        timeout=180,
        check=False,
    )
    dom = proc.stdout.decode("utf-8", "replace")
    match = TITLE_RE.search(dom)
    return match.group(1).strip() if match else ""


_counter = {"n": 0}


def _profile_dir() -> str:
    """不使用 tempfile：受限环境里 mkdtemp 可能被拒绝。"""
    _counter["n"] += 1
    base = os.environ.get("MCC_E2E_CHROME_DIR") or os.path.join(os.path.expanduser("~"), ".cache", "mcc-web-check")
    path = "%s-%d-%d" % (base, os.getpid(), _counter["n"])
    os.makedirs(path, exist_ok=True)
    return path


def main(argv: Optional[list] = None) -> int:
    parser = argparse.ArgumentParser(description="前端单测页 HTTP 验收")
    parser.add_argument("--web-dir", default=os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app", "web"))
    parser.add_argument("--page", default="/tests/index.html")
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--budget", type=int, default=12000, help="Chrome virtual-time-budget（毫秒）")
    parser.add_argument("--retries", type=int, default=3)
    parser.add_argument("--chrome", default=None)
    args = parser.parse_args(argv)

    chrome = find_chrome(args.chrome)
    if not chrome:
        sys.stderr.write("check_web_ui: 找不到 Chrome/Chromium\n")
        return 2
    if not os.path.isdir(args.web_dir):
        sys.stderr.write("check_web_ui: 目录不存在 %s\n" % args.web_dir)
        return 2

    httpd, thread = serve(args.web_dir, args.host, args.port)
    port = httpd.server_address[1]
    url = "http://%s:%d%s" % (args.host, port, args.page)
    try:
        last = ""
        for attempt in range(1, args.retries + 1):
            title = dump_title(chrome, url, args.budget)
            last = title
            if title.startswith("ALL PASSED"):
                sys.stdout.write("%s（第 %d 次尝试，%s）\n" % (title, attempt, url))
                return 0
            if VALID.match(title or ""):
                sys.stdout.write("%s（第 %d 次尝试，%s）\n" % (title, attempt, url))
                return 1
            time.sleep(0.4)
        sys.stderr.write("check_web_ui: 未取得有效结论，最后一次标题=%r\n" % last)
        return 1
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)


if __name__ == "__main__":
    raise SystemExit(main())
