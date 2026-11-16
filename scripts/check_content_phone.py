"""Synthetic component phone audit. Disposable gateway; no provider or live mutations."""
import json, os, sys, tempfile
from pathlib import Path
import httpx
from playwright.sync_api import sync_playwright
import capture_wiki as cap
import build_console as build

ROOT=Path(__file__).resolve().parents[1]
os.environ['PYTHONPATH']=str(ROOT/'src')
METRICS=r'''() => {
 const root=document.querySelector('#phone-audit');
 const controls=[...root.querySelectorAll('button,input:not([type=checkbox]),select,textarea,summary')].filter(e=>e.getClientRects().length);
 const bad=controls.filter(e=>(e.getBoundingClientRect().height<43||e.getBoundingClientRect().width<43)).map(e=>({text:(e.innerText||e.placeholder||e.tagName).slice(0,60),h:e.getBoundingClientRect().height}));
 const overflow=[...root.querySelectorAll('*')].filter(e=>{const r=e.getBoundingClientRect();return r.width>0&&(r.right>innerWidth+1||r.left< -1)&&!e.closest('.score-stages')}).map(e=>({tag:e.tagName,text:e.innerText?.slice(0,55),w:e.getBoundingClientRect().width})).slice(0,15);
 const fragments=[];const walker=document.createTreeWalker(root,NodeFilter.SHOW_TEXT);let n;
 while(n=walker.nextNode()){if(!n.parentElement.getClientRects().length||n.parentElement.closest('style,script,option'))continue;for(const m of n.textContent.matchAll(/[A-Za-z]{5,}/g)){const r=document.createRange();r.setStart(n,m.index);r.setEnd(n,m.index+m[0].length);if(new Set([...r.getClientRects()].map(rect=>Math.round(rect.top))).size>1)fragments.push(m[0]);}}
 return {bad,overflow,fragments:[...new Set(fragments)].slice(0,20)};
}'''

