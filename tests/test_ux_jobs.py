"""Run actual console adapter and JSX handlers against in-memory Node fixtures."""
import base64
import gzip
import json
from pathlib import Path
import re
import shutil
import subprocess

import pytest

ROOT = Path(__file__).parents[1]
SRC = ROOT / "src/mco/console_src"
ADAPTER = SRC / "47e66145-9c4d-41a1-acbb-42b12848f160.js"
JOBS = SRC / "5adac14f-6645-4e02-866b-22c4e571989b.js"


@pytest.fixture(scope="module")
def js_fixture(tmp_path_factory):
    directory = tmp_path_factory.mktemp("ux-jobs-node")
    html = (ROOT / "src/mco/static/console.html").read_text(encoding="utf-8")
    manifest = json.loads(re.search(r'<script type="__bundler/manifest">(.*?)</script>', html, re.S)[1])
    for entry in manifest.values():
        if not entry["mime"].startswith(("text/", "application/")):
            continue
        data = base64.b64decode(entry["data"])
        if entry.get("compressed"):
            data = gzip.decompress(data)
        if b"@babel/standalone" in data or b"Babel =" in data or b"Babel=" in data:
            path = directory / "babel.js"
            path.write_bytes(data)
            return path
    pytest.fail("Bundled Babel not found")


def run_js(js_fixture, code):
    node = shutil.which("node")
    if not node:
        pytest.skip("Node required for console regression checks")
    bootstrap = r'''
const fs=require('fs'),vm=require('vm'),assert=require('assert/strict');
const input=JSON.parse(fs.readFileSync(0,'utf8'));
const hooks=[];let cursor=0; const effects=[];let effectTasks=[];
const h=(type,props,...children)=>({type,props:props||{},children:children.flat(Infinity).filter(x=>x!==null&&x!==false&&x!==undefined)});
const React={createElement:h,Fragment:'Fragment',useState(init){const i=cursor++;if(!(i in hooks))hooks[i]=typeof init==='function'?init():init;return[hooks[i],v=>hooks[i]=typeof v==='function'?v(hooks[i]):v]},useMemo:f=>f(),useEffect(callback,deps){const i=cursor++;if(!effects[i]||deps.some((d,j)=>d!==effects[i].deps[j]))effectTasks.push(()=>{effects[i]?.cleanup?.();effects[i]={deps,cleanup:callback()}})},useRef(init){const i=cursor++;if(!(i in hooks))hooks[i]={current:init};return hooks[i]}};
const all=n=>n&&typeof n==='object'?[n,...n.children.flatMap(all)]:[];
const text=n=>n&&typeof n==='object'?n.children.map(text).join(''):String(n??'');
const find=(tree,label)=>all(tree).find(n=>n.type==='Btn'&&text(n)===label);
const storage={getItem:()=>null,setItem(){},removeItem(){}};
const demo={subscribe(){},onToast(){},stopSim(){},getJobs:()=>[],getAgents:()=>[]};
let response={success:true,succeeded:['a'],failed:{b:'scope denied'}},requests=[],toasts=[],events=[];
const sandbox={React,window:{BitCadenceStore:demo,dispatchEvent:e=>events.push(e)},localStorage:storage,location:{protocol:'http:',origin:'http://fixture'},CustomEvent:function(type,opts){this.type=type;this.detail=opts?.detail},setInterval:()=>1,clearInterval(){},setTimeout:()=>1,clearTimeout(){},console,
fetch:async(path,opts={})=>{requests.push({path,opts});return{ok:true,status:200,json:async()=>path==='/api/agents'?[]:path.startsWith('/api/jobs?')?[]:response}},
};
for(const n of ['Btn','Card','EmptyState','StatusBadge','RoleChip','Td','Mono','SectionTitle','AuditTrail','StatCard','THead'])sandbox[n]=n;
sandbox.shortId=x=>x;sandbox.timeAgo=x=>x;sandbox.eventLabel=x=>x;
vm.createContext(sandbox);
vm.runInContext(input.adapter,sandbox);
const store=sandbox.window.BitCadenceStore;store.onToast(t=>toasts.push(t));
vm.runInContext(fs.readFileSync(input.babel,'utf8'),sandbox);
vm.runInContext(sandbox.Babel.transform(input.jobs,{presets:['react']}).code,sandbox);
function render(name,props){cursor=0;const tree=sandbox.window[name](props);const tasks=effectTasks;effectTasks=[];tasks.forEach(fn=>fn());return tree}
function reset(){effects.forEach(e=>e?.cleanup?.());effects.length=0;hooks.length=0;cursor=0}
(async()=>{ CODE })().catch(e=>{console.error(e);process.exitCode=1});
'''.replace("CODE", code)
    result = subprocess.run([node, "-e", bootstrap], input=json.dumps({"adapter": ADAPTER.read_text(encoding="utf-8"), "jobs": JOBS.read_text(encoding="utf-8"), "babel": str(js_fixture)}), capture_output=True, text=True, encoding="utf-8", cwd=ROOT)
    assert result.returncode == 0, result.stdout + result.stderr


