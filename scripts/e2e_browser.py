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
import hashlib
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
LEGACY_TOKEN = "obsolete-token"
EVIDENCE_DIR = os.environ.get("MCC_E2E_EVIDENCE_DIR") or os.path.join(REPO_ROOT, "docs", "evidence", "m16-40")


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
            "enabled": name == "E2E HTTP 来源",
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
            elif kind == "assert-disabled-source":
                ok = self.browser.eval("Array.from(document.querySelectorAll('[data-sync-source]')).some(r => r.textContent.includes('E2E 停用来源') && r.textContent.includes('已停用') && r.querySelector('[data-sync-run]')?.disabled)")
                if not ok:
                    notes.append("停用来源状态或更新按钮错误")
                    return False, notes, shot
                notes.append("停用来源不可更新")
            elif kind == "validate-connection-form":
                checked = self.browser.eval("""(function(){
                  var d=document.querySelector('[data-conn-create-submit]').closest('.modal');
                  var name=d.querySelector('[data-conn-name]'),origin=d.querySelector('[data-conn-origin]'),submit=d.querySelector('[data-conn-create-submit]');
                  function check(input){var error=document.getElementById(input.getAttribute('aria-describedby'));return document.activeElement===input && input.getAttribute('aria-invalid')==='true' && !!error && !!error.textContent;}
                  submit.click();if(!check(name))return false;name.value='测试连接';name.dispatchEvent(new Event('input',{bubbles:true}));
                  submit.click();if(!check(origin))return false;origin.value='https://example.invalid';origin.dispatchEvent(new Event('input',{bubbles:true}));
                  return !name.hasAttribute('aria-invalid') && !origin.hasAttribute('aria-invalid') && !d.querySelector('[data-conn-token]');
                })()""")
                if not checked:
                    notes.append("连接名称/地址校验或管理员免令牌状态不正确")
                    return False, notes, shot
                notes.append("连接表单校验关联和焦点正常")
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
            elif kind == "assert-source-tab-wrap":
                self.browser.eval("document.querySelector('[data-sync-save]').focus()")
                self.browser.press("Tab")
                if not self.browser.eval("document.activeElement && document.activeElement.getAttribute('aria-label') === '关闭' && !!document.activeElement.closest('.sync-source-dialog')"):
                    notes.append("Tab 未从抽屉末尾回到首个可见控件")
                    return False, notes, shot
                notes.append("Tab 从抽屉末尾循环到关闭按钮")
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
        # 访客：只读。SPA 的 session 是启动时拉取的，换身份必须整页重载。
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
                "title": "管理员可编辑 / 访客只读",
                "level": "interactive",
                "ok": bool(admin_ok and member_ok),
                "notes": ["admin_edit=%s member_edit=%s" % (admin_ok, member_ok)],
                "screenshot": admin_shot if admin_ok else member_shot,
            }
        )
        log("[%s] roles-admin-member" % ("PASS" if admin_ok and member_ok else "FAIL"))

    def run_source_scope(self) -> None:
        """隔离工作区、两个机型，验证服务端范围筛选和全量预览互不干扰。"""
        assert self.browser and self.server
        from app.services import sync_connections

        b = self.browser
        notes = []
        ok = False
        try:
            status, data = api(self.server, "POST", "/api/v3/workspaces", {"name": "R6 范围验收"})
            if status != 201:
                raise AssertionError("创建隔离工作区失败：%s" % status)
            wid = data["workspace"]["id"]
            status, data = api(self.server, "POST", "/api/v3/workspaces/%s/profiles" % wid, {"name": "第二机型"})
            if status != 201:
                raise AssertionError("创建隔离机型失败：%s" % status)
            second = data["profile"]["id"]
            connection = sync_connections.create_connection(wid, "合成连接", "https://example.invalid")["connection_id"]
            for pid, name in (("default", "当前机型来源"), (second, "第二机型来源")):
                status, result = api(self.server, "POST", "/api/v3/workspaces/%s/sync/sources" % wid, {
                    "profile_id": pid, "name": name, "kind": "sda_catalog", "target": None,
                    "enabled": True, "schedule_mode": "manual", "interval_hours": None,
                    "connection_id": connection, "resource_ref": "scope-" + pid, "display_path": "/synthetic/" + pid,
                }, headers={"X-Workspace-Id": wid})
                if status != 201:
                    raise AssertionError("创建范围测试来源失败：%s %s" % (status, result))
            b.navigate(self.url(e2e_scenarios.updates_hash("sources", wid)))
            if not b.wait_for("document.querySelector('[data-sync-source]') && document.body.innerText.includes('当前机型来源')", timeout=20):
                raise AssertionError("当前机型来源未显示")
            if b.eval("document.body.innerText.includes('第二机型来源')"):
                raise AssertionError("当前机型范围显示了其他机型来源")
            status, preview = api(self.server, "POST", "/api/v3/workspaces/%s/sync/previews" % wid, {"scope": "workspace_all"}, headers={"X-Workspace-Id": wid})
            if status != 201 or preview.get("total") != 2:
                raise AssertionError("当前机型筛选错误缩小了工作区全量预览")
            b.eval("var s=document.querySelector('[data-sync-scope]');s.value='workspace';s.dispatchEvent(new Event('change',{bubbles:true}));")
            if not b.wait_for("document.body.innerText.includes('当前机型来源') && document.body.innerText.includes('第二机型来源')", timeout=10):
                raise AssertionError("全部机型范围未显示两个来源")
            notes.append("当前机型仅显示其来源；全量预览覆盖两个机型；切换后显示全部来源")
            ok = True
        except Exception as exc:
            notes.append(str(exc))
        self.report.append({"id": "source-profile-scope", "title": "来源机型范围与工作区全量预览", "level": "interactive", "ok": ok, "notes": notes, "screenshot": None})
        log("[%s] source-profile-scope %s" % ("PASS" if ok else "FAIL", notes[-1:]))

    def run_candidate_responsive(self) -> None:
        """真实页面加合成候选响应；验证多视口长值与差异操作可达。"""
        assert self.browser
        b = self.browser
        notes = []
        ok = False
        try:
            b.viewport(1366, 768)
            b.navigate(self.url(e2e_scenarios.updates_hash("sources", "default")))
            if not b.wait_for("!!document.querySelector('[data-sync-tab=candidates]')", timeout=20):
                raise AssertionError("更新管理未加载")
            b.eval("""window.__r6OriginalApi=MC.api.requestV3;
              MC.api.requestV3=function(method,path,opts){
                if(method==='GET' && path.endsWith('/sync/candidates')) return Promise.resolve({items:[{candidate_id:'candidate-r6',source_id:'配置来源_'+ 'long'.repeat(20),profile_id:'default',state:'pending',content_hash:'a'.repeat(64),created_at:'2026-11-02T00:00:00Z',file_version_id:'version-r6'}],total:1});
                if(method==='GET' && path.endsWith('/configs')) return Promise.resolve({items:[{config_id:'config-r6',name:'配置'}]});
                if(method==='GET' && path.endsWith('/releases')) return Promise.resolve({items:[{release_id:'release-r6',version:'691'}]});
                if(method==='POST' && path.endsWith('/apply-preview')) return Promise.resolve({preview_id:'preview-r6',draft_revision:1,can_apply:false,items:[{parameter_id:'parameter-r6',name:'长参数名称',action:'conflict',allowed:['use_new','inherit_old'],ours:{value:{type:'string',text:'x'.repeat(200)}},theirs:{value:{type:'null',text:'null'}}}],blockers:[]});
                return window.__r6OriginalApi.apply(this,arguments);
              };document.querySelector('[data-sync-tab=candidates]').click();""")
            if not b.wait_for("!!document.querySelector('[data-sync-candidate] button')", timeout=10):
                raise AssertionError("合成候选未显示")
            b.eval("document.querySelector('[data-sync-candidate] button').click()")
            if not b.wait_for("!!document.querySelector('[data-sync-preview]')", timeout=10):
                raise AssertionError("候选详情未打开")
            b.eval("document.querySelector('[data-sync-preview]').click()")
            if not b.wait_for("!!document.querySelector('[data-sync-diff]')", timeout=10):
                raise AssertionError("候选差异未显示")
            for viewport in e2e_scenarios.VIEWPORTS:
                b.viewport(viewport["width"], viewport["height"])
                time.sleep(0.25)
                geometry = b.eval("""(function(){var d=document.querySelector('.sync-candidate-dialog'),body=d.querySelector('.modal-body'),w=d.querySelector('.sync-diff-wrap');w.scrollLeft=w.scrollWidth;body.scrollTop=body.scrollHeight;var a=d.querySelector('[data-sync-apply]').getBoundingClientRect(),r=body.getBoundingClientRect();return {bodyHeight:r.height,width:d.getBoundingClientRect().width,horizontalEnd:w.scrollLeft+w.clientWidth>=w.scrollWidth-1,applyVisible:a.bottom<=r.bottom+1&&a.top>=r.top-1,overflow:document.documentElement.scrollWidth-document.documentElement.clientWidth}})()""")
                if not geometry or geometry["bodyHeight"] <= 0 or geometry["width"] > viewport["width"] or not geometry["horizontalEnd"] or not geometry["applyVisible"] or geometry["overflow"] > 1:
                    raise AssertionError("%s 候选详情不可达：%s" % (viewport["id"], geometry))
                notes.append("%s %s" % (viewport["id"], geometry))
            ok = True
        except Exception as exc:
            notes.append(str(exc))
        finally:
            b.eval("if(window.__r6OriginalApi){MC.api.requestV3=window.__r6OriginalApi;delete window.__r6OriginalApi;} Array.from(document.querySelectorAll('.sync-candidate-dialog .modal-footer button')).forEach(b=>{if(b.textContent==='关闭')b.click()});")
            b.viewport(1366, 768)
        self.report.append({"id": "candidate-responsive-synthetic", "title": "合成候选长值与多视口详情", "level": "interactive", "ok": ok, "notes": notes, "screenshot": None})
        log("[%s] candidate-responsive-synthetic %s" % ("PASS" if ok else "FAIL", notes[-1:]))

    def run_candidate_real_apply(self) -> None:
        """隔离数据中建立文件候选，通过真实 API 和页面写入准备版草稿。"""
        assert self.browser and self.server
        from app.core import storage
        from app.services import config_v3_catalog, config_v3_drafts, file_versions, sync_candidates, sync_sources

        b = self.browser
        notes = []
        ok = False
        try:
            status, data = api(self.server, "POST", "/api/v3/workspaces", {"name": "R6 候选真实服务验收"})
            if status != 201:
                raise AssertionError("创建隔离工作区失败：%s" % status)
            wid = data["workspace"]["id"]
            with storage.use_workspace(wid), storage.use_profile("default"):
                storage.save_config_file("candidate.xml", b"<R><x>1</x></R>")
                base_file_version_id = file_versions.current_identity("default", "candidate.xml")["version_id"]
                storage.save_config_file("candidate.xml", b"<R><x>2</x></R>")
                file_row = next(x for x in file_versions.list_resources("default") if x["name"] == "candidate.xml")
            with storage.use_workspace(wid):
                config = config_v3_catalog.create_config("default", {"code": "R6", "name": "候选测试配置"})
                cid = config["config_id"]
                parameter = config_v3_catalog.create_parameter("default", cid, {"name": "x", "directory": [], "type": "int"})
                pid = parameter["parameter_id"]
                release = config_v3_catalog.create_release("default", cid, {"version": "1"})
                config_v3_catalog.create_release("default", cid, {"version": "2"})
                did = "d0-" + release["release_id"]
                config_v3_drafts.patch_values("default", cid, did, {"request_id": "r6-seed", "items": [{"parameter_id": pid, "membership": "included", "value_state": "present", "value": "1"}]}, if_match='"0"')
                before = config_v3_drafts.get_draft("default", cid, did)
                head_before = (config_v3_catalog._load("default")["configs"][cid].get("main_branch") or {}).get("head_id")
            source = sync_sources.create_source(wid, {"profile_id": "default", "name": "candidate.xml", "kind": "http_file", "resource_ref": "https://example.invalid/candidate.xml", "target": {"kind": "file_archive", "file_id": file_row["file_id"]}})
            candidate = sync_candidates.create_candidate(wid, "default", source["source_id"], 1, file_row["blob_sha256"], "file_archive:%s" % file_row["file_id"], file_version_id=file_row["version_id"], base_file_version_id=base_file_version_id)
            b.navigate("about:blank")
            b.navigate(self.url(e2e_scenarios.updates_hash("candidates", wid)))
            selector = '[data-sync-candidate="%s"] button' % candidate["candidate_id"]
            if not b.wait_for("!!document.querySelector(%s)" % json.dumps(selector), timeout=20):
                raise AssertionError("真实候选未出现在列表")
            b.eval("document.querySelector(%s).click()" % json.dumps(selector))
            if not b.wait_for("document.querySelectorAll('.sync-candidate-dialog select[aria-label=\"软件版本\"] option').length === 2", timeout=15):
                raise AssertionError("固定配置或版本未加载")
            b.eval("document.querySelector('[data-sync-preview]').click()")
            if not b.wait_for("!!document.querySelector('[data-sync-diff]')", timeout=15):
                raise AssertionError("真实候选差异未生成")
            b.eval("var s=document.querySelector('.sync-candidate-dialog select[aria-label=\"软件版本\"]');s.selectedIndex=1;s.dispatchEvent(new Event('change',{bubbles:true}))")
            if b.eval("!!document.querySelector('[data-sync-apply]')"):
                raise AssertionError("切换版本后旧预览仍可写入")
            b.eval("var s=document.querySelector('.sync-candidate-dialog select[aria-label=\"软件版本\"]');s.selectedIndex=0;s.dispatchEvent(new Event('change',{bubbles:true}));document.querySelector('[data-sync-preview]').click()")
            if not b.wait_for("!!document.querySelector('[data-sync-diff]')", timeout=15):
                raise AssertionError("切换回来后无法重新预览")
            if not b.eval("document.querySelector('[data-sync-diff]').textContent.includes('2') && !document.querySelector('[data-sync-apply]').disabled"):
                raise AssertionError("候选差异或写入状态不正确：%s" % b.eval("document.querySelector('.sync-candidate-dialog').innerText"))
            with storage.use_workspace(wid):
                config_v3_drafts.patch_values("default", cid, did, {"request_id": "r6-concurrent", "items": [{"parameter_id": pid, "membership": "included", "value_state": "present", "value": "3"}]}, if_match='"%d"' % before["revision"])
            b.eval("document.querySelector('[data-sync-apply]').click()")
            if not b.wait_for("!!document.querySelector('[data-sync-repreview]')", timeout=15):
                raise AssertionError("真实并发修改未触发候选 409 恢复")
            b.eval("document.querySelector('[data-sync-repreview]').click()")
            if not b.wait_for("!!document.querySelector('[data-sync-decision]') && !document.querySelector('[data-sync-repreview]')", timeout=15):
                raise AssertionError("候选重新预览失败")
            b.eval("var s=document.querySelector('[data-sync-decision]');s.value='use_new';s.dispatchEvent(new Event('change',{bubbles:true}))")
            if b.eval("document.querySelector('[data-sync-apply]').disabled"):
                raise AssertionError("明确决议后仍无法写入草稿")
            b.eval("document.querySelector('[data-sync-apply]').click()")
            if not b.wait_for("!!document.querySelector('[data-sync-applied]')", timeout=15):
                raise AssertionError("候选写入未返回成功")
            with storage.use_workspace(wid):
                after = config_v3_drafts.get_draft("default", cid, did)
                head_after = (config_v3_catalog._load("default")["configs"][cid].get("main_branch") or {}).get("head_id")
            if after["values"][pid]["value"] != "2" or after["revision"] != before["revision"] + 2 or head_after != head_before:
                raise AssertionError("写入值、草稿修订或发布边界不符")
            if sync_candidates.get_candidate(wid, candidate["candidate_id"])["state"] != "applied":
                raise AssertionError("服务端候选状态未更新")
            notes.append("合成文件候选经真实 API 预览；并发修改触发 409，重新预览后写入草稿，主分支 head 不变")
            ok = True
        except Exception as exc:
            notes.append(str(exc))
        finally:
            b.navigate("about:blank")
        self.report.append({"id": "candidate-real-apply", "title": "隔离文件候选真实服务写入草稿", "level": "interactive", "ok": ok, "notes": notes, "screenshot": None})
        log("[%s] candidate-real-apply %s" % ("PASS" if ok else "FAIL", notes[-1:]))

    def run_candidate_conflict_contract(self) -> None:
        """验证显式基线产生真实冲突；该基线参数不由当前界面提供。"""
        assert self.server
        from app.core import storage
        from app.services import config_v3_catalog, config_v3_drafts, file_versions, sync_candidates, sync_sources

        notes = []
        ok = False
        try:
            status, data = api(self.server, "POST", "/api/v3/workspaces", {"name": "R6 冲突契约验收"})
            if status != 201:
                raise AssertionError("创建隔离工作区失败")
            wid = data["workspace"]["id"]
            with storage.use_workspace(wid), storage.use_profile("default"):
                storage.save_config_file("conflict.xml", b"<R><x>1</x></R>")
                base_file_version_id = file_versions.current_identity("default", "conflict.xml")["version_id"]
                storage.save_config_file("conflict.xml", b"<R><x>3</x></R>")
                file_row = next(x for x in file_versions.list_resources("default") if x["name"] == "conflict.xml")
            with storage.use_workspace(wid):
                config = config_v3_catalog.create_config("default", {"code": "R6-CONFLICT", "name": "冲突测试"})
                cid = config["config_id"]
                pid = config_v3_catalog.create_parameter("default", cid, {"name": "x", "directory": [], "type": "int"})["parameter_id"]
                old = config_v3_catalog.create_release("default", cid, {"version": "1"})
                old_draft = "d0-" + old["release_id"]
                config_v3_drafts.patch_values("default", cid, old_draft, {"request_id": "base", "items": [{"parameter_id": pid, "membership": "included", "value_state": "present", "value": "1"}]}, if_match='"0"')
                config_v3_drafts.freeze_default("default", cid, old_draft, {"request_id": "freeze-base"})
                release = config_v3_catalog.create_release("default", cid, {"version": "2"})
                did = "d0-" + release["release_id"]
                config_v3_drafts.patch_values("default", cid, did, {"request_id": "current", "items": [{"parameter_id": pid, "membership": "included", "value_state": "present", "value": "2"}]}, if_match='"0"')
            source = sync_sources.create_source(wid, {"profile_id": "default", "name": "conflict.xml", "kind": "http_file", "resource_ref": "https://example.invalid/conflict.xml", "target": {"kind": "file_archive", "file_id": file_row["file_id"]}})
            candidate = sync_candidates.create_candidate(wid, "default", source["source_id"], 1, file_row["blob_sha256"], "file_archive:%s" % file_row["file_id"], file_version_id=file_row["version_id"], base_file_version_id=base_file_version_id)
            path = "/api/v3/workspaces/%s/sync/candidates/%s" % (wid, candidate["candidate_id"])
            headers = {"X-Workspace-Id": wid}
            status, default = api(self.server, "POST", path + "/apply-preview", {"config_id": cid, "draft_id": did}, headers=headers)
            if status != 201 or not any(x.get("parameter_id") == pid and x.get("action") == "conflict" for x in default.get("items", [])):
                raise AssertionError("原来源文件基线未识别真实冲突：%s" % default)
            status, preview = api(self.server, "POST", path + "/apply-preview", {"config_id": cid, "draft_id": did, "base_revision_id": old_draft}, headers=headers)
            if status != 400:
                raise AssertionError("不允许客户端换成任意配置修订作为原来源基线：%s" % preview)
            preview = default
            status, applied = api(self.server, "POST", path + "/apply", {"preview_id": preview["preview_id"], "request_id": "r6-conflict-apply", "decisions": {pid: "use_new"}}, headers=headers)
            if status != 200:
                raise AssertionError("真实冲突决议写入失败：%s %s" % (status, applied))
            with storage.use_workspace(wid):
                result = config_v3_drafts.get_draft("default", cid, did)
            if result["values"][pid]["value"] != "3":
                raise AssertionError("真实冲突决议未写入预期值")
            notes.append("真实 API：原来源文件值 1、本地值 2、新来源值 3 产生冲突；拒绝客户端改用任意修订，use_new 写入值 3")
            ok = True
        except Exception as exc:
            notes.append(str(exc))
        self.report.append({"id": "candidate-conflict-contract", "title": "候选显式基线与真实决议契约", "level": "api", "ok": ok, "notes": notes, "screenshot": None})
        log("[%s] candidate-conflict-contract %s" % ("PASS" if ok else "FAIL", notes[-1:]))

    def run_permission_revocation(self) -> None:
        """仅修改本次隔离数据根的管理员名单，验证撤权后草稿与权威文件。"""
        assert self.browser and self.server
        from app.core import storage
        from app.services import sync_paths

        b = self.browser
        notes = []
        ok = False
        revoked = False
        try:
            self.role(ADMIN_IP)
            b.navigate(self.url(e2e_scenarios.updates_hash("sources", "default")))
            if not b.wait_for("!!document.querySelector('[data-sync-edit]')", timeout=20):
                raise AssertionError("管理员编辑入口未加载")
            source_path = sync_paths.sync_file("default", "sources.json")
            with open(source_path, "rb") as stream:
                before = hashlib.sha256(stream.read()).hexdigest()
            source_id = b.eval("document.querySelector('[data-sync-edit]').getAttribute('data-sync-edit')")
            b.eval("document.querySelector('[data-sync-edit]').click()")
            b.eval("var d=document.querySelector('.sync-source-dialog');var l=Array.from(d.querySelectorAll('label')).find(x=>x.textContent==='名称');var f=document.getElementById(l.htmlFor);f.value='权限撤销后的本地草稿';f.dispatchEvent(new Event('input',{bubbles:true}));")
            revoked = storage.remove_admin_user(ADMIN_NAME)
            if not revoked:
                raise AssertionError("隔离管理员撤权失败")
            event_start = len(b.events)
            b.eval("document.querySelector('[data-sync-save]').click()")
            if not b.wait_for("!!document.querySelector('.sync-source-dialog .form-error')?.textContent", timeout=10):
                raise AssertionError("撤权保存没有错误反馈")
            denied = any(event.get("method") == "Network.responseReceived" and
                         event.get("params", {}).get("response", {}).get("status") == 403 and
                         event.get("params", {}).get("response", {}).get("url", "").endswith("/sync/sources/" + source_id)
                         for event in b.events[event_start:])
            if not denied:
                raise AssertionError("未观察到来源写入 403")
            if b.eval("document.querySelector('.sync-source-dialog input[autofocus]').value") != "权限撤销后的本地草稿":
                raise AssertionError("撤权后草稿丢失")
            with open(source_path, "rb") as stream:
                after = hashlib.sha256(stream.read()).hexdigest()
            if before != after:
                raise AssertionError("被拒绝后来源权威文件发生变化")
            b.eval("document.querySelector('[data-sync-cancel]').click()")
            if not b.wait_for("document.querySelectorAll('.modal').length === 2", timeout=5):
                raise AssertionError("未保存草稿关闭前没有确认")
            b.eval("Array.from(document.querySelectorAll('.modal')).pop().querySelector('.modal-footer .btn-danger').click()")
            if not b.wait_for("!document.querySelector('.sync-source-dialog')", timeout=5):
                raise AssertionError("放弃草稿后抽屉未关闭")
            notes.append("服务端 403、草稿保留、sources.json SHA-256 未变")
            ok = True
        except Exception as exc:
            notes.append(str(exc))
        finally:
            if revoked:
                storage.upsert_admin_user(ADMIN_NAME)
        self.report.append({"id": "permission-revocation", "title": "来源编辑期间撤销权限", "level": "interactive", "ok": ok, "notes": notes, "screenshot": None})
        log("[%s] permission-revocation %s" % ("PASS" if ok else "FAIL", notes[-1:]))

    def run_source_conflict(self) -> None:
        """通过真实 HTTP 和浏览器验证窄屏新建及并发冲突恢复。"""
        assert self.browser and self.server
        b = self.browser
        notes = []
        ok = False
        try:
            b.viewport(375, 812)
            b.navigate(self.url(e2e_scenarios.updates_hash("sources", "default")))
            if not b.wait_for("!!document.querySelector('[data-sync-new]')", timeout=20):
                raise AssertionError("更新源页面未加载")
            b.eval("document.querySelector('[data-sync-new]').click()")
            if not b.wait_for("document.querySelector('[data-sync-target-file]')?.options.length > 1", timeout=20):
                raise AssertionError("目标文件未加载")
            b.eval("document.querySelector('[data-sync-save]').click()")
            valid_error = b.eval("""(function(){var d=document.querySelector('.sync-source-dialog'),l=Array.from(d.querySelectorAll('label')).find(x=>x.textContent==='名称'),i=document.getElementById(l.htmlFor),e=document.getElementById(i.getAttribute('aria-describedby'));return i.getAttribute('aria-invalid')==='true' && document.activeElement===i && !!e && e.textContent.includes('名称不能为空')})()""")
            if not valid_error:
                raise AssertionError("名称校验缺少错误关联或焦点")
            b.eval("var i=document.querySelector('.sync-source-dialog input[autofocus]');i.value='R6 来源长名称测试';i.dispatchEvent(new Event('input',{bubbles:true}));document.querySelector('[data-sync-save]').click()")
            ref_error = b.eval("""(function(){var d=document.querySelector('.sync-source-dialog'),l=Array.from(d.querySelectorAll('label')).find(x=>x.textContent==='下载地址'),i=document.getElementById(l.htmlFor),e=document.getElementById(i.getAttribute('aria-describedby'));return i.getAttribute('aria-invalid')==='true' && document.activeElement===i && !!e && e.textContent.includes('下载 URL')})()""")
            if not ref_error:
                raise AssertionError("资源引用校验缺少错误关联或焦点")
            b.eval("""(function(){var d=document.querySelector('.sync-source-dialog');function input(label,value){var l=Array.from(d.querySelectorAll('label')).find(x=>x.textContent===label),i=document.getElementById(l.htmlFor);i.value=value;i.dispatchEvent(new Event('input',{bubbles:true}))}input('下载地址','http://127.0.0.1:9/test.xml');input('业务路径','wrong');d.querySelector('[data-sync-save]').click()})()""")
            if not b.eval("""(function(){var d=document.querySelector('.sync-source-dialog'),l=Array.from(d.querySelectorAll('label')).find(x=>x.textContent==='业务路径'),i=document.getElementById(l.htmlFor);return document.activeElement===i && i.getAttribute('aria-invalid')==='true'})()"""):
                raise AssertionError("业务路径错误未定位到输入")
            b.eval("""(function(){var d=document.querySelector('.sync-source-dialog');function input(label,value,event){var l=Array.from(d.querySelectorAll('label')).find(x=>x.textContent===label),i=document.getElementById(l.htmlFor);i.value=value;i.dispatchEvent(new Event(event,{bubbles:true}))}input('业务路径','/valid','input');input('计划','custom','change');d.querySelector('[data-sync-save]').click()})()""")
            if not b.eval("""(function(){var d=document.querySelector('.sync-source-dialog'),l=Array.from(d.querySelectorAll('label')).find(x=>x.textContent==='间隔（小时）'),i=document.getElementById(l.htmlFor);return document.activeElement===i && i.getAttribute('aria-invalid')==='true'})()"""):
                raise AssertionError("自定义间隔错误未定位到输入")
            b.eval("""(function(){var d=document.querySelector('.sync-source-dialog'),l=Array.from(d.querySelectorAll('label')).find(x=>x.textContent==='计划'),i=document.getElementById(l.htmlFor);i.value='inherit';i.dispatchEvent(new Event('change',{bubbles:true}));d.querySelector('[data-sync-save]').click()})()""")
            if not b.eval("document.activeElement===document.querySelector('[data-sync-target-file]') && document.activeElement.getAttribute('aria-invalid')==='true'"):
                raise AssertionError("目标文件错误未定位到选择框")
            b.eval("""(function(){var d=document.querySelector('.sync-source-dialog');
              function set(label,value){var l=Array.from(d.querySelectorAll('label')).find(x=>x.textContent===label);var i=document.getElementById(l.htmlFor);i.value=value;i.dispatchEvent(new Event('input',{bubbles:true}));}
              set('名称','R6 来源长名称测试');set('下载地址','http://127.0.0.1:9/'+'a'.repeat(100));set('业务路径','/'+'long'.repeat(30));
              var file=d.querySelector('[data-sync-target-file]');file.selectedIndex=1;file.dispatchEvent(new Event('change',{bubbles:true}));})()""")
            geometry = b.eval("(function(){var d=document.querySelector('.sync-source-dialog'),a=d.querySelector('[data-sync-save]').getBoundingClientRect();return {width:d.getBoundingClientRect().width,save:a.bottom<=innerHeight,body:d.querySelector('.modal-body').clientHeight,overflow:document.documentElement.scrollWidth-document.documentElement.clientWidth}})()")
            if not geometry or geometry["width"] > 375 or not geometry["save"] or geometry["body"] <= 0 or geometry["overflow"] > 1:
                raise AssertionError("窄屏来源抽屉不可达：%s" % geometry)
            b.eval("document.querySelector('[data-sync-save]').click()")
            if not b.wait_for("!document.querySelector('.sync-source-dialog')", timeout=10):
                raise AssertionError("新建来源失败")
            status, listing = api(self.server, "GET", "/api/v3/workspaces/default/sync/sources")
            row = next((x for x in listing.get("items", []) if x.get("name") == "R6 来源长名称测试"), None)
            if status != 200 or not row or len(row.get("display_path") or "") != 121:
                raise AssertionError("未读取到已保存的长路径来源")
            selector = '[data-sync-edit="%s"]' % row["source_id"]
            if not b.wait_for("!!document.querySelector(%s)" % json.dumps(selector), timeout=10):
                raise AssertionError("新建来源未显示在列表中")
            b.eval("document.querySelector(%s).click()" % json.dumps(selector))
            b.eval("var d=document.querySelector('.sync-source-dialog');var l=Array.from(d.querySelectorAll('label')).find(x=>x.textContent==='名称');var f=document.getElementById(l.htmlFor);f.value='本地草稿';f.dispatchEvent(new Event('input',{bubbles:true}));")
            status, _ = api(self.server, "PATCH", "/api/v3/workspaces/default/sync/sources/" + row["source_id"], {"name": "并发修改"}, headers={"If-Match": str(row["revision"]), "X-Workspace-Id": "default"})
            if status != 200:
                raise AssertionError("模拟并发修改失败：%s" % status)
            b.eval("document.querySelector('[data-sync-save]').click()")
            if not b.wait_for("document.querySelector('[data-sync-reload]')?.style.display !== 'none'", timeout=10):
                raise AssertionError("并发修改后没有 409 恢复入口")
            if b.eval("document.querySelector('.sync-source-dialog input[autofocus]').value") != "本地草稿":
                raise AssertionError("冲突后草稿丢失")
            b.eval("document.querySelector('[data-sync-reload]').click()")
            if not b.wait_for("document.querySelectorAll('.modal').length === 2", timeout=10):
                details = b.eval("document.querySelector('.sync-source-dialog')?.innerText || '来源抽屉不存在'")
                raise AssertionError("未展示版本对比：%s" % details)
            if not b.eval("Array.from(document.querySelectorAll('.modal')).pop().innerText.includes('并发修改')"):
                raise AssertionError("对比未显示服务端修改")
            b.eval("Array.from(document.querySelectorAll('.modal')).pop().querySelector('.modal-footer .btn-danger').click()")
            if not b.wait_for("document.querySelector('[data-sync-hint]').textContent.includes('已核对')", timeout=10):
                raise AssertionError("未确认最新版本")
            b.eval("document.querySelector('[data-sync-save]').click()")
            if not b.wait_for("!document.querySelector('.sync-source-dialog')", timeout=10):
                raise AssertionError("确认对比后保存失败")
            status, listing = api(self.server, "GET", "/api/v3/workspaces/default/sync/sources")
            latest = next((x for x in listing.get("items", []) if x.get("source_id") == row["source_id"]), None)
            if status != 200 or not latest or latest.get("name") != "本地草稿" or latest.get("revision") != row["revision"] + 2:
                raise AssertionError("服务端没有保存已确认的草稿")
            notes.append("375px 长路径创建、409 草稿保留、对比确认与再次保存通过")
            ok = True
        except Exception as exc:
            notes.append(str(exc))
        finally:
            b.viewport(1366, 768)
        self.report.append({"id": "source-conflict", "title": "来源新建与并发冲突恢复", "level": "interactive", "ok": ok, "notes": notes, "screenshot": None})
        log("[%s] source-conflict %s" % ("PASS" if ok else "FAIL", notes[-1:]))

    def run_workspace_draft(self) -> None:
        """隔离工作区内验证计划草稿的取消切换与明确放弃。"""
        assert self.browser and self.server
        b = self.browser
        notes = []
        ok = False
        try:
            status, data = api(self.server, "POST", "/api/v3/workspaces", {"name": "R6 工作区验收"})
            if status != 201:
                raise AssertionError("创建隔离工作区失败：%s" % status)
            wid = data["workspace"]["id"]
            b.navigate(self.url(e2e_scenarios.updates_hash("plan", "default")))
            if not b.wait_for("!!document.querySelector('[data-plan-interval]')", timeout=20):
                raise AssertionError("计划未加载")
            b.eval("var input=document.querySelector('[data-plan-interval]'); input.value='9'; input.dispatchEvent(new Event('input',{bubbles:true})); document.querySelector('[data-sync-sheet-close]').click();")
            b.eval("window.__workspaceSwitch=MC.appInstance.switchWorkspace(%s); true" % json.dumps(wid))
            if not b.wait_for("!!document.querySelector('.modal-footer .btn-danger')", timeout=5):
                raise AssertionError("未保存计划切换时没有确认")
            b.eval("document.querySelector('.modal-footer .btn:not(.btn-danger)').click()")
            b.wait_for("!document.querySelector('.modal')", timeout=5)
            if not b.eval("MC.appInstance.wid === 'default' && MC.appInstance.instances[MC.appInstance.activeKey].isDirty()"):
                raise AssertionError("取消后工作区或草稿状态错误")
            b.eval("document.querySelector('[data-sync-plan]').click()")
            if not b.eval("document.querySelector('[data-plan-interval]').value === '9'"):
                raise AssertionError("重新打开计划后草稿丢失")
            b.eval("document.querySelector('[data-sync-sheet-close]').click(); window.__workspaceSwitch=MC.appInstance.switchWorkspace(%s); true" % json.dumps(wid))
            if not b.wait_for("!!document.querySelector('.modal-footer .btn-danger')", timeout=5):
                raise AssertionError("第二次切换缺少确认")
            b.eval("document.querySelector('.modal-footer .btn-danger').click()")
            if not b.wait_for("MC.appInstance.wid === %s" % json.dumps(wid), timeout=20):
                raise AssertionError("确认后未切换工作区")
            b.navigate(self.url(e2e_scenarios.updates_hash("plan", wid)))
            if not b.wait_for("!!document.querySelector('[data-plan-interval]')", timeout=20):
                raise AssertionError("新工作区计划未加载")
            if b.eval("document.querySelector('[data-plan-interval]').value") == "9":
                raise AssertionError("旧工作区草稿串入新工作区")
            notes.append("取消后保留草稿；确认后切换，旧草稿未串入新工作区")
            ok = True
        except Exception as exc:
            notes.append(str(exc))
        self.report.append({"id": "workspace-plan-draft", "title": "计划草稿与工作区切换", "level": "interactive", "ok": ok, "notes": notes, "screenshot": None})
        log("[%s] workspace-plan-draft %s" % ("PASS" if ok else "FAIL", notes[-1:]))

    def run_plan_save_preview(self) -> None:
        """真实浏览器保存、全量预览并验证并发冲突保留草稿。"""
        assert self.browser and self.server
        b = self.browser
        notes = []
        ok = False
        try:
            b.navigate("about:blank")
            b.navigate(self.url(e2e_scenarios.updates_hash("plan", "default")))
            if not b.wait_for("!!document.querySelector('[data-plan-interval]')", timeout=20):
                raise AssertionError("计划未加载")
            b.eval("var i=document.querySelector('[data-plan-interval]');i.value='0';i.dispatchEvent(new Event('input',{bubbles:true}));document.querySelector('[data-plan-save]').click()")
            accessible = b.eval("""(function(){var i=document.querySelector('[data-plan-interval]'),e=document.getElementById(i.getAttribute('aria-describedby'));return i.getAttribute('aria-label')==='默认间隔（小时）' && i.getAttribute('aria-invalid')==='true' && !!e && e.textContent.includes('1–2160') && document.activeElement===i})()""")
            if not accessible:
                raise AssertionError("无效间隔缺少错误关联或焦点")
            b.eval("var i=document.querySelector('[data-plan-interval]');i.value='11';i.dispatchEvent(new Event('input',{bubbles:true}));document.querySelector('[data-plan-save]').click()")
            if not b.wait_for("document.body.innerText.includes('更新计划已保存')", timeout=10):
                raise AssertionError("没有显示计划保存成功")
            status, policy = api(self.server, "GET", "/api/v3/workspaces/default/sync/policy")
            if status != 200 or policy.get("default_interval_hours") != 11:
                raise AssertionError("真实服务未保存计划：%s" % policy)
            b.eval("document.querySelector('[data-plan-preview]').click()")
            if not b.wait_for("!!document.querySelector('[data-plan-read-set]')", timeout=10):
                raise AssertionError("全量预览未显示")
            before = b.eval("document.querySelector('[data-plan-read-set]').getAttribute('data-plan-read-set')")
            if not before or not b.eval("document.querySelectorAll('[data-plan-preview-source]').length > 0"):
                raise AssertionError("预览缺少冻结指纹或启用的来源")
            b.eval("document.querySelector('[data-plan-failures]').click()")
            if b.eval("document.querySelector('[data-plan-read-set]').getAttribute('data-plan-read-set')") != before:
                raise AssertionError("显示筛选改变了冻结 read_set")
            status, fresh = api(self.server, "PUT", "/api/v3/workspaces/default/sync/policy", {"enabled": False, "default_interval_hours": 17}, headers={"If-Match": str(policy["revision"])})
            if status != 200:
                raise AssertionError("构造并发修改失败：%s %s" % (status, fresh))
            b.eval("var i=document.querySelector('[data-plan-interval]');i.value='13';i.dispatchEvent(new Event('input',{bubbles:true}));document.querySelector('[data-plan-save]').click()")
            if not b.wait_for("document.body.innerText.includes('计划已在其他客户端更新')", timeout=10):
                raise AssertionError("未显示计划并发冲突")
            if b.eval("document.querySelector('[data-plan-interval]').value") != "13":
                raise AssertionError("冲突后计划草稿丢失")
            status, actual = api(self.server, "GET", "/api/v3/workspaces/default/sync/policy")
            if status != 200 or actual.get("default_interval_hours") != 17:
                raise AssertionError("冲突覆盖了服务端计划")
            b.eval("document.querySelector('[data-plan-conflict-load]').click()")
            if not b.wait_for("!!document.querySelector('[data-plan-conflict-compare]')", timeout=10):
                raise AssertionError("未显示计划冲突对比")
            comparison = b.eval("document.querySelector('[data-plan-conflict-compare]').innerText")
            if "17" not in comparison or "13" not in comparison:
                raise AssertionError("冲突对比缺少服务端值或草稿值：%s" % comparison)
            b.eval("var i=document.querySelector('[data-plan-interval]');i.value='14';i.dispatchEvent(new Event('input',{bubbles:true}));document.querySelector('[data-plan-conflict-adopt]').click()")
            if not b.eval("document.body.innerText.includes('草稿已变化') && !!document.querySelector('[data-plan-conflict-load]')"):
                raise AssertionError("对比后草稿变化未要求重新确认")
            b.eval("var i=document.querySelector('[data-plan-interval]');i.value='13';i.dispatchEvent(new Event('input',{bubbles:true}));document.querySelector('[data-plan-conflict-load]').click()")
            if not b.wait_for("!!document.querySelector('[data-plan-conflict-adopt]')", timeout=10):
                raise AssertionError("修改草稿后无法重新对比")
            b.eval("document.querySelector('[data-plan-conflict-adopt]').click()")
            if b.eval("document.querySelector('[data-plan-interval]').value") != "13":
                raise AssertionError("采用最新修订后草稿丢失")
            status, newer = api(self.server, "PUT", "/api/v3/workspaces/default/sync/policy", {"enabled": False, "default_interval_hours": 19}, headers={"If-Match": str(fresh["revision"])})
            if status != 200:
                raise AssertionError("构造第二次并发修改失败：%s %s" % (status, newer))
            b.eval("document.querySelector('[data-plan-save]').click()")
            if not b.wait_for("!!document.querySelector('[data-plan-conflict-load]')", timeout=10):
                raise AssertionError("第二次并发修改未触发冲突")
            status, actual = api(self.server, "GET", "/api/v3/workspaces/default/sync/policy")
            if status != 200 or actual.get("default_interval_hours") != 19:
                raise AssertionError("第二次并发修改被覆盖")
            b.eval("document.querySelector('[data-plan-conflict-load]').click()")
            if not b.wait_for("!!document.querySelector('[data-plan-conflict-adopt]')", timeout=10):
                raise AssertionError("第二次冲突无法重新对比")
            b.eval("document.querySelector('[data-plan-conflict-adopt]').click()")
            b.eval("document.querySelector('[data-plan-save]').click()")
            if not b.wait_for("!document.querySelector('[data-plan-conflict-load]') && document.querySelector('[data-plan-save]') && !document.querySelector('[data-plan-save]').disabled", timeout=10):
                raise AssertionError("采用新修订后保存未完成")
            status, saved = api(self.server, "GET", "/api/v3/workspaces/default/sync/policy")
            if status != 200 or saved.get("default_interval_hours") != 13 or saved.get("revision") != newer.get("revision") + 1:
                raise AssertionError("恢复后没有以新修订保存草稿：%s" % saved)
            notes.append("计划保存与预览；草稿变化须重新对比，再次并发触发 409，确认后以最新修订保存")
            ok = True
        except Exception as exc:
            notes.append(str(exc))
        finally:
            b.navigate("about:blank")
        self.report.append({"id": "plan-save-preview", "title": "计划保存、预览与并发冲突", "level": "interactive", "ok": ok, "notes": notes, "screenshot": None})
        log("[%s] plan-save-preview %s" % ("PASS" if ok else "FAIL", notes[-1:]))

    def run_diagnostic_download(self) -> None:
        """对隔离来源启动任务，通过真实 UI 下载并校验诊断文件。"""
        assert self.browser and self.server
        b = self.browser
        notes = []
        ok = False
        try:
            status, data = api(self.server, "POST", "/api/v3/workspaces/default/sync/runs", {"scope": "workspace_all"})
            if status != 202 or not data.get("runs"):
                raise AssertionError("创建隔离运行失败：%s" % status)
            run_id = data["runs"][0]["run"]["run_id"]
            downloads = os.path.join(self.root, "downloads")
            os.makedirs(downloads, exist_ok=True)
            b.call("Page.setDownloadBehavior", behavior="allow", downloadPath=downloads)
            b.navigate(self.url(e2e_scenarios.updates_hash("runs", "default")))
            if not b.wait_for("!!document.querySelector('[data-sync-run]')", timeout=20):
                raise AssertionError("运行记录未显示")
            b.eval("document.querySelector('[data-sync-run]').click()")
            if not b.wait_for("Array.from(document.querySelectorAll('.modal-footer button')).some(b => b.textContent === '下载诊断')", timeout=5):
                raise AssertionError("诊断下载入口不存在")
            b.viewport(375, 812)
            geometry = b.eval("""(function(){var d=document.querySelector('.sync-run-dialog'),f=d.querySelector('.modal-footer').getBoundingClientRect(),body=d.querySelector('.modal-body').getBoundingClientRect();return {footerVisible:f.top>=0&&f.bottom<=innerHeight,bodyHeight:body.height,overflow:document.documentElement.scrollWidth-innerWidth}})()""")
            if not geometry or not geometry["footerVisible"] or geometry["bodyHeight"] < 100 or geometry["overflow"] > 1:
                raise AssertionError("窄屏运行详情操作不可达：%s" % geometry)
            notes.append("375px 运行详情页脚换行且可达：%s" % geometry)
            b.viewport(1366, 768)
            b.eval("Array.from(document.querySelectorAll('.modal-footer button')).find(b => b.textContent === '下载诊断').click()")
            deadline = time.time() + 15
            filename = "sync-run-%s-diagnostics.json" % run_id
            path = os.path.join(downloads, filename)
            while time.time() < deadline and not os.path.isfile(path):
                time.sleep(0.1)
            if not os.path.isfile(path):
                raise AssertionError("未收到诊断下载文件")
            with open(path, encoding="utf-8") as stream:
                payload = json.load(stream)
            if payload.get("run", {}).get("run_id") != run_id or not isinstance(payload.get("events"), list):
                raise AssertionError("诊断文件中的运行或事件不匹配")
            b.eval("Array.from(document.querySelectorAll('.modal-footer button')).find(b => b.textContent === '关闭').click()")
            if not b.wait_for("!document.querySelector('.modal')", timeout=5):
                raise AssertionError("下载后运行详情未关闭")
            notes.append("下载文件中的运行 ID 和事件列表与隔离任务一致")
            ok = True
        except Exception as exc:
            notes.append(str(exc))
        finally:
            b.viewport(1366, 768)
        self.report.append({"id": "diagnostic-download", "title": "运行诊断文件下载", "level": "interactive", "ok": ok, "notes": notes, "screenshot": None})
        log("[%s] diagnostic-download %s" % ("PASS" if ok else "FAIL", notes[-1:]))

    def run_viewports(self) -> None:
        assert self.browser
        for viewport in e2e_scenarios.VIEWPORTS:
            self.browser.viewport(viewport["width"], viewport["height"])
            self.browser.navigate(self.url(e2e_scenarios.updates_hash("sources", "default")))
            self.browser.wait_for("document.querySelector('[data-sync-source]')", timeout=20)
            overflow = self.browser.eval(
                "document.documentElement.scrollWidth - document.documentElement.clientWidth"
            )
            geometry = self.browser.eval("""(function () {
              var page = document.querySelector('.sync-page');
              var wrap = document.querySelector('.sync-table-wrap');
              var row = document.querySelector('[data-sync-source]:last-child');
              if (!page || !wrap || !row) return null;
              page.scrollTop = page.scrollHeight;
              wrap.scrollLeft = wrap.scrollWidth;
              row.scrollIntoView({block: 'nearest', inline: 'nearest'});
              var p = page.getBoundingClientRect(), w = wrap.getBoundingClientRect(), r = row.getBoundingClientRect();
              var footer = document.querySelector(".sync-list-footer").getBoundingClientRect();
              return {tableTop: w.top, footerVisible: footer.top >= p.top && footer.bottom <= p.bottom + 1, height: p.height, tableHeight: w.height, rowVisible: r.bottom <= p.bottom + 1 && r.top >= p.top - 1,
                horizontalEnd: wrap.scrollLeft + wrap.clientWidth >= wrap.scrollWidth - 1};
            })()""")
            shot = self.shoot("updates-sources-%s-admin" % viewport["id"].split("-")[-1])
            ok = (isinstance(overflow, (int, float)) and overflow <= 1 and geometry and
                  geometry["height"] > 0 and geometry["tableHeight"] >= 100 and geometry["footerVisible"] and
                  (viewport["width"] != 1366 or geometry["tableTop"] < 300) and geometry["rowVisible"] and geometry["horizontalEnd"])
            self.report.append(
                {
                    "id": "viewport-%s" % viewport["id"],
                    "title": "视口 %dx%d 无页面级横向溢出" % (viewport["width"], viewport["height"]),
                    "level": "interactive",
                    "ok": ok,
                    "notes": ["overflow=%s geometry=%s" % (overflow, geometry)],
                    "screenshot": shot,
                }
            )
            log("[%s] viewport-%s overflow=%s" % ("PASS" if ok else "FAIL", viewport["id"], overflow))
        self.browser.viewport(1366, 768)

    def run_panel_viewports(self) -> None:
        """在真实服务上检查来源、计划、连接及嵌套对话框的可用空间。"""
        assert self.browser
        b = self.browser
        cases = (
            ("source", "[data-sync-new]", "[data-sync-kind]", ".sync-source-dialog"),
            ("plan", "[data-sync-plan]", "[data-plan-save]", ".sync-settings-dialog"),
            ("connection", "[data-sync-connections]", "[data-conn-create]", ".sync-settings-dialog"),
        )
        for viewport in e2e_scenarios.VIEWPORTS:
            for name, trigger, ready, dialog in cases:
                notes = []
                ok = False
                shot = None
                try:
                    b.viewport(viewport["width"], viewport["height"])
                    # 同一 URL 的导航可能保留 SPA 弹层，先离开页面保证每项独立。
                    b.navigate("about:blank")
                    b.navigate(self.url(e2e_scenarios.updates_hash("sources", "default")))
                    if not b.wait_for("!!document.querySelector(%s)" % json.dumps(trigger), timeout=20):
                        raise AssertionError("打开入口未加载")
                    # 后台 Chrome 可能暂停动画帧；本场景只验证静态终态几何。
                    b.eval("var s=document.createElement('style');s.textContent='*,*::before,*::after{animation:none!important;transition:none!important}';document.head.appendChild(s)")
                    b.eval("document.querySelector(%s).click()" % json.dumps(trigger))
                    if not b.wait_for("!!document.querySelector(%s)" % json.dumps(ready), timeout=20):
                        raise AssertionError("面板内容未加载")
                    if not b.eval("!!document.querySelector(%s)" % json.dumps(dialog)):
                        raise AssertionError("目标面板未打开")
                    time.sleep(0.35)  # 等待共享弹层入场动画结束后测量最终位置
                    geometry = b.eval("""(function(sel){
                      var d=document.querySelector(sel), body=d.querySelector('.modal-body'), footer=d.querySelector('.modal-footer');
                      var targets=Array.from(body.querySelectorAll('input,select,textarea,button')).filter(function(e){return e.getClientRects().length && !e.disabled});
                      var last=targets[targets.length-1]; if(last) last.scrollIntoView({block:'nearest'});
                      var r=d.getBoundingClientRect(), br=body.getBoundingClientRect(), fr=footer.getBoundingClientRect(), lr=last && last.getBoundingClientRect();
                      return {width:r.width,height:r.height,bodyHeight:br.height,footerTop:fr.top,footerBottom:fr.bottom,footerVisible:fr.bottom<=innerHeight+1 && fr.top>=0,
                        lastReachable:!lr || (lr.top>=br.top-1 && lr.bottom<=br.bottom+1),overflow:document.documentElement.scrollWidth-document.documentElement.clientWidth};
                    })(%s)""" % json.dumps(dialog))
                    if not geometry or geometry["width"] > viewport["width"] + 1 or geometry["bodyHeight"] < 100 or not geometry["footerVisible"] or not geometry["lastReachable"] or geometry["overflow"] > 1:
                        raise AssertionError("面板内容或操作不可达：%s" % geometry)
                    notes.append(str(geometry))
                    if name == "connection":
                        b.eval("document.querySelector('[data-conn-create]').click()")
                        if not b.wait_for("!!document.querySelector('[data-conn-name]')", timeout=10):
                            raise AssertionError("嵌套连接对话框未打开")
                        time.sleep(0.35)
                        nested = b.eval("""(function(){var d=Array.from(document.querySelectorAll('.modal')).pop(),r=d.getBoundingClientRect(),f=d.querySelector('.modal-footer').getBoundingClientRect();return {width:r.width,height:r.height,footerVisible:f.bottom<=innerHeight+1,overflow:document.documentElement.scrollWidth-document.documentElement.clientWidth}})()""")
                        if nested["width"] > viewport["width"] + 1 or not nested["footerVisible"] or nested["overflow"] > 1:
                            raise AssertionError("嵌套对话框不可达：%s" % nested)
                        notes.append("nested %s" % nested)
                    shot = self.shoot("updates-panel-%s-%s" % (name, viewport["id"]))
                    ok = True
                except Exception as exc:
                    notes.append(str(exc))
                self.report.append({"id": "panel-%s-%s" % (name, viewport["id"]), "title": "多视口面板 %s" % name, "level": "interactive", "ok": ok, "notes": notes, "screenshot": shot})
                log("[%s] panel-%s-%s %s" % ("PASS" if ok else "FAIL", name, viewport["id"], notes[-1:]))
        # 最后一项保留了嵌套弹层，退出页面避免影响后续场景。
        b.navigate("about:blank")
        b.viewport(1366, 768)

    def run_touch_controls(self) -> None:
        """在移动触摸仿真中用 CDP touch 事件操作真实页面。"""
        assert self.browser
        b = self.browser
        notes = []
        ok = False
        try:
            b.call("Emulation.setDeviceMetricsOverride", width=375, height=812, deviceScaleFactor=1, mobile=True)
            b.call("Emulation.setTouchEmulationEnabled", enabled=True, maxTouchPoints=1)
            b.navigate("about:blank")
            b.navigate(self.url(e2e_scenarios.updates_hash("sources", "default")))
            if not b.wait_for("!!document.querySelector('[data-sync-new]') && !!document.querySelector('[data-sync-source]')", timeout=20):
                raise AssertionError("更新管理未加载")

            def tap(selector: str) -> None:
                point = b.eval("""(function(sel){var e=document.querySelector(sel);if(!e)return null;e.scrollIntoView({block:'nearest',inline:'nearest'});var r=e.getBoundingClientRect(),x=r.left+r.width/2,y=r.top+r.height/2;var hit=document.elementFromPoint(x,y);return {x:x,y:y,hit:!!hit&&(hit===e||e.contains(hit))}})(%s)""" % json.dumps(selector))
                if not point or not point["hit"]:
                    raise AssertionError("触摸目标被遮挡：%s %s" % (selector, point))
                b.call("Input.dispatchTouchEvent", type="touchStart", touchPoints=[{"x": point["x"], "y": point["y"]}])
                b.call("Input.dispatchTouchEvent", type="touchEnd", touchPoints=[])

            tap(".sync-source-more summary")
            if not b.wait_for("!!document.querySelector('.sync-source-more[open]')", timeout=10):
                raise AssertionError("触摸未展开更多操作")
            if not b.eval("document.querySelector('.sync-source-more[open] [data-sync-delete]').getClientRects().length > 0"):
                raise AssertionError("低频操作在触屏上不可见")
            tap(".sync-source-more[open] summary")
            tap("[data-sync-new]")
            if not b.wait_for("!!document.querySelector('.sync-source-dialog [data-sync-cancel]')", timeout=10):
                raise AssertionError("触摸未打开来源抽屉")
            tap(".sync-source-dialog [data-sync-cancel]")
            if not b.wait_for("!document.querySelector('.sync-source-dialog')", timeout=10):
                raise AssertionError("触摸未关闭来源抽屉")
            tap("[data-sync-tab=runs]")
            if not b.wait_for("document.querySelector('[data-sync-tab=runs]')?.getAttribute('aria-selected')==='true'", timeout=10):
                raise AssertionError("触摸未切换运行记录")
            tap("[data-sync-tab=candidates]")
            if not b.wait_for("document.querySelector('[data-sync-tab=candidates]')?.getAttribute('aria-selected')==='true'", timeout=10):
                raise AssertionError("触摸未切换候选")
            tap("[data-sync-plan]")
            if not b.wait_for("!!document.querySelector('.sync-settings-dialog [data-plan-preview]')", timeout=10):
                raise AssertionError("触摸未打开计划面板")
            tap(".sync-settings-dialog [data-sync-sheet-close]")
            if not b.wait_for("!document.querySelector('.sync-settings-dialog')", timeout=10):
                raise AssertionError("触摸未关闭计划面板")
            notes.append("375×812 模拟触摸：来源打开/关闭、内容标签切换、计划面板打开/关闭")
            ok = True
        except Exception as exc:
            notes.append(str(exc))
        finally:
            b.call("Emulation.setTouchEmulationEnabled", enabled=False)
            b.navigate("about:blank")
            b.viewport(1366, 768)
        self.report.append({"id": "touch-controls", "title": "移动触摸基本操作", "level": "interactive", "ok": ok, "notes": notes, "screenshot": None})
        log("[%s] touch-controls %s" % ("PASS" if ok else "FAIL", notes[-1:]))

    def run_shared_modal_regression(self) -> None:
        """在更新管理以外的真实调用方检查共享弹窗焦点与 Escape。"""
        assert self.browser
        b = self.browser
        notes = []
        ok = False
        try:
            b.navigate("about:blank")
            b.navigate(self.url(e2e_scenarios.route_path("settings", "profiles", "workspace=default")))
            selector = "Array.from(document.querySelectorAll('button')).find(b=>b.textContent.includes('新增机型')&&!b.disabled)"
            if not b.wait_for("!!(%s)" % selector, timeout=20):
                raise AssertionError("设置页新增机型入口未加载")
            b.eval("window.__modalTrigger=(%s);window.__modalTrigger.focus();window.__modalTrigger.click()" % selector)
            if not b.wait_for("!!document.querySelector('.modal input')", timeout=10):
                raise AssertionError("设置页弹窗未打开")
            if not b.wait_for("document.querySelector('.modal')?.contains(document.activeElement)", timeout=5):
                raise AssertionError("焦点未进入设置页弹窗")
            b.eval("var m=document.querySelector('.modal');var b=Array.from(m.querySelectorAll('button')).filter(x=>!x.disabled).pop();b.focus()")
            b.press("Tab")
            if not b.eval("document.querySelector('.modal')?.contains(document.activeElement)"):
                raise AssertionError("设置页弹窗 Tab 逃逸")
            b.press("Escape")
            if not b.wait_for("!document.querySelector('.modal')", timeout=5):
                raise AssertionError("设置页弹窗 Escape 未关闭")
            if not b.eval("document.activeElement===window.__modalTrigger"):
                raise AssertionError("设置页弹窗未恢复焦点")
            notes.append("设置/机型新增弹窗：进入焦点、Tab 循环、Escape 和焦点返回")
            b.navigate("about:blank")
            b.navigate(self.url(e2e_scenarios.route_path("tools", query="workspace=default")))
            selector = "Array.from(document.querySelectorAll('.tools-view button')).find(b=>b.textContent.trim()==='添加'&&!b.disabled)"
            if not b.wait_for("!!(%s)" % selector, timeout=20):
                raise AssertionError("外部工具添加入口未加载")
            b.eval("window.__modalTrigger=(%s);window.__modalTrigger.focus();window.__modalTrigger.click()" % selector)
            if not b.wait_for("document.querySelector('.modal')?.contains(document.activeElement)", timeout=10):
                raise AssertionError("工具弹窗未取得焦点")
            b.press("Escape")
            if not b.wait_for("!document.querySelector('.modal')", timeout=5) or not b.eval("document.activeElement===window.__modalTrigger"):
                raise AssertionError("工具弹窗关闭或焦点返回失败")
            notes.append("外部工具添加弹窗：进入焦点、Escape 和焦点返回")
            ok = True
        except Exception as exc:
            notes.append(str(exc))
        finally:
            b.navigate("about:blank")
        self.report.append({"id": "shared-modal-settings", "title": "共享弹窗其他调用方", "level": "interactive", "ok": ok, "notes": notes, "screenshot": None})
        log("[%s] shared-modal-settings %s" % ("PASS" if ok else "FAIL", notes[-1:]))

    def run_long_sources(self) -> None:
        """真实分页接口下检查 55 个长名称、长路径来源的可达性。"""
        assert self.browser and self.server
        from app.core import storage
        from app.services import file_versions
        b = self.browser
        notes = []
        ok = False
        try:
            status, data = api(self.server, "POST", "/api/v3/workspaces", {"name": "R6 长数据验收"})
            if status != 201:
                raise AssertionError("创建隔离工作区失败")
            wid = data["workspace"]["id"]
            with storage.use_workspace(wid), storage.use_profile("default"):
                storage.save_config_file("R6-long.xml", b"<R><x>1</x></R>")
                file_id = next(row["file_id"] for row in file_versions.list_resources("default") if row["name"] == "R6-long.xml")
            for i in range(55):
                status, result = api(self.server, "POST", "/api/v3/workspaces/%s/sync/sources" % wid, {
                    "profile_id": "default", "name": "R6-%03d-%s" % (i, "长名称" * 25),
                    "kind": "http_file", "resource_ref": "https://example.invalid/" + "x" * 100,
                    "display_path": "/" + "longpath" * 27, "schedule_mode": "manual", "enabled": True,
                    "target": {"kind": "file_archive", "file_id": file_id},
                }, headers={"X-Workspace-Id": wid})
                if status != 201:
                    raise AssertionError("创建长来源 %d 失败：%s %s" % (i, status, result))
            b.navigate("about:blank")
            b.navigate(self.url(e2e_scenarios.updates_hash("sources", wid)))
            if not b.wait_for("document.querySelectorAll('[data-sync-source]').length===50", timeout=20):
                raise AssertionError("第一页未按 50 条显示")
            compact = b.eval("""(function(){var r=document.querySelector('[data-sync-source]'),d=r.querySelector('details[data-sync-long-path]');return {height:r.getBoundingClientRect().height,hasDisclosure:!!d}})()""")
            if not compact["hasDisclosure"] or compact["height"] > 190:
                raise AssertionError("长路径未提供紧凑且可展开的显示：%s" % compact)
            b.eval("document.querySelector('details[data-sync-long-path] summary').click()")
            if not b.eval("document.querySelector('details[data-sync-long-path]').open && document.querySelector('details[data-sync-long-path]').innerText.includes('longpath'.repeat(27))"):
                raise AssertionError("长路径无法展开查看全文")
            b.eval("document.querySelector('details[data-sync-long-path] summary').click()")
            b.eval("document.querySelector('details[data-sync-long-name] summary').focus()")
            b.call("Input.dispatchKeyEvent", type="keyDown", key="Enter", code="Enter", windowsVirtualKeyCode=13, nativeVirtualKeyCode=13, text="\r")
            b.call("Input.dispatchKeyEvent", type="keyUp", key="Enter", code="Enter", windowsVirtualKeyCode=13, nativeVirtualKeyCode=13)
            if not b.wait_for("document.querySelector('details[data-sync-long-name]').open && document.querySelector('details[data-sync-long-name]').innerText.includes('长名称'.repeat(25))", timeout=5):
                raise AssertionError("键盘无法展开完整名称：%s" % b.eval("({active:document.activeElement.outerHTML,open:document.querySelector('details[data-sync-long-name]').open,text:document.querySelector('details[data-sync-long-name]').innerText})"))
            b.call("Input.dispatchKeyEvent", type="keyDown", key="Enter", code="Enter", windowsVirtualKeyCode=13, nativeVirtualKeyCode=13, text="\r")
            b.call("Input.dispatchKeyEvent", type="keyUp", key="Enter", code="Enter", windowsVirtualKeyCode=13, nativeVirtualKeyCode=13)
            b.eval("Array.from(document.querySelectorAll('.sync-content button')).find(x=>x.textContent==='加载更多').click()")
            if not b.wait_for("document.querySelectorAll('[data-sync-source]').length===55", timeout=20):
                raise AssertionError("第二页末项未加载")
            for viewport in (e2e_scenarios.VIEWPORTS[0], e2e_scenarios.VIEWPORTS[3]):
                b.viewport(viewport["width"], viewport["height"])
                geometry = b.eval("""(function(){var p=document.querySelector('.sync-page'),w=document.querySelector('.sync-table-wrap'),r=Array.from(document.querySelectorAll('[data-sync-source]')).pop();r.scrollIntoView({block:'nearest'});w.scrollLeft=w.scrollWidth;var a=p.getBoundingClientRect(),z=r.getBoundingClientRect();return {height:a.height,rowBottom:z.bottom,workspaceBottom:a.bottom,rowVisible:z.bottom<=a.bottom+1&&z.top>=a.top-1,horizontalEnd:w.scrollLeft+w.clientWidth>=w.scrollWidth-1,overflow:document.documentElement.scrollWidth-document.documentElement.clientWidth,rows:document.querySelectorAll('[data-sync-source]').length}})()""")
                if not geometry or geometry["height"] < 100 or not geometry["rowVisible"] or not geometry["horizontalEnd"] or geometry["overflow"] > 1:
                    raise AssertionError("%s 长列表不可达：%s" % (viewport["id"], geometry))
                notes.append("%s %s" % (viewport["id"], geometry))
            b.call("Emulation.setDeviceMetricsOverride", width=375, height=812, deviceScaleFactor=1, mobile=True)
            b.call("Emulation.setTouchEmulationEnabled", enabled=True, maxTouchPoints=1)
            b.eval("document.querySelector('.sync-table-wrap').scrollTop=0")
            b.call("Input.synthesizeScrollGesture", x=180, y=700, yDistance=-400, speed=800, gestureSourceType="touch")
            if not b.wait_for("document.querySelector('.sync-table-wrap').scrollTop>10 && document.querySelector('.sync-page').scrollTop===0", timeout=5):
                raise AssertionError("移动触摸滑动未滚动长列表")
            notes.append("移动触摸滑动仅滚动数据区，页面工具栏保持可见")
            point = b.eval("""(function(){var s=document.querySelector('details[data-sync-long-path] summary');s.scrollIntoView({block:'center',inline:'center'});var r=s.getBoundingClientRect(),x=r.left+r.width/2,y=r.top+r.height/2,h=document.elementFromPoint(x,y);return {x:x,y:y,hit:!!h&&(h===s||s.contains(h))}})()""")
            if not point or not point["hit"]:
                raise AssertionError("长路径展开入口被遮挡：%s" % point)
            b.call("Input.dispatchTouchEvent", type="touchStart", touchPoints=[{"x": point["x"], "y": point["y"]}])
            b.call("Input.dispatchTouchEvent", type="touchEnd", touchPoints=[])
            if not b.wait_for("document.querySelector('details[data-sync-long-path]').open", timeout=5):
                raise AssertionError("触摸不能展开完整路径")
            notes.append("触摸可展开完整路径")
            self.shoot("updates-long-phone-375")
            ok = True
        except Exception as exc:
            notes.append(str(exc))
        finally:
            b.call("Emulation.setTouchEmulationEnabled", enabled=False)
            b.navigate("about:blank")
            b.viewport(1366, 768)
        self.report.append({"id": "long-sources", "title": "长名称路径与分页末项", "level": "interactive", "ok": ok, "notes": notes, "screenshot": None})
        log("[%s] long-sources %s" % ("PASS" if ok else "FAIL", notes[-1:]))

    def run_source_error_recovery(self) -> None:
        """在真实页面注入一次读取失败，再恢复真实 API 验证重试及空搜索。"""
        assert self.browser
        b = self.browser
        notes = []
        ok = False
        try:
            b.viewport(375, 812)
            b.navigate("about:blank")
            b.navigate(self.url(e2e_scenarios.updates_hash("sources", "default")))
            if not b.wait_for("!!document.querySelector('[data-sync-source]')", timeout=20):
                raise AssertionError("来源列表未加载")
            b.eval("""window.__errorOriginal=MC.api.requestV3;MC.api.requestV3=function(m,p,o){if(m==='GET'&&p.endsWith('/sync/sources')&&o&&o.query&&o.query.status!=='all')return Promise.reject({code:'NETWORK',message:'模拟读取失败：'+ 'x'.repeat(600)});return window.__errorOriginal.apply(this,arguments)};document.querySelector('[data-sync-refresh]').click()""")
            if not b.wait_for("!!document.querySelector('[data-sync-retry]') && document.body.innerText.includes('模拟读取失败')", timeout=10):
                raise AssertionError("失败状态或重试入口缺失")
            if b.eval("!!document.querySelector('.sync-empty')"):
                raise AssertionError("读取失败误显示为空结果")
            geometry = b.eval("""(function(){var e=document.querySelector('[data-sync-error]'),r=e.getBoundingClientRect(),b=e.querySelector('button').getBoundingClientRect();return {overflow:document.documentElement.scrollWidth-document.documentElement.clientWidth,errorWidth:e.scrollWidth-e.clientWidth,retryVisible:b.right<=innerWidth+1&&b.left>=0,height:r.height}})()""")
            if geometry["overflow"] > 1 or geometry["errorWidth"] > 1 or not geometry["retryVisible"]:
                raise AssertionError("长错误信息或重试按钮不可达：%s" % geometry)
            b.eval("MC.api.requestV3=window.__errorOriginal;delete window.__errorOriginal;document.querySelector('[data-sync-retry]').click()")
            if not b.wait_for("!!document.querySelector('[data-sync-source]') && !document.querySelector('[data-sync-retry]')", timeout=10):
                raise AssertionError("恢复真实 API 后重试失败")
            b.eval("var i=document.querySelector('[aria-label=\"搜索更新源名称或路径\"]');i.value='R6-不存在的来源-9287';i.dispatchEvent(new KeyboardEvent('keydown',{key:'Enter',bubbles:true}))")
            if not b.wait_for("!!document.querySelector('.sync-empty')", timeout=10):
                raise AssertionError("无结果状态未显示")
            if not b.eval("document.querySelector('[aria-label=\"搜索更新源名称或路径\"]')?.value==='R6-不存在的来源-9287'"):
                raise AssertionError("空结果丢失搜索条件")
            b.navigate("about:blank")
            b.navigate(self.url(e2e_scenarios.updates_hash("connections", "default")))
            if not b.wait_for("!!document.querySelector('[data-conn-create]')", timeout=15):
                raise AssertionError("连接设置未加载")
            b.eval("""window.__errorOriginal=MC.api.requestV3;MC.api.requestV3=function(m,p,o){if(m==='POST'&&p.endsWith('/connections'))return Promise.reject({code:'NETWORK',message:'模拟连接失败：'+'x'.repeat(600)});return window.__errorOriginal.apply(this,arguments)};document.querySelector('[data-conn-create]').click();var d=Array.from(document.querySelectorAll('.modal')).pop();d.querySelector('[data-conn-name]').value='测试';d.querySelector('[data-conn-origin]').value='https://example.invalid';d.querySelector('[data-conn-create-submit]').click()""")
            if not b.wait_for("document.querySelector('[data-conn-error]')?.textContent.includes('模拟连接失败')", timeout=10):
                raise AssertionError("连接错误未显示")
            modal_error = b.eval("""(function(){var e=document.querySelector('[data-conn-error]'),d=e.closest('.modal');return {overflow:e.scrollWidth-e.clientWidth,modalOverflow:d.scrollWidth-d.clientWidth}})()""")
            if modal_error["overflow"] > 1 or modal_error["modalOverflow"] > 1:
                raise AssertionError("连接弹窗长错误溢出：%s" % modal_error)
            notes.append("长错误可换行；合成读取失败可重试并恢复真实 API；空搜索保留查询；连接弹窗长错误可读")
            ok = True
        except Exception as exc:
            notes.append(str(exc))
        finally:
            b.eval("if(window.__errorOriginal){MC.api.requestV3=window.__errorOriginal;delete window.__errorOriginal}")
            b.navigate("about:blank")
            b.viewport(1366, 768)
        self.report.append({"id": "source-error-recovery", "title": "来源错误重试和空结果", "level": "interactive", "ok": ok, "notes": notes, "screenshot": None})
        log("[%s] source-error-recovery %s" % ("PASS" if ok else "FAIL", notes[-1:]))

    def run_operator(self) -> None:
        """管理员无需 token，访客无法通过旧 token 提权。"""
        assert self.browser
        b = self.browser
        notes = []
        ok = False
        script = """(async function(){const r=await fetch('/api/v3/workspaces/default/connections',{method:'POST',headers:{'Content-Type':'application/json','X-Requested-With':'MCChecker','X-Operator-Token':'obsolete-token'},body:JSON.stringify({name:'权限回归连接',origin:'https://example.invalid'})});return r.status;})()"""
        try:
            self.role(MEMBER_IP)
            b.navigate(self.url(e2e_scenarios.updates_hash("connections", "default")))
            if not b.wait_for("document.querySelector('[data-connections-readonly]')", timeout=10):
                raise AssertionError("访客未显示只读状态")
            denied = b.eval(script)
            if denied != 403:
                raise AssertionError("访客带旧 token 被放行：%s" % denied)
            self.role(ADMIN_IP)
            b.navigate("about:blank")
            b.navigate(self.url(e2e_scenarios.updates_hash("connections", "default")))
            if not b.wait_for("document.querySelector('[data-conn-create]')", timeout=10):
                raise AssertionError("管理员缺少连接管理入口")
            allowed = b.eval(script.replace(",'X-Operator-Token':'obsolete-token'", ""))
            if allowed != 201:
                raise AssertionError("管理员无 token 创建失败：%s" % allowed)
            notes.append("访客（含旧 token）403；管理员不带 token 201")
            ok = True
        except Exception as exc:
            notes.append(str(exc))
        finally:
            self.role(ADMIN_IP)
        self.report.append({"id":"admin-visitor-auth","title":"管理员与访客两档授权","level":"interactive","ok":ok,"notes":notes,"screenshot":None})
        log("[%s] admin-visitor-auth %s" % ("PASS" if ok else "FAIL", notes))

    def run_connection_settings(self) -> None:
        """隔离工作区通过界面写连接和停用状态的保活配置，不连接 SDA。"""
        assert self.browser and self.server
        from app.services import sync_paths

        b = self.browser
        notes = []
        ok = False
        try:
            status, data = api(self.server, "POST", "/api/v3/workspaces", {"name": "R6 连接验收"})
            if status != 201:
                raise AssertionError("创建隔离工作区失败：%s" % status)
            wid = data["workspace"]["id"]
            base = "/api/v3/workspaces/%s/connections" % wid
            b.navigate("about:blank")
            b.navigate(self.url(e2e_scenarios.updates_hash("connections", wid)))
            if not b.wait_for("!!document.querySelector('[data-conn-create]')", timeout=20):
                raise AssertionError("连接设置未加载")
            b.eval("document.querySelector('[data-conn-create]').click()")
            b.eval("""(function(){var d=Array.from(document.querySelectorAll('.modal')).pop();d.querySelector('[data-conn-name]').value='R6 测试连接';d.querySelector('[data-conn-origin]').value='https://example.invalid';d.querySelector('[data-conn-create-submit]').click()})()""")
            if not b.wait_for("!document.querySelector('[data-conn-create-submit]') && !!document.querySelector('[data-conn-edit]')", timeout=15):
                raise AssertionError("界面创建连接未完成")
            status, listing = api(self.server, "GET", base)
            rows = listing.get("items", []) if status == 200 else []
            row = next((x for x in rows if x.get("name") == "R6 测试连接"), None)
            if not row:
                raise AssertionError("服务端没有创建连接")
            cid = row["connection_id"]
            b.eval("document.querySelector('[data-conn-edit]').click()")
            b.eval("""(function(){var d=Array.from(document.querySelectorAll('.modal')).pop();d.querySelector('[data-conn-name]').value='R6 已编辑连接';d.querySelector('[data-conn-save]').click()})()""")
            if not b.wait_for("!document.querySelector('[data-conn-save]') && document.body.innerText.includes('R6 已编辑连接')", timeout=15):
                raise AssertionError("连接编辑未完成")
            status, listing = api(self.server, "GET", base)
            row = next((x for x in listing.get("items", []) if x.get("connection_id") == cid), None)
            if status != 200 or not row or row.get("name") != "R6 已编辑连接" or row.get("policy_revision", 0) <= 0:
                raise AssertionError("连接编辑未写入服务端")
            b.eval("""(function(){var i=document.querySelector('[data-hb-interval]');i.value='15';i.dispatchEvent(new Event('input',{bubbles:true}));document.querySelector('[data-hb-save]').click()})()""")
            if not b.wait_for("document.querySelector('[data-hb-status]')?.textContent.includes('间隔 15 分钟')", timeout=15):
                raise AssertionError("保活设置未回显")
            status, hb = api(self.server, "GET", base + "/" + cid + "/heartbeat")
            if status != 200 or hb.get("interval_minutes") != 15 or hb.get("enabled") is not False:
                raise AssertionError("保活设置未正确保存")
            for name in ("connections.json", "heartbeat.json"):
                with open(sync_paths.sync_file(wid, name), "rb") as stream:
                    if LEGACY_TOKEN.encode() in stream.read():
                        raise AssertionError("运维 token 出现在配置文件中")
            if b.eval("!!document.querySelector('[data-hb-token], [data-conn-token], [data-cred-token]')"):
                raise AssertionError("管理员界面仍残留运维令牌输入")
            notes.append("浏览器创建和编辑连接、保存 15 分钟停用保活；服务端配置不含令牌")
            ok = True
        except Exception as exc:
            notes.append(str(exc))
        finally:
            b.navigate("about:blank")
        self.report.append({"id": "connection-settings", "title": "隔离连接及保活设置", "level": "interactive", "ok": ok, "notes": notes, "screenshot": None})
        log("[%s] connection-settings %s" % ("PASS" if ok else "FAIL", notes[-1:]))

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
            "汇总：interactive %d/%d 通过，reachable %d/%d 通过，api %d/%d 通过"
            % (
                summary["interactive_pass"],
                summary["interactive_pass"] + summary["interactive_fail"],
                summary["reachable_pass"],
                summary["reachable_pass"] + summary["reachable_fail"],
                summary["api_pass"],
                summary["api_pass"] + summary["api_fail"],
            )
        )
        return payload


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="M16-40 真实浏览器 E2E")
    parser.add_argument("--only", default="all", choices=("all", "updates", "legacy", "roles", "viewports", "panel-viewports", "touch-controls", "shared-modal", "long-sources", "source-error-recovery", "operator", "connection-settings", "workspace", "plan-save-preview", "download", "source-conflict", "revocation", "candidate-responsive", "candidate-real-apply", "candidate-conflict-contract", "source-scope", "config-prototype"))
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
        # CDP 截图后后台合成帧可能停滞；触摸手势必须先于截图场景执行。
        groups = [args.only] if args.only != "all" else ["touch-controls", "long-sources", "updates", "legacy", "roles", "viewports", "panel-viewports", "shared-modal", "source-error-recovery", "operator", "connection-settings", "download", "workspace", "plan-save-preview", "source-conflict", "revocation", "candidate-responsive", "candidate-real-apply", "candidate-conflict-contract", "source-scope", "config-prototype"]
        for group in groups:
            if group == "config-prototype":
                from e2e_config_prototype import run
                run(runner, api, ADMIN_IP, MEMBER_IP)
            elif group == "updates":
                runner.run_updates()
            elif group == "legacy":
                runner.run_legacy()
            elif group == "roles":
                runner.run_roles()
            elif group == "viewports":
                runner.run_viewports()
            elif group == "panel-viewports":
                runner.run_panel_viewports()
            elif group == "touch-controls":
                runner.run_touch_controls()
            elif group == "shared-modal":
                runner.run_shared_modal_regression()
            elif group == "long-sources":
                runner.run_long_sources()
            elif group == "source-error-recovery":
                runner.run_source_error_recovery()
            elif group == "operator":
                runner.run_operator()
            elif group == "connection-settings":
                runner.run_connection_settings()
            elif group == "workspace":
                runner.run_workspace_draft()
            elif group == "plan-save-preview":
                runner.run_plan_save_preview()
            elif group == "download":
                runner.run_diagnostic_download()
            elif group == "source-conflict":
                runner.run_source_conflict()
            elif group == "revocation":
                runner.run_permission_revocation()
            elif group == "candidate-responsive":
                runner.run_candidate_responsive()
            elif group == "candidate-real-apply":
                runner.run_candidate_real_apply()
            elif group == "candidate-conflict-contract":
                runner.run_candidate_conflict_contract()
            elif group == "source-scope":
                runner.run_source_scope()
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
