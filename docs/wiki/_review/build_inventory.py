"""Generate source-indexed GUI coverage without importing or running BitCadence."""
import argparse
import ast
import hashlib
import json
import re
from pathlib import Path

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--repo', type=Path, default=Path.cwd(), help='Source repository (run from repository root by default)')
parser.add_argument('--output', type=Path, help='Output directory; default <repo>/docs/wiki/_review')
parser.add_argument('--freeze-source-hashes', action='store_true', help='Record LF-normalized source fingerprints only after the source owner declares a final freeze')
args = parser.parse_args()
ROOT = args.repo.resolve()
OUT = (args.output or ROOT/'docs/wiki/_review').resolve()
OUT.mkdir(parents=True, exist_ok=True)
SHELL = 'src/mco/console_src/8ec84a72-7b37-434d-a68b-2ac1188e3e6d.js'
JOBS = 'src/mco/console_src/5adac14f-6645-4e02-866b-22c4e571989b.js'
PAGES = 'src/mco/console_src/43b328d0-9d0e-4fce-a105-be0a939d7e48.js'
OPS = 'src/mco/console_src/2ed3f6b1-e1c6-43fc-8b29-31917195cbd5.js'
ASK = 'src/mco/console_src/bb158701-74ed-4f8d-8363-e8eb7edb6768.js'
UI = 'src/mco/console_src/32c20ad6-cc53-43b3-a432-3a75e249b495.js'
DASH = 'src/mco/dashboard.py'
APP = 'src/mco/desktop/app.py'
entries = []

COMPONENTS = {
    SHELL: {'shell':'App', 'prototype edit mode':'App', 'Administration':'Administration', 'Helpers':'App'},
    JOBS: {'Portfolio':'PortfolioPage', 'Job Board':'JobBoard', 'Job Board batch':'JobBoard', 'New job':'NewJobForm', 'Job details':'JobDetail', 'Activity':'ActivityFeedScreen'},
    PAGES: {'Home':'Home', 'Score live':'ScoreLive', 'Approvals':'Approvals', 'Governance':'Governance', 'Drumline':'DrumlineMemory', 'Context':'DrumlineContext', 'Memory details':'MemoryDetailDrawer', 'Exchange':'AgentExchange'},
    OPS: {'Helpers':'HelpersPage', 'Agent Fleet':'AgentFleet', 'Connect an AI':'ConnectPage', 'Schedules':'SchedulesPage', 'Settings':'PlainSettings', 'Gateway controls':'LiveControlSettings', 'Connectors':'ConnectorsCard', 'Tenancy':'TenancyCard'},
    ASK: {'Ask':'AskPage'}, UI: {'drawer':'Drawer', 'Notifications':'ToastHost'},
    DASH: {'Lock':'unlock', 'Navigation':'nav', 'Operations':'refreshOps', 'Autonomy':'openLiveLook', 'Agents & Tokens':'openRegister', 'Model Connections':'openAddModel', 'Workflows':'submitWorkflow', 'Settings':'renderGroups'},
}

def source_location(source, screen, controls):
    text=(ROOT/source).read_text(encoding='utf-8')
    if source == APP:
        marker = 'class DesktopApp:'
    elif source == 'src/mco/orchestrator/admin_routes.py':
        marker = '"' + controls + '":'
    else:
        name=COMPONENTS.get(source,{}).get(screen)
        if not name:
            raise ValueError(f'No source component for {source} / {screen}')
        marker='function '+name+'('
    at=text.find(marker)
    if at < 0:
        raise ValueError(f'Missing source anchor {marker} in {source}')
    return text[:at].count('\n')+1, marker

def add(surface, screen, controls, source, line, doc, effect='read', status='source_implemented'):
    # Historical call-site integers are ignored: component anchors are resolved
    # on every generation so concurrent source edits cannot silently stale them.
    line, marker=source_location(source,screen,controls)
    for label in controls.split('|'):
        entries.append({'id':f'gui-{len(entries)+1:03}', 'surface':surface, 'screen':screen,
                        'control':label, 'effect':effect, 'verification':status,
                        'source':source, 'line':line, 'source_anchor':marker,
                        'documentation':'gui-reference.md#'+doc})

