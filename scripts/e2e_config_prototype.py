"""Configuration prototype acceptance against a real isolated HTTP service."""

from __future__ import annotations

import json
import time


def run(runner, api, admin_ip, visitor_ip):
    b, server = runner.browser, runner.server
    notes, ok, shot = [], False, None
    stage = "setup"

    def request(method, path, body=None, **kwargs):
        status, result = api(server, method, path, body, **kwargs)
        assert 200 <= status < 300, (status, result)
        return result

    def wait(expression):
        assert b.wait_for(expression, timeout=15), (stage, expression, b.eval("document.body.innerText")[ -1600:])

    def click(text, scope="document"):
        script = "Array.from((%s).querySelectorAll('button')).find(x=>x.textContent.trim()===%s && !x.disabled)" % (scope, json.dumps(text))
        wait("!!(" + script + ")")
        b.eval("(" + script + ").click()")

    modal = "Array.from(document.querySelectorAll('.modal')).slice(-1)[0]"

    def label(text, value):
        b.eval("""(()=>{let m=%s,l=Array.from(m.querySelectorAll('label')).find(x=>x.textContent.replace('*','').trim()===%s);if(!l)throw Error('missing label');let c=document.getElementById(l.htmlFor);c.value=%s;c.dispatchEvent(new Event('input',{bubbles:true}));c.dispatchEvent(new Event('change',{bubbles:true}));})()""" % (modal, json.dumps(text), json.dumps(value)))

    def control(name, value, event="change"):
        b.eval("""(()=>{let c=document.querySelector('[aria-label='+JSON.stringify(%s)+']');if(!c)throw Error('missing control');c.value=%s;c.dispatchEvent(new Event(%s,{bubbles:true}));})()""" % (json.dumps(name), json.dumps(value), json.dumps(event)))

    def close_saved():
        wait("!document.querySelector('.modal')")

    def tab(name):
        click(name, "document.querySelector('.cv-tabs')")
        wait("!!document.querySelector('.cv-tab-content')")

    def edit(value):
        wait("!!document.querySelector('[aria-label=\"送料速度的配置值\"]')")
        control("送料速度的值状态", "present")
        control("送料速度的配置值", value, "input")

    try:
        runner.role(admin_ip)
        wid = request("POST", "/api/v3/workspaces", {"name": "配置原型验收"})["workspace"]["id"]
        root = "/api/v3/workspaces/%s/profiles/default" % wid
        route = "#/p/default/configv3/configs?workspace=" + wid
        b.navigate(runner.url(route))
        stage = "catalog/create"
        click("新建固定配置")
        label("配置标识", "UI-PARITY")
        label("配置名称", "配置原型验收")
        click("创建", modal)
        close_saved()
        wait("!!document.querySelector('.cv-tabs')")
        cid = request("GET", root + "/configs")["items"][0]["config_id"]
        base = root + "/configs/" + cid
        click("新建软件版本")
        label("软件版本号", "690")
        click("创建", modal)
        close_saved()
        wait("document.querySelector('.cv-graph-scroll')?.innerText.includes('690')")
        rid = request("GET", base)["config"]["releases"][0]["release_id"]
        tab("参数总览")
        click("手动新增")
        label("参数名称", "送料速度")
        label("来源层级", "设备控制/运动参数")
        label("单位", "mm/s")
        click("保存", modal)
        close_saved()
        wait("document.querySelector('.cv-param-data')?.innerText.includes('送料速度')")
        pid = request("GET", base + "/parameters")["items"][0]["parameter_id"]
        assert pid in request("GET", base + "/drafts/d0-" + rid)["values"]
        notes.append("UI 创建目录总表条目、软件版本、稳定参数，并真实纳入准备版")

        stage = "file binding picker"
        request("PUT", "/api/v1/p/default/files/Parity.json", {"Speed": 1, "Other": 2, "Keep": "unchanged", "NullValue": None}, headers={"X-Workspace-Id": wid})
        file = request("GET", root + "/file-selector?q=Parity.json")["items"][0]
        click("本版未绑定")
        wait("!document.querySelector('[aria-label=\"绑定来源文件\"]').disabled")
        control("绑定来源文件", file["version_id"])
        click("从文件选择字段", modal)
        wait("document.querySelector('.cv-field-picker')?.innerText.includes('Speed')")
        b.eval("Array.from(document.querySelectorAll('.cv-field-picker tr')).find(x=>x.innerText.includes('/Speed')).querySelector('button').click()")
        click("确认绑定", modal)
        close_saved()
        binding = request("GET", base + "/releases/" + rid + "/bindings")["binding_set"]
        assert next(iter(binding["bindings"].values()))["locator"] == "/Speed"
        notes.append("真实配置文件 → 字段选择 → 版本绑定，保留 file/version 身份")

        stage = "D0 save/CAS/freeze"
        tab("参数工作台")
        edit("7")
        # A second client changes the same draft. Failed saves must preserve UI input.
        draft = request("GET", base + "/drafts/d0-" + rid)
        request("PATCH", base + "/drafts/d0-" + rid + "/values", {"request_id": "other-client", "expected_revision": draft["revision"], "items": [{"parameter_id": pid, "membership": "included", "value_state": "present", "value": "5"}]})
        click("保存值")
        wait("document.body.innerText.includes('保存失败，修改已保留')")
        assert b.eval("document.querySelector('[aria-label=\"送料速度的配置值\"]').value") == "7"
        click("核对最新草稿")
        click("保留修改，采用此修订", modal)
        close_saved()
        click("保存值")
        wait("document.body.innerText.includes('草稿已保存')")
        wait("!document.querySelector('[aria-label=\"送料速度的配置值\"]').disabled")
        assert request("GET", base + "/drafts/d0-" + rid)["values"][pid]["value"] == "7"
        click("确认版本默认")
        click("确认并固化", modal)
        close_saved()
        wait("document.querySelector('.cv-tab-content')?.innerText.includes('历史值只读')")
        assert request("GET", base + "/drafts/d0-" + rid)["frozen"]
        notes.append("409 保留输入 → 比较最新草稿 → 重试保存 → D0 固化并只读")

        stage = "baseline/machine publication"
        tab("版本演进")
        click("建立版本基线创建变更草稿")
        wait("!!document.querySelector('[aria-label=\"送料速度的配置值\"]')")
        edit("9")
        click("保存值")
        wait("document.body.innerText.includes('草稿已保存') && !document.querySelector('[aria-label=\"送料速度的配置值\"]').disabled")
        time.sleep(.3)
        click("发布修订")
        click("发布", modal)
        close_saved()
        wait("document.querySelector('.cv-tab-content')?.innerText.includes('历史值只读')")
        baseline = request("GET", base + "/branches/main")["head_id"]
        assert request("GET", base + "/revisions/" + baseline)["snapshot"]["values"][pid]["value"] == "9"
        tab("版本演进")
        click("新建机台分支")
        label("机台编号", "MC-001")
        control("机台采用基准", baseline)
        click("创建", modal)
        close_saved()
        edit("11")
        control("送料速度的继承方式", "pin")
        click("保存值")
        time.sleep(.5)
        click("发布修订")
        click("发布", modal)
        close_saved()
        wait("document.querySelector('.cv-tab-content')?.innerText.includes('历史值只读')")
        machine = request("GET", base + "/machines")["items"][0]
        mr = machine["head_id"]
        snapshot = request("GET", base + "/revisions/" + mr)["snapshot"]
        assert snapshot["values"][pid]["value"] == "11" and snapshot["values"][pid]["intent"] == "pin"
        assert request("GET", base + "/branches/main")["head_id"] == baseline
        notes.append("创建基线草稿/保存/发布 B1；选择真实 B1 建机台草稿，固定值发布 r1；主线未被改写")

        stage = "export mapping/artifact"
        tab("模板导出")
        wait("document.querySelector('[aria-label=\"模板文件\"]')?.options.length>1")
        control("模板文件", file["version_id"])
        click("预览导出")
        wait("!!document.querySelector('[data-export-commit]')")
        control("送料速度的模板落点", "/Other", "input")
        assert b.eval("document.querySelector('[data-export-commit]').disabled")
        click("预览导出")
        wait("document.querySelector('.cv-export-code')?.innerText.includes('\"Other\": 11')")
        click("生成不可变文件")
        wait("!!document.querySelector('[data-export-download]')")
        click(b.eval("document.querySelector('[data-export-download]').textContent.trim()"))
        notes.append("修改映射立即使旧预览失效；重新预览的完整输出保留未匹配字段并生成可下载文件")
        shot = runner.shoot("config-prototype-export")

        stage = "history restore"
        tab("配置历史")
        wait("document.querySelector('.cv-history-events')?.innerText.includes('发布机台修订')")
        b.eval("Array.from(document.querySelectorAll('.cv-history-event')).find(x=>x.innerText.includes('发布机台修订')).querySelectorAll('button')[1].click()")
        wait("!!document.querySelector('[aria-label=\"送料速度的配置值\"]')")
        restored = request("GET", base + "/machines")["items"][0]
        assert restored["head_id"] == mr and restored["draft"]["status"] == "draft"
        assert restored["draft"]["values"][pid]["value"] == "11"
        notes.append("事件时间线恢复产生新草稿，已发布 r1 与基线 head 不变")

        stage = "workspace drafts"
        other = request("POST", "/api/v3/workspaces", {"name": "配置隔离B"})["workspace"]["id"]
        edit("13")
        b.eval("MC.appInstance.switchWorkspace(%s);true" % json.dumps(other))
        wait("!!document.querySelector('.modal-footer .btn-danger')")
        b.eval("document.querySelector('.modal-footer .btn:not(.btn-danger)').click()")
        close_saved()
        assert b.eval("document.querySelector('[aria-label=\"送料速度的配置值\"]').value") == "13"
        b.eval("MC.appInstance.switchWorkspace(%s);true" % json.dumps(other))
        wait("!!document.querySelector('.modal-footer .btn-danger')")
        b.eval("document.querySelector('.modal-footer .btn-danger').click()")
        wait("MC.appInstance.wid === %s" % json.dumps(other))
        b.navigate(runner.url(route + "&config=" + cid))
        wait("!!document.querySelector('.cv-tabs')")
        tab("版本演进")
        click("新建软件版本")
        label("软件版本号", "unsaved")
        b.eval("MC.appInstance.switchWorkspace(%s);true" % json.dumps(other))
        wait("document.querySelectorAll('.modal').length === 2")
        b.eval("Array.from(document.querySelectorAll('.modal')).slice(-1)[0].querySelector('.modal-footer .btn:not(.btn-danger)').click()")
        wait("document.querySelectorAll('.modal').length === 1")
        assert b.eval("document.querySelector('.modal input').value") == "unsaved"
        b.eval("MC.appInstance.switchWorkspace(%s);true" % json.dumps(other))
        wait("document.querySelectorAll('.modal').length === 2")
        b.eval("Array.from(document.querySelectorAll('.modal')).slice(-1)[0].querySelector('.btn-danger').click()")
        wait("MC.appInstance.wid === %s && !document.querySelector('.modal')" % json.dumps(other))
        notes.append("未保存值/新增表单切换工作区均先确认；取消保留，放弃后关闭旧弹窗，不向 B 写入")

        stage = "responsive/long content/visitor"
        # Seed extra identities through HTTP for bounded matrix DOM and final-page reachability.
        for i in range(104):
            request("POST", base + "/parameters", {"name": "长参数_%03d_" % i + "名称与说明" * 6, "directory": ["长目录" * 8, "子目录"], "type": "string"})
        # Remount to read the new catalog metadata, using the real route.
        b.navigate("about:blank")
        b.navigate(runner.url(route + "&config=" + cid))
        wait("!!document.querySelector('.cv-tabs')")
        for width, height in [(1366, 768), (1920, 1080), (768, 1024), (375, 812)]:
            b.viewport(width, height)
            for name in ["版本演进", "参数工作台", "参数总览", "模板导出", "配置历史"]:
                tab(name)
                wait("!document.querySelector('.cv-tab-content .state.is-loading')")
                metrics = b.eval("""(()=>{let e=document.querySelector('.cv-tab-content'),t=e.querySelector('.table-wrap');return {page:document.documentElement.scrollWidth,viewport:innerWidth,height:e.clientHeight,footerBottom:e.querySelector(".cv-param-footer")?.getBoundingClientRect().bottom,table:t?{client:t.clientHeight,scroll:t.scrollHeight}:null}})()""")
                assert metrics["page"] <= width + 2 and metrics["height"] > 100, metrics
                if width > 760 and name in ("参数工作台", "参数总览"):
                    assert metrics["footerBottom"] <= height + 2, metrics
                if name == "模板导出":
                    layout = b.eval("""(()=>{let s=document.querySelector('.cv-export-source .cv-detail-body'),a=s.querySelector('[aria-label="导出修订"]').getBoundingClientRect(),f=s.querySelector('[aria-label="模板文件"]').getBoundingClientRect(),p=document.querySelector('.cv-export-preview .cv-detail-body'),t=p.querySelector('.table-wrap');return {stacked:f.top>=a.bottom,fieldWidth:f.width,sourceWidth:s.clientWidth,mappingWidth:t?.clientWidth,previewWidth:p.clientWidth}})()""")
                    assert layout["stacked"] and layout["fieldWidth"] >= layout["sourceWidth"] - 34, layout
                    assert layout["mappingWidth"] >= layout["previewWidth"] - 34, layout
                    notes.append("export layout " + json.dumps(layout))
                notes.append("%s %s %s" % (width, name, json.dumps(metrics)))
                if name == "参数总览":
                    click("下一页")
                    wait("document.querySelector('.cv-param-footer')?.innerText.includes('第 2 / 2 页') && document.querySelectorAll('.cv-param-data tbody tr').length===5")
                    click("上一页")
                    wait("document.querySelectorAll('.cv-param-data tbody tr').length===100")
                if name in ("版本演进", "参数总览"):
                    runner.shoot("config-%s-%s" % (width, "graph" if name == "版本演进" else "matrix"))
        stage = "complete dialog layout inventory"
        prepared = request("POST", base + "/releases", {"version": "UI-Layout-Draft"})["release"]["release_id"]
        comparison = request("POST", base + "/releases", {"version": "LongVersion-" + "V" * 50})["release"]["release_id"]
        request("PATCH", base + "/drafts/d0-" + comparison + "/values", {"request_id": "layout-target", "expected_revision": 0, "items": [{"parameter_id": pid, "membership": "included", "value_state": "present", "value": "13"}]})
        request("POST", base + "/drafts/d0-" + comparison + "/freeze", {"request_id": "layout-target-freeze", "expected_revision": 1})
        request("POST", base + "/machine-branches", {"machine_no": "MC-" + "DEVICE" * 10, "release_id": rid, "base_kind": "default", "base_revision_id": "d0-" + rid})
        from e2e_config_layout import audit
        audit(runner, route, cid, "d0-" + rid, file, prepared, notes, "d0-" + comparison)
        runner.role(visitor_ip)
        b.navigate("about:blank")
        b.navigate(runner.url(route + "&config=" + cid + "&tab=workbench"))
        wait("!!document.querySelector('.cv-tabs')")
        assert not b.eval("document.querySelector('[aria-label=\"送料速度的配置值\"]') && !document.querySelector('[aria-label=\"送料速度的配置值\"]').disabled")
        assert api(server, "POST", base + "/parameters", {"name": "Denied"}, ip=visitor_ip)[0] == 403
        assert api(server, "GET", base.replace(wid, other))[0] == 404
        notes.append("四视口五页面无整页横向溢出；105 参数分页末项可达；访客403/跨工作区404")
        ok = True
    except Exception as exc:
        notes.append("%s: %s" % (stage, exc))
        shot = runner.shoot("config-prototype-failure")
    finally:
        runner.role(admin_ip)
        b.viewport(1366, 768)
    runner.report.append({"id": "config-prototype", "title": "配置原型真实功能与四视口", "level": "interactive", "ok": ok, "notes": notes, "screenshot": shot})
    print("[%s] config-prototype %s" % ("PASS" if ok else "FAIL", notes[-1:]), flush=True)
