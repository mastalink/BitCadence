"""Explicit operator CLI for the authorized read-only VIA score run."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess

from mco.orchestrator.client import GatewayClient
from mco.orchestrator.score_bridge import ScoreBridge, GatewayBoard
from mco.orchestrator.scores import ScoreError

RUN = "via-g01-20260914"
AUDITOR = "score-v1-auditor-20260914"
REVIEWER = "score-v1-reviewer-20260914"
SCOPES = ["jobs:read", "jobs:write"]


def client(token_path, role, identity):
    token = Path(token_path).read_text(encoding="utf-8").strip()
    if not token:
        raise ScoreError("Missing token")
    return GatewayClient(base_url="http://127.0.0.1:18789",token=token,role=role,instance_id=identity)


def register(board, private, instance, role):
    path = private / (instance + ".token")
    if path.exists():
        return
    # Never automatically rotate an existing identity or repeat ambiguous registration.
    existing = board.request("GET", "/api/agents")
    if any(a.get("instance_id") == instance for a in existing):
        raise ScoreError("Identity exists without retained credential; explicit recovery needed")
    response = board.request("POST", "/api/agents", json=dict(instance_id=instance,role=role,org="default",scopes=SCOPES))
    agent = response.get("agent", {})
    if not response.get("success") or agent.get("instance_id") != instance or agent.get("role") != role or sorted(agent.get("scopes") or []) != sorted(SCOPES):
        raise ScoreError("Registration scope/identity verification failed; do not use credential")
    token = response.get("token")
    if not isinstance(token,str) or not token:
        raise ScoreError("No credential returned")
    with path.open("x",encoding="utf-8") as stream:
        stream.write(token)
    os.chmod(path,0o600)


def audit_score():
    return dict(score_version=1,id="via-cloud-readonly-audit",revision=1,objective="Collect and independently verify VIA cloud-readiness evidence without changing production.",
        constraints=["Read-only AWS/public API inspection only.","No deployments, purchases, IAM changes, service restarts, secret dumps or source-data mutations.","Audit acceptance is not launch approval; missing evidence remains explicit."],
        budget_cents=0,max_parallel=1,launch_requires=["G01-audit"],tasks=[
            dict(id="G01-audit",goal="G01",title="VIA read-only cloud baseline",instructions="Execute only the fixed via_readonly_audit collector. Inspect current cloud configuration and report unknowns honestly. Do not execute job-provided arbitrary commands.",
                role="score-auditor",review_role="score-review",depends_on=[],resources=["via-readonly-audit"],capabilities=["cloud:inspect","evidence:write"],
                evidence=["audit_report"],max_attempts=1,timeout_seconds=14400,max_cost_cents=0,checkpoint=None)])


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("command",choices=["init","tick","audit","status"])
    parser.add_argument("--root",type=Path,required=True)
    parser.add_argument("--conductor-token",type=Path,required=True)
    parser.add_argument("--run",default=RUN,
                        help="New run identifier; never reuse an accepted run for fresh evidence.")
    parser.add_argument("--identity-root",type=Path,
                        help="Existing approved identity root. Tokens are referenced in place, never copied or rotated.")
    args=parser.parse_args()
    root=args.root.resolve()
    run_id=args.run
    root.mkdir(parents=True,exist_ok=True)
    private=(args.identity_root.resolve() if args.identity_root else root)/"private"
    private.mkdir(exist_ok=True)
    if args.command=="init" and os.name=="nt":
        principal=subprocess.run(["whoami"],capture_output=True,text=True,check=True).stdout.strip()
        subprocess.run(["icacls",str(private),"/inheritance:r","/grant:r",principal+":(OI)(CI)F"],capture_output=True,check=True)
    os.environ["MCO_RESULT_SPOOL_DIR"]=str(private/"reports")
    board=GatewayBoard(client(args.conductor_token,"codex","codex-beast"))
    bridge=ScoreBridge(root/"score.db",root/"artifacts")
    if args.command=="init":
        register(board,private,AUDITOR,"score-auditor")
        register(board,private,REVIEWER,"score-review")
        bridge.initialize(run_id,audit_score(),principal="codex-beast",org="default",targets={"score-auditor":AUDITOR,"score-review":REVIEWER},credential_hash=board.identity)
    elif args.command=="tick":
        bridge.poll(run_id,board)
        bridge.plan(run_id)
        bridge.dispatch(run_id,board)
    elif args.command=="audit":
        from mco.orchestrator.via_readonly_audit import run_audit
        rows=[r for r in bridge.status(run_id)["dispatches"] if r["phase"]=="work"]
        if len(rows)!=1:
            raise ScoreError("Expected exactly one persisted audit job")
        job_id=rows[0]["job_id"]
        job=board.get(job_id)
        if job.get("status")!="completed":
            if job.get("target_agent_id")!=AUDITOR or job.get("input_payload",{}).get("score",{}).get("run_id")!=run_id:
                raise ScoreError("Wrong audit assignment")
            worker=client(private/(AUDITOR+".token"),"score-auditor",AUDITOR)
            claim_file=private/"audit-lease.json"
            if job.get("status") in ("leased","in_progress") and claim_file.exists():
                claim=json.loads(claim_file.read_text())
                worker._leases[job_id]=claim
                worker.renew(job_id)
            else:
                lease=worker.lease(job_id)
                if not lease.get("success") or not lease.get("lease"):
                    raise ScoreError("Audit lease unavailable")
                claim_file.write_text(json.dumps(lease["lease"]),encoding="utf-8")
            artifact=run_audit(bridge.root,include_ssm_diagnostic=False)
            result={"artifacts":{"audit_report":{"path":artifact.name,"sha256":hashlib.sha256(artifact.read_bytes()).hexdigest()}}}
            worker.complete(job_id,json.dumps(result))
        bridge.poll(run_id,board)
        bridge.plan(run_id)
        bridge.dispatch(run_id,board)
    print(json.dumps(bridge.status(run_id),indent=2))


if __name__=="__main__":
    main()
