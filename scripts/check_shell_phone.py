"""Disposable rendered checks for shared phone shell, portfolio, activity and composer."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

from playwright.sync_api import sync_playwright
import capture_wiki as fixture
import build_console

ROOT=Path(__file__).resolve().parents[1]


def main():
    out=ROOT/'output/playwright/app-phone-shell';out.mkdir(parents=True,exist_ok=True)
    home=Path(tempfile.mkdtemp(prefix='bc-shell-phone-'));port=fixture.free_port();base=f'http://127.0.0.1:{port}'
    proc=fixture.start_gateway(home,port)
    issues=[];checks=[];errors=[];blocked=[]
    def check(ok,name,**detail):
        checks.append({'name':name,'passed':bool(ok),**detail})
        if not ok:issues.append(checks[-1])
    try:
        fixture.seed(base)
        temporary=home/'console.html';shutil.copyfile(build_console.HTML,temporary)
        original=build_console.HTML;build_console.HTML=temporary
        try:build_console.build()
        finally:build_console.HTML=original
        snapshot={'configured':True,'flags':[], 'projects':[{'id':'community','name':'Community illustration application','status':'active','priority':1,'health':'amber','done_definition':'The community can review and approve the illustrated grant application.','progress':{'percent':50,'done':1,'total':2},'next_milestone':'Prepare the community review','last_activity':None,'milestones':[{'title':'Community application review','status':'next'},{'title':'Publish the illustrated report','status':'planned'}],'recent_jobs':[],'open_prs':[],'repository':{}}]}
        with sync_playwright() as p:
            browser=p.chromium.launch(channel='msedge' if os.name=='nt' else None)
            context=browser.new_context(viewport={'width':320,'height':800},has_touch=True,is_mobile=True)
            def guard(route):
                request=route.request
                if not request.url.startswith(base+'/') or (request.method not in ('GET','HEAD','OPTIONS') and not (request.method=='POST' and request.url==base+'/api/schedules/preview')):
                    blocked.append({'method':request.method,'url':request.url});route.abort()
                else:route.continue_()
            context.route('**/*',guard)
            context.route(base+'/console',lambda r:r.fulfill(status=200,content_type='text/html',body=temporary.read_text(encoding='utf-8')))
            context.route(base+'/api/portfolio',lambda r:r.fulfill(json=snapshot))
            context.add_init_script('localStorage.setItem("bitcadence_conn",'+json.dumps(json.dumps({'url':base,'token':fixture.TOKEN}))+');')
            page=context.new_page();page.on('pageerror',lambda error:errors.append(str(error)))
            page.goto(base+'/console');page.wait_for_function("window.BitCadenceStore.mode()==='live'")
            for button in page.get_by_role('button',name=__import__('re').compile('^Dismiss ')).all():button.click()
            def nav(name):
                menu=page.get_by_role('button',name='Menu',exact=True)
                menu.click()
                page.get_by_role('navigation',name='Main navigation').get_by_role('button',name=name,exact=True).click()
                page.locator('main').get_by_role('heading',name=name,exact=True).first.wait_for()
            for width in [320,390,430]:
                page.set_viewport_size({'width':width,'height':800})
                for destination in ['Home','Portfolio','Job Board','Approvals','Governance','Ask for something','Helpers','Connect an AI','Schedules','Drumline','Activity','Settings','Administration']:
                    nav(destination)
                    metrics=page.locator('main').evaluate(r"""root=>{
                      const fragments=[];
                      const walker=document.createTreeWalker(root,NodeFilter.SHOW_TEXT);let node;
                      while(node=walker.nextNode()){
                        const parent=node.parentElement;
                        if(!parent.getClientRects().length||parent.closest('style,script,option,code,pre')||getComputedStyle(parent).fontFamily.includes('mono'))continue;
                        for(const word of node.textContent.matchAll(/\b[A-Za-z]{5,}\b/g)){
                          const r=document.createRange();r.setStart(node,word.index);r.setEnd(node,word.index+word[0].length);
                          if(new Set([...r.getClientRects()].map(x=>Math.round(x.top))).size>1)fragments.push(word[0]);
                        }
                      }
                      const targets=[...root.querySelectorAll('button,input:not([type=checkbox]),select,textarea')].filter(e=>e.getClientRects().length).filter(e=>{const r=e.getBoundingClientRect();return r.width<43||r.height<43}).map(e=>(e.innerText||e.placeholder||e.tagName).slice(0,60));
                      return {fits:document.documentElement.scrollWidth<=innerWidth+1,fragments:[...new Set(fragments)],targets};
                    }""")
                    check(metrics['fits'] and not metrics['fragments'] and not metrics['targets'],'actual route readable and usable',width=width,destination=destination,**metrics)
                nav('Portfolio')
                heading=page.get_by_role('heading',name='Community illustration application',exact=True)
                heading.wait_for()
                check(heading.bounding_box()['width']>=180,'portfolio headline width',width=width,actual=heading.bounding_box()['width'])
                heading.click()
                check(page.get_by_text('Community application review',exact=True).is_visible(),'portfolio expanded milestone',width=width)
                nav('Activity')
                title=page.locator('tbody').get_by_role('button',name=__import__('re').compile('^Open job ')).first
                title.wait_for()
                check(title.bounding_box()['width']>=180,'activity title width',width=width,actual=title.bounding_box()['width'])
                check(page.evaluate('document.documentElement.scrollWidth<=innerWidth+1'),'activity fits page',width=width)
                page.screenshot(path=str(out/f'activity-{width}.png'),full_page=True)
                nav('Job Board')
                page.get_by_role('button',name='+ New job',exact=True).click()
                dialog=page.get_by_role('dialog',name='New job',exact=True);dialog.wait_for()
                bounds=dialog.bounding_box()
                for button in dialog.locator('button[aria-pressed]').all():
                    b=button.bounding_box()
                    check(b['x']>=bounds['x'] and b['x']+b['width']<=bounds['x']+bounds['width']+1,'composer role inside panel',width=width,role=button.inner_text())
                    check(b['height']>=44,'composer role target',width=width,actual=b['height'])
                check(dialog.locator('input#bc-job-title').bounding_box()['width']>=180,'composer title field width',width=width)
                page.set_viewport_size({'width':width,'height':400})
                dialog.locator('input#bc-job-title').fill('Draft a community review report')
                dialog.get_by_role('button',name='Cancel',exact=True).scroll_into_view_if_needed()
                check(dialog.get_by_role('button',name='Cancel',exact=True).is_visible(),'short composer cancel reachable',width=width)
                create=dialog.get_by_role('button',name='Create job',exact=True)
                check(create.evaluate('el=>{const r=el.getBoundingClientRect(),hit=document.elementFromPoint(r.x+r.width/2,r.y+r.height/2);return !!hit&&(hit===el||el.contains(hit));}'),'create action not obscured by notifications',width=width)
                page.keyboard.press('Escape');dialog.wait_for(state='hidden')
                menu=page.get_by_role('button',name='Menu',exact=True)
                check(menu.bounding_box()['height']>=44,'menu hit target',width=width,actual=menu.bounding_box()['height'])
                menu.click()
                check(page.evaluate("!!document.activeElement.closest('nav')"),'menu receives keyboard focus',width=width)
                for _ in range(18):page.keyboard.press('Tab')
                check(page.evaluate("!!document.activeElement.closest('nav')"),'menu contains keyboard focus',width=width)
                page.keyboard.press('Escape')
                page.wait_for_function("document.querySelector('[data-bc-menu]').getAttribute('aria-expanded')==='false'")
                check(menu.get_attribute('aria-expanded')=='false','menu Escape closes',width=width)
            check(not errors,'no page errors',errors=errors)
            check(not blocked,'no unexpected mutations or requests',requests=blocked)
            browser.close()
        (out/'results.json').write_text(json.dumps({'checks':checks,'issues':issues},indent=2),encoding='utf-8')
        print(json.dumps({'checks':len(checks),'failures':len(issues),'issues':issues[:12]}))
        if issues:raise SystemExit(1)
    finally:
        if proc.poll() is None:
            if os.name=='nt':subprocess.run(['taskkill','/PID',str(proc.pid),'/T','/F'],capture_output=True)
            else:proc.terminate();proc.wait(timeout=10)


if __name__=='__main__':main()
