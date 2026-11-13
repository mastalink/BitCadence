"""Exercise the console in a throwaway local gateway with synthetic jobs only."""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import capture_wiki as fixture

ROOT=Path(__file__).resolve().parents[1]

def main():
    from playwright.sync_api import sync_playwright
    out=ROOT/'output/ux-release';out.mkdir(parents=True,exist_ok=True)
    home=Path(tempfile.mkdtemp(prefix='bitcadence-ux-fixture-'))
    port=fixture.free_port();base=f'http://127.0.0.1:{port}'
    proc=fixture.start_gateway(home,port)
    results=[];errors=[];blocked=[]
    try:
        fixture.seed(base)
        with sync_playwright() as p:
            browser=p.chromium.launch(channel='msedge' if os.name=='nt' else None)
            context=browser.new_context(viewport={'width':1440,'height':1000})
            def guard(route):
                url=route.request.url
                if '/api/' in url and not url.startswith(base+'/'):
                    blocked.append(url);route.abort()
                else:route.continue_()
            context.route('**/*',guard)
            page=context.new_page();page.on('pageerror',lambda e:errors.append(str(e)))
            page.goto(base+'/console');page.get_by_role('button',name='Connect to server',exact=True).click()
            main=page.locator('main')
            main.locator('input').first.fill(base)
            main.locator('input[type=password]').first.fill(fixture.TOKEN)
            assert main.locator('input').first.input_value()==base
            main.get_by_role('button',name='Connect',exact=True).click()
            page.wait_for_function("window.BitCadenceStore.mode()==='live'")
            results.append('fixture-only authenticated connection')
            nav=page.get_by_role('navigation',name='Main navigation')
            for name in ['Home','Portfolio','Job Board','Approvals','Governance','Ask for something','Helpers','Connect an AI','Schedules','Drumline','Activity','Settings','Administration']:
                nav.get_by_role('button',name=name,exact=True).click()
                page.locator('main').get_by_role('heading',name=name,exact=True).first.wait_for()
                results.append('desktop navigation '+name)
            page.screenshot(path=str(out/'administration-desktop.png'))
            with context.expect_page() as opened:
                page.get_by_role('link',name='Open model connections').click()
            admin=opened.value;admin.wait_for_url('**/dashboard#models')
            admin.locator('#view-models.on').wait_for();assert 'token=' not in admin.url
            results.append('Administration deep link and same-gateway sign-in')
            admin.get_by_role('button',name='+ Add connection',exact=True).click()
            native=admin.get_by_role('dialog',name='Add a model connection',exact=True)
            native.wait_for()
            for _ in range(15):
                admin.keyboard.press('Tab');assert admin.evaluate("!!document.activeElement.closest('[role=dialog]')")
            admin.keyboard.press('Escape');native.wait_for(state='hidden')
            assert admin.evaluate("document.activeElement.textContent.includes('Add connection')")
            results.append('Administration modal keyboard trap, Escape and focus return')
            admin.close()
            nav.get_by_role('button',name='Job Board',exact=True).click()
            opener=main.get_by_role('button',name='+ New job',exact=True).first
            opener.click();dialog=page.get_by_role('dialog',name='New job',exact=True);dialog.wait_for()
            assert page.evaluate("!!document.activeElement.closest('[role=dialog]')")
            for _ in range(18):
                page.keyboard.press('Tab');assert page.evaluate("!!document.activeElement.closest('[role=dialog]')")
            page.keyboard.press('Escape');dialog.wait_for(state='hidden')
            page.wait_for_function("document.activeElement.tagName==='BUTTON' && document.activeElement.textContent.includes('New job')")
            results.append('drawer keyboard focus trap, Escape and focus return')
            page.get_by_text('Review pull request 42',exact=True).first.click()
            page.get_by_role('dialog',name='Job details',exact=True).wait_for()
            page.keyboard.press('Escape')
            results.append('job details open by native control')
            page.set_viewport_size({'width':390,'height':844})
            for name in ['Home','Job Board','Approvals','Settings','Administration']:
                page.get_by_role('button',name='Menu',exact=True).click()
                nav.get_by_role('button',name=name,exact=True).click()
                page.locator('main').get_by_role('heading',name=name,exact=True).first.wait_for()
                dims=page.evaluate('({width:innerWidth,scroll:document.documentElement.scrollWidth})')
                assert dims['scroll']<=dims['width']+1,(name,dims)
                results.append('390px no page overflow '+name)
            page.screenshot(path=str(out/'administration-phone.png'))
            page.set_viewport_size({'width':390,'height':400})
            page.get_by_role('button',name='Menu',exact=True).click()
            nav.get_by_role('button',name='Administration',exact=True).scroll_into_view_if_needed()
            nav.get_by_role('button',name='Administration',exact=True).click()
            results.append('short-screen navigation reaches Administration')
            page.get_by_role('button',name='Menu',exact=True).click()
            nav.get_by_role('button',name='Home',exact=True).click()
            page.set_viewport_size({'width':320,'height':568})
            for name in ['Home','Job Board','Approvals','Settings','Administration']:
                page.get_by_role('button',name='Menu',exact=True).click()
                nav.get_by_role('button',name=name,exact=True).click()
                dims=page.evaluate('({width:innerWidth,scroll:document.documentElement.scrollWidth})')
                assert dims['scroll']<=dims['width']+1,(name,dims)
                results.append('320px no page overflow '+name)
            browser.close()
        assert not blocked,blocked
        assert not errors,errors
        (out/'results.json').write_text(json.dumps({'checks':results,'page_errors':errors,'blocked_nonfixture_requests':blocked},indent=2),encoding='utf-8')
        print(json.dumps({'passed':len(results),'errors':errors,'output':str(out)},indent=2))
    except Exception:
        print(json.dumps({'completed':results,'page_errors':errors,'blocked_nonfixture_requests':blocked}),flush=True)
        raise
    finally:
        if proc.poll() is None:
            if os.name=='nt':subprocess.run(['taskkill','/PID',str(proc.pid),'/T','/F'],capture_output=True)
            else:proc.terminate();proc.wait(timeout=10)

if __name__=='__main__':main()
