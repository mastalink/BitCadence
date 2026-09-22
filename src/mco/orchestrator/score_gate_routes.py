"""Authenticated API and minimal operator console for Score human gates."""
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import HTMLResponse

from mco.orchestrator.auth import require_scopes
from mco.orchestrator.score_authority import AuthorityError, GrantService
from mco.orchestrator.score_policy import GateService, authenticated_human
from mco.orchestrator.scores import ScoreError


score_gates_router = APIRouter(prefix="/api/score/gates")
score_grants_router = APIRouter(prefix="/api/score/grants")


def _service() -> GateService:
    from mco.orchestrator.routes import get_db_client
    db = get_db_client()
    if db is None:
        raise HTTPException(status_code=400, detail="Database not configured")
    return GateService(db)


def _grant_service() -> GrantService:
    from mco.orchestrator.routes import get_db_client
    db = get_db_client()
    if db is None:
        raise HTTPException(status_code=400, detail="Database not configured")
    try:
        return GrantService(db)
    except AuthorityError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@score_grants_router.get("/console", response_class=HTMLResponse)
async def score_grants_console(caller: dict = Depends(require_scopes("jobs:approve"))) -> str:
    """Serve minimal operator console for issuing Score grants."""
    try:
        authenticated_human(caller)
    except ScoreError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    return SCORE_GRANT_HTML


@score_grants_router.post("")
async def issue_score_grant(payload: dict,
                            caller: dict = Depends(require_scopes("jobs:approve"))):
    """Issue signed authority only from a human-authenticated server path."""
    try:
        owner = authenticated_human(caller)
        grant = {
            "org_id": caller.get("org_id") or "default",
            "run_id": payload.get("run_id"),
            "digest": payload.get("digest"),
            "actions": payload.get("actions"),
            "resources": payload.get("resources"),
            "env": payload.get("environment"),
            "not_before": payload.get("not_before"),
            "expires_at": payload.get("expires_at"),
            "budget_cents": payload.get("budget_cents"),
            "human_principal": owner,
        }
        saved = _grant_service().issue(grant)
    except (AuthorityError, ScoreError) as exc:
        message = str(exc)
        status = 403 if "human principal" in message else 400
        raise HTTPException(status_code=status, detail=message) from exc
    return {"success": True, "grant": saved}


@score_gates_router.get("")
async def list_score_gates(caller: dict = Depends(require_scopes("jobs:read"))):
    return {"gates": _service().list(org_id=caller.get("org_id") or "default")}


@score_gates_router.post("/{gate_id}/decision")
async def decide_score_gate(gate_id: str, payload: dict,
                            caller: dict = Depends(require_scopes("jobs:approve"))):
    try:
        decision = _service().decide(
            gate_id,
            caller=caller,
            decision=payload.get("decision"),
            reason=payload.get("reason") or "",
        )
    except ScoreError as exc:
        message = str(exc)
        status = 403 if "human principal" in message else 409
        raise HTTPException(status_code=status, detail=message) from exc
    return {"success": True, "decision": decision}


