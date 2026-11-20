"""Execute the shipped adapter to catch connection races and offline controls."""
from pathlib import Path
import subprocess


ROOT = Path(__file__).parents[1]


def test_live_adapter_connection_lifecycle():
    script = r'''
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');
const source = fs.readFileSync('src/mco/console_src/47e66145-9c4d-41a1-acbb-42b12848f160.js', 'utf8');
const flush = () => new Promise(resolve => setImmediate(resolve));
function setup() {
  const calls = [], intervals = [], timeouts = [], events = {};
  const approval = {jobs:[{id:'old-decision',status:'needs_approval'}],truncated:false};
  const demo = {subscribe(){}, onToast(){}, stopSim(){}, getJobs(){return [{id:'demo'}];}};
  const context = {window:{BitCadenceStore:demo}, location:{protocol:'http:',origin:'http://gateway'},
    document:{hidden:false,addEventListener:(name, fn)=>events[name]=fn},
    localStorage:{getItem(){return null},setItem(){},removeItem(){}},
    AbortController, URL, console,
    setTimeout:(fn, ms)=>{timeouts.push({fn,ms});return timeouts.length},clearTimeout(){},
    setInterval:(fn, ms)=>{intervals.push({fn,ms});return intervals.length},clearInterval(){},
    fetch:(url, opts)=>url.endsWith('/api/jobs/approval-view')
      ? approval.error ? Promise.reject(new Error(approval.error)) : Promise.resolve({ok:true,json:async()=>approval})
      : new Promise((resolve,reject)=>{
      const call = {url,opts,ok:data=>resolve({ok:true,json:async()=>data}),fail:()=>reject(new Error('offline'))};
      opts.signal.addEventListener('abort',()=>reject(new Error('aborted')));
      calls.push(call);
    })};
  vm.runInNewContext(source.replace('window.BitCadenceStore = facade;', 'window.BitCadenceStore = facade; window.testApi = api;'),context);
  return {store:context.window.BitCadenceStore,api:context.window.testApi,calls,intervals,timeouts,events,approval,document:context.document};
}
async function connect(h) {
  const connecting=h.store.connect('http://gateway','token');
  h.calls.at(-1).ok([]); await flush();
  h.calls.at(-2).ok([{id:'old',status:'pending'}]);h.calls.at(-1).ok([]);
  assert.equal(await connecting,true);
}
(async()=>{
  const h=setup();await connect(h);
  assert.equal(h.store.getApprovalJobs()[0].id,'old-decision','older decisions come from their dedicated queue');
  assert.equal(h.store.getApprovalCoverage().loaded,true);
  const tick=h.intervals.at(-1).fn;
  tick();tick();tick();
  assert.equal(h.calls.length,5,'event bursts share a single refresh');
  h.calls.at(-2).ok([{id:'first',status:'pending'}]);h.calls.at(-1).ok([]);await flush();
  assert.equal(h.calls.length,7,'one trailing refresh retains newer event hints');
  h.store.disconnect();h.calls.at(-2).ok([{id:'stale',status:'pending'}]);h.calls.at(-1).ok([]);await flush();
  assert.equal(h.store.mode(),'demo');assert.equal(h.store.getJobs()[0].id,'demo');

  const broken=setup();const pending=broken.store.connect('http://gateway','token');
  broken.calls[0].ok([]);await flush();broken.calls[1].fail();broken.calls[2].ok([]);
  assert.equal(await pending,false,'initial board refresh failure cannot claim connection success');
  assert.equal(broken.store.mode(),'offline');
  const pause=broken.store.pauseAutonomy();assert.match(broken.calls.at(-1).url,/autonomy\/pause$/);
  broken.calls.at(-1).fail();await assert.rejects(pause,/offline/);

  const timed=setup();const waiting=timed.store.connect('http://gateway','token');
  timed.timeouts[0].fn();assert.equal(await waiting,false);assert.match(timed.store.lastError(),/timed out/);
  const signaled=setup(), caller=new AbortController();const read=signaled.api('/read',{signal:caller.signal});
  signaled.timeouts[0].fn();await assert.rejects(read,/timed out/);
  const cancelled=setup(), cancellation=new AbortController();const cancelledRead=cancelled.api('/read',{signal:cancellation.signal});
  cancellation.abort();await assert.rejects(cancelledRead,/aborted/);

  const switching=setup();const oldConnect=switching.store.connect('http://old','old');
  const newConnect=switching.store.connect('http://new','new');
  switching.calls[1].ok([]);await flush();switching.calls[2].ok([{id:'new',status:'pending'}]);switching.calls[3].ok([]);
  assert.equal(await newConnect,true);switching.calls[0].ok([]);assert.equal(await oldConnect,false);
  assert.equal(switching.store.config().url,'http://new');assert.equal(switching.store.getJobs()[0].id,'new');
  const oldPortfolio=switching.store.refreshPortfolio(true);const portfolioCall=switching.calls.at(-1);
  const oldProjects=switching.store.refreshProjectView(true);const projectCall=switching.calls.at(-1);
  switching.store.disconnect();portfolioCall.ok({private:'old'});projectCall.ok({jobs:[{id:'private'}]});
  await Promise.all([oldPortfolio,oldProjects]);assert.equal(switching.store.getPortfolio(),null);
  assert.equal(switching.store.getProjectJobs()[0].id,'demo');

  const visible=setup();await connect(visible);visible.document.hidden=true;
  const count=visible.calls.length;visible.intervals.at(-1).fn();assert.equal(visible.calls.length,count);
  visible.document.hidden=false;visible.events.visibilitychange();assert.equal(visible.calls.length,count+2);
  visible.calls.at(-2).ok([]);visible.calls.at(-1).ok([]);await flush();
  visible.approval.error='queue unavailable';const retry=visible.store.refreshApprovals();
  visible.calls.at(-2).ok([]);visible.calls.at(-1).ok([]);await retry;
  assert.equal(visible.store.getApprovalJobs()[0].id,'old-decision');
  assert.equal(visible.store.getApprovalCoverage().error,'queue unavailable');
})().catch(error=>{console.error(error);process.exitCode=1});
'''
    result = subprocess.run(["node", "-e", script], cwd=ROOT, capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr


def test_job_search_matches_ids_projects_and_multiple_words():
    source = (ROOT / "src/mco/console_src/5adac14f-6645-4e02-866b-22c4e571989b.js").read_text(encoding="utf-8")
    helpers = source[source.index("const JOB_FILTERS"):source.index("// ----- Portfolio -----")]
    script = helpers + r'''
const assert = require('node:assert/strict');
const job={id:'abc-123',title:'Render prayer',target_agent_role:'builder',input_payload:{project:'Saints Studio'}};
assert.equal(jobMatchesSearch(job,'123 saints'),true);
assert.equal(jobMatchesSearch(job,'prayer builder'),true);
assert.equal(jobMatchesSearch(job,'missing saints'),false);
assert.equal(jobMatchesSearch({},'   '),true);
global.sessionStorage={getItem(){return '{"filter":"removed","role":4,"query":"saints"}';}};
assert.deepEqual(savedJobView(),{filter:'all',role:'all',query:'saints'});
'''
    result = subprocess.run(["node", "-e", script], cwd=ROOT, capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr


def test_screen_polling_pauses_in_background_and_cleans_up():
    source = (ROOT / "src/mco/console_src/32c20ad6-cc53-43b3-a432-3a75e249b495.js").read_text(encoding="utf-8")
    helper = source[source.index("function visibleInterval("):source.index("// ----- Copy tone")]
    script = helper + r'''
const assert = require('node:assert/strict');
let calls=0, interval, event, cleared=false;
global.document={hidden:true,addEventListener:(name,fn)=>event=fn,removeEventListener:(name,fn)=>{assert.equal(fn,event);event=null;}};
global.setInterval=fn=>{interval=fn;return 9;};global.clearInterval=id=>{assert.equal(id,9);cleared=true;};
const stop=visibleInterval(()=>calls++,1000);
interval();assert.equal(calls,0);
document.hidden=false;event();assert.equal(calls,1);
interval();assert.equal(calls,2);
document.hidden=true;interval();assert.equal(calls,2);
stop();assert.equal(cleared,true);assert.equal(event,null);
'''
    result = subprocess.run(["node", "-e", script], cwd=ROOT, capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