def main():
 out=ROOT/'output/playwright/app-phone-content';out.mkdir(parents=True,exist_ok=True)
 os.environ.setdefault('CONTENT_PHONE_SHOTS',str(out))
 home=Path(tempfile.mkdtemp(prefix='content-phone-'));port=cap.free_port();proc=cap.start_gateway(home,port)
 base=f'http://127.0.0.1:{port}'
 try:
  cap.seed(base)
  html=home/'console.html';html.write_bytes(build.HTML.read_bytes());build.HTML=html;build.build()
  with sync_playwright() as p:
   browser=p.chromium.launch(channel="msedge" if os.name=='nt' else None);page=browser.new_page(viewport={'width':320,'height':740})
   blocked=[]
   def guard(route):
    req=route.request
    if not req.url.startswith(base) and not req.url.startswith(('blob:','data:')):blocked.append(req.url);return route.abort()
    if req.method not in ('GET','HEAD','OPTIONS'):blocked.append(req.method+' '+req.url);return route.abort()
    if req.url==base+'/console':return route.fulfill(body=html.read_text(encoding='utf8'),content_type='text/html')
    return route.continue_()
   page.route('**/*',guard);page.goto(base+'/console');page.wait_for_function('!!(window.BitCadenceStore && window.Approvals)')
   page.get_by_role('button',name='See all work',exact=True).click()
   page.wait_for_function("localStorage.getItem('bitcadence_page') === 'jobs'")
   page.evaluate('''() => {
    window.fixtureJobs=[{id:'synthetic-job-approval',title:'Review the scheduled deployment and dependency verification report',description:'Check the deployment evidence before approving any work.',target_agent_role:'deploy',source_agent_id:'synthetic-operator-with-a-long-identifier',status:'needs_approval',created_at:new Date().toISOString()}];
    fixtureJobs.push({...fixtureJobs[0],id:'synthetic-approved',status:'completed',approved_by:'synthetic-operator-with-a-long-identifier',updated_at:new Date().toISOString()});
    window.fixtureMemory={id:'synthetic-memory',kind:'decision',title:'Deployment requires independent verification and approval',content:'Remember the approved deployment process and preserve existing ownership. '+ 'Verification '.repeat(30),created_by:'synthetic-author-with-a-long-identifier',created_at:new Date().toISOString(),tags:['verification','synthetic-long-tag-identifier'],source_job_id:'synthetic-source-job-identifier',org_id:'synthetic-organization-identifier'};
    window.fixtureExchange={id:'synthetic-thread',thread_id:'synthetic-thread',job_id:'synthetic-job-approval',kind:'decision',body:'Review the deployment verification evidence before making the final decision.',created_at:new Date().toISOString(),author_id:'synthetic-author'};
    Object.assign(window.BitCadenceStore,{mode:()=> 'live',getJobs:()=>fixtureJobs,getContext:async()=>[fixtureMemory],batchAction:async()=>({succeeded:[],failed:{'synthetic-job-approval':'Synthetic decision failure: approval permission remains unchanged.'}}),getRecentEvents:async()=>[{id:'synthetic-event',event:'approved',job_id:'synthetic-approved',job_title:fixtureJobs[0].title,actor_id:'synthetic-operator-with-a-long-identifier',created_at:new Date().toISOString(),detail:{reason:'Synthetic decision evidence'}}],getExchanges:async()=>({items:[fixtureExchange]}),getExchange:async()=>({thread:[fixtureExchange],promotions:[]}),onExchange:()=>()=>{},getScoreRun:async()=>({title:'Deployment verification',tasks:[{id:'verify',title:'Independent verification'},{id:'review',title:'Review the deployment'}],dispatch:[{task:'verify',job_id:'synthetic-approved',status:'accepted',phase:'work'}]})});
    const shellStyle=document.querySelector('#root style');if(shellStyle)document.head.append(shellStyle.cloneNode(true));
    document.querySelector('#root').remove();const el=document.createElement('main');el.id='phone-audit';el.className='bc-main-content';el.style.padding='14px';document.body.append(el);window.auditRoot=ReactDOM.createRoot(el);
    localStorage.setItem('bitcadence_exchange_job',fixtureJobs[0].id);localStorage.setItem('bitcadence_exchange_thread','synthetic-thread');
    window.mountAudit=(name)=>auditRoot.render(React.createElement(window[name],{key:name,jobs:fixtureJobs,agents:[],tone:'plain',advanced:true,onOpen:()=>{},onNav:()=>{},onWatch:()=>{},onBack:()=>{},runId:'synthetic-run',onClose:()=>auditRoot.render(null),onOpenJob:()=>{},entry:fixtureMemory}));
   }''')
   results=[]
   for width in (320,390,430):
    page.set_viewport_size({'width':width,'height':740})
    for name in ('Home','Approvals','Governance','DrumlineMemory','AgentExchange','ScoreLive','MemoryDetailDrawer'):
     page.evaluate('(n)=>mountAudit(n)',name);page.wait_for_timeout(120)
     if name=='Approvals':
      page.get_by_label('Select all',exact=False).check()
      page.get_by_role('button',name='✓ Approve (1)',exact=True).click()
      page.get_by_text('0 succeeded; 1 failed.',exact=False).wait_for()
     if name=='Governance':
      page.get_by_label('Start',exact=True).fill('2026-11-01');page.get_by_label('End',exact=True).fill('2026-11-13')
     if name=='DrumlineMemory':page.get_by_role('button',name='+ Add memory').click()
     metric=page.evaluate(METRICS)
     if os.environ.get('CONTENT_PHONE_SHOTS') and width==320 and name in ('Approvals','Governance','DrumlineMemory','AgentExchange','MemoryDetailDrawer'):
      out=Path(os.environ['CONTENT_PHONE_SHOTS']);out.mkdir(parents=True,exist_ok=True);page.screenshot(path=str(out/(name+'.png')),full_page=True)
     results.append({'width':width,'screen':name,**metric})
     if name=='AgentExchange':
      page.get_by_role('button',name='Reply',exact=False).first.click();results.append({'width':width,'screen':'Exchange reply',**page.evaluate(METRICS)})
      page.get_by_role('button',name='Cancel reply',exact=False).click()
      page.get_by_role('button',name='Promote to context',exact=True).click();results.append({'width':width,'screen':'Exchange promotion',**page.evaluate(METRICS)})
      page.get_by_role('button',name='Cancel',exact=True).click()
     if name=='MemoryDetailDrawer':
      page.set_viewport_size({'width':width,'height':420});page.get_by_role('button',name='Open Source Job',exact=False).scroll_into_view_if_needed();page.get_by_role('button',name='Open Source Job',exact=False).focus();page.keyboard.press('Escape')
      page.wait_for_function("document.querySelector('#phone-audit').childElementCount === 0")
    page.set_viewport_size({'width':width,'height':740})
   (out/'results.json').write_text(json.dumps({'results':results,'blocked':blocked},indent=2),encoding='utf-8')
   print(json.dumps({'results':results,'blocked':blocked},indent=2));browser.close()
   assert not blocked, blocked
   assert not [r for r in results if r['bad'] or r['overflow'] or r['fragments']], 'Phone layout failures in results above'
 finally:
  if proc.poll() is None:
   if os.name=='nt':__import__('subprocess').run(['taskkill','/PID',str(proc.pid),'/T','/F'],capture_output=True)
   else:proc.terminate();proc.wait(timeout=20)
if __name__=='__main__':main()