SCORE_GATE_HTML = r'''<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width">
<title>Score gates</title><style>
body{font:15px system-ui;background:#0d1117;color:#e6edf3;max-width:950px;margin:2rem auto;padding:0 1rem}
header{display:flex;justify-content:space-between;align-items:center}.gate{border:1px solid #30363d;border-radius:10px;padding:1rem;margin:1rem 0;background:#161b22}
.meta{color:#8b949e;font-size:.85rem}pre{white-space:pre-wrap;background:#0d1117;padding:.7rem;border-radius:6px}
button,input{background:#21262d;color:#e6edf3;border:1px solid #30363d;border-radius:6px;padding:.5rem}button{cursor:pointer}.ok{border-color:#238636}.no{border-color:#da3633}#token{width:23rem;max-width:70%}.error{color:#f85149}
</style></head><body><header><div><h1>Score human gates</h1><div class="meta">Only G08 launch sign-off and spend above the owner cap appear here.</div></div><button onclick="load()">Refresh</button></header>
<p><input id="token" type="password" placeholder="Bearer token (or use signed-in browser session)"> <button onclick="load()">Open gate view</button></p><div id="message"></div><main id="gates"></main>
<script>
const el=id=>document.getElementById(id); function headers(){let h={'Content-Type':'application/json'},t=el('token').value.trim();if(t)h.Authorization='Bearer '+t;return h}
function node(tag,text,cls){let n=document.createElement(tag);if(text!==undefined)n.textContent=text;if(cls)n.className=cls;return n}
async function decide(id,decision){let reason=prompt('Decision reason (optional)')||'';let r=await fetch('/api/score/gates/'+encodeURIComponent(id)+'/decision',{method:'POST',headers:headers(),body:JSON.stringify({decision,reason})});if(!r.ok){el('message').textContent=(await r.json()).detail;el('message').className='error';return}load()}
async function load(){el('message').textContent='';let r=await fetch('/api/score/gates',{headers:headers()});if(!r.ok){el('message').textContent=(await r.json()).detail;el('message').className='error';return}let data=await r.json(),root=el('gates');root.replaceChildren();for(let g of data.gates){let card=node('section',undefined,'gate');card.append(node('h2',g.kind.replaceAll('_',' ')),node('div',g.run_id+' / '+g.task_id+' — '+g.status,'meta'),node('h3','Evidence'),node('pre',JSON.stringify(g.evidence,null,2)));if(g.decision){card.append(node('h3','Decision'),node('pre',JSON.stringify(g.decision,null,2)))}else{let yes=node('button','Approve','ok'),no=node('button','Reject','no');yes.onclick=()=>decide(g.id,'approved');no.onclick=()=>decide(g.id,'rejected');card.append(yes,document.createTextNode(' '),no)}root.append(card)}if(!data.gates.length)root.append(node('p','No Score gates.'))}
</script></body></html>'''


