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
        wait("!!document.querySelector('.cv-tabs') && !document.querySelector('.config-root>.state.is-loading')")
        if name in ("版本演进", "配置历史"):
            if b.eval("!!document.querySelector('.cv32-object-heading')"):
                b.eval("document.querySelector('[aria-label=\"当前配置的更多操作\"]').click()")
                click("版本关系", "document.querySelector('[role=menu]')")
            if not b.eval("!!document.querySelector('.cv32-evo-scroll')"):
                click("版本演进", "document.querySelector('.cv-tabs')")
            wait("!!document.querySelector('.cv32-evo-scroll')")
            if name == "配置历史":
                click("操作记录", "document.querySelector('.cv-tabs')")
        elif name in ("参数工作台", "参数总览", "模板导出"):
            if not b.eval("!!document.querySelector('.cv32-object-heading')"):
                wait("!!document.querySelector('.cv-release-band button')")
                b.eval("(document.querySelector('.cv-graph-node[aria-pressed=true]')||document.querySelector('.cv-release-band button')).click()")
                wait("!!document.querySelector('.cv32-object-heading') && !document.querySelector('.state.is-loading')")
            if name == "参数工作台":
                b.eval("Array.from(document.querySelectorAll('.cv-tabs button')).find(x=>['参数','标准参数'].includes(x.textContent.trim())).click()")
            elif name == "参数总览":
                b.eval("document.querySelector('[aria-label=\"当前配置的更多操作\"]').click()")
                click("管理参数", "document.querySelector('[role=menu]')")
            else:
                click("导出文件", "document.querySelector('.cv32-object-heading')")
                if b.eval("!!document.querySelector('.modal')"):
                    click("导出上次保存的配置", "document.querySelector('.modal')")
        wait("!!document.querySelector('.cv-tab-content')")

    def edit(value):
        wait("!!document.querySelector('[aria-label=\"送料速度的配置值\"]')")
        control("送料速度的值状态", "present")
        control("送料速度的配置值", value, "input")

    try:
        runner.role(admin_ip)
        stage = "V3.2 file home and explicit ownership"
        files_wid = request("POST", "/api/v3/workspaces", {"name": "V3.2 文件首页验收"})["workspace"]["id"]
        files_root = "/api/v3/workspaces/%s/profiles/default" % files_wid
        request("PUT", "/api/v1/p/default/files/Managed.json", {"Speed": 1}, headers={"X-Workspace-Id": files_wid})
        files_route = "#/p/default/configv3?workspace=" + files_wid
        b.navigate(runner.url(files_route))
        wait("document.querySelector('.config-resource-surface')?.innerText.includes('Managed.json')")
        runner.shoot("v32-file-home")
        click("Managed.json")
        wait("!!document.querySelector('.modal')")
        label("配置名称", "文件管理验收")
        click("进入配置管理", modal)
        close_saved()
        wait("document.querySelector('.config-root')?.innerText.includes('添加第一个软件版本')")
        owned = request("GET", files_root + "/config-files")["items"][0]
        managed_id = owned["configuration"]["config_id"]
        assert owned["configuration"]["file_id"] == owned["file_id"]
        b.navigate(runner.url(files_route))
        click("Managed.json")
        wait("document.querySelector('.config-root')?.innerText.includes('添加第一个软件版本')")
        assert len(request("GET", files_root + "/configs")["items"]) == 1
        assert managed_id in b.eval("location.hash")
        b.eval("document.querySelector('.app-sidebar [aria-label=\"打开 Managed.json\"]').click()")
        wait("location.hash.includes('config=' + %s) && !!document.querySelector('.config-root:not(.hidden)')" % json.dumps(managed_id))
        stage = "V3.2 file-scoped update management"
        sync_root = "/api/v3/workspaces/%s/sync" % files_wid
        request("POST", sync_root + "/sources", {"name": "Owned update", "profile_id": "default", "kind": "http_file", "resource_ref": "https://example.invalid/owned", "target": {"kind": "file_archive", "file_id": owned["file_id"]}})
        request("POST", sync_root + "/sources", {"name": "Other file update", "profile_id": "default", "kind": "http_file", "resource_ref": "https://example.invalid/other", "target": {"kind": "file_archive", "file_id": "file-other"}})
        b.eval("""(()=>{
          const original=window.fetch; window.__sourceLayout=[];
          window.__captureSourceLayout=()=>{const root=document.querySelector('.sync-root:not(.hidden).is-file-config');if(!root)return;
            const heading=root.querySelector('.sync-file-heading'),work=root.querySelector('.sync-workbench'),table=root.querySelector('.sync-source-table');
            window.__sourceLayout.push({heading:heading?.getBoundingClientRect().height,top:work?.getBoundingClientRect().top,
              tableTop:table?.getBoundingClientRect().top,columns:table?.querySelectorAll('thead th').length,
              loading:!!root.querySelector('.sync-loading'),oldFilters:!!root.querySelector('.sync-filters')});};
          window.__restoreLayoutFetch=()=>{window.fetch=original;clearInterval(window.__sourceLayoutTimer)};
          window.fetch=function(url,options){const response=original.call(window,url,options),path=String(url).split('?')[0];
            return /\\/configs\\/[^/]+$|\\/sync\\/sources$/.test(path)?Promise.all([response,new Promise(r=>setTimeout(r,200))])
              .then(v=>{window.__captureSourceLayout();return v[0]}):response};
          window.__sourceLayoutTimer=setInterval(window.__captureSourceLayout,16);
        })()""")
        b.navigate(runner.url("#/p/default/updates/sources?workspace=" + files_wid + "&config=" + managed_id))
        wait("document.querySelector('.sync-content')?.innerText.includes('Owned update')")
        samples = b.eval("window.__captureSourceLayout();window.__restoreLayoutFetch();window.__sourceLayout")
        assert len(samples) >= 2 and any(sample["loading"] for sample in samples) and any(not sample["loading"] for sample in samples), samples
        assert all(not sample["oldFilters"] and sample["columns"] == 5 for sample in samples), samples
        assert all(abs(sample[key] - samples[0][key]) < 1 for sample in samples for key in ("heading", "top", "tableTop")), samples
        notes.append("文件更新慢响应200ms：%s次布局观测包含加载/完成阶段，标题、工作面、表头稳定；无旧筛选栏" % len(samples))
        runner.shoot("v32-file-updates")
        assert not b.eval("document.querySelector('.sync-content').innerText.includes('Other file update')")
        click("立即更新")
        wait("document.querySelector('.modal')?.innerText.includes('Owned update')")
        assert not b.eval("document.querySelector('.modal').innerText.includes('Other file update')")
        click("关闭", modal)
        click("运行记录", "document.querySelector('.sync-subnav')")
        assert "config=" + managed_id in b.eval("location.hash")
        click("待处理变化", "document.querySelector('.sync-subnav')")
        assert "config=" + managed_id in b.eval("location.hash")
        notes.append("文件内更新：真实归档来源按唯一文件身份筛选；当前文件预览排除其他文件，切换运行/候选保留文件上下文；没有执行外部下载")
        request("PUT", "/api/v1/p/default/files/ReadOnly.json", {"Speed": 2}, headers={"X-Workspace-Id": files_wid})
        runner.role(visitor_ip)
        b.navigate("about:blank")
        b.navigate(runner.url(files_route))
        click("ReadOnly.json")
        wait("document.querySelector('.modal')?.innerText.includes('请联系管理员')")
        assert not b.eval("Array.from(document.querySelector('.modal').querySelectorAll('button')).some(x=>x.textContent.includes('进入配置管理'))")
        notes.append("V3.2 默认文件首页：真实文件建立配置、持久归属、再次进入复用同一配置，访客未配置文件只读")
        runner.role(admin_ip)
        wid = request("POST", "/api/v3/workspaces", {"name": "配置原型验收"})["workspace"]["id"]
        root = "/api/v3/workspaces/%s/profiles/default" % wid
        route = "#/p/default/configv3/configs?workspace=" + wid
        b.navigate(runner.url(route))
        stage = "catalog/create"
        click("新建配置分组")
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
        wait("document.querySelector('.cv32-object-heading')?.innerText.includes('软件 690')")
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
        request("PUT", base + "/file", {"file_id": file["file_id"]})
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
        click("暂存草稿")
        wait("document.body.innerText.includes('保存未完成，已暂存的内容和本次输入均已保留')")
        assert b.eval("document.querySelector('[aria-label=\"送料速度的配置值\"]').value") == "7"
        click("核对最新草稿")
        wait("!!document.querySelector('[aria-label$=的冲突处理]')")
        b.eval("(()=>{let s=document.querySelector('[aria-label$=的冲突处理]');s.value='mine';s.dispatchEvent(new Event('change',{bubbles:true}));})()")
        click("继续编辑", modal)
        close_saved()
        click("暂存草稿")
        wait("document.body.innerText.includes('草稿已暂存')")
        wait("!document.querySelector('[aria-label=\"送料速度的配置值\"]').disabled")
        assert request("GET", base + "/drafts/d0-" + rid)["values"][pid]["value"] == "7"
        click("核对并保存")
        click("保存配置", modal)
        close_saved()
        wait("document.querySelector('.cv32-object-heading')?.innerText.includes('已保存') && !document.querySelector('[aria-label=\"送料速度的配置值\"]')")
        assert request("GET", base + "/drafts/d0-" + rid)["frozen"]
        assert request("GET", base)["config"]["releases"][0]["initial_revision_id"] is None
        notes.append("409 保留输入 → 比较最新草稿 → 重试保存 → D0 固化并只读")

        stage = "baseline/machine publication"
        tab("版本演进")
        click("层级列表")
        click("编辑标准")
        wait("!!document.querySelector('[aria-label=\"送料速度的配置值\"]')")
        edit("9")
        click("暂存草稿")
        wait("document.body.innerText.includes('草稿已暂存') && !document.querySelector('[aria-label=\"送料速度的配置值\"]').disabled")
        time.sleep(.3)
        click("核对并保存")
        click("保存配置", modal)
        close_saved()
        wait("document.querySelector('.cv32-object-heading')?.innerText.includes('已保存') && !document.querySelector('[aria-label=\"送料速度的配置值\"]')")
        baseline = request("GET", base + "/branches/main")["head_id"]
        assert request("GET", base + "/revisions/" + baseline)["snapshot"]["values"][pid]["value"] == "9"
        stage = "V3.2 unified value import"
        click("导入", "document.querySelector('.cv-param-data')")
        wait("!!document.querySelector('[aria-label=导入文件]')")
        click("下一步", modal)
        wait("!!document.querySelector('[data-import-decision]')")
        assert b.eval("Array.from(document.querySelectorAll('.modal button')).find(x=>x.textContent==='导入到草稿').disabled")
        b.eval("(()=>{for(let i=0;i<20;i++){let s=Array.from(document.querySelectorAll('[data-import-decision]')).find(x=>!x.value);if(!s)break;s.value='skip';s.dispatchEvent(new Event('change',{bubbles:true}));}})()")
        click("导入到草稿", modal)
        close_saved()
        wait("document.querySelector('[aria-label=\\\"送料速度的配置值\\\"]')?.value==='1'")
        imported_release = next(r for r in request("GET", base)["config"]["releases"] if r["release_id"] == rid)
        imported_draft = request("GET", base + "/baseline-drafts/" + imported_release["baseline_draft"]["draft_id"])
        assert imported_draft["values"][pid]["value"] == 1 and imported_draft["source_revision_id"] == baseline
        assert request("GET", base + "/branches/main")["head_id"] == baseline
        assert request("GET", base + "/revisions/" + baseline)["snapshot"]["values"][pid]["value"] == "9"
        click("取消编辑")
        wait("!!document.querySelector('.cv32-object-heading') && !!document.querySelector('.cv-param-data') && !document.querySelector('[aria-label=\\\"送料速度的配置值\\\"]')")
        notes.append("V3.2 真实文件值导入：未知字段必须逐项明确忽略；写入版本草稿、沿用本版本标准，已保存记录与 head 未改变")
        stage = "machine publication"
        tab("版本演进")
        click("新建机台")
        label("机台编号", "MC-001")
        control("机台的软件版本", baseline)
        click("创建", modal)
        close_saved()
        edit("11")
        control("送料速度的取值方式", "pin")
        click("暂存草稿")
        time.sleep(.5)
        click("核对并保存")
        click("保存配置", modal)
        close_saved()
        wait("document.querySelector('.cv32-object-heading')?.innerText.includes('已保存') && !document.querySelector('[aria-label=\"送料速度的配置值\"]')")
        machine = request("GET", base + "/machines")["items"][0]
        mr = machine["head_id"]
        snapshot = request("GET", base + "/revisions/" + mr)["snapshot"]
        assert snapshot["values"][pid]["value"] == "11" and snapshot["values"][pid]["intent"] == "pin"
        assert request("GET", base + "/branches/main")["head_id"] == baseline
        notes.append("创建基线草稿/保存/发布 B1；选择真实 B1 建机台草稿，固定值发布 r1；主线未被改写")

        stage = "V3.2 hierarchy and graph navigation"
        tab("版本演进")
        wait("!!document.querySelector('.cv32-hierarchy')")
        assert b.eval("document.querySelectorAll('[data-machine-link]').length") == 1
        b.eval("document.querySelector('[data-version-toggle]').click()")
        assert b.eval("document.querySelector('.cv32-hierarchy tbody[id]').hidden")
        click("展开全部")
        assert not b.eval("document.querySelector('.cv32-hierarchy tbody[id]').hidden")
        control("搜索版本或机台", "missing", "input")
        b.eval("document.querySelector('[aria-label=搜索版本或机台]').dispatchEvent(new KeyboardEvent('keydown',{key:'Enter',bubbles:true}))")
        wait("document.querySelector('.cv32-evo-scroll').innerText.includes('没有匹配')")
        control("搜索版本或机台", "MC-001", "input")
        b.eval("document.querySelector('[aria-label=搜索版本或机台]').dispatchEvent(new KeyboardEvent('keydown',{key:'Enter',bubbles:true}))")
        wait("document.querySelectorAll('[data-machine-link]').length===1")
        b.eval("document.querySelector('[data-machine-link]').click()")
        wait("document.querySelector('.cv-param-data')?.innerText.includes('11')")
        tab("版本演进")
        control("搜索版本或机台", "", "input")
        b.eval("document.querySelector('[aria-label=搜索版本或机台]').dispatchEvent(new KeyboardEvent('keydown',{key:'Enter',bubbles:true}))")
        click("关系图")
        b.eval("document.querySelector('[aria-label=放大关系图]').click()")
        assert b.eval("document.querySelector('.cv32-canvas').style.transform") == "scale(1.1)"
        b.eval("document.querySelector('[aria-label=缩小关系图]').click()")
        notes.append("V3.2 真实层级列表按保存版本合并机台；折叠、搜索、完整参数查看及关系图缩放通过")

        stage = "export mapping/artifact"
        tab("模板导出")
        wait("document.querySelector('[aria-label=\"模板文件\"]')?.options.length>1")
        control("模板文件", file["version_id"])
        click("预览导出")
        wait("!!document.querySelector('[data-export-commit]')")
        click("查看对应字段")
        control("送料速度的文件字段", "/Other", "input")
        b.eval("document.querySelector('.modal-header [aria-label=关闭]').click()")
        assert b.eval("document.querySelector('[data-export-commit]').disabled")
        click("预览导出")
        wait("document.querySelector('.cv-export-code')?.innerText.includes('\"Other\": 11')")
        click("下载文件")
        wait("!!document.querySelector('[data-export-download]')")
        click(b.eval("document.querySelector('[data-export-download]').textContent.trim()"))
        notes.append("修改映射立即使旧预览失效；重新预览的完整输出保留未匹配字段并生成可下载文件")
        shot = runner.shoot("config-prototype-export")

        stage = "history restore"
        tab("配置历史")
        wait("document.querySelector('.cv-history-events')?.innerText.includes('保存机台配置')")
        b.eval("Array.from(document.querySelectorAll('.cv-history-event')).find(x=>x.innerText.includes('保存机台配置')).querySelectorAll('button')[1].click()")
        wait("!!document.querySelector('[aria-label=\"送料速度的配置值\"]')")
        restored = request("GET", base + "/machines")["items"][0]
        assert restored["head_id"] == mr and restored["draft"]["status"] == "draft"
        assert restored["draft"]["values"][pid]["value"] == "11"
        notes.append("事件时间线恢复产生新草稿，已发布 r1 与基线 head 不变")

        stage = "V3.2 machine upgrade and explicit discard"
        newer = request("POST", base + "/releases", {"version": "691", "initial_mode": "separate"})["release"]["release_id"]
        request("PATCH", base + "/drafts/d0-" + newer + "/values", {"request_id": "upgrade-target", "expected_revision": 0, "items": [{"parameter_id": pid, "membership": "included", "value_state": "present", "value": "21"}]})
        request("POST", base + "/drafts/d0-" + newer + "/freeze", {"request_id": "upgrade-target-save", "expected_revision": 1})
        b.navigate("about:blank")
        b.navigate(runner.url(route + "&config=" + cid + "&machine=" + machine["machine_id"] + "&revision=" + restored["draft"]["draft_id"] + "&tab=workbench"))
        wait("!!document.querySelector('[aria-label=\\\"送料速度的配置值\\\"]')")
        b.eval("document.querySelector('[aria-label=\\\"当前配置的更多操作\\\"]').click()")
        click("更新到其他标准", "document.querySelector('[role=menu]')")
        control("新的版本标准", "d0-" + newer)
        click("比较变化", modal)
        wait("document.querySelector('.cv32-review-table')?.innerText.includes('11')")
        assert b.eval("document.querySelector('[aria-label=\\\"送料速度的处理方式\\\"]').value") == "keep_local"
        click("继续编辑", modal)
        close_saved()
        wait("document.querySelector('.cv32-object-heading')?.innerText.includes('691')")
        upgraded = request("GET", base + "/machines/" + machine["machine_id"])["machine"]
        assert upgraded["head_id"] == mr and upgraded["draft"]["base_revision_id"] == "d0-" + newer
        assert upgraded["draft"]["values"][pid]["value"] == "11" and upgraded["draft"]["values"][pid]["intent"] == "pin"
        b.eval("document.querySelector('[aria-label=\\\"当前配置的更多操作\\\"]').click()")
        click("放弃已暂存草稿", "document.querySelector('[role=menu]')")
        click("放弃草稿", modal)
        close_saved()
        wait("document.querySelector('.cv32-object-heading')?.innerText.includes('690') && !document.querySelector('[aria-label=\\\"送料速度的配置值\\\"]')")
        discarded = request("GET", base + "/machines/" + machine["machine_id"])["machine"]
        assert not discarded.get("draft") and discarded["head_id"] == mr and discarded["release_id"] == rid
        request("POST", base + "/restore-plans", {"request_id": "continue-edit-after-discard", "revision_id": mr})
        b.navigate("about:blank")
        b.navigate(runner.url(route + "&config=" + cid + "&machine=" + machine["machine_id"] + "&revision=" + request("GET", base + "/machines/" + machine["machine_id"])["machine"]["draft"]["draft_id"] + "&tab=workbench"))
        wait("!!document.querySelector('[aria-label=\\\"送料速度的配置值\\\"]')")
        notes.append("V3.2 机台升级使用真实草稿与原标准，保持固定值，仅改目标草稿；显式放弃恢复原保存版本，历史 r1 未改变")

        stage = "workspace drafts"
        other = request("POST", "/api/v3/workspaces", {"name": "配置隔离B"})["workspace"]["id"]
        edit("13")
        b.eval("MC.appInstance.switchWorkspace(%s);true" % json.dumps(other))
        click("继续编辑", modal)
        close_saved()
        assert b.eval("document.querySelector('[aria-label=\"送料速度的配置值\"]').value") == "13"
        b.eval("MC.appInstance.switchWorkspace(%s);true" % json.dumps(other))
        click("暂存并离开", modal)
        wait("MC.appInstance.wid === %s" % json.dumps(other))
        assert request("GET", base + "/machines/" + machine["machine_id"])["machine"]["draft"]["values"][pid]["value"] == "13"
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
        notes.append("未保存值/新增表单切换工作区均先确认；取消保留，暂存并离开写入原工作区；放弃表单后关闭旧弹窗，不向 B 写入")

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
                if name == "参数工作台":
                    parity = b.eval("(()=>{let directory=document.querySelector('.cv32-workbench .cv-param-folders'),data=document.querySelector('.cv32-workbench .cv-param-data'),crumb=document.querySelector('.cv-breadcrumb');return {directoryWidth:directory.getBoundingClientRect().width,dataX:data.getBoundingClientRect().x,breadcrumbHeight:crumb.getBoundingClientRect().height,tabbarHidden:getComputedStyle(document.querySelector('.tabbar')).display==='none'};})()")
                    assert parity["tabbarHidden"], parity
                    assert parity["breadcrumbHeight"] == (38 if width <= 760 else 42), parity
                    if width > 760:
                        expected = {1366: (172, 424), 1920: (172, 440), 768: (128, 321)}[width]
                        assert abs(parity["directoryWidth"] - expected[0]) <= 1 and abs(parity["dataX"] - expected[1]) <= 1, parity
                    notes.append("prototype geometry %s %s" % (width, json.dumps(parity)))
                if width > 760 and name in ("参数工作台", "参数总览"):
                    assert metrics["footerBottom"] <= height + 2, metrics
                if name == "版本演进":
                    graph_metrics = b.eval("""(()=>{let s=document.querySelector('.cv32-evo-scroll'),f=document.querySelector('.cv32-evo-footer');return {height:s.clientHeight,footer:f.getBoundingClientRect().bottom};})()""")
                    assert graph_metrics["height"] >= 180 and graph_metrics["footer"] <= height + 2, graph_metrics
                    notes.append("V3.2 graph workspace %s %s" % (width, json.dumps(graph_metrics)))
                if name == "模板导出":
                    layout = b.eval("""(()=>{let s=document.querySelector('.cv-export-source .cv-detail-body'),a=s.querySelector('[aria-label="导出配置"]').getBoundingClientRect(),f=s.querySelector('[aria-label="模板文件"]').getBoundingClientRect(),p=document.querySelector('.cv-export-preview .cv-detail-body'),t=p.querySelector('.table-wrap');return {stacked:a.top>=f.bottom,fieldWidth:f.width,sourceWidth:s.clientWidth,mappingWidth:t?.clientWidth,previewWidth:p.clientWidth}})()""")
                    assert layout["stacked"] and layout["fieldWidth"] >= layout["sourceWidth"] - 34, layout
                    assert layout["previewWidth"] > 100, layout
                    assert not b.eval("!!document.querySelector('.cv-export-preview .cv-export-mapping')"), "mapping must use the prototype dialog"
                    click("查看对应字段")
                    mapping = b.eval("(()=>{let d=document.querySelector('.modal-body'),w=d.querySelector('.table-wrap');w.scrollLeft=w.scrollWidth;return {width:w.clientWidth,bodyWidth:d.clientWidth,end:w.scrollLeft+w.clientWidth>=w.scrollWidth-2};})()")
                    assert mapping["width"] >= mapping["bodyWidth"] - 50 and mapping["end"], mapping
                    runner.shoot("v32-export-fields-%s" % width)
                    b.eval("document.querySelector('.modal-header [aria-label=关闭]').click()")
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
        stage = "V3.2 3248-parameter workbench"
        large_workspace = request("POST", "/api/v3/workspaces", {"name": "V3.2 大参数验收"})["workspace"]["id"]
        large_root = "/api/v3/workspaces/%s/profiles/default" % large_workspace
        request("PUT", "/api/v1/p/default/files/Large.json", {"Group": {"P%04d" % i: i for i in range(3248)}}, headers={"X-Workspace-Id": large_workspace})
        large_file = request("GET", large_root + "/file-selector?q=Large.json")["items"][0]
        large_config = request("POST", large_root + "/configs", {"code": "LARGE", "name": "3248 参数", "file_id": large_file["file_id"]})["config"]["config_id"]
        large_base = large_root + "/configs/" + large_config
        extraction = request("POST", large_base + "/extraction-plans", {"version_id": large_file["version_id"]})["plan"]
        extracted = request("POST", large_base + "/extraction-plans/" + extraction["plan_id"] + "/commit", {"request_id": "large-parameters"})
        assert extracted["result"]["created"] == 3248, extracted
        large_release = request("POST", large_base + "/releases", {"version": "3.2-large", "initial_mode": "separate"})["release"]["release_id"]
        large_params = request("GET", large_base + "/parameters?q=P3247")["items"]
        last_parameter = next(p for p in large_params if p["name"] == "P3247")
        request("PATCH", large_base + "/drafts/d0-" + large_release + "/values", {"request_id": "large-last-value", "expected_revision": 0, "items": [{"parameter_id": last_parameter["parameter_id"], "membership": "included", "enabled": True, "value_state": "present", "value": "3247"}]})
        b.viewport(1366, 768)
        b.navigate("about:blank")
        b.navigate(runner.url("#/p/default/configv3/configs?workspace=" + large_workspace + "&config=" + large_config + "&revision=d0-" + large_release + "&standard=" + large_release + "&tab=workbench"))
        wait("document.querySelectorAll('.cv-param-data tbody tr').length===30 && document.querySelector('.cv-param-footer')?.innerText.includes('3248')")
        assert b.eval("document.querySelectorAll('[aria-label$=的配置值]').length") == 30
        for size in (50, 100):
            control("每页参数数", str(size))
            wait("document.querySelectorAll('.cv-param-data tbody tr').length===%d" % size)
        for page in range(2, 34):
            click("下一页", "document.querySelector('.cv-param-footer')")
            wait("document.querySelector('.cv-param-footer')?.innerText.includes('第 %d / 33 页')" % page)
        assert b.eval("document.querySelectorAll('.cv-param-data tbody tr').length") == 48
        assert b.eval("document.querySelector('.cv-param-data tbody tr:last-child').innerText.includes('P3247')")
        b.eval("var largeScroll=document.querySelector('.cv-param-data .table-wrap');largeScroll.scrollTop=largeScroll.scrollHeight")
        assert b.eval("(()=>{let t=document.querySelector('.cv-param-data .table-wrap'),r=t.querySelector('tr:last-child').getBoundingClientRect(),box=t.getBoundingClientRect();return r.bottom<=box.bottom+2;})()")
        control("搜索参数", "P3247", "input")
        b.eval("document.querySelector('[aria-label=搜索参数]').dispatchEvent(new KeyboardEvent('keydown',{key:'Enter',bubbles:true}))")
        wait("document.querySelectorAll('.cv-param-data tbody tr').length===1 && !!document.querySelector('[aria-label=\\\"P3247的配置值\\\"]')")
        assert b.eval("document.querySelector('[aria-label=\\\"P3247的配置值\\\"]').value") == "3247"
        for width, height in [(1366, 768), (1920, 1080), (768, 1024), (375, 812), (683, 384)]:
            b.viewport(width, height)
            wait("document.getAnimations().every(a=>a.playState!=='running')")
            assert b.eval("document.documentElement.scrollWidth<=innerWidth+2"), (width, height)
            metrics = b.eval("(()=>{let p=document.querySelector('.cv-param-data'),s=p.querySelector('.table-wrap'),f=document.querySelector('.cv32-editbar');return {tableHeight:s.clientHeight,tableScroll:s.scrollHeight,editbarBottom:f.getBoundingClientRect().bottom,viewport:innerHeight};})()")
            assert metrics["tableHeight"] > 0, metrics
            if height >= 700:
                assert metrics["editbarBottom"] <= height + 2, metrics
            else:
                b.eval("var shortView=document.querySelector('.config-root');shortView.scrollTop=shortView.scrollHeight")
                assert b.eval("(()=>{let v=document.querySelector('.config-root'),bar=v.querySelector('.cv32-editbar');return bar.getBoundingClientRect().bottom<=v.getBoundingClientRect().bottom+2;})()"), metrics
            notes.append("3248 parameters %sx%s %s" % (width, height, json.dumps(metrics)))
            runner.shoot("v32-large-%s" % width)
        notes.append("3248 项真实服务合成数据：30/50/100 控件分页、33 页末项、全量名称搜索；683×384 为 1366×768 在 200% 缩放下的等效 CSS 视口，未冒充原生浏览器缩放操作")
        stage = "V3.2 coarse touch and modal keyboard focus"
        # In the combined run the old headless compositor stops acknowledging
        # touchStart after screenshot scenarios. Keep input verification in a
        # fresh browser, against the same persisted data and actual HTTP server.
        from e2e_cdp import Chrome
        touch_url = b.eval("location.href")
        b.close()
        b = runner.browser = Chrome(chrome=runner.args.chrome)
        runner.role(admin_ip)
        b.call("Emulation.setDeviceMetricsOverride", width=375, height=812, deviceScaleFactor=1, mobile=True)
        b.call("Emulation.setTouchEmulationEnabled", enabled=True, maxTouchPoints=1)
        b.navigate(touch_url)
        wait("document.querySelectorAll('.cv-param-data tbody tr').length===30")
        control("每页参数数", "100")
        assert b.eval("matchMedia('(pointer:coarse)').matches"), "Chrome touch emulation did not enable coarse pointer"
        control("搜索参数", "", "input")
        b.eval("document.querySelector('[aria-label=搜索参数]').dispatchEvent(new KeyboardEvent('keydown',{key:'Enter',bubbles:true}))")
        wait("document.querySelectorAll('.cv-param-data tbody tr').length===100")
        wait("document.getAnimations().every(a=>a.playState!=='running')")
        point = b.eval("(()=>{let t=document.querySelector('.cv-param-data .table-wrap'),r=t.getBoundingClientRect();return {x:r.x,y:r.y,width:r.width,height:r.height};})()")
        assert point["height"] > 0, point
        def swipe(x, y, dx, dy):
            phase = "touchStart"
            try:
                b.call("Input.dispatchTouchEvent", type="touchStart", touchPoints=[{"x": x, "y": y}])
                for step in range(1, 9):
                    phase = "touchMove-%s" % step
                    b.call("Input.dispatchTouchEvent", type="touchMove", touchPoints=[{"x": x + dx * step / 8, "y": y + dy * step / 8}])
                phase = "touchEnd"
                b.call("Input.dispatchTouchEvent", type="touchEnd", touchPoints=[])
            except Exception as exc:
                raise AssertionError("%s at (%s,%s) delta(%s,%s): %s" % (phase, x, y, dx, dy, exc)) from exc
        stage = "V3.2 vertical touch scrolling"
        for _ in range(3):
            swipe(point["x"] + 35, point["y"] + point["height"] - 15, 0, -(point["height"] - 40))
        wait("document.querySelector('.cv-param-data .table-wrap').scrollTop>100")
        stage = "V3.2 horizontal touch scrolling"
        swipe(point["x"] + point["width"] - 20, point["y"] + 15, -(point["width"] - 40), 0)
        wait("document.querySelector('.cv-param-data .table-wrap').scrollLeft>0")
        hit_sizes = b.eval("Array.from(document.querySelectorAll('.cv-tabs button,.cv32-editbar button,[aria-label=每页参数数]')).filter(e=>e.getBoundingClientRect().width>0).map(e=>({text:e.textContent,height:e.getBoundingClientRect().height}))")
        assert all(row["height"] >= 44 for row in hit_sizes), hit_sizes
        runner.shoot("v32-touch-workbench")
        stage = "V3.2 modal keyboard focus"
        b.eval("(()=>{let trigger=Array.from(document.querySelectorAll('.cv32-editbar button')).find(x=>x.textContent.trim()==='核对并保存');if(trigger.disabled)throw Error('review trigger disabled');trigger.focus();if(document.activeElement!==trigger)throw Error('review trigger could not receive focus');trigger.click();})()")
        wait("document.activeElement.tagName==='H2'&&!!document.querySelector('.cv32-review-table')")
        for kind in ("rawKeyDown", "keyUp"):
            b.call("Input.dispatchKeyEvent", type=kind, key="Tab", code="Tab", windowsVirtualKeyCode=9, modifiers=8)
        assert b.eval("document.activeElement===document.querySelector('.modal-footer button:last-child')"), "Shift+Tab must wrap from title to final action"
        b.press("Tab")
        assert b.eval("document.activeElement.tagName==='H2'"), "Tab must wrap to dialog title"
        b.press("Escape")
        wait("!document.querySelector('.modal')")
        assert b.eval("document.activeElement.textContent.trim()") == "核对并保存", "Escape must return focus to the keyboard trigger"
        b.call("Emulation.setTouchEmulationEnabled", enabled=False)
        b.viewport(1366, 768)
        notes.append("375×812 粗指针仿真：真实触摸手势滚动参数表的横纵两轴，主控件高度≥44；核对弹窗标题进入焦点、Shift+Tab/Tab循环、Esc关闭和焦点返回通过。非物理触屏实机证据")
        stage = "many software versions and machines"
        previous = comparison
        for i in range(20):
            previous = request("POST", base + "/releases", {"version": "route-%02d" % i, "predecessor_id": previous, "initial_mode": "separate"})["release"]["release_id"]
        for i in range(30):
            request("POST", base + "/machine-branches", {"machine_no": "MORE-%02d" % i, "release_id": rid, "base_kind": "default", "base_revision_id": "d0-" + rid})
        b.navigate("about:blank")
        b.navigate(runner.url(route + "&config=" + cid))
        wait("document.querySelector('.cv32-graph-plane')?.textContent.includes('route-19')")
        for width, height in [(1366, 768), (375, 812)]:
            b.viewport(width, height)
            wait("document.getAnimations().every(a=>a.playState!=='running')")
            graph = b.eval("(()=>{let s=document.querySelector('.cv32-evo-scroll');s.scrollLeft=s.scrollWidth;s.scrollTop=s.scrollHeight;let r=s.getBoundingClientRect(),nodes=Array.from(s.querySelectorAll('[data-config-revision]')),bottom=Math.max(...nodes.map(n=>n.getBoundingClientRect().bottom));return {height:s.clientHeight,width:s.clientWidth,scrollHeight:s.scrollHeight,scrollWidth:s.scrollWidth,top:s.scrollTop,left:s.scrollLeft,bottom,viewportBottom:r.bottom};})()")
            assert graph["height"] >= 180 and graph["scrollHeight"] > graph["height"] + 100 and graph["scrollWidth"] > graph["width"] + 100, graph
            assert graph["top"] >= graph["scrollHeight"] - graph["height"] - 2 and graph["left"] >= graph["scrollWidth"] - graph["width"] - 2, graph
            assert graph["bottom"] <= graph["viewportBottom"] + 2, graph
            notes.append("many versions %sx%s %s" % (width, height, json.dumps(graph)))
            for name in ("MORE-29", "软件 route-19"):
                visible = b.eval("""(()=>{let s=document.querySelector('.cv32-evo-scroll'),n=Array.from(s.querySelectorAll('[data-config-revision]')).find(n=>n.getAttribute('aria-label').includes(%s));if(!n)return {missing:true};n.scrollIntoView({block:'center',inline:'center'});let a=n.getBoundingClientRect(),r=s.getBoundingClientRect(),hit=document.elementFromPoint(a.x+a.width/2,a.y+a.height/2);return {visible:a.top>=r.top&&a.bottom<=r.bottom&&a.left>=r.left&&a.right<=r.right,hit:hit===n||n.contains(hit),text:n.textContent};})()""" % json.dumps(name))
                assert visible.get("visible") and visible.get("hit"), (name, visible)
                notes.append("graph last target %s %s %s" % (width, name, json.dumps(visible)))
            runner.shoot("v32-many-versions-%s" % width)
        stage = "shared file header and frozen graph axes"
        from e2e_config_axes import run as check_file_axes
        notes.extend(check_file_axes(runner, route + "&config=" + cid))
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
        b.call("Emulation.setTouchEmulationEnabled", enabled=False)
        b.viewport(1366, 768)
    runner.report.append({"id": "config-prototype", "title": "配置原型真实功能与四视口", "level": "interactive", "ok": ok, "notes": notes, "screenshot": shot})
    print("[%s] config-prototype %s" % ("PASS" if ok else "FAIL", notes[-1:]), flush=True)