def test_batch_counts_schema_and_failed_ids(js_fixture):
    run_js(js_fixture, """
await store.connect('', 'fixture');
const result=await store.batchAction('reassign',['a','b'],{target_agent_role:'codex'});
assert.equal(result.succeeded.length,1);assert.equal(Object.keys(result.failed).length,1);
assert.equal(toasts.at(-1).body,'Completed: 1 succeeded, 1 failed.');
assert.notEqual(toasts.at(-1).kind,'ok');
assert.equal(JSON.parse(requests.find(r=>r.path.endsWith('batch-action')).opts.body).target_agent_role,'codex');
""")


@pytest.mark.parametrize("detail,kind", [("jobs:approve scope required", "scope"), ("Your role is not permitted to reassign jobs", "authority"), ({"code": "tenant_boundary", "message": "Different organization"}, "tenant"), ({"code": "policy_denied", "message": "Policy denies this action"}, "policy")])
def test_denial_is_typed_and_retains_true_reason(js_fixture, detail, kind):
    run_js(js_fixture, f"""
await store.connect('', 'fixture');
sandbox.fetch=async()=>({{ok:false,status:403,json:async()=>({{detail:{json.dumps(detail)}}})}});
let error;try{{await store.reject('a','owner','reason')}}catch(e){{error=e}}
assert.ok(error,'mutation failure must reject');assert.equal(error.status,403);assert.equal(error.denialKind,{json.dumps(kind)});
assert.ok(!toasts.at(-1).body.includes('bitcadence fix'));
assert.ok(!toasts.at(-1).body.includes('[object Object]'));
""")


def test_single_reassign_schema_and_replacement(js_fixture):
    run_js(js_fixture, """
await store.connect('', 'fixture'); response={job:{id:'replacement',target_agent_role:'codex'}};
const result=await store.reassignJob('old','codex','worker');
const body=JSON.parse(requests.find(r=>r.path==='/api/jobs/old/reassign').opts.body);
assert.equal(body.target_agent_role,'codex');assert.equal(body.target_agent_id,'worker');assert.ok(!('to_role' in body));
assert.equal(result.job.id,'replacement');assert.match(toasts.at(-1).body,/replacement/);
""")


def test_batch_only_eligible_visible_ids_and_retains_failures(js_fixture):
    run_js(js_fixture, """
const jobs=[{id:'a',title:'A',status:'failed',target_agent_role:'codex'},{id:'b',title:'B',status:'pending',target_agent_role:'codex'},{id:'c',title:'C',status:'failed',target_agent_role:'gemini'}];
let received; sandbox.window.BitCadenceStore={batchAction:async(action,ids)=>{received=ids;return{succeeded:[],failed:{a:'denied'}}},mode:()=> 'live'};
const props={jobs,tone:'plain',advanced:false,onOpen(){}};
let tree=render('JobBoard',props);for(const id of ['a','b','c']){all(tree).find(n=>n.type==='input'&&n.props['aria-label']==='Select job '+id.toUpperCase()).props.onChange();tree=render('JobBoard',props)}
all(tree).find(n=>n.type==='select'&&n.props['aria-label']==='Filter by assigned role').props.onChange({target:{value:'codex'}});tree=render('JobBoard',props);
assert.match(text(tree),/1 hidden/);assert.match(text(tree),/skipped/);
await find(tree,'Retry (1)').props.onClick();assert.deepEqual(Array.from(received),['a']);
tree=render('JobBoard',props);assert.match(text(tree),/denied/);assert.ok(all(tree).find(n=>n.type==='input'&&n.props['aria-label']==='Select job A').props.checked);
""")


def test_detail_failure_retains_draft_and_blocks_double_submit(js_fixture):
    run_js(js_fixture, """
let reject,calls=0; sandbox.window.BitCadenceStore={getAgents:()=>[],reject:()=>{calls++;return new Promise((_,no)=>reject=no)}};
const props={jobId:'a',jobs:[{id:'a',title:'A',status:'needs_approval'}],tone:'plain',onClose(){}};
let tree=render('JobDetail',props);find(tree,'Reject…').props.onClick();tree=render('JobDetail',props);
all(tree).find(n=>n.type==='input').props.onChange({target:{value:'Keep this explanation'}});tree=render('JobDetail',props);
const first=find(tree,'Reject').props.onClick();const second=find(tree,'Reject').props.onClick();await Promise.resolve();assert.equal(calls,1);
reject(new Error('Policy denied'));await first;await second;tree=render('JobDetail',props);
assert.match(text(tree),/Policy denied/);assert.equal(all(tree).find(n=>n.type==='input').props.value,'Keep this explanation');assert.ok(find(tree,'Reject'));
""")


