"""Layout inventory: real configuration views and dialogs, isolated E2E data only."""
import json


def audit(runner, route, cid, revision_id, file, prepared_release, notes, merge_target):
    b = runner.browser
    modal = "Array.from(document.querySelectorAll('.modal')).slice(-1)[0]"

    def wait(js):
        assert b.wait_for(js, timeout=15), (js, b.eval("document.body.innerText")[-1000:])

    def click(text, scope="document"):
        target = "Array.from((%s).querySelectorAll('button')).find(x=>x.textContent.trim()===%s&&!x.disabled)" % (scope, json.dumps(text))
        wait("!!(" + target + ")")
        b.eval("(" + target + ").click()")

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
                b.eval("document.querySelector('.cv-release-band button').click()")
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

    def check(name, width, height):
        wait("!!document.querySelector('.modal')")
        data = b.eval("""(()=>{let m=%s,r=m.getBoundingClientRect(),body=m.querySelector('.modal-body'),f=m.querySelector('.modal-footer'),fields=Array.from(body.querySelectorAll('input:not([type=checkbox]),select,textarea')).filter(e=>e.getBoundingClientRect().width>0);return {name:m.querySelector('h2').textContent,x:r.x,y:r.y,width:r.width,bottom:r.bottom,bodyOverflow:body.scrollWidth-body.clientWidth,footerBottom:f?.getBoundingClientRect().bottom,small:fields.filter(e=>e.getBoundingClientRect().width<100).map(e=>e.getAttribute('aria-label')||e.name),buttons:f?Array.from(f.querySelectorAll('button')).map(e=>({text:e.textContent,width:e.getBoundingClientRect().width,right:e.getBoundingClientRect().right})):[]}})()""" % modal)
        assert data["x"] >= -1 and data["width"] <= width + 1 and data["bottom"] <= height + 1 and data["y"] >= -1, (name, data)
        assert data["bodyOverflow"] <= 2 and not data["small"], (name, data)
        assert all(x["width"] >= 40 and x["right"] <= width + 1 for x in data["buttons"]), (name, data)
        reach = b.eval("""(()=>{let body=(%s).querySelector('.modal-body'),top=body.scrollTop;body.scrollTop=body.scrollHeight;let last=body.lastElementChild,vertical=!last||last.getBoundingClientRect().bottom<=body.getBoundingClientRect().bottom+1;let horizontal=Array.from(body.querySelectorAll('.table-wrap')).every(e=>{let old=e.scrollLeft;e.scrollLeft=e.scrollWidth;let end=e.scrollLeft>=e.scrollWidth-e.clientWidth-1;e.scrollLeft=old;return end});body.scrollTop=top;return {vertical,horizontal}})()""" % modal)
        assert reach["vertical"] and reach["horizontal"], (name, reach)
        notes.append("dialog %sx%s %s %s" % (width, height, name, json.dumps(data, ensure_ascii=False)))
        if width in (1366, 375) and name in ("merge-preview", "extract-preview", "revision-picker"):
            runner.shoot("config-dialog-%s-%s" % (name, width))

    def close():
        initial = b.eval("document.querySelectorAll('.modal').length")
        b.eval("(%s).querySelector('.modal-header button').click()" % modal)
        # Only form changes made by this audit can require discard confirmation.
        if b.eval("document.querySelector('.modal') && (%s).querySelector('h2').textContent==='放弃未保存的修改？'" % modal):
            click("放弃修改", modal)
        wait("document.querySelectorAll('.modal').length<%s" % initial)

    for width, height in [(1366, 768), (1920, 1080), (768, 1024), (375, 812)]:
        b.viewport(width, height)
        b.navigate("about:blank")
        b.navigate(runner.url(route + "&config=" + cid + "&revision=" + revision_id))
        wait("!!document.querySelector('.cv-tabs')")
        tab("版本演进")
        overflow = b.eval("Array.from(document.querySelectorAll('.cv-graph-node,.cv-release-band,.cv-lane-label')).filter(e=>e.scrollWidth>e.clientWidth+2).map(e=>e.className)")
        assert not overflow, (width, overflow)
        for action, name in [("新建软件版本", "new-release"), ("新建机台", "new-machine"), ("读图说明", "graph-help")]:
            click(action); check(name, width, height); close()
        tab("参数工作台")
        runner.shoot("v32-parameters-%s" % width)
        b.eval("document.querySelector('.cv32-parameter-name').click()")
        check("parameter-details", width, height); close()
        b.eval("document.querySelector('[aria-label=\"参数表显示设置\"]').click()")
        check("parameter-display", width, height); close()
        b.eval("Array.from(document.querySelectorAll('.cv32-object-heading button')).find(x=>['编辑参数','继续编辑草稿'].includes(x.textContent.trim())).click()")
        wait("!!Array.from(document.querySelectorAll('button')).find(x=>x.textContent.trim()==='核对并保存')")
        click("核对并保存"); check("save-review", width, height); close()
        b.eval("document.querySelector('[aria-label=\"当前配置的更多操作\"]').click()")
        click("从文件提取参数", "document.querySelector('[role=menu]')")
        wait("!!document.querySelector('.modal [aria-label=\"配置文件\"]')")
        check("extract-picker", width, height)
        click("预览参数", modal)
        wait("!!document.querySelector('.modal .table-wrap') || !!document.querySelector('.modal .state.is-error')")
        check("extract-preview", width, height); close()
        b.eval("document.querySelector('[aria-label=\"当前配置的更多操作\"]').click()")
        click("合并配置", "document.querySelector('[role=menu]')")
        check("merge-picker", width, height)
        b.eval("var sourceO=document.querySelector('[aria-label=\"原来的标准\"]'),sourceN=document.querySelector('[aria-label=\"新的标准\"]');sourceO.value=%s;sourceN.value=%s;sourceN.dispatchEvent(new Event('change',{bubbles:true}))" % (json.dumps(revision_id), json.dumps(merge_target)))
        click("预览合并", modal)
        wait("!!document.querySelector('.modal .table-wrap') || !!document.querySelector('.modal .state.is-error')")
        check("merge-preview", width, height)
        wait("!!document.querySelector('.modal [aria-label=\"合并决议\"]')")
        b.eval("var decision=document.querySelector('.modal [aria-label=\"合并决议\"]');decision.value='custom';decision.dispatchEvent(new Event('change',{bubbles:true}))")
        wait("!!document.querySelector('.modal [aria-label=\"送料速度的合并自定义值\"]')")
        check("merge-custom-decision", width, height); close()
        tab("参数总览")
        click("手动新增"); check("new-parameter", width, height)
        b.eval("var layoutInput=document.querySelector('.modal input');layoutInput.value='Long unsaved parameter name for layout inspection';layoutInput.dispatchEvent(new Event('input',{bubbles:true}));(%s).querySelector('.modal-header button').click()" % modal)
        check("discard-confirmation", width, height); close(); close()
        click("新增目录"); check("new-directory", width, height); close()
        wait("!!document.querySelector('[aria-label=\"选择目录 设备控制\"]')")
        b.eval("document.querySelector('[aria-label=\"选择目录 设备控制\"]').click()")
        wait("!!document.querySelector('[aria-label=\"当前目录操作\"]')")
        for action, name in [("重命名 / 移动目录", "move-directory"), ("删除空目录", "delete-directory")]:
            b.eval("document.querySelector('[aria-label=\"当前目录操作\"]').click()")
            click(action); check(name, width, height); close()
        b.eval("document.querySelector('[aria-label=\"选择目录 全部参数\"]').click()")
        wait("!!document.querySelector('.cv-param-data tbody')")
        click("送料速度", "document.querySelector('.cv-param-data')")
        check("parameter-properties", width, height); close()
        row = "Array.from(document.querySelectorAll('.cv-param-data tbody tr')).find(x=>x.textContent.includes('送料速度'))"
        b.eval("(%s).querySelector('.cv-binding-link').click()" % row)
        check("frozen-binding", width, height); close()
        b.eval("(%s).querySelector('td:last-child button').click()" % row)
        wait("document.querySelector('.modal h2')?.textContent.includes('参数历程')")
        check("parameter-history", width, height); close()
        # Prepared release: editable binding plus nested exact file field picker.
        b.eval("var layoutControl=document.querySelector('[aria-label=\"查看的软件版本\"]');layoutControl.value=%s;layoutControl.dispatchEvent(new Event('change',{bubbles:true}))" % json.dumps(prepared_release))
        wait("!!document.querySelector('.cv-param-data') && document.querySelector('[aria-label=\"查看的软件版本\"]').value===%s" % json.dumps(prepared_release))
        b.eval("(%s).querySelector('.cv-binding-link').click()" % row)
        wait("!document.querySelector('[aria-label=\"绑定来源文件\"]').disabled")
        check("editable-binding", width, height)
        b.eval("var layoutControl=document.querySelector('[aria-label=\"绑定来源文件\"]');layoutControl.value=%s;layoutControl.dispatchEvent(new Event('change',{bubbles:true}))" % json.dumps(file["version_id"]))
        click("从文件选择字段", modal)
        wait("!!document.querySelector('.cv-field-picker')")
        check("nested-field-picker", width, height); close(); close()
        tab("参数工作台")
        click("核对并保存")
        check("freeze-confirmation", width, height); close()
        # No saves or publication are committed by the dialog inventory.
        click("其他配置", "document.querySelector('.cv-breadcrumb')")
        assert b.eval("document.documentElement.scrollWidth<=innerWidth+2")
        click("新建配置分组")
        check("new-config", width, height); close()
        for surface, label in [("files", "配置文件"), ("records", "配置记录")]:
            b.navigate(runner.url("#/p/default/configv3/" + surface + "?workspace=" + route.split("workspace=", 1)[1]))
            wait("!!document.querySelector('.config-resource-surface')")
            assert b.eval("document.documentElement.scrollWidth<=innerWidth+2"), label
            notes.append("resource %s %s reachable/layout" % (width, label))