add('console','shell','Home|Portfolio|Job Board|Approvals|Governance|Ask for something|Helpers|Connect an AI|Schedules|Drumline|Activity|Settings|Administration',SHELL,4,'navigation-and-display-controls')
add('console','shell','Technical details|Live/Connecting/Offline/Demo status|Menu / navigation backdrop|Skip to content|Disconnected Connect',SHELL,174,'navigation-and-display-controls','local_view')
add('console','drawer','Close ×|Backdrop dismiss|Escape close|Tab containment and focus restoration',UI,144,'navigation-and-display-controls','local_view')
add('console','Notifications','Dismiss notification|Recent notifications|Close notification history|Latest forty session records',UI,187,'navigation-and-display-controls','local_view')
add('console','Administration','Helper credentials|Back to Helpers|Open model connections|Open saved workflow files|Open server administration|Administration instructions',SHELL,87,'administration','administrative_view')
add('console','prototype edit mode','Accent|Density|Copy tone|Live simulation',SHELL,236,'navigation-and-display-controls','local_view','host_edit_mode_only')
add('console','Home','Look first|Normal work card|Score run card|See all work|Coverage notice',PAGES,577,'home-and-the-score-live-view')
add('console','Home','Approve',PAGES,599,'home-and-the-score-live-view','approval')
add('console','Score live','Read-only automatic drawing|Back to Home',PAGES,635,'home-and-the-score-live-view')
add('console','Portfolio','Refresh|Try again|Project card expand/collapse|Recent board jobs / open job|Open pull request links|Flow flag rows|Capacity strip (GPU queue, CPU queue, Workers)',JOBS,173,'portfolio')
add('console','Portfolio','Priority and pause controls (read-only notice)',JOBS,257,'portfolio')
add('console','Job Board','All|Active|Needs approval|Waiting|Done|Problems|Search jobs|All roles / role filter|Sort jobs / six choices|Job/Status/Assigned to/Updated header sort|Job row',JOBS,372,'find-and-order-work')
add('console','New job','+ New job|Title|Details|Role picker|Ask me before it runs|Retry budget|Escalate to role|Cancel / Ã—',JOBS,823,'create-a-job','draft')
add('console','New job','Create job',JOBS,878,'create-a-job','work_write')
add('console','Job details','History / Audit trail|Connected steps|Advanced payloads and ID|Close Ã—',JOBS,668,'inspect-and-act-on-one-job')
add('console','Job details','Approve & run|Reject / reason / Cancel',JOBS,702,'inspect-and-act-on-one-job','approval')
add('console','Job details','Try again|Call it off / reason / Back',JOBS,723,'inspect-and-act-on-one-job','work_write')
add('console','Job details','Reassign / role / Back|Create replacement|Replacement identity / open exact job|Pending action / retained failure input',JOBS,727,'inspect-and-act-on-one-job','work_write')
add('console','Job Board batch','Row checkbox|Select all jobs|Clear selection',JOBS,565,'batch-controls','local_view')
add('console','Job Board batch','Approve|Reject / reason',JOBS,541,'batch-controls','approval')
add('console','Job Board batch','Retry|Cancel / reason|Archive|Reassign role / Create replacements|Succeeded / failed / skipped results|Per-item failure reason|Replacement / original History link',JOBS,543,'batch-controls','work_write')
add('console','Approvals','Queue item|Full details|Select all|Individual checkboxes|Recent decision row|Got it',PAGES,671,'approvals','local_view')
add('console','Approvals','Approve & run|Reject / reason|Batch approve|Batch reject / reason',PAGES,698,'approvals','approval')
add('console','Governance','Refresh|Review|Decision history|Oversight event',PAGES,834,'governance')
add('console','Governance','Start date|End date|Download evidence JSON',PAGES,938,'governance','export')
add('console','Ask','Request|Example buttons|Draft a plan|Remove a step / Remove|Always ask me at end|Make it repeat / repeat choices|Do not repeat|Change my request',ASK,45,'ask-for-something','draft')
add('console','Ask','Approve and start',ASK,155,'ask-for-something','approval_and_work_write')
add('console','Ask','Watch it on Home|Ask for something else',ASK,100,'ask-for-something','local_view')
add('console','Helpers','Cards / health / last heard|Add helper AI and name / Cancel',OPS,300,'everyday-helpers','draft')
add('console','Helpers','Add helper',OPS,327,'everyday-helpers','identity_write')
add('console','Helpers','Fix it / diagnosis|Not now',OPS,336,'everyday-helpers')
add('console','Helpers','Yes fix it',OPS,344,'everyday-helpers','repair_write')
add('console','Helpers','Manage credentials',SHELL,197,'agent-fleet--tokens','local_view')
add('console','Agent Fleet','Per-role cards / heartbeat / current job|Register panel / Instance ID / Role / Org|Copy once-shown token|Done / Close',OPS,5,'agent-fleet--tokens','administrative_view')
add('console','Agent Fleet','Register / Add agent|Reset token / confirm|Remove / confirm|Move / target / Confirm / Cancel',OPS,92,'agent-fleet--tokens','identity_write')
add('console','Connect an AI','App cards / Found status',OPS,414,'connect-an-ai')
add('console','Connect an AI','Connect|Disconnect',OPS,437,'connect-an-ai','client_config_write')
add('console','Connect an AI','Send a test|Show me a code|Copy',OPS,471,'connect-an-ai','configuration_test_or_copy')
add('console','Schedules','Cards / next run|What should run|Frequency choices|Day toggles|Time|Sentence preview|Time zone IANA|Validated next date / offset',OPS,502,'schedules','draft')
add('console','Schedules','On / Off|Save',OPS,535,'schedules','schedule_write')
add('console','Drumline','Context tab|Agent Exchange tab / arrow keys',PAGES,1619,'drumline-context-and-agent-exchange','local_view')
add('console','Context','Query|Tags|Search / Recall|Show more / Collapse|Drill down / details / source job|Copy text / Copied',PAGES,1106,'context')
add('console','Context','Add memory / Title / Kind / Tags / Content / Cancel',PAGES,1180,'context','draft')
add('console','Context','Save memory / Remember',PAGES,1142,'context','memory_write')
add('console','Memory details','Copy text|Open Source Job|Close / Escape with shared dialog focus',PAGES,1046,'context')
add('console','Exchange','Job picker|Kind filter|Thread|Provenance / receipts',PAGES,1452,'agent-exchange')
add('console','Exchange','Root type|Message|Reply|Resolve|Replace|Cancel composition action',PAGES,1538,'agent-exchange','draft')
add('console','Exchange','Post',PAGES,1605,'agent-exchange','discussion_write')
add('console','Exchange','Promote to context / preview / target kind / title / Cancel',PAGES,1543,'agent-exchange','draft')
add('console','Exchange','Promote to decision / lesson / handoff',PAGES,1567,'agent-exchange','memory_write')
add('console','Activity','Search|Event type|Refresh|Load more / fetch cap|Statistics|Job event row|Advanced detail',JOBS,949,'activity')
add('console','Settings','Server administration|Connection URL / token|Connect|Disconnect|Experience Technical details',OPS,925,'open-and-connect-the-console','local_connection')
add('console','Settings','Stop work review|Stop work now|Cancel stop|Resume',OPS,1020,'everyday-settings','halt_and_intake_write')
add('console','Settings','Phone status|Memory pointer',OPS,1040,'everyday-settings')
add('console','Gateway controls','Governance / notifications / presence / memory fields|Metadata choice dropdowns|Save gateway settings|Confirm stop on save',OPS,1060,'show-advanced','configuration_write')
add('console','Connectors','ServiceNow / Dynatrace fields|Save connectors',OPS,636,'show-advanced','configuration_write')
add('console','Connectors','Test connection',OPS,721,'show-advanced','external_probe')
add('console','Connectors','Import now|Unsaved changes / invalidated test notice',OPS,774,'show-advanced','import_write')
add('console','Tenancy','Org list / in use|New org / Add org',OPS,811,'show-advanced','configuration_write')
add('dashboard','Lock','Token / Unlock / Enter|Lock|Matching gateway browser credential reuse',DASH,266,'unlock-and-lock','local_connection')
add('dashboard','Navigation','Operations|Agents & Tokens|Model Connections|Workflows|Settings|Allowlisted view hashes|Back to workspace',DASH,121,'technical-control-plane-dashboard','local_view')
add('dashboard','Operations','Seven sortable headers|Audit|Row/header selection|Clear',DASH,310,'operations-and-conductor')
add('dashboard','Operations','Approve|Reject / reason|Batch approve|Batch reject',DASH,361,'operations-and-conductor','approval')
add('dashboard','Operations','Retry|Batch retry|Batch cancel|Batch archive|Batch reassign / target role',DASH,361,'operations-and-conductor','work_write')
add('dashboard','Autonomy','State / interval / write status|Live Look|Close / backdrop',DASH,436,'operations-and-conductor')
add('dashboard','Autonomy','Pause / Resume|Tick Now|Trigger Sweep Now|Pause / Resume Conductor',DASH,397,'operations-and-conductor','conductor_write')
add('dashboard','Autonomy','Abort Run / reason',DASH,421,'operations-and-conductor','run_abort')
add('dashboard','Agents & Tokens','Register dialog / Name / Role / Org / Scopes|Token copy / Done|Worker/MCP help Copy',DASH,551,'agents--tokens','draft_or_copy')
add('dashboard','Agents & Tokens','Register & generate token|Edit / Role / Status / Scopes / Save|Reset token / confirm|Delete / confirm',DASH,638,'agents--tokens','identity_write')
add('dashboard','Agents & Tokens','Cancel',DASH,635,'agents--tokens','local_view')
add('dashboard','Model Connections','Add dialog / Name / Provider / Base URL / Model / API key|Edit dialog / new key|Cancel',DASH,712,'model-connections','draft')
add('dashboard','Model Connections','Add & save|Save|Delete / confirm',DASH,734,'model-connections','credential_write')
add('dashboard','Model Connections','Test',DASH,768,'model-connections','external_model_request')
add('dashboard','Workflows','Workflow YAML',DASH,215,'workflows','draft')
add('dashboard','Workflows','Submit workflow',DASH,781,'workflows','work_write')
add('dashboard','Settings','Reload|Edition feature matrix|Connector health',DASH,793,'technical-control-plane-settings')
add('dashboard','Settings','Per-group Save|Secret Clear / confirm',DASH,827,'technical-control-plane-settings','configuration_write')
add('dashboard','Settings','Connector Sync now',DASH,889,'technical-control-plane-settings','import_write')
add('windows','Local Control','Start all|Stop all|Restart all|Start selected|Stop selected|Restart selected',APP,51,'windows-local-control-and-tray','process_write')
add('windows','Local Control','Component selection / states / process / last error|Log viewer',APP,60,'windows-local-control-and-tray')
add('windows','Local Control','Open console',APP,57,'windows-local-control-and-tray','open_browser')
add('windows','Local Control','Fleet settings|Apply fleet changes review|Apply / Cancel fleet change|Move workers into app review|Confirm / Cancel handoff|Component and scheduled-task scope preview|Stop all / Exit scope explanation',APP,73,'windows-local-control-and-tray','configuration_or_process_write')
add('windows','Local Control','Window X',APP,229,'windows-local-control-and-tray','hide_or_exit')
add('windows','Tray','Open BitCadence|Open console',APP,88,'windows-local-control-and-tray','open_view')
add('windows','Tray','Start all|Stop all|Exit',APP,89,'windows-local-control-and-tray','process_write')

