"""Real-browser readability and interaction checks against disposable jobs only."""
import json
import os
import re
from pathlib import Path
import subprocess
import tempfile

import httpx
from playwright.sync_api import sync_playwright

import capture_wiki as fixture

ROOT = Path(__file__).resolve().parents[1]
TITLE = 'Review the community grant application and prepare the final illustrated report'
LONG_HELPER = 'phone-fixture-helper-'+'identifier'*4
LONG_WORKFLOW = 'phone-fixture-workflow-'+'identifier'*8


def main():
    out = ROOT/'output/playwright/job-board-phone'
    out.mkdir(parents=True, exist_ok=True)
    home = Path(tempfile.mkdtemp(prefix='bitcadence-phone-fixture-'))
    port = fixture.free_port()
    base = f'http://127.0.0.1:{port}'
    proc = fixture.start_gateway(home, port)
    checks, errors, blocked = [], [], []
    page = None
    try:
        fixture.seed(base)
        api = httpx.Client(base_url=base, headers={'Authorization': 'Bearer '+fixture.TOKEN}, timeout=20)
        registration=api.post('/api/agents',json={'instance_id':LONG_HELPER,'role':'codex','org':'default','scopes':['jobs:read','jobs:write']})
        registration.raise_for_status()
        writer=httpx.Client(base_url=base,headers={'Authorization':'Bearer '+registration.json()['token']},timeout=20)
        for title in [TITLE, 'Inspect the unbroken identifier '+('traceidentifier'*8)]:
            response = writer.post('/api/jobs', json={'title': title, 'target_agent_role': 'codex', 'priority': 100,'input_payload':{'workflow':{'name':LONG_WORKFLOW}}})
            response.raise_for_status()
        from mco.orchestrator.client import GatewayClient
        worker=GatewayClient(base,registration.json()['token'],'codex',LONG_HELPER,lease_store_dir=home/'fixture-leases')
        assert worker.lease_next()['job']['title']==TITLE
        before = {j['id']:j['status'] for j in api.get('/api/jobs').json()}
        with sync_playwright() as p:
            browser = p.chromium.launch(channel='msedge' if os.name == 'nt' else None)
            context = browser.new_context(viewport={'width':320,'height':740})
            def guard(route):
                request = route.request
                if not request.url.startswith(base+'/') or request.method not in ('GET','HEAD','OPTIONS'):
                    blocked.append({'method':request.method,'url':request.url});route.abort()
                else:
                    route.continue_()
            context.route('**/*', guard)
            context.add_init_script('localStorage.setItem("bitcadence_conn",'+json.dumps(json.dumps({'url':base,'token':fixture.TOKEN}))+');localStorage.setItem("bitcadence_page","jobs");')
            page = context.new_page()
            page.on('pageerror', lambda error: errors.append(str(error)))
            page.goto(base+'/console')
            title_button = page.get_by_role('button', name='Open job '+TITLE, exact=True)
            title_button.wait_for()
            page.wait_for_function("window.BitCadenceStore.mode()==='live'")
            for dismiss in page.get_by_role('button',name=re.compile('^Dismiss ')).all():
                dismiss.click()
            for advanced in [False,True]:
                toggle=page.get_by_role('button',name='Technical details',exact=True)
                if (toggle.get_attribute('aria-pressed')=='true')!=advanced:
                    toggle.click()
                for width in [320,390,430]:
                    page.set_viewport_size({'width':width,'height':740})
                    page.screenshot(path=str(out/f'phone-{width}-{"technical" if advanced else "plain"}.png'),full_page=True)
                    metrics = title_button.evaluate('''el => {
                        const r=el.getBoundingClientRect(),style=getComputedStyle(el);
                        const walker=document.createTreeWalker(el,NodeFilter.SHOW_TEXT);
                        const fragmented=[];let node;
                        while(node=walker.nextNode())for(const match of node.textContent.matchAll(/[A-Za-z]{3,12}/g)){
                            const range=document.createRange();range.setStart(node,match.index);range.setEnd(node,match.index+match[0].length);
                            const lines=new Set(Array.from(range.getClientRects()).filter(x=>x.width>.1).map(x=>Math.round(x.top)));
                            if(lines.size>1)fragmented.push(match[0]);
                        }
                        return {width:r.width,height:r.height,font:parseFloat(style.fontSize),fragmented,pageWidth:innerWidth,scrollWidth:document.documentElement.scrollWidth};
                    }''')
                    assert metrics['width'] >= 180, (width, 'headline squeezed', metrics)
                    assert metrics['font'] >= 14, (width, 'headline too small', metrics)
                    assert metrics['height'] >= 44, (width, 'headline touch target', metrics)
                    assert not metrics['fragmented'], (width, 'ordinary words fragmented', metrics)
                    assert metrics['scrollWidth'] <= width+1, (width, 'page overflow', metrics)
                    checks.append({'width':width,'technical_details':advanced,'headline':metrics})
                    for name in ['All','Active','Needs approval','Waiting','Done','Problems']:
                        button=page.get_by_role('button',name=re.compile('^'+name+r'\d'))
                        rect=button.bounding_box()
                        assert rect['height']>=44,(width,name,'small target',rect)
                        assert button.evaluate('el=>el.scrollWidth<=el.clientWidth+1'),(width,name,'clipped filter')
                    for label in ['Status','Assigned to','From','Updated','Retries' if advanced else 'Workflow']:
                        assert page.locator('tbody').get_by_text(label,exact=True).first.is_visible(),(width,label)
                    row=page.get_by_role('row').filter(has=title_button)
                    for cell in row.get_by_role('cell').all():
                        assert cell.evaluate('el=>el.scrollWidth<=el.clientWidth+1'),(width,'clipped cell',cell.inner_text())
                    assert row.get_by_text(LONG_HELPER,exact=True).count()==2
                    assert LONG_WORKFLOW in row.inner_text()
                    for control in [page.get_by_role('textbox',name='Search loaded jobs',exact=True),page.get_by_role('combobox',name='Filter by assigned role',exact=True),page.get_by_role('combobox',name='Sort jobs',exact=True),page.get_by_role('button',name='+ New job',exact=True)]:
                        assert control.bounding_box()['height']>=44,(width,'small control')
                    selector=page.get_by_role('checkbox',name='Select job '+TITLE,exact=True)
                    selector.scroll_into_view_if_needed()
                    hit=selector.evaluate("el=>(el.closest('label')||el).getBoundingClientRect().toJSON()")
                    assert hit['width']>=44 and hit['height']>=44,(width,'selection hit area',hit)
                    page.mouse.click(hit['x']+hit['width']-3,hit['y']+hit['height']-3)
                    assert selector.is_checked(),'Outer selection hit target did not select'
                    assert page.get_by_role('dialog').count()==0, 'Selection opened job'
                    assert page.get_by_role('button',name='Clear selection',exact=True).is_visible()
                    page.get_by_role('button',name='Clear selection',exact=True).click()
                    all_visible=page.get_by_role('checkbox',name='Select all visible jobs',exact=True)
                    all_visible.check()
                    selected=page.get_by_role('checkbox',name=re.compile('^Select job '))
                    assert all(box.is_checked() for box in selected.all())
                    all_visible.uncheck()
                    page.get_by_role('checkbox',name='Select job Backfill audit events to cold storage',exact=True).check()
                    page.get_by_role('combobox',name='Replacement target role',exact=True).select_option('codex')
                    replacement=page.get_by_role('button',name=re.compile('^Create replacements'))
                    assert replacement.is_visible()
                    assert page.evaluate('document.documentElement.scrollWidth<=innerWidth+1'),(width,'reassignment bar overflow')
                    page.get_by_role('button',name='Clear selection',exact=True).click()
                    title_button.focus();page.keyboard.press('Enter')
                    page.get_by_role('dialog',name='Job details',exact=True).wait_for()
                    page.keyboard.press('Escape')
                    page.wait_for_function("document.activeElement.getAttribute('aria-label')===document.querySelector('[aria-label^=\"Open job Review the community\"]').getAttribute('aria-label')")
                    checks.append({'width':width,'technical_details':advanced,'selection_and_keyboard':True})
            search=page.get_by_role('textbox',name='Search loaded jobs',exact=True)
            search.fill('there-is-no-job-with-this-title')
            page.get_by_text('No matches in the loaded window',exact=True).wait_for()
            search.fill('')
            title_button.wait_for()
            page.set_viewport_size({'width':390,'height':1000})
            search.fill('Review pull request')
            page.get_by_role('button',name='Open job Review pull request 42',exact=True).wait_for()
            page.screenshot(path=str(out/'wiki-phone.png'),full_page=True)
            search.fill('')
            page.get_by_role('combobox',name='Sort jobs',exact=True).select_option('title')
            title_button.wait_for()
            page.set_viewport_size({'width':1440,'height':1000})
            assert page.get_by_role('button',name='Sort by job',exact=True).is_visible()
            assert page.get_by_role('table').evaluate('el=>getComputedStyle(el).display')=='table'
            page.screenshot(path=str(out/'desktop.png'))
            checks.append({'desktop_table_and_search_sort':True})
            assert not errors, errors
            assert not blocked, blocked
            after={j['id']:j['status'] for j in api.get('/api/jobs').json()}
            assert before==after, 'Browser changed job state'
            browser.close()
        (out/'results.json').write_text(json.dumps({'checks':checks,'page_errors':errors,'blocked_requests':blocked},indent=2),encoding='utf-8')
        print(json.dumps({'passed':len(checks),'output':str(out)}))
    finally:
        if proc.poll() is None:
            if os.name=='nt':subprocess.run(['taskkill','/PID',str(proc.pid),'/T','/F'],capture_output=True)
            else:proc.terminate();proc.wait(timeout=10)


if __name__=='__main__':main()