SCORE_GRANT_HTML = r'''<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width">
<title>Issue Score grant</title><style>
body{font:15px system-ui;background:#0d1117;color:#e6edf3;max-width:950px;margin:2rem auto;padding:0 1rem}
header{margin-bottom:1.5rem}
.meta{color:#8b949e;font-size:.85rem;margin-top:.25rem}
.card{border:1px solid #30363d;border-radius:10px;padding:1.2rem;background:#161b22;margin:1rem 0}
.field{margin-bottom:.85rem}
label{display:block;color:#c9d1d9;font-weight:600;font-size:.88rem;margin-bottom:.3rem}
.hint{color:#8b949e;font-size:.78rem;margin-top:.2rem}
button,input,textarea{background:#21262d;color:#e6edf3;border:1px solid #30363d;border-radius:6px;padding:.5rem;box-sizing:border-box;font:inherit}
input,textarea{width:100%}
input:focus,textarea:focus{outline:none;border-color:#58a6ff}
button{cursor:pointer}
.ok{border-color:#238636;background:#238636;color:#fff;font-weight:600;padding:.6rem 1.2rem}
.ok:hover{background:#2ea043}
#token{width:23rem;max-width:70%}
pre{white-space:pre-wrap;background:#0d1117;padding:.7rem;border-radius:6px;border:1px solid #30363d}
#message:empty{display:none}
.error{color:#f85149;border-color:#da3633}
.success{color:#3fb950;border-color:#238636}
</style></head><body><header>
<div><h1>Issue Score grant</h1><div class="meta">Operator console for issuing signed authority to Score runs.</div></div>
</header>
<p><input id="token" type="password" placeholder="Bearer token (optional if signed in with session cookie)"></p>
<div class="card">
<form id="grant-form" onsubmit="submitGrant(event);return false;">
  <div class="field">
    <label for="run_id">Run ID</label>
    <input id="run_id" name="run_id" required placeholder="e.g. run-123">
  </div>
  <div class="field">
    <label for="digest">Digest</label>
    <input id="digest" name="digest" required placeholder="64-char hex score digest">
  </div>
  <div class="field">
    <label for="actions">Actions (comma or newline separated)</label>
    <textarea id="actions" name="actions" rows="2" required placeholder="e.g. repository:write"></textarea>
    <div class="hint">Comma or newline separated list of actions</div>
  </div>
  <div class="field">
    <label for="resources">Resources (comma or newline separated)</label>
    <textarea id="resources" name="resources" rows="2" required placeholder="e.g. repo:mco"></textarea>
    <div class="hint">Comma or newline separated list of resources</div>
  </div>
  <div class="field">
    <label for="environment">Environment</label>
    <input id="environment" name="environment" required placeholder="e.g. local or production">
  </div>
  <div class="field">
    <label for="not_before">Not Before</label>
    <input id="not_before" name="not_before" required>
  </div>
  <div class="field">
    <label for="expires_at">Expires At</label>
    <input id="expires_at" name="expires_at" required>
  </div>
  <div class="field">
    <label for="budget_cents">Budget (cents)</label>
    <input id="budget_cents" name="budget_cents" type="number" min="0" value="0" required>
  </div>
  <button type="submit" class="ok">Issue Grant</button>
</form>
</div>
<pre id="message"></pre>
<script>
const el=id=>document.getElementById(id);
function parseList(val){
  return (val||'').split(/[,\n]/).map(s=>s.trim()).filter(Boolean);
}
function headers(){
  let h={'Content-Type':'application/json'},t=el('token').value.trim();
  if(t)h.Authorization='Bearer '+t;
  return h;
}
function init(){
  const p=new URLSearchParams(window.location.search);
  const now=new Date();
  const exp=new Date(now.getTime()+24*60*60*1000);
  el('not_before').value=p.get('not_before')||now.toISOString();
  el('expires_at').value=p.get('expires_at')||exp.toISOString();
  el('budget_cents').value=p.get('budget_cents')||'0';
  if(p.has('run_id'))el('run_id').value=p.get('run_id');
  if(p.has('digest'))el('digest').value=p.get('digest');
  if(p.has('actions'))el('actions').value=p.get('actions');
  if(p.has('resources'))el('resources').value=p.get('resources');
  if(p.has('environment'))el('environment').value=p.get('environment');
  if(p.has('token'))el('token').value=p.get('token');
}
async function submitGrant(e){
  if(e&&e.preventDefault)e.preventDefault();
  let msg=el('message');
  msg.textContent='';
  msg.className='';
  let payload={
    run_id:el('run_id').value.trim(),
    digest:el('digest').value.trim(),
    actions:parseList(el('actions').value),
    resources:parseList(el('resources').value),
    environment:el('environment').value.trim(),
    not_before:el('not_before').value.trim(),
    expires_at:el('expires_at').value.trim(),
    budget_cents:parseInt(el('budget_cents').value.trim(),10)||0
  };
  try{
    let r=await fetch('/api/score/grants',{
      method:'POST',
      credentials:'same-origin',
      headers:headers(),
      body:JSON.stringify(payload)
    });
    let data;
    try{
      data=await r.json();
    }catch(_){
      data={status:r.status,statusText:r.statusText};
    }
    msg.textContent=JSON.stringify(data,null,2);
    msg.className=r.ok?'success':'error';
  }catch(err){
    msg.textContent=JSON.stringify({error:err.message||String(err)},null,2);
    msg.className='error';
  }
}
init();
</script></body></html>'''


score_autonomy_router = APIRouter(prefix="/api/score/autonomy")