tree = ast.parse((ROOT/'src/mco/orchestrator/admin_routes.py').read_text(encoding='utf-8'))
groups = next(ast.literal_eval(n.value) for n in tree.body if isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='SETTING_GROUPS' for t in n.targets))
rows = ['| Group | Setting / control | Type and use |', '|---|---|---|']
for group, fields in groups.items():
    for key, meta in fields.items():
        detail = meta['label'].replace('|','\\|')
        if meta.get('choices') is not None:
            detail += ' Allowed: '+', '.join('`'+str(v)+'`' if v else 'blank (infer)' for v in meta['choices'])+'.'
        rows.append(f'| {group} | `{key}` | {meta["type"]}: {detail} |')
        add('dashboard','Settings / '+group,key,'src/mco/orchestrator/admin_routes.py',255,'technical-control-plane-settings','configuration_write')
(OUT/'gui-settings-catalogue.md').write_text('\n'.join(rows)+'\n',encoding='utf-8')
sources=[]
source_files=sorted({p.relative_to(ROOT).as_posix() for p in (ROOT/'src/mco/console_src').glob('*.js*')} | {DASH,APP,'src/mco/desktop/controller.py','src/mco/orchestrator/admin_routes.py','src/mco/orchestrator/schedules_routes.py','src/mco/schedules_plain.py'})
for path in source_files if args.freeze_source_hashes else []:
    raw=(ROOT/path).read_text(encoding='utf-8').replace('\r\n','\n').encode('utf-8')
    sources.append({'path':path,'sha256':hashlib.sha256(raw).hexdigest()})
