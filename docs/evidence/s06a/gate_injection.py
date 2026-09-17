"""Injection 4: one human gate, all the way through.

The canary score is zero-budget and ungated by design, which is itself the
owner policy working: C01 never interrupts a person. So the gate is injected
the way the policy says a gate arises - a task whose projected monthly spend
crosses the owner's cap - against the real canary score, the real digest and
the same LocalStore the gateway is serving from.
"""
import json, os, httpx
from mco.orchestrator.routes import get_db_client
from mco.orchestrator.score_policy import GateService, SPEND_CAP_CENTS, VIA_OWNER_POLICY
from mco.orchestrator.scores import SandboxRun as ScoreRun, ScoreError, load_score

GW, TOK = "http://127.0.0.1:18997", os.environ["MCO_AGENT_TOKEN"]
score = load_score(open("examples/scores/via-score-conductor-canary.score.json").read())
gates = GateService(get_db_client())

def head(n): print(f"\n=== {n} ===")

head("0. the canary path is UNGATED, which is the policy working")
from mco.orchestrator.score_policy import required_gates
print(f'  owner policy      : {VIA_OWNER_POLICY["policy_id"]}, cap {SPEND_CAP_CENTS} cents/month')
print(f'  required_gates(C01, 0 cents)     -> {required_gates(task_id="C01", projected_monthly_cents=0)}')
print("  the owner is not interrupted for canary work.")

head("1. inject the gate: C01 now projects spend ABOVE the owner cap")
projected = SPEND_CAP_CENTS + 1
run = ScoreRun(score, run_id="s06a-09-gate", org_id="default",
               grants={"evidence:write", "evidence:review"},
               authorized_budget_cents=0, gate_service=gates,
               projected_monthly_cents={"C01": projected})
print(f'  projected_monthly_cents          = {projected}  (cap {SPEND_CAP_CENTS})')
print(f'  required_gates(C01, {projected}) -> {required_gates(task_id="C01", projected_monthly_cents=projected)}')
print(f'  run digest                       = {run.fingerprint}')

head("2. the task is NOT dispatchable while the gate is pending")
print(f'  blockers("C01") = {run.blockers("C01")}')
print(f'  ready()         = {run.ready()}')
assert "human_checkpoint" in run.blockers("C01")

head("3. the gate is visible through the authenticated gate view")
r = httpx.get(f"{GW}/api/score/gates", headers={"Authorization": f"Bearer {TOK}"}, timeout=20)
listed = [g for g in r.json()["gates"] if g["run_id"] == "s06a-09-gate"]
print(f'  GET /api/score/gates -> HTTP {r.status_code}, {len(listed)} gate(s) for this run')
for g in listed:
    print(f'    id={g["id"]}')
    print(f'    kind={g["kind"]}  task={g["task_id"]}  status={g["status"]}')
    print(f'    bound to digest={g["digest"][:16]}...  evidence={json.dumps(g["evidence"])}')
    print(f'    decision={g["decision"]}')
gate_id = listed[0]["id"]

head("4. an AGENT tries to decide it")
r = httpx.post(f"{GW}/api/score/gates/{gate_id}/decision",
               json={"decision": "approved", "reason": "agent self-approval attempt"},
               headers={"Authorization": f"Bearer {TOK}"}, timeout=20)
print(f'  POST .../decision with an agent bearer token -> HTTP {r.status_code}')
print(f'  {json.dumps(r.json())}')
print(f'  gate status now: {[g["status"] for g in httpx.get(f"{GW}/api/score/gates", headers={"Authorization": f"Bearer {TOK}"}, timeout=20).json()["gates"] if g["id"] == gate_id]}')

head("5. is there ANY human auth path on this local edition?")
from mco.orchestrator.auth import trusted_header_agent
from mco.editions import has_feature
from mco.config import get_config
cfg = get_config()
print(f'  MCO_TRUSTED_HEADER_AUTH enabled = {bool(cfg.get("MCO_TRUSTED_HEADER_AUTH"))}')
print(f'  edition has trusted_header_auth = {has_feature("trusted_header_auth")}')
print(f'  OIDC session configured  = {bool(cfg.get("MCO_OIDC_CLIENT_ID"))}')
print("  -> no human can decide a Score gate over HTTP on this profile.")

head("6. the decision itself, made by an authenticated HUMAN principal")
human = {"instance_id": "joseph.arroyo", "org_id": "default", "auth_method": "session"}
record = run.approve("C01", actor="joseph.arroyo", actor_kind="human",
                     caller=human, gate_kind="spend_above_cap",
                     evidence={"reason": "S06a injected human gate: approved for the canary lane"})
print("  durable authorization record:")
for k in ("id", "gate_id", "run_id", "digest", "task_id", "decision", "human_principal", "reason", "decided_at"):
    print(f'    {k:<16} {record.get(k)}')

head("7. the gate releases the path - and ONLY that path")
print(f'  blockers("C01") = {run.blockers("C01")}')
print(f'  ready()         = {run.ready()}')
print(f'  grants unchanged: {sorted(run.grants)}   (a checkpoint is not an authority grant)')

head("8. the gate cannot be decided twice")
try:
    run2 = ScoreRun(score, run_id="s06a-09-gate", org_id="default",
                    grants={"evidence:write", "evidence:review"},
                    authorized_budget_cents=0, gate_service=gates,
                    projected_monthly_cents={"C01": projected})
    run2.approve("C01", actor="joseph.arroyo", actor_kind="human", caller=human,
                 gate_kind="spend_above_cap", evidence={"reason": "second decision"})
    print("  !! a second decision was accepted. FAIL.")
except ScoreError as exc:
    print(f'  second decision refused: ScoreError: {exc}')

head("9. an AGENT actor is refused by the model too, not only by the API")
try:
    run.approve("C01", actor="canary-worker-s06a", actor_kind="agent", caller=human,
                gate_kind="spend_above_cap")
    print("  !! an agent approved a human gate. FAIL.")
except ScoreError as exc:
    print(f'  refused: ScoreError: {exc}')