@score_autonomy_router.get("")
async def get_autonomy_status(caller: dict = Depends(require_scopes("jobs:read"))):
    """Current state of autonomous execution, conductor loops, and active runs."""
    from mco.config import get_config
    from mco.orchestrator import score_sweep
    from mco.orchestrator.routes import kill_switch_active
    from mco.orchestrator.score_conductor import sqlite_runs, TERMINAL_RUN_STATES
    from mco.orchestrator.score_sweep import FAILING_RUN_STATES

    config = get_config()
    interval = score_sweep.get_sweep_seconds(config)
    configured = interval > 0
    paused = score_sweep.is_sweep_paused()
    kill_switch = kill_switch_active()
    live_repository_write = str(config.get("MCO_SCORE_LIVE_REPOSITORY_WRITE", "false")).lower() == "true"

    if kill_switch:
        status = "frozen"
    elif paused:
        status = "paused"
    elif not configured:
        status = "standby"
    else:
        status = "active"

    db_path = score_sweep.get_database(config)
    all_runs = []
    active_runs = []
    failing_runs = []
    if db_path.exists():
        try:
            all_runs = sqlite_runs(db_path)
            for r in all_runs:
                st = r.get("status")
                if st not in TERMINAL_RUN_STATES:
                    active_runs.append(r)
                if st in FAILING_RUN_STATES:
                    failing_runs.append(r)
        except Exception:
            pass

    return {
        "status": status,
        "configured": configured,
        "interval_seconds": interval,
        "paused": paused,
        "kill_switch": kill_switch,
        "live_repository_write": live_repository_write,
        "database": str(db_path),
        "total_runs": len(all_runs),
        "active_runs": active_runs,
        "failing_runs": failing_runs,
    }


@score_autonomy_router.post("/pause")
async def pause_autonomy(caller: dict = Depends(require_scopes("jobs:approve"))):
    """Pause autonomous conductor sweeps without halting general gateway work."""
    from mco.orchestrator import score_sweep
    from mco.orchestrator.audit import record_event
    from mco.orchestrator.routes import get_db_client

    score_sweep.set_sweep_paused(True, paused_by=caller.get("instance_id"))
    db = get_db_client()
    if db:
        record_event(db, "system:autonomy", "autonomy_paused", caller.get("instance_id"),
                     caller.get("role"), {"action": "pause"})
    return {"success": True, "paused": True, "status": "paused"}


@score_autonomy_router.post("/resume")
async def resume_autonomy(caller: dict = Depends(require_scopes("jobs:approve"))):
    """Resume autonomous conductor sweeps."""
    from mco.orchestrator import score_sweep
    from mco.orchestrator.audit import record_event
    from mco.orchestrator.routes import get_db_client

    score_sweep.set_sweep_paused(False)
    db = get_db_client()
    if db:
        record_event(db, "system:autonomy", "autonomy_resumed", caller.get("instance_id"),
                     caller.get("role"), {"action": "resume"})
    interval = score_sweep.get_sweep_seconds()
    status = "active" if interval > 0 else "standby"
    return {"success": True, "paused": False, "status": status}


@score_autonomy_router.post("/tick")
async def tick_autonomy(caller: dict = Depends(require_scopes("jobs:approve"))):
    """Manually trigger a single conductor sweep pass ('Tick Now')."""
    from mco.orchestrator import score_sweep
    from mco.orchestrator.audit import record_event
    from mco.orchestrator.routes import get_db_client

    conductor = score_sweep.open_conductor()
    res = score_sweep.sweep(conductor, force=True)
    db = get_db_client()
    if db:
        record_event(db, "system:autonomy", "autonomy_manual_tick", caller.get("instance_id"),
                     caller.get("role"), {"ticked": len(res.ticked), "advanced": len(res.advanced)})
    return {
        "success": True,
        "result": {
            "ticked": res.ticked,
            "advanced": res.advanced,
            "blocked": res.blocked,
            "skipped": res.skipped,
            "errors": res.errors,
            "failing": res.failing,
        }
    }


@score_autonomy_router.post("/abort")
async def abort_run(payload: dict, caller: dict = Depends(require_scopes("jobs:approve"))):
    """Abort an active Score run, blocking future automatic ticks."""
    from mco.orchestrator import score_sweep
    from mco.orchestrator.audit import record_event
    from mco.orchestrator.routes import get_db_client

    run_id = (payload or {}).get("run_id")
    reason = (payload or {}).get("reason") or "Aborted by operator"
    if not run_id:
        raise HTTPException(status_code=400, detail="run_id is required")

    conductor = score_sweep.open_conductor()
    conductor._block(run_id, "operator_abort", reason)
    db = get_db_client()
    if db:
        record_event(db, f"score:run:{run_id}", "run_aborted_by_operator",
                     caller.get("instance_id"), caller.get("role"),
                     {"run_id": run_id, "reason": reason})
    return {"success": True, "run_id": run_id, "status": "blocked"}