data={'schema_version':2,'date':'2026-11-13','basis':'Current UX source branch and isolated fixtures; authenticated runtime and deployment not claimed','baseline_commit':'d511c6d','source_hash_status':'frozen' if args.freeze_source_hashes else 'pending_final_source_freeze','counts':{'grouped_control_or_workflow_entries':len(entries),'console_sidebar_pages':13,'dashboard_pages':5,'server_settings':sum(map(len,groups.values()))},'entries':entries,'sources':sources,'source_files':source_files,'unreachable_legacy_console_components':['Overview','AutonomyControlCard','AutonomyLiveLookModal','DemoLaunchPanel'],'limitations':['Entries are grouped controls, not enumeration of dynamic server records or every rendered option.','Source locations resolve current component/function anchors; grouped controls share a component location.','Static and fixture evidence does not establish rendered accessibility, live effects, running app update or deployment.','Prototype tweaks require host edit mode.','Shared memory/nested drawers and the specialist modal have focus handling; real keyboard and assistive technology acceptance remains pending.','Global server pagination and complete historical search were not added.',('Source fingerprints are frozen for the recorded source snapshot.' if args.freeze_source_hashes else 'Source fingerprints are omitted until the source owner requests final freeze.'),'Dashboard/conductor mutations and external provider calls were not executed.']}
(OUT/'gui-feature-coverage.json').write_text(json.dumps(data,indent=2)+'\n',encoding='utf-8')
print(json.dumps(data['counts']))
