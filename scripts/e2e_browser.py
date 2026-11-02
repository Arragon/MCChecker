"""M16-40 真实浏览器 E2E 驱动。

流程：临时数据根 → 种子数据（真 HTTP API）→ 起真实服务 → Chrome(CDP) 逐场景
点击/输入/断言/截图 → 写 JSON 报告 + Markdown 摘要。

不做的事：不连内网 SDA、不写仓库 `data/`、不把 `reachable` 冒充 `interactive`。

用法：
    ./.venv39/bin/python scripts/e2e_browser.py                # 全部场景
    ./.venv39/bin/python scripts/e2e_browser.py --only updates # 只跑更新管理
    ./.venv39/bin/python scripts/e2e_browser.py --keep         # 保留下载/报告目录
"""

from __future__ import annotations

import argparse
import http.client
import json
import os
import random
import shutil
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from typing import Any, Dict, List, Optional, Tuple

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)
sys.path.insert(0, os.path.join(REPO_ROOT, "scripts"))

import e2e_scenarios  # noqa: E402  - 同目录脚本
from e2e_cdp import Chrome, CdpError  # noqa: E402

ADMIN_IP = "10.9.9.1"
MEMBER_IP = "10.9.9.2"
ADMIN_NAME = "E2E管理员"
OPERATOR_TOKEN = "e2e-operator-token"
EVIDENCE_DIR = os.path.join(REPO_ROOT, "docs", "evidence", "m16-40")


def log(message: str) -> None:
    sys.stdout.write(message + "\n")
    sys.stdout.flush()


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def work_dir(name: str = "mcc-e2e-40") -> str:
    base = os.environ.get("MCC_E2E_HOME") or os.path.join(os.path.expanduser("~"), ".cache", name)
    path = "%s-%d-%d" % (base, os.getpid(), int(time.time()) % 100000)
    os.makedirs(path, exist_ok=True)
    return path


class Server:
    def __init__(self, data_dir: str, port: int) -> None:
        self.data_dir = data_dir
        self.port = port
        env = dict(os.environ)
        env.update(
            {
                "MCHECKER_TRUSTED_PROXIES": "127.0.0.1",
                "MCHECKER_OPERATOR_TOKEN": OPERATOR_TOKEN,
                "PYTHONUNBUFFERED": "1",
            }
        )
        self.log_path = os.path.join(data_dir, "server.log")
        self._log = open(self.log_path, "wb")
        self.proc = subprocess.Popen(
            [
                sys.executable,
                os.path.join(REPO_ROOT, "run.py"),
                "--data-dir",
                data_dir,
                "--host",
                "127.0.0.1",
                "--port",
                str(port),
                "--no-reload",
            ],
            cwd=REPO_ROOT,
            env=env,
            stdout=self._log,
            stderr=subprocess.STDOUT,
        )

    def base_url(self) -> str:
        return "http://127.0.0.1:%d" % self.port

    def wait_ready(self, timeout: float = 60.0) -> None:
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self.proc.poll() is not None:
                raise RuntimeError("服务提前退出，见 %s" % self.log_path)
            try:
                with urllib.request.urlopen(self.base_url() + "/api/v3/session", timeout=2):
                    return
            except (urllib.error.URLError, OSError):
                time.sleep(0.3)
        raise RuntimeError("服务启动超时，见 %s" % self.log_path)

    def stop(self) -> None:
        try:
            self.proc.terminate()
            self.proc.wait(timeout=20)
        except (subprocess.TimeoutExpired, OSError):
            try:
                self.proc.kill()
            except OSError:
                pass
        finally:
            self._log.close()


