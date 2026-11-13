"""Operations regressions using the shipped JSX and isolated scheduler fixtures."""
import base64
import gzip
import json
from datetime import datetime, timezone
from pathlib import Path
import re
import shutil
import subprocess

import pytest
from fastapi import HTTPException

ROOT = Path(__file__).resolve().parents[1]
OPS = ROOT / 'src/mco/console_src/2ed3f6b1-e1c6-43fc-8b29-31917195cbd5.js'


def jsx(tmp_path, scenario):
    """Execute actual components with deterministic hooks and fixture-only APIs."""
    node = shutil.which('node')
    if not node:
        pytest.skip('Node is required for JSX interaction regressions')
    manifest = json.loads(re.search(r'<script type="__bundler/manifest">(.*?)</script>',
                                   (ROOT/'src/mco/static/console.html').read_text(encoding='utf-8'), re.S)[1])
    entry = manifest['0b8ac89f-6446-474a-8a90-5672b3fc131f']
    babel = base64.b64decode(entry['data'])
    if entry.get('compressed'):
        babel = gzip.decompress(babel)
    (tmp_path/'babel.js').write_bytes(babel)
    harness = r'''
const fs = require('fs'), vm = require('vm'), assert = require('assert');
const slots = [], deps = []; let cursor = 0, effects = [], tree;
const React = {
 useRef(initial) { const i=cursor++; if (!(i in slots)) slots[i]={current:initial};return slots[i]; },
 useState(initial) { const i=cursor++; if (!(i in slots)) slots[i]=typeof initial==='function'?initial():initial;
   return [slots[i], v => {slots[i]=typeof v==='function'?v(slots[i]):v;}]; },
 useEffect(fn, next) {const i=cursor++; if(!deps[i]||!next||next.some((v,n)=>v!==deps[i][n])) {deps[i]=next;effects.push(fn);} },
 createElement(type, props, ...children) {return {type,props:{...props,children}};}
};
const calls=[]; const store={mode:()=> 'live', config:()=>({url:'https://gateway.example:18789'}),
 getSettings:async()=>({groups:{governance:[{key:'MCO_KILL_SWITCH',type:'bool',value:false,label:'Stop work'},
 {key:'MCO_DEFAULT_JOB_SORT',type:'choice',value:'priority_desc',label:'Sort',choices:['created_desc','priority_desc']}],
 connectors:[{key:'DYNATRACE_BASE_URL',type:'text',value:'https://old.example',label:'URL'},
 {key:'DYNATRACE_API_TOKEN',type:'secret',value:true,label:'Key'}]}}),
 saveSettings:async p=>calls.push(['save',p]),getIntegrations:async()=>[],
 testConnector:async n=>{calls.push(['test',n]);return {ok:true,detail:'fixture ok'};},
 connectList:async()=>({apps:[]})};
const context={React,window:{BitCadenceStore:store,confirm:()=>true,location:{href:'https://console.example/console'}},
 location:{href:'https://console.example/console'},URL,Intl,console,setTimeout:()=>0,setInterval:()=>0,clearInterval:()=>{},
 Card:'Card',SectionTitle:'SectionTitle',Btn:'Btn',Mono:'Mono',EmptyState:'EmptyState'};
vm.createContext(context);vm.runInContext(fs.readFileSync(process.argv[2],'utf8'),context);
vm.runInContext(context.Babel.transform(fs.readFileSync(process.argv[3],'utf8'),{presets:['react']}).code,context);
const all = (v,p) => !v||typeof v!=='object'?[]:Array.isArray(v)?v.flatMap(x=>all(x,p)):[...(p(v)?[v]:[]),...Object.values(v.props||{}).flatMap(x=>all(x,p))];
const text = n => !n?'':typeof n==='string'?n:Array.isArray(n)?n.map(text).join(' '):text(n.props&&n.props.children);
const nodes = p=>all(tree,p); const button = label=>nodes(n=>typeof n.props?.onClick==='function' && text(n).trim()===label)[0];
const render = (name,props={})=>{cursor=0;tree=(context.window[name]||context[name])(props);return tree;};
const settle = async()=>{const pending=effects;effects=[];pending.forEach(f=>f());for(let n=0;n<8;n++)await Promise.resolve();};
(async()=>{SCENARIO})().catch(e=>{console.error(e);process.exitCode=1;});
'''.replace('SCENARIO', scenario)
    script = tmp_path/'test.js'
    script.write_text(harness, encoding='utf-8')
    result = subprocess.run([node, str(script), str(tmp_path/'babel.js'), str(OPS)], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr


def test_stop_needs_deliberate_confirmation_and_cancel_writes_nothing(tmp_path):
    jsx(tmp_path, """
render('PlainSettings');await settle();render('PlainSettings');
assert(!text(tree).includes('Jobs in progress finish'));
await button('Stop work…').props.onClick();render('PlainSettings');
assert.equal(calls.length,0);assert(text(tree).includes('halted jobs need Retry'));
button('Cancel').props.onClick();render('PlainSettings');assert.equal(calls.length,0);
button('Stop work…').props.onClick();render('PlainSettings');await button('Stop work now').props.onClick();
assert.equal(calls.length,1);assert.equal(calls[0][1].MCO_KILL_SWITCH,true);
"""
    )


def test_gateway_choices_render_only_metadata_options(tmp_path):
    jsx(tmp_path, """
render('LiveControlSettings');await settle();render('LiveControlSettings');
const select=nodes(n=>n.type==='select'&&n.props['aria-label']==='Sort')[0];assert(select,'choice must be a select');
assert.deepEqual(select.props.children[0].map(n=>n.props.value),['created_desc','priority_desc']);
select.props.onChange({target:{value:'created_desc'}});render('LiveControlSettings');
await button('Save gateway settings').props.onClick();assert.equal(calls[0][1].MCO_DEFAULT_JOB_SORT,'created_desc');
"""
    )


def test_connector_edit_invalidates_test_and_blocks_import_until_saved(tmp_path):
    jsx(tmp_path, """
render('ConnectorsCard',{tone:'plain'});await settle();render('ConnectorsCard',{tone:'plain'});
const tests=nodes(n=>text(n).trim()==='Test connection'&&n.props.onClick);await tests[1].props.onClick();
render('ConnectorsCard',{tone:'plain'});assert(button('Import now'));
const input=nodes(n=>n.type==='input'&&n.props.value==='https://old.example')[0];input.props.onChange({target:{value:'https://new.example'}});
render('ConnectorsCard',{tone:'plain'});assert(!button('Import now'));assert(text(tree).includes('Save changes before testing'));
const nextTests=nodes(n=>text(n).trim()==='Test connection'&&n.props.onClick);assert(nextTests[1].props.disabled);
"""
    )


def test_connector_late_test_result_cannot_restore_import_after_edit(tmp_path):
    jsx(tmp_path, """
let finish;store.testConnector=()=>new Promise(resolve=>finish=resolve);
render('ConnectorsCard',{tone:'plain'});await settle();render('ConnectorsCard',{tone:'plain'});
const tests=nodes(n=>text(n).trim()==='Test connection'&&n.props.onClick);const request=tests[1].props.onClick();
const input=nodes(n=>n.type==='input'&&n.props.value==='https://old.example')[0];input.props.onChange({target:{value:'https://changed.example'}});
finish({ok:true});await request;render('ConnectorsCard',{tone:'plain'});assert(!button('Import now'));
"""
    )


def test_connector_test_from_previous_connection_cannot_enable_import(tmp_path):
    jsx(tmp_path, """
let finish;store.testConnector=()=>new Promise(resolve=>finish=resolve);
render('ConnectorsCard',{tone:'plain'});await settle();render('ConnectorsCard',{tone:'plain'});
const tests=nodes(n=>text(n).trim()==='Test connection'&&n.props.onClick);const request=tests[1].props.onClick();
store.mode=()=> 'demo';render('ConnectorsCard',{tone:'plain'});await settle();
store.mode=()=> 'live';render('ConnectorsCard',{tone:'plain'});await settle();
finish({ok:true});await request;render('ConnectorsCard',{tone:'plain'});assert(!button('Import now'));
"""
    )


def test_disconnected_settings_exposes_connection_without_disclosure(tmp_path):
    jsx(tmp_path, """
store.mode=()=> 'demo';render('PlainSettings',{children:{type:'connection-fields',props:{children:['Gateway URL','Agent token']}}});
assert(text(tree).includes('Gateway URL'));assert.equal(nodes(n=>n.type==='details').length,0);
"""
    )


def test_connect_page_names_gateway_target_in_remote_browser(tmp_path):
    jsx(tmp_path, """
render('ConnectPage');assert(text(tree).includes('gateway.example:18789'));assert(text(tree).includes('gateway computer'));
assert(!text(tree).includes('One tap each'));
"""
    )


def test_preview_validates_timezone_and_uses_existing_scheduler_across_dst(monkeypatch):
    from mco import schedules_plain as sp
    now = datetime(2026, 3, 7, 15, tzinfo=timezone.utc)
    before = sp.preview_schedule('0 9 * * *', 'America/New_York', now=now)
    assert before['timezone'] == 'America/New_York'
    assert before['next_run'] == '2026-03-08T09:00:00-04:00'
    after = sp.preview_schedule('0 9 * * *', 'America/New_York', now=datetime(2026, 11, 1, 0, tzinfo=timezone.utc))
    assert after['next_run'] == '2026-11-01T09:00:00-05:00'
    with pytest.raises(sp.ScheduleWordsError, match='time zone'):
        sp.preview_schedule('0 9 * * *', 'Bogus/Somewhere', now=now)


def test_preview_route_returns_zone_and_next_without_file_mutation(monkeypatch):
    from mco.orchestrator import schedules_routes as routes
    from mco import schedules_plain as sp
    monkeypatch.setattr(sp, '_path', lambda: pytest.fail('preview must not open schedule files'))
    result = routes.preview({'frequency':'day','time':'09:00','timezone':'Asia/Tokyo'}, caller={})
    assert result['timezone'] == 'Asia/Tokyo'
    assert '+09:00' in result['next_run']
    with pytest.raises(HTTPException) as error:
        routes.preview({'frequency':'day','time':'09:00','timezone':'Bad\nzone'}, caller={})
    assert error.value.status_code == 400


def test_invalid_zone_does_not_modify_schedule_file(tmp_path, monkeypatch):
    from mco import schedules_plain as sp
    path = tmp_path/'schedules.yaml'
    original = 'launchers:\n  audit:\n    role: reviewer\n    title: Audit\nschedules: {}\n'
    path.write_text(original, encoding='utf-8')
    monkeypatch.setattr(sp, '_path', lambda: path)
    with pytest.raises(sp.ScheduleWordsError, match='time zone'):
        sp.add_schedule('audit', '0 9 * * *', timezone_name='America/New_York\n  injected: value')
    assert path.read_text(encoding='utf-8') == original


def test_legacy_blank_zone_save_keeps_gateway_default(tmp_path, monkeypatch):
    from mco import schedules_plain as sp
    from mco.orchestrator import schedules_routes as routes
    path = tmp_path/'schedules.yaml'
    path.write_text('launchers:\n  audit:\n    role: reviewer\n    title: Audit\nschedules: {}\n', encoding='utf-8')
    monkeypatch.setattr(sp, '_path', lambda: path)
    monkeypatch.setattr(sp, 'detect_timezone', lambda: 'UTC')
    result = routes.add({'what':'audit','frequency':'day','time':'09:00','timezone':''}, caller={})
    assert result['timezone'] == 'UTC'
    assert 'UTC' in path.read_text(encoding='utf-8')
    # The new explicit-zone preview must still require a valid visible choice.
    with pytest.raises(HTTPException) as error:
        routes.preview({'frequency':'day','time':'09:00','timezone':''}, caller={})
    assert error.value.status_code == 400


def test_schedule_form_sends_selected_zone_to_preview_and_save(tmp_path):
    jsx(tmp_path, """
store.schedulesList=async()=>({schedules:[],can_schedule:[{id:'audit',label:'Audit'}],frequencies:[{id:'day',label:'Every day'}],days:[]});
store.schedulePreview=async p=>{calls.push(['preview',p]);return {words:'Every day',timezone:p.timezone,next_run:'2026-11-14T09:00:00+09:00',next_words:'2026-11-14 09:00 +0900'};};
store.addSchedule=async p=>{calls.push(['add',p]);return {words:'Every day'};};
render('SchedulesPage');await settle();render('SchedulesPage');await settle();render('SchedulesPage');
const zone=nodes(n=>n.type==='input'&&n.props.id==='sc-zone')[0];assert(zone,'explicit time zone input');
zone.props.onChange({target:{value:'Asia/Tokyo'}});render('SchedulesPage');assert(button('Save').props.disabled);
await settle();render('SchedulesPage');assert(text(tree).includes('+0900'));
await button('Save').props.onClick();assert.equal(calls.find(c=>c[0]==='add')[1].timezone,'Asia/Tokyo');
"""
    )