@pytest.mark.parametrize("status,eligible", [("failed", True), ("rejected", True), ("cancelled", True), ("halted", True), ("in_progress", False), ("completed", False)])
def test_reassignment_only_on_eligible_terminal_jobs(js_fixture, status, eligible):
    run_js(js_fixture, f"""
sandbox.window.BitCadenceStore={{getAgents:()=>[]}};
const tree=render('JobDetail',{{jobId:'a',jobs:[{{id:'a',title:'A',status:{json.dumps(status)}}}],tone:'plain',onClose(){{}}}});
assert.equal(Boolean(find(tree,'⇄ Reassign…')),{str(eligible).lower()});
""")


def test_job_open_and_sort_are_native_buttons_and_coverage_honest(js_fixture):
    run_js(js_fixture, """
sandbox.window.BitCadenceStore={mode:()=> 'live'};let opened;
const tree=render('JobBoard',{jobs:[{id:'a',title:'Alpha',status:'pending'}],onOpen:id=>opened=id});
const open=all(tree).find(n=>n.type==='button'&&n.props['aria-label']==='Open job Alpha');assert.ok(open);open.props.onClick({stopPropagation(){}});assert.equal(opened,'a');
assert.ok(all(tree).some(n=>n.type==='button'&&n.props['aria-label']==='Sort by job'));
assert.match(text(tree),/latest.*200/);assert.match(text(tree),/loaded/);
""")


def test_creation_failure_retains_values_and_prevents_double_submit(js_fixture):
    run_js(js_fixture, """
let no,calls=0,closed=0;sandbox.window.BitCadenceStore={getAgents:()=>[{role:'codex'}],createJob:()=>{calls++;return new Promise((_,reject)=>no=reject)}};
const props={tone:'plain',onClose:()=>closed++};let tree=render('NewJobForm',props);
all(tree).find(n=>n.type==='input'&&n.props.id==='bc-job-title').props.onChange({target:{value:'Keep my title'}});tree=render('NewJobForm',props);
const one=find(tree,'Create job').props.onClick();const two=find(tree,'Create job').props.onClick();assert.equal(calls,1);
no(new Error('Creation denied'));await one;await two;tree=render('NewJobForm',props);
assert.equal(closed,0);assert.match(text(tree),/Creation denied/);assert.equal(all(tree).find(n=>n.props.id==='bc-job-title').props.value,'Keep my title');
""")


def test_replacement_ids_are_looked_up_without_repeating_mutation(js_fixture):
    run_js(js_fixture, """
await store.connect('', 'fixture');
sandbox.fetch=async(path,opts={})=>{requests.push({path,opts});return{ok:true,json:async()=>path==='/api/jobs/batch-action'?{succeeded:['old'],failed:{}}:path==='/api/jobs/old'?{id:'old',reassigned_to_job_id:'new'}:[]}};
const result=await store.batchAction('reassign',['old'],{target_agent_role:'codex'});
assert.equal(result.replacements.old,'new');assert.equal(requests.filter(r=>r.path==='/api/jobs/batch-action').length,1);
assert.equal(store.getMutationResults()[0].replacements.old,'new');
""")


def test_batch_unreported_outcome_is_not_claimed_as_success(js_fixture):
    run_js(js_fixture, """
await store.connect('', 'fixture');response={success:true};
const result=await store.batchAction('retry',['a']);
assert.equal(result.succeeded.length,0);assert.match(result.failed.a,/outcome.*reported/i);
""")


def test_activity_copy_and_empty_state_are_bounded(js_fixture):
    run_js(js_fixture, """
sandbox.window.BitCadenceStore={mode:()=> 'live'};
const tree=render('ActivityFeedScreen',{jobs:[],tone:'plain'});
assert.match(text(tree),/loaded window/);assert.ok(!text(tree).includes('Every step every agent took'));
""")


def test_detail_reads_exact_id_outside_board_window(js_fixture):
    run_js(js_fixture, """
let reads=[];sandbox.window.BitCadenceStore={getAgents:()=>[],fetchJob:async id=>{reads.push(id);return{id,title:'An older attempt',status:'completed'}}};
const props={jobId:'older',jobs:[],tone:'plain',onClose(){}};let tree=render('JobDetail',props);
assert.match(text(tree),/outside the current board window/);await Promise.resolve();tree=render('JobDetail',props);
assert.match(text(tree),/An older attempt/);assert.deepEqual(reads,['older']);
""")


def test_batch_result_and_failed_selection_survive_page_remount(js_fixture):
    run_js(js_fixture, """
sandbox.window.BitCadenceStore={mode:()=> 'live',getMutationResults:()=>[{action:'retry',succeeded:['a'],failed:{b:'Retained reason'}}]};
const tree=render('JobBoard',{jobs:[{id:'b',title:'B',status:'failed'}]});
assert.match(text(tree),/Retained reason/);assert.equal(all(tree).find(n=>n.type==='input'&&n.props['aria-label']==='Select job B').props.checked,true);
""")