def api(
    server: Server,
    method: str,
    path: str,
    body: Optional[dict] = None,
    ip: str = ADMIN_IP,
    headers: Optional[Dict[str, str]] = None,
) -> Tuple[int, Any]:
    url = server.base_url() + path
    payload = json.dumps(body).encode("utf-8") if body is not None else None
    request = urllib.request.Request(url, data=payload, method=method)
    request.add_header("X-Forwarded-For", ip)
    request.add_header("X-Requested-With", "MCChecker")
    request.add_header("Content-Type", "application/json")
    for key, value in (headers or {}).items():
        request.add_header(key, value)
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            raw = response.read()
            return response.status, (json.loads(raw.decode("utf-8")) if raw else None)
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        try:
            parsed = json.loads(raw.decode("utf-8")) if raw else None
        except ValueError:
            parsed = raw.decode("utf-8", "replace")
        return exc.code, parsed


def seed(server: Server, data_dir: str) -> Dict[str, str]:
    """在临时数据根里造：一个管理员身份、一个文件、一个 HTTP 来源。"""
    from app.core import storage  # 延迟导入：只有种子阶段需要

    storage.configure_data_dir(data_dir)
    storage.upsert_ip_mapping(ADMIN_IP, ADMIN_NAME)
    storage.upsert_admin_user(ADMIN_NAME)
    saved = {}
    try:
        with storage.use_profile("default"):
            storage.save_config_file("A.xml", b"<R><x>1</x><y>2</y><z>3</z></R>")
            saved["file"] = "A.xml"
    except Exception as exc:  # pragma: no cover - 环境相关
        log("种子文件写入失败（不影响更新管理场景）：%s" % exc)

    status, files = api(server, "GET", "/api/v3/workspaces/default/profiles/default/file-selector")
    file_id = ""
    if status == 200 and isinstance(files, dict):
        for row in files.get("items") or []:
            if row.get("name") == "A.xml":
                file_id = row.get("file_id") or ""
                break
    target = {"kind": "file_archive", "file_id": file_id} if file_id else None
    for name in ("E2E HTTP 来源", "E2E 停用来源"):
        payload = {
            "profile_id": "default",
            "name": name,
            "kind": "http_file",
            "resource_ref": "http://127.0.0.1:9/e2e.xml",
            "display_path": "/e2e/A.xml",
            "schedule_mode": "custom",
            "interval_hours": 6,
            "enabled": name.endswith("来源"),
        }
        if target:
            payload["target"] = target
        status, body = api(server, "POST", "/api/v3/workspaces/default/sync/sources", payload)
        if status not in (201, 400):
            log("种子来源失败：%s %s" % (status, body))
    return saved


