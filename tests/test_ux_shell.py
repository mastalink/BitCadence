"""Behavioral fixtures for shared UI and navigation; never call a live gateway."""
import base64
import gzip
import json
import re
import shutil
import subprocess
from functools import lru_cache
from pathlib import Path

import pytest

ROOT=Path(__file__).resolve().parents[1]
SRC=ROOT/'src/mco/console_src'
VIEWS='43b328d0-9d0e-4fce-a105-be0a939d7e48.js'
SHELL='8ec84a72-7b37-434d-a68b-2ac1188e3e6d.js'
UI='32c20ad6-cc53-43b3-a432-3a75e249b495.js'

@lru_cache(maxsize=1)
def babel():
    h=(ROOT/'src/mco/static/console.html').read_text(encoding='utf-8')
    m=json.loads(re.search(r'<script type="__bundler/manifest">(.*?)</script>',h,re.S)[1])
    e=m['0b8ac89f-6446-474a-8a90-5672b3fc131f'];b=base64.b64decode(e['data'])
    return (gzip.decompress(b) if e.get('compressed') else b).decode()

def evaluate(files,code,extra=''):
    node=shutil.which('node')
    if not node:pytest.skip('Node is needed for actual console JavaScript fixtures')
    setup='''var document={getElementById:()=>({})};var window={BitCadenceStore:{}};var React={useState:v=>[typeof v==='function'?v():v,()=>{}],useEffect:()=>{},useRef:v=>({current:v}),useMemo:f=>f(),createElement:(type,props,...children)=>({type:typeof type==='function'?type.name:type,props:props||{},children}),Fragment:'fragment'}; var ReactDOM={createRoot:()=>({render:()=>{}})};'''+extra
    runner="""const vm=require('vm'),p=JSON.parse(require('fs').readFileSync(0,'utf8'));const c=vm.createContext({URL,console:{log:()=>{}},setTimeout:()=>0,clearTimeout:()=>{},setInterval:()=>0,clearInterval:()=>{}});vm.runInContext(p.babel,c);vm.runInContext(p.setup,c);for(const s of p.sources)vm.runInContext(c.Babel.transform(s,{presets:['react']}).code,c);Promise.resolve(vm.runInContext(p.code,c)).then(v=>process.stdout.write(JSON.stringify(v))).catch(e=>{console.error(e);process.exit(1)});"""
    result=subprocess.run([node,'-e',runner],input=json.dumps({'babel':babel(),'sources':[(SRC/f).read_text(encoding='utf-8') for f in files],'setup':setup,'code':code}),text=True,capture_output=True,timeout=35)
    assert result.returncode==0,result.stderr[-1600:]
    return json.loads(result.stdout)

def test_approval_is_not_execution_completion():
    r=evaluate([VIEWS],"approvalDecision({status:'pending',approved_by:'operator'})")
    assert r=={'label':'Approved','status':'pending'}

def test_rejection_remains_a_decision_with_actual_status():
    assert evaluate([VIEWS],"approvalDecision({status:'rejected',approved_by:'operator'})")=={'label':'Rejected','status':'rejected'}

def test_administration_links_never_transport_tokens():
    code="administrationHref('https://gateway.example/prefix?token=secret#old','models')"
    assert evaluate([SHELL],code)=='https://gateway.example/prefix/dashboard#models'

@pytest.mark.parametrize('url',['javascript:alert(1)','data:text/html,bad','https://user:password@gateway.example'])
def test_administration_rejects_unsafe_gateway_addresses(url):
    assert evaluate([SHELL],f'administrationHref({json.dumps(url)},"models")') is None

def test_administration_view_is_allowlisted():
    assert evaluate([SHELL],"administrationHref('http://127.0.0.1:18789','unknown')").endswith('/dashboard#ops')

def test_recent_notifications_keep_complete_error_text():
    value=evaluate([UI],"notificationRecord({id:'err',kind:'err',title:'Failed',body:'Reason that must remain available'})")
    assert value['body']=='Reason that must remain available' and value['kind']=='err'

