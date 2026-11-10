"""Cross-page geometry and frozen graph axes against a real service."""
import json


def run(runner, route):
    b = runner.browser
    evidence = []

    def wait(expression):
        if not b.wait_for(expression, timeout=15):
            actual = b.eval("""(()=>{let s=document.querySelector('.cv32-evo-scroll');if(!s)return null;let c=s.querySelector('.cv32-fixed-columns'),r=s.querySelector('.cv32-fixed-rows');return {scroll:[s.scrollLeft,s.scrollTop],zoom:s.querySelector('.cv32-canvas')?.style.transform,columns:[c?.style.transform,c?.getBoundingClientRect().top],rows:[r?.style.transform,r?.getBoundingClientRect().left],origin:s.getBoundingClientRect().toJSON()}})()""")
            raise AssertionError((expression, actual))

    def click_tab(label, scope):
        b.eval("Array.from(document.querySelector(%s).querySelectorAll('.cv-tabs button')).find(b=>b.textContent===%s).click()" % (json.dumps(scope), json.dumps(label)))

    def measure():
        return b.eval("""(()=>{let r=document.querySelector('.view-host>.view:not(.hidden)'),h=r.querySelector('.cv32-file-heading'),tabs=r.querySelector('.cv-tabs'),work=r.querySelector('.cv32-evo-surface,.sync-workbench,.cv-history');function rect(e){let v=e.getBoundingClientRect();return [v.x,v.y,v.width,v.height]};return {header:rect(h),tabs:rect(tabs),work:rect(work),label:h.querySelector('.cv-file-label').textContent}})()""")

    for width, height in [(1230, 873), (1366, 768), (1920, 1080), (768, 1024), (375, 812)]:
        b.viewport(width, height)
        b.navigate(runner.url(route))
        wait("!!document.querySelector('.config-root:not(.hidden) .cv32-evo-scroll')")
        baseline = measure()
        for label, scope in [("更新管理", '.config-root:not(.hidden)'), ("操作记录", '.sync-root:not(.hidden)'), ("版本演进", '.config-root:not(.hidden)')]:
            click_tab(label, scope)
            wait("!!document.querySelector('.view-host>.view:not(.hidden) .cv32-file-summary')?.textContent.includes('台机台')")
            if label == '更新管理':
                wait("!!document.querySelector('.sync-root:not(.hidden) .sync-source-table')")
            elif label == '操作记录':
                wait("!!document.querySelector('.config-root:not(.hidden) .cv-history')")
            else:
                wait("!!document.querySelector('.config-root:not(.hidden) .cv32-evo-scroll')")
            value = measure()
            for key in ('header', 'tabs'):
                assert all(abs(a - c) < 1 for a, c in zip(value[key], baseline[key])), (width, label, key, baseline, value)
            assert all(abs(a - c) < 1 for a, c in zip(value['work'][:3], baseline['work'][:3])), (width, label, baseline, value)
            if label == '更新管理':
                assert all(abs(a - c) < 1 for a, c in zip(value['work'], baseline['work'])), (width, label, baseline, value)
            assert value['label'] == baseline['label'], (label, baseline, value)
        evidence.append('cross-page %s: %s' % (width, json.dumps(baseline)))
        runner.shoot('shared-file-header-%s' % width)

    for width, height in [(1366, 768), (375, 812)]:
        b.viewport(width, height)
        b.navigate('about:blank')
        b.navigate(runner.url(route))
        wait("!!document.querySelector('.config-root:not(.hidden) .cv32-canvas')")
        for zoom in (1, 0.75, 1.25):
            b.eval("""(()=>{let current=Number(document.querySelector('.cv32-canvas').style.transform.match(/[\\d.]+/)[0]),direction=%s>current?'放大关系图':'缩小关系图',steps=Math.round(Math.abs(%s-current)*10);for(let i=0;i<steps;i++)document.querySelector('[aria-label="'+direction+'"]').click()})()""" % (zoom, zoom))
            b.eval("(()=>{let s=document.querySelector('.cv32-evo-scroll');s.scrollTo(0,0)})()")
            wait("Math.abs(document.querySelector('.cv32-fixed-columns').getBoundingClientRect().top-document.querySelector('.cv32-evo-scroll').getBoundingClientRect().top)<2")
            before = b.eval("(()=>{let s=document.querySelector('.cv32-evo-scroll');return {x:s.querySelector('.cv32-fixed-rows').getBoundingClientRect().left,y:s.querySelector('.cv32-fixed-columns').getBoundingClientRect().top}})()")
            b.eval("(()=>{let s=document.querySelector('.cv32-evo-scroll');s.scrollTo(s.scrollWidth/2,s.scrollHeight/2)})()")
            wait("Math.abs(document.querySelector('.cv32-fixed-columns').getBoundingClientRect().top-%s)<1&&Math.abs(document.querySelector('.cv32-fixed-rows').getBoundingClientRect().left-%s)<1" % (before['y'], before['x']))
            result = b.eval("(()=>{let s=document.querySelector('.cv32-evo-scroll'),c=s.querySelector('.cv32-fixed-corner').getBoundingClientRect();return {left:s.scrollLeft,top:s.scrollTop,cornerX:c.x,cornerY:c.y}})()")
            assert result['left'] > 100 and result['top'] > 100, result
            assert abs(result['cornerX'] - before['x']) < 1 and abs(result['cornerY'] - before['y']) < 1, result
            evidence.append('frozen axes %s zoom=%s %s' % (width, zoom, json.dumps(result)))
            runner.shoot('frozen-axes-%s-%s' % (width, zoom))
    # Hit-test the real fixed title, rather than programmatically invoking its callback.
    b.eval("document.querySelector('.config-root:not(.hidden) .cv-release-band button')?.scrollIntoView({block:'nearest',inline:'center'})")
    wait("Math.abs(document.querySelector('.cv32-fixed-columns').getBoundingClientRect().top-%s)<1&&Math.abs(document.querySelector('.cv32-fixed-rows').getBoundingClientRect().left-%s)<1" % (before['y'], before['x']))
    hit = b.eval("""(()=>{let s=document.querySelector('.cv32-evo-scroll'),r=s.getBoundingClientRect(),edge=s.querySelector('.cv32-fixed-corner').getBoundingClientRect().right;for(let b of s.querySelectorAll('.cv-release-band button')){let a=b.getBoundingClientRect(),left=Math.max(a.left,edge+2),right=Math.min(a.right,r.right-2);if(right-left<8)continue;let x=(left+right)/2,y=(a.top+a.bottom)/2,e=document.elementFromPoint(x,y);if(e===b||b.contains(e))return {x,y,name:b.textContent}}return null})()""")
    assert hit, 'fixed software title must remain clickable after scrolling'
    for kind in ('mousePressed', 'mouseReleased'):
        b.call('Input.dispatchMouseEvent', type=kind, x=hit['x'], y=hit['y'], button='left', clickCount=1)
    wait("document.querySelector('.config-root:not(.hidden) .cv32-object-heading')?.textContent.includes(%s)" % json.dumps(hit['name']))
    evidence.append('fixed header real click opened ' + hit['name'])
    return evidence