class Runner:
    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args
        self.root = work_dir()
        self.data_dir = os.path.join(self.root, "data")
        os.makedirs(self.data_dir, exist_ok=True)
        self.report: List[Dict[str, Any]] = []
        self.server: Optional[Server] = None
        self.browser: Optional[Chrome] = None
        self.shots = 0

    # ---------------- 基础设施 ----------------

    def start(self) -> None:
        port = self.args.port or free_port()
        self.server = Server(self.data_dir, port)
        self.server.wait_ready()
        log("服务就绪：%s（数据根 %s）" % (self.server.base_url(), self.data_dir))
        seed(self.server, self.data_dir)
        self.browser = Chrome(chrome=self.args.chrome)
        self.browser.viewport(1366, 768)
        self.role(ADMIN_IP)
        log("Chrome 就绪（profile %s）" % self.browser.profile_dir)

    def role(self, ip: str) -> None:
        assert self.browser
        self.browser.headers({"X-Forwarded-For": ip})

    def stop(self) -> None:
        if self.browser:
            self.browser.close()
        if self.server:
            self.server.stop()

    def url(self, hash_path: str) -> str:
        assert self.server
        return self.server.base_url() + "/index.html" + hash_path

    def verify_identity(self, expect_admin: bool) -> bool:
        assert self.browser
        session = self.browser.eval("MC && MC.session ? JSON.stringify(MC.session.capabilities || {}) : 'null'")
        try:
            caps = json.loads(session) if session and session != "null" else {}
        except ValueError:
            caps = {}
        has_admin = bool(caps.get("settings_write"))
        return has_admin == expect_admin

    # ---------------- 步骤执行 ----------------

    def shoot(self, name: str) -> str:
        os.makedirs(EVIDENCE_DIR, exist_ok=True)
        self.shots += 1
        path = os.path.join(EVIDENCE_DIR, "%s.png" % name)
        assert self.browser
        self.browser.screenshot(path)
        return os.path.relpath(path, REPO_ROOT)

    def run_steps(self, scenario: Dict[str, Any]) -> Tuple[bool, List[str], Optional[str]]:
        assert self.browser
        notes: List[str] = []
        shot = None
        for step in scenario.get("steps") or []:
            kind, _, arg = str(step).partition(":")
            if kind == "wait":
                if not self.browser.wait_for("document.querySelector(%s)" % json.dumps(arg), timeout=20):
                    return False, notes, shot
                notes.append("wait %s ok" % arg)
            elif kind == "assert":
                if not self.browser.eval("!!document.querySelector(%s)" % json.dumps(arg)):
                    notes.append("缺少 %s" % arg)
                    return False, notes, shot
                notes.append("assert %s ok" % arg)
            elif kind == "assert-text":
                body = self.browser.eval("document.body.innerText") or ""
                if arg not in body:
                    notes.append("页面缺少文本：%s" % arg)
                    return False, notes, shot
                notes.append("text %s ok" % arg)
            elif kind == "click":
                clicked = self.browser.eval(
                    "(function(){var el=document.querySelector(%s); if(!el) return false; el.click(); return true;})()"
                    % json.dumps(arg)
                )
                if not clicked:
                    notes.append("点击目标不存在：%s" % arg)
                    return False, notes, shot
                time.sleep(0.35)
            elif kind == "key":
                self.browser.press(arg)
                time.sleep(0.35)
            elif kind == "wait-close":
                if not self.browser.wait_for("!document.querySelector('.modal')", timeout=10):
                    notes.append("抽屉未关闭")
                    return False, notes, shot
                notes.append("抽屉已关闭")
            elif kind == "remember-focus":
                self.browser.eval("window.__e2eFocus = document.activeElement; true")
            elif kind == "assert-focus-in":
                inside = self.browser.eval(
                    "!!(document.activeElement && document.activeElement.closest(%s))" % json.dumps(arg)
                )
                if not inside:
                    notes.append("焦点不在 %s 内" % arg)
                    return False, notes, shot
                notes.append("焦点在抽屉内")
            elif kind == "assert-focus-restored":
                restored = self.browser.eval(
                    "!!(window.__e2eFocus && document.activeElement === window.__e2eFocus) || "
                    "!!(document.activeElement && document.activeElement.getAttribute('data-sync-new'))"
                )
                if not restored:
                    notes.append("焦点未回到触发元素")
                    return False, notes, shot
                notes.append("焦点已恢复")
            elif kind == "shot":
                shot = self.shoot(scenario["id"])
                notes.append("截图 %s" % shot)
            else:
                notes.append("未知步骤 %s" % step)
                return False, notes, shot
        return True, notes, shot

    # ---------------- 场景组 ----------------

    def run_updates(self) -> None:
        assert self.browser
        for scenario in e2e_scenarios.UPDATE_SCENARIOS:
            self.browser.navigate(self.url(e2e_scenarios.updates_hash(scenario["tab"], "default")))
            ok, notes, shot = self.run_steps(scenario)
            self.report.append(
                {
                    "id": scenario["id"],
                    "title": scenario["title"],
                    "level": "interactive",
                    "ok": ok,
                    "notes": notes,
                    "screenshot": shot,
                }
            )
            log("[%s] %s %s" % ("PASS" if ok else "FAIL", scenario["id"], "" if ok else notes[-1:]))

    def run_legacy(self) -> None:
        assert self.browser
        for item in e2e_scenarios.LEGACY_ROUTES:
            target = e2e_scenarios.legacy_hash(item, "default")
            self.browser.navigate(self.url(target))
            time.sleep(0.6)
            body = self.browser.eval("document.body.innerText") or ""
            view = self.browser.eval("!!document.querySelector('.view')")
            error = self.browser.eval("!!document.querySelector('.form-error')")
            hit = any(word in body for word in item["expect"]) if item.get("expect") else True
            ok = bool(view) and hit and not error
            self.report.append(
                {
                    "id": item["f"],
                    "title": item["label"],
                    "level": "reachable",
                    "ok": ok,
                    "notes": ["route=%s" % target, "view=%s text=%s error=%s" % (view, hit, error)],
                    "screenshot": None,
                }
            )
            log("[%s] %s %s" % ("PASS" if ok else "FAIL", item["f"], item["label"]))

    def run_roles(self) -> None:
        assert self.browser
        # 管理员：能看到编辑入口
        self.role(ADMIN_IP)
        self.browser.navigate(self.url(e2e_scenarios.updates_hash("sources", "default")))
        self.browser.wait_for("document.querySelector('[data-sync-source]')", timeout=20)
        admin_ok = self.browser.eval("!!document.querySelector('[data-sync-edit]')")
        admin_shot = self.shoot("updates-sources-1366-admin")
        # 成员：只读。SPA 的 session 是启动时拉取的，换身份必须整页重载。
        self.role(MEMBER_IP)
        self.browser.reload()
        self.browser.navigate(self.url(e2e_scenarios.updates_hash("sources", "default")))
        self.browser.wait_for("document.querySelector('[data-sync-source]')", timeout=20)
        member_ok = not self.browser.eval("!!document.querySelector('[data-sync-edit]')")
        member_shot = self.shoot("updates-sources-1366-member")
        self.role(ADMIN_IP)
        self.browser.reload()
        self.report.append(
            {
                "id": "roles-admin-member",
                "title": "管理员可编辑 / 成员只读",
                "level": "interactive",
                "ok": bool(admin_ok and member_ok),
                "notes": ["admin_edit=%s member_edit=%s" % (admin_ok, member_ok)],
                "screenshot": admin_shot if admin_ok else member_shot,
            }
        )
        log("[%s] roles-admin-member" % ("PASS" if admin_ok and member_ok else "FAIL"))

    def run_viewports(self) -> None:
        assert self.browser
        for viewport in e2e_scenarios.VIEWPORTS:
            self.browser.viewport(viewport["width"], viewport["height"])
            self.browser.navigate(self.url(e2e_scenarios.updates_hash("sources", "default")))
            self.browser.wait_for("document.querySelector('[data-sync-source]')", timeout=20)
            overflow = self.browser.eval(
                "document.documentElement.scrollWidth - document.documentElement.clientWidth"
            )
            shot = self.shoot("updates-sources-%s-admin" % viewport["id"].split("-")[-1])
            ok = isinstance(overflow, (int, float)) and overflow <= 1
            self.report.append(
                {
                    "id": "viewport-%s" % viewport["id"],
                    "title": "视口 %dx%d 无页面级横向溢出" % (viewport["width"], viewport["height"]),
                    "level": "interactive",
                    "ok": ok,
                    "notes": ["overflow=%s" % overflow],
                    "screenshot": shot,
                }
            )
            log("[%s] viewport-%s overflow=%s" % ("PASS" if ok else "FAIL", viewport["id"], overflow))
        self.browser.viewport(1366, 768)

    def run_operator(self) -> None:
        """运维 token 三段式：无 token / 错 token 都是 403，正确 token 成功。"""
        assert self.browser
        self.browser.navigate(self.url(e2e_scenarios.updates_hash("connections", "default")))
        script = """
        (async function(){
          const body = {name: "E2E 连接", origin: "https://sda.e2e.invalid"};
          async function call(token) {
            const headers = {"Content-Type": "application/json", "X-Requested-With": "MCChecker"};
            if (token) headers["X-Operator-Token"] = token;
            const res = await fetch("/api/v3/workspaces/default/connections", {
              method: "POST", headers: headers, body: JSON.stringify(body)
            });
            const text = await res.text();
            return {status: res.status, body: text};
          }
          const missing = await call("");
          const wrong = await call("wrong-token-value");
          const right = await call(%s);
          return JSON.stringify({missing, wrong, right});
        })()
        """ % json.dumps(OPERATOR_TOKEN)
        raw = self.browser.eval(script)
        try:
            data = json.loads(raw) if isinstance(raw, str) else {}
        except ValueError:
            data = {}
        missing_ok = data.get("missing", {}).get("status") == 403
        wrong_ok = data.get("wrong", {}).get("status") == 403
        right_ok = data.get("right", {}).get("status") in (200, 201)
        leaked = OPERATOR_TOKEN in json.dumps(data.get("missing", {})) + json.dumps(data.get("wrong", {}))
        ok = bool(missing_ok and wrong_ok and right_ok and not leaked)
        shot = self.shoot("updates-connections-operator-token")
        self.report.append(
            {
                "id": "operator-token",
                "title": "运维 token：缺失/错误拒绝，正确放行，且不回显 token",
                "level": "interactive",
                "ok": ok,
                "notes": [
                    "missing=%s wrong=%s right=%s leaked=%s"
                    % (
                        data.get("missing", {}).get("status"),
                        data.get("wrong", {}).get("status"),
                        data.get("right", {}).get("status"),
                        leaked,
                    )
                ],
                "screenshot": shot,
            }
        )
        log("[%s] operator-token" % ("PASS" if ok else "FAIL"))

    # ---------------- 收尾 ----------------

    def write_report(self) -> Dict[str, Any]:
        summary = e2e_scenarios.summarize(self.report)
        payload = {
            "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "data_dir": self.data_dir,
            "server": self.server.base_url() if self.server else None,
            "summary": summary,
            "results": self.report,
        }
        path = self.args.report or os.path.join(self.root, "report.json")
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
        log("报告：%s" % path)
        log(
            "汇总：interactive %d/%d 通过，reachable %d/%d 通过"
            % (
                summary["interactive_pass"],
                summary["interactive_pass"] + summary["interactive_fail"],
                summary["reachable_pass"],
                summary["reachable_pass"] + summary["reachable_fail"],
            )
        )
        return payload


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="M16-40 真实浏览器 E2E")
    parser.add_argument("--only", default="all", choices=("all", "updates", "legacy", "roles", "viewports", "operator"))
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument("--chrome", default=None)
    parser.add_argument("--report", default=None)
    parser.add_argument("--keep", action="store_true", help="保留临时数据根（默认也保留，便于取证）")
    args = parser.parse_args(argv)

    runner = Runner(args)
    started = time.time()
    code = 0
    try:
        runner.start()
        groups = [args.only] if args.only != "all" else ["updates", "legacy", "roles", "viewports", "operator"]
        for group in groups:
            if group == "updates":
                runner.run_updates()
            elif group == "legacy":
                runner.run_legacy()
            elif group == "roles":
                runner.run_roles()
            elif group == "viewports":
                runner.run_viewports()
            elif group == "operator":
                runner.run_operator()
    finally:
        try:
            payload = runner.write_report()
        except Exception as exc:  # pragma: no cover - 报告失败也要给出原因
            log("写报告失败：%s" % exc)
            payload = {"summary": {}, "results": []}
        runner.stop()
    failed = [row for row in payload.get("results", []) if not row.get("ok")]
    if failed:
        code = 1
        log("失败项：%s" % ", ".join(str(row.get("id")) for row in failed))
    log("用时 %.1fs" % (time.time() - started))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