def test_memory_details_reuses_accessible_drawer():
    value=evaluate([UI,VIEWS],"MemoryDetailDrawer({entry:{id:'memory',title:'Fixture memory',content:'Fixture'},onClose:()=>{},onOpenJob:()=>{},tone:'plain',advanced:false})")
    assert value['type']=='Drawer' and value['props']['title']=='Memory details'

def dashboard_helpers():
    source=(ROOT/'src/mco/dashboard.py').read_text(encoding='utf-8')
    start=source.index('function dashboardView(')
    return source[start:source.index('function nav(',start)]

def test_administration_deep_link_selects_an_existing_view():
    assert evaluate([],dashboard_helpers()+";dashboardView('#models')")=='models'
    assert evaluate([],dashboard_helpers()+";dashboardView('#<bad>')")=='ops'

def test_administration_reuses_only_matching_gateway_credentials():
    helpers=dashboard_helpers()
    raw=json.dumps({'url':'https://gateway.example/team','token':'fixture-token'})
    assert evaluate([],helpers+f';consoleToken({json.dumps(raw)},"https://gateway.example/team/dashboard")')=='fixture-token'
    for page in ['https://another.example/team/dashboard','https://gateway.example/other/dashboard']:
        assert evaluate([],helpers+f';consoleToken({json.dumps(raw)},{json.dumps(page)})')==''

def test_administration_api_preserves_the_credentials_path_boundary():
    helpers=dashboard_helpers()
    assert evaluate([],helpers+';dashboardApiPath("/api/jobs","https://gateway.example/team/dashboard")')=='/team/api/jobs'
    assert evaluate([],helpers+';dashboardApiPath("/api/jobs","https://gateway.example/dashboard/")')=='/api/jobs'

def test_nested_drawers_only_topmost_handles_keys_and_restores_scroll():
    extra='''
var refs=[],effects=[],listeners=[],closed=[];
React.useRef=v=>{const r={current:v};refs.push(r);return r};React.useEffect=f=>effects.push(f);
const origin={name:'origin',isConnected:true,focus(){document.activeElement=this}};
document={activeElement:origin,body:{style:{overflow:'auto'}},addEventListener:(k,f)=>listeners.push(f),removeEventListener:(k,f)=>{listeners=listeners.filter(x=>x!==f)}};
function panel(name){const button={name:name+'-button',isConnected:true,getClientRects:()=>[1],focus(){document.activeElement=this}};return {name,isConnected:true,querySelectorAll:()=>[button],querySelector:()=>button,contains:x=>x===button,focus(){document.activeElement=this}}}
'''
    code='''
let cleanupOuter,cleanupInner;
Drawer({open:true,onClose:()=>{closed.push('outer');cleanupOuter()},children:null});refs[0].current=panel('outer');document.activeElement=refs[0].current.querySelector();cleanupOuter=effects.shift()();
Drawer({open:true,onClose:()=>{closed.push('inner');cleanupInner()},children:null});refs[3].current=panel('inner');document.activeElement=refs[3].current.querySelector();cleanupInner=effects.shift()();
let trapped=0;for(const f of [...listeners])f({key:'Tab',preventDefault:()=>trapped++,stopPropagation:()=>{}});
for(const f of [...listeners])f({key:'Escape',preventDefault:()=>{},stopPropagation:()=>{}});
const middle={closed:[...closed],overflow:document.body.style.overflow,focus:document.activeElement.name};
for(const f of [...listeners])f({key:'Escape',preventDefault:()=>{},stopPropagation:()=>{}});
({middle,closed,overflow:document.body.style.overflow,focus:document.activeElement.name,trapped})
'''
    value=evaluate([UI],code,extra)
    assert value['middle']=={'closed':['inner'],'overflow':'hidden','focus':'outer-button'}
    assert value['closed']==['inner','outer'] and value['overflow']=='auto' and value['focus']=='origin'
    assert value['trapped']==1
