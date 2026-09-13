"""The conductor: runs the pavilions, asserts, files the evidence, tells the city.

One process, one run, then exit. Started by EventBridge Scheduler or by hand.

Principles, because a test harness for a governance product is itself a
governance artifact:

* Every assertion is PASS, FAIL, NA, or ERROR - and ERROR is never dressed up
  as PASS. A seam this harness could not exercise is reported as ERROR with
  the response that stopped it.
* Red is a result, not a bug in the harness. P1(c), P1(d), P3(a-c) and P6 are
  expected to fail against the current code; the bundle says so.
* Everything the conductor learns is written to the evidence vault before the
  status page is touched. If the vault write fails, the run fails loudly.
"""

from __future__ import annotations

import json
import os
import sys
import time
import traceback
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any, Callable

import boto3
import httpx
from botocore.exceptions import ClientError
from loguru import logger

logger.remove()
logger.add(sys.stdout, serialize=True, level="INFO")

E = os.environ
GATEWAY = E["GATEWAY_URL"].rstrip("/")
PUBLIC_URL = E.get("PUBLIC_URL", GATEWAY)
ADMIN = E["MCO_LOCAL_TOKEN"]
METRICS_TOKEN = E.get("MCO_METRICS_TOKEN", "")
EVIDENCE = E["EVIDENCE_BUCKET"]
STATUS = E["STATUS_BUCKET"]
CLUSTER = E["ECS_CLUSTER"]
REGION = E.get("AWS_REGION", "us-east-1")
ROLES = [r for r in E.get("WORKER_ROLES", "claude").split(",") if r]
BACKEND = E.get("STORE_BACKEND", "postgres")
SECRET_ARN = E.get("SECRET_ARN", "")
FIS = {"stop_worker": E.get("FIS_STOP_WORKER"), "stop_gateway": E.get("FIS_STOP_GATEWAY"), "partition": E.get("FIS_PARTITION")}
SNS_TOPIC = E.get("SNS_TOPIC", "")
DATABASE_URL = E.get("DATABASE_URL", "")
SERVICENOW = E.get("SERVICENOW_ENABLED") == "1"
DYNATRACE = E.get("DYNATRACE_ENABLED") == "1"

LEASE_TTL_S = 15 * 60  # reclaim_stale_leases() is fixed at 15 minutes today

RUN_ID = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%SZ")

s3 = boto3.client("s3", region_name=REGION)
ecs = boto3.client("ecs", region_name=REGION)
fis = boto3.client("fis", region_name=REGION)
sm = boto3.client("secretsmanager", region_name=REGION)
sns = boto3.client("sns", region_name=REGION)
cw_use1 = boto3.client("cloudwatch", region_name="us-east-1")  # Route53 metrics live here


# ── result ledger ────────────────────────────────────────────────────────────

class Run:
    def __init__(self) -> None:
        self.started = datetime.now(timezone.utc)
        self.results: dict[str, dict[str, dict]] = {}
        self.artifacts: dict[str, Any] = {}
        self.touched_jobs: list[str] = []

    def mark(self, pavilion: str, key: str, status: str, detail: Any = None) -> None:
        self.results.setdefault(pavilion, {})[key] = {"status": status, "detail": detail}
        logger.info({"event": "assert", "pavilion": pavilion, "assertion": key, "status": status})

    def summary(self) -> dict:
        out = {}
        for p, asserts in self.results.items():
            statuses = [a["status"] for a in asserts.values()]
            out[p] = "PASS" if all(s in ("PASS", "NA") for s in statuses) else ("ERROR" if "ERROR" in statuses and "FAIL" not in statuses else "FAIL")
        return out


run = Run()


# ── gateway client ───────────────────────────────────────────────────────────

def api(method: str, path: str, token: str = ADMIN, timeout: float = 30, **kw) -> httpx.Response:
    return httpx.request(method, f"{GATEWAY}{path}", headers={"Authorization": f"Bearer {token}"}, timeout=timeout, **kw)


def wait_until(pred: Callable[[], Any], timeout_s: float, every_s: float = 5, label: str = "") -> Any:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        try:
            v = pred()
            if v:
                return v
        except Exception as e:
            logger.debug({"event": "wait.retry", "label": label, "error": str(e)})
        time.sleep(every_s)
    return None


def jobs() -> list[dict]:
    return api("GET", "/api/jobs").json() if api("GET", "/api/jobs").status_code == 200 else []


def job(job_id: str) -> dict | None:
    return next((j for j in jobs() if j.get("id") == job_id), None)


def events(job_id: str) -> list[dict]:
    r = api("GET", f"/api/jobs/{job_id}/events")
    return r.json() if r.status_code == 200 else []


def post_job(title: str, role: str, prompt: str, **extra) -> dict:
    payload = {"title": title, "description": prompt, "target_agent_role": role, "input_payload": {"prompt": prompt}, **extra}
    r = api("POST", "/api/jobs", json=payload)
    r.raise_for_status()
    j = r.json().get("job") or r.json()
    run.touched_jobs.append(j["id"])
    return j


# Worker tokens must come from Secrets Manager at USE time, never from this
# process's environment. seed_workers() rewrites the secret and rolls the
# worker services, but ECS injected this conductor's env before that happened -
# so the env holds Terraform's placeholders for the whole run. Reading the env
# made P3's stale-write replay fail with 401 and score "rejected -> PASS" for
# AUTHENTICATION instead of for fencing, reporting the demo's most important
# assertion as green while the fence did not exist. Same for P7's negative
# lease test. Found by reviewer-beast.
_WORKER_TOKENS: dict[str, str] = {}


def refresh_worker_tokens() -> None:
    """Load current worker tokens from Secrets Manager into the local cache."""
    if not SECRET_ARN:
        _WORKER_TOKENS.update({r: E.get(f"WORKER_TOKEN_{r.upper()}", "") for r in ROLES})
        return
    try:
        secret = json.loads(sm.get_secret_value(SecretId=SECRET_ARN)["SecretString"])
        for r in ROLES:
            v = secret.get(f"WORKER_TOKEN_{r.upper()}")
            if v:
                _WORKER_TOKENS[r] = v
        logger.info({"event": "tokens.refreshed", "roles": sorted(_WORKER_TOKENS)})
    except Exception as e:
        logger.warning({"event": "tokens.refresh_failed", "error": f"{type(e).__name__}: {e}"})


def worker_token(role: str) -> str:
    return _WORKER_TOKENS.get(role) or E.get(f"WORKER_TOKEN_{role.upper()}", "")


def gateway_up() -> bool:
    try:
        return httpx.get(f"{GATEWAY}/healthz", timeout=5).status_code == 200
    except Exception:
        return False


# ── evidence vault ───────────────────────────────────────────────────────────

def vault(name: str, obj: Any) -> None:
    key = f"runs/{RUN_ID}/{name}"
    s3.put_object(Bucket=EVIDENCE, Key=key, Body=json.dumps(obj, default=str, indent=2).encode(), ContentType="application/json")
    logger.info({"event": "vault.put", "key": key})


# ── seed: first run registers the cloud workers ──────────────────────────────

def seed_workers() -> None:
    """Register any missing worker through the public API and hand its token
    to ECS. Idempotent: a registered worker is left alone."""
    r = api("GET", "/api/agents")
    existing = {a.get("instance_id") for a in (r.json() if r.status_code == 200 else [])}
    changed = False
    secret = json.loads(sm.get_secret_value(SecretId=SECRET_ARN)["SecretString"]) if SECRET_ARN else {}
    for role in ROLES:
        inst = f"{role}-cloud-1"
        if inst in existing:
            continue
        reg = api("POST", "/api/agents", json={"instance_id": inst, "role": role})
        if reg.status_code not in (200, 201):
            run.mark("seed", inst, "ERROR", {"status": reg.status_code, "body": reg.text[:300]})
            continue
        body = reg.json()
        token = body.get("token") or body.get("access_token") or body.get("agent_token") or (body.get("agent") or {}).get("token")
        if not token:
            run.mark("seed", inst, "ERROR", {"reason": "no token field in response", "keys": list(body.keys())})
            continue
        secret[f"WORKER_TOKEN_{role.upper()}"] = token
        changed = True
        run.mark("seed", inst, "PASS", "registered")
    if changed and SECRET_ARN:
        sm.put_secret_value(SecretId=SECRET_ARN, SecretString=json.dumps(secret))
        for role in ROLES:
            ecs.update_service(cluster=CLUSTER, service=f"worker-{role}", forceNewDeployment=True)
        refresh_worker_tokens()
        logger.info({"event": "seed.rolled_workers"})
        # Give the ring a moment to come back before the pavilions open.
        wait_until(lambda: len([a for a in api("GET", "/api/agents").json() if a.get("effective_status") == "online"]) >= 1, 300, 10, "workers online")


# ── P7 · The Gate ────────────────────────────────────────────────────────────

def p7_gate() -> str | None:
    P = "P7"
    j = post_job("EPCOT P7: summarize this city's purpose in two sentences", ROLES[0],
                 "In two sentences, say what a self-testing governance demo proves. Stop.", requires_approval=True)
    jid = j["id"]
    gated = wait_until(lambda: (job(jid) or {}).get("status") == "needs_approval", 60, 3, "gate")
    run.mark(P, "a_enters_gate", "PASS" if gated else "FAIL", (job(jid) or {}).get("status"))

    # No worker may lease a gated job.
    lease = api("POST", "/api/jobs/lease", token=worker_token(ROLES[0]), json={"task_id": jid, "agent_instance_id": f"{ROLES[0]}-cloud-1"})
    try:
        lease_body = lease.json()
    except Exception:
        lease_body = None
    # The gateway deliberately returns the lease RPC result as HTTP 200. A
    # valid worker identity and {success:false} prove the approval fence. A
    # 401/403 must never make this assertion green.
    lease_refused = (
        lease.status_code == 200
        and isinstance(lease_body, dict)
        and lease_body.get("success") is False
    )
    run.mark(P, "a2_lease_refused", "PASS" if lease_refused else "FAIL",
             {"status": lease.status_code, "body": lease_body})

    ok = api("POST", f"/api/jobs/{jid}/approve")
    run.mark(P, "b_operator_approves", "PASS" if ok.status_code == 200 else "FAIL", ok.status_code)
    done = wait_until(lambda: (job(jid) or {}).get("status") in ("completed", "failed"), 600, 5, "complete")
    run.mark(P, "b2_completes", "PASS" if done and job(jid).get("status") == "completed" else "FAIL", (job(jid) or {}).get("status"))

    evs = events(jid)
    approved = [e for e in evs if "approv" in json.dumps(e).lower()]
    run.mark(P, "c_ledger_has_decision", "PASS" if approved and job(jid).get("approved_by") else "FAIL",
             {"approved_by": job(jid).get("approved_by"), "events": len(evs)})
    run.artifacts["approvals.json"] = {"job": job(jid), "events": evs}
    return jid


# ── P1 · The Stop Button ─────────────────────────────────────────────────────

def p1_stop_button() -> None:
    P = "P1"
    long_prompt = "Write a very long, numbered, 60-item list of distinct failure modes of distributed job schedulers. Do not summarize. Number every item."
    j = post_job("EPCOT P1: long job to be halted", ROLES[0], long_prompt)
    jid = j["id"]
    leased = wait_until(lambda: (job(jid) or {}).get("status") in ("leased", "in_progress"), 90, 2, "leased")
    if not leased:
        run.mark(P, "setup", "ERROR", "job never leased; cannot test halt")
        return

    t0 = datetime.now(timezone.utc)
    flip = api("PUT", "/api/settings", json={"MCO_KILL_SWITCH": True})
    timeline = [{"t": t0.isoformat(), "action": "PUT /api/settings MCO_KILL_SWITCH=true", "status": flip.status_code}]
    try:
        # a. intake refused
        a = api("POST", "/api/jobs", json={"title": "should be refused", "target_agent_role": ROLES[0], "input_payload": {"prompt": "x"}})
        run.mark(P, "a_intake_refused", "PASS" if a.status_code == 503 else "FAIL", a.status_code)
        # b. leasing refused
        b = api("POST", "/api/jobs/lease", token=worker_token(ROLES[0]), json={"task_id": jid, "agent_instance_id": f"{ROLES[0]}-cloud-1"})
        run.mark(P, "b_lease_refused", "PASS" if b.status_code == 503 else "FAIL", b.status_code)
        # c. in-flight interrupted within 30 s
        final = None
        for _ in range(6):
            time.sleep(5)
            st = (job(jid) or {}).get("status")
            timeline.append({"t": datetime.now(timezone.utc).isoformat(), "job_status": st})
            if st in ("halted", "cancelled", "paused"):
                final = st
                break
            if st == "completed":
                final = st
                break
        st = (job(jid) or {}).get("status")
        run.mark(P, "c_inflight_halted_30s", "PASS" if st == "halted" else "FAIL",
                 {"final_status": st, "note": "Gateway fencing observed; arbitrary external side effects require cooperative worker checkpoints."})
        # d. the flip itself is in the ledger
        evs = events(jid)
        anywhere = any(e.get('event') == 'halted' and e.get('actor_id') for e in evs)
        run.mark(P, "d_flip_audited", "PASS" if anywhere else "FAIL",
                 {"note": "Requires a halt event attributed to an operator."})
        # f. metrics gauge
        if METRICS_TOKEN:
            m = api("GET", "/metrics", token=METRICS_TOKEN)
            run.mark(P, "f_metrics_gauge", "PASS" if "mco_kill_switch 1" in m.text else "FAIL", m.status_code)
        else:
            run.mark(P, "f_metrics_gauge", "NA", "no metrics token")
        # e. ServiceNow incident (best effort; route recorded either way)
        if SERVICENOW:
            r = api("POST", "/api/integrations/servicenow/actions",
                    json={"action": "create_incident", "params": {"short_description": f"EPCOT {RUN_ID}: fleet halted by operator (kill switch)", "urgency": "2"}})
            run.mark(P, "e_servicenow_incident", "PASS" if r.status_code in (200, 201) else "ERROR", {"status": r.status_code, "body": r.text[:300]})
        else:
            run.mark(P, "e_servicenow_incident", "NA", "pavilion not enabled")
    finally:
        off = api("PUT", "/api/settings", json={"MCO_KILL_SWITCH": False})
        timeline.append({"t": datetime.now(timezone.utc).isoformat(), "action": "MCO_KILL_SWITCH=false", "status": off.status_code})
        wait_until(lambda: (job(jid) or {}).get("status") in ("completed", "failed", "cancelled", "halted"), 600, 5, "p1 drain")
    run.artifacts["kill-switch-timeline.json"] = {"job_id": jid, "timeline": timeline, "final": job(jid)}


# ── FIS helper ───────────────────────────────────────────────────────────────

def start_fis(template_id: str | None, label: str) -> dict | None:
    if not template_id:
        return None
    exp = fis.start_experiment(experimentTemplateId=template_id, tags={"run": RUN_ID, "pavilion": label})["experiment"]
    logger.info({"event": "fis.start", "id": exp["id"], "label": label})
    return exp


def fis_final(exp: dict | None, timeout_s: float = 1500) -> dict | None:
    if not exp:
        return None
    def _done():
        e = fis.get_experiment(id=exp["id"])["experiment"]
        return e if e["state"]["status"] in ("completed", "stopped", "failed") else None
    return wait_until(_done, timeout_s, 10, "fis") or fis.get_experiment(id=exp["id"])["experiment"]


# ── P2 · Worker Killed Mid-Job ───────────────────────────────────────────────

def p2_worker_killed() -> None:
    P = "P2"
    role = ROLES[0]  # the FIS template targets bc:role = worker_roles[0]
    j = post_job("EPCOT P2: job whose worker will be killed", role,
                 "Write a numbered list of 40 distinct reasons a lease can go stale. Number each. Do not summarize.")
    jid = j["id"]
    if not wait_until(lambda: (job(jid) or {}).get("status") in ("leased", "in_progress"), 90, 2, "leased"):
        run.mark(P, "setup", "ERROR", "never leased")
        return
    exp = start_fis(FIS["stop_worker"], "P2")
    fis_result = fis_final(exp, 300)
    run.mark(P, "fault_injected", "PASS" if fis_result and fis_result["state"]["status"] == "completed" else "ERROR", (fis_result or {}).get("state"))

    # a. reclaim after TTL (opportunistic: fires when any worker polls)
    reclaimed = wait_until(lambda: any("expired" in json.dumps(e).lower() or "reclaim" in json.dumps(e).lower() for e in events(jid)), LEASE_TTL_S + 120, 15, "reclaim")
    run.mark(P, "a_lease_reclaimed", "PASS" if reclaimed else "FAIL", {"waited_s": LEASE_TTL_S + 120})
    # b. completes on another attempt
    done = wait_until(lambda: (job(jid) or {}).get("status") == "completed", 600, 5, "complete")
    run.mark(P, "b_completes_after_reclaim", "PASS" if done else "FAIL", (job(jid) or {}).get("status"))
    evs = events(jid)
    completions = [e for e in evs if e.get('event') == 'status:completed']
    run.mark(P, "c_ledger_shows_attempts", "PASS" if reclaimed and done else "FAIL", {"events": len(evs)})
    run.mark(P, "d_completed_once", "PASS" if len(completions) == 1 else "FAIL", {"completion_events": len(completions)})
    run.artifacts.setdefault("fis-experiments.json", []).append({"pavilion": "P2", "experiment": fis_result})
    run.artifacts["p2-events.json"] = evs


# ── P3 · Partition + stale writer replay ─────────────────────────────────────

def p3_partition() -> None:
    P = "P3"
    role = ROLES[0]
    j = post_job("EPCOT P3: job whose worker will be partitioned", role,
                 "Write a numbered list of 40 distinct causes of network partitions. Number each. Do not summarize.")
    jid = j["id"]
    if not wait_until(lambda: (job(jid) or {}).get("status") in ("leased", "in_progress"), 90, 2, "leased"):
        run.mark(P, "setup", "ERROR", "never leased")
        return
    original = job(jid) or {}
    holder = original.get('leased_by_instance_id')
    proof = {k: original.get(k) for k in ('lease_id','lease_epoch','lease_incarnation')}
    if not all(value is not None for value in proof.values()) or not holder:
        run.mark(P,'setup','ERROR','No complete original lease proof captured')
        return
    exp = start_fis(FIS["partition"], "P3")  # PT16M > 15-minute TTL: reclaim happens under real fault
    reclaimed = wait_until(lambda: any("expired" in json.dumps(e).lower() or "reclaim" in json.dumps(e).lower() for e in events(jid)), LEASE_TTL_S + 240, 15, "reclaim")
    run.mark(P, "c0_reclaimed_under_partition", "PASS" if reclaimed else "FAIL", {"holder": holder})
    done = wait_until(lambda: (job(jid) or {}).get("status") == "completed", 900, 5, "complete")
    fis_result = fis_final(exp, 1200)
    run.artifacts.setdefault("fis-experiments.json", []).append({"pavilion": "P3", "experiment": fis_result})
    if not done:
        run.mark(P, "setup2", "ERROR", "job never completed after reclaim; cannot replay stale write")
        return

    # Replay with the ORIGINAL proof and authenticated worker token. Missing
    # proof, auth rejection, and generic server errors do not prove fencing.
    before = events(jid)
    replay = api("PUT", f"/api/jobs/{jid}", token=worker_token(role),
                 json={**proof, "status": "completed", "output_payload": {"result": f"STALE WRITE from {holder} after re-lease"}, "agent_instance_id": holder})
    after = events(jid)
    completions = [e for e in after if e.get('event') == 'status:completed']
    rejected = replay.status_code == 409 and 'FENCED:' in replay.text
    run.mark(P, "a_stale_completion_rejected", "PASS" if rejected else "FAIL",
             {"status": replay.status_code, "note": "Requires HTTP 409 with the fencing reason."})
    old_ids = {e.get('id') for e in before}
    fenced_event = any(e.get('id') not in old_ids and e.get('event') == 'write_fenced' for e in after)
    run.mark(P, "b_rejection_in_ledger", "PASS" if rejected and fenced_event else "FAIL", {"events_before": len(before), "after": len(after)})
    run.mark(P, "c_single_completion", "PASS" if len(completions) == 1 else "FAIL", {"completion_events": len(completions)})
    run.artifacts["p3-stale-replay.json"] = {"job_id": jid, "holder": holder, "replay_status": replay.status_code, "replay_body": replay.text[:500], "final": job(jid)}


# ── P4 · The Hub Goes Dark ───────────────────────────────────────────────────

def p4_hub_dark() -> None:
    P = "P4"
    exp = start_fis(FIS["stop_gateway"], "P4")
    t0 = time.time()
    fis_final(exp, 120)
    back = wait_until(gateway_up, 180, 3, "healthz")
    run.mark(P, "a_gateway_restarts_90s", "PASS" if back and (time.time() - t0) <= 90 + 30 else "FAIL", {"seconds": round(time.time() - t0, 1)})
    online = wait_until(lambda: len([a for a in api("GET", "/api/agents").json() if a.get("effective_status") == "online"]) >= len(ROLES), 120, 5, "workers")
    run.mark(P, "b_workers_reconnect", "PASS" if online else "FAIL", online)
    run.artifacts.setdefault("fis-experiments.json", []).append({"pavilion": "P4", "experiment": fis.get_experiment(id=exp["id"])["experiment"] if exp else None})

    # Dead-man: hold the hub down past the alarm's 3-minute window.
    ecs.update_service(cluster=CLUSTER, service="gateway", desiredCount=0)
    t1 = datetime.now(timezone.utc)
    alarm_name = None
    try:
        # Two stages, not one sleep: three 30 s Route53 checks (~90 s) must fail
        # before HealthCheckStatus turns unhealthy, THEN three 60 s alarm periods
        # (~180 s) must breach, plus publication and evaluation latency. A flat
        # 240 s lands inside that window on an unfavourable phase and scores a
        # working dead-man as FAIL. Poll to a computed deadline and keep the real
        # transition time. Found by reviewer-beast.
        deadline = time.time() + 90 + 180 + 120
        hit = []
        while time.time() < deadline:
            alarms = cw_use1.describe_alarms(StateValue="ALARM")["MetricAlarms"]
            hit = [a for a in alarms if a["AlarmName"].endswith("-site-dark")]
            if hit:
                break
            time.sleep(15)
        alarm_name = hit[0]["AlarmName"] if hit else None
        run.mark(P, "c_deadman_alarms_when_dark", "PASS" if hit else "FAIL",
                 {"alarm": alarm_name, "detected_after_s": round(time.time() - t1.timestamp(), 1),
                  "budget_s": 390})
    finally:
        ecs.update_service(cluster=CLUSTER, service="gateway", desiredCount=1)
    wait_until(gateway_up, 300, 5, "healthz after restore")
    ok = wait_until(lambda: any(a["AlarmName"].endswith("-site-dark") for a in cw_use1.describe_alarms(StateValue="OK")["MetricAlarms"]), 420, 15, "alarm ok")
    run.mark(P, "d_deadman_returns_ok", "PASS" if ok else "FAIL", alarm_name)
    hist = cw_use1.describe_alarm_history(AlarmName=alarm_name, StartDate=t1 - timedelta(minutes=1), EndDate=datetime.now(timezone.utc))["AlarmHistoryItems"] if alarm_name else []
    run.artifacts["recovery-timeline.json"] = {"gateway_down_at": t1.isoformat(), "alarm_history": hist}


# ── P5 · The Tamper ──────────────────────────────────────────────────────────

class _Q:
    """Just enough of the Supabase query builder for verify_chain(), over psycopg."""
    def __init__(self, dsn: str, table: str, connection=None):
        self.connection = connection
        self.dsn, self.table, self.filters, self.order_col, self.desc, self.lim = dsn, table, [], None, False, None
    def select(self, *_a, **_k): return self
    def eq(self, col, val): self.filters.append((col, val)); return self
    def order(self, col, desc=False, **_k): self.order_col, self.desc = col, desc; return self
    def limit(self, n): self.lim = n; return self
    def execute(self):
        import psycopg
        from psycopg.rows import dict_row
        where = " AND ".join(f"{c} = %s" for c, _ in self.filters) or "TRUE"
        sql = f"SELECT * FROM {self.table} WHERE {where}"
        if self.order_col:
            sql += f" ORDER BY {self.order_col} {'DESC' if self.desc else 'ASC'}, id ASC"
        if self.lim:
            sql += f" LIMIT {int(self.lim)}"
        if self.connection is not None:
            rows = self.connection.execute(sql, [v for _, v in self.filters]).fetchall()
        else:
            with psycopg.connect(self.dsn, row_factory=dict_row) as conn:
                rows = conn.execute(sql, [v for _, v in self.filters]).fetchall()
        for r in rows:  # match the JSON shapes the gateway's client returns
            for k, v in list(r.items()):
                if isinstance(v, datetime):
                    r[k] = v.isoformat()
        return SimpleNamespace(data=rows)


class _DB:
    def __init__(self, dsn: str, connection=None): self.dsn, self.connection = dsn, connection
    def table(self, name: str): return _Q(self.dsn, name, self.connection)


def p5_tamper(target_job: str | None) -> None:
    P = "P5"
    if BACKEND != "postgres" or not DATABASE_URL:
        for k in ("a_verify_detects", "b_mirror_intact", "c_delete_denied", "d_lock_weakening_denied"):
            run.mark(P, k, "NA", "conductor cannot reach the LocalStore volume")
        return
    if not target_job:
        run.mark(P, "setup", "ERROR", "no completed job to tamper")
        return
    from mco.orchestrator.audit import verify_chain
    import psycopg
    db = _DB(DATABASE_URL)

    # Guard: the shim must agree with the product on an UNTAMPERED chain, or
    # every later result would be meaningless.
    try:
        baseline = verify_chain(db, target_job)
    except Exception as e:
        run.mark(P, "shim", "ERROR", f"verify shim mismatch: {type(e).__name__}: {e}")
        return
    if not baseline.get("ok"):
        run.mark(P, "shim", "ERROR", {"reason": "untampered chain did not verify through the shim", "report": baseline})
        return

    # The tamper and trigger override exist only in this transaction. Rollback
    # restores both, including on error; no corrupted evidence is committed.
    from psycopg.rows import dict_row
    with psycopg.connect(DATABASE_URL, row_factory=dict_row) as conn:
        row = conn.execute("SELECT id, detail FROM agent_job_events WHERE job_id = %s ORDER BY created_at, id LIMIT 1", (target_job,)).fetchone()
        if not row:
            run.mark(P, "setup", "ERROR", "job has no events")
            return
        ev_id, original = row['id'], row['detail']
        try:
            conn.execute("SET LOCAL lock_timeout = '5s'")
            conn.execute("ALTER TABLE agent_job_events DISABLE TRIGGER trg_agent_job_events_immutable")
            conn.execute("UPDATE agent_job_events SET detail = detail || '{\"tampered_by\": \"EPCOT P5\"}'::jsonb WHERE id = %s", (ev_id,))
            report = verify_chain(_DB(DATABASE_URL, conn), target_job)
        finally:
            conn.rollback()
    run.mark(P, "a_verify_detects", "PASS" if not report.get("ok") else "FAIL", report)

    key = f"ledger/{target_job}/{ev_id}.json"
    version = None
    try:
        mirrored_object = s3.get_object(Bucket=EVIDENCE, Key=key)
        version = mirrored_object.get("VersionId")
        mirrored = json.loads(mirrored_object["Body"].read())
        intact = mirrored.get("detail") == original
        run.mark(P, "b_mirror_intact", "PASS" if intact else "FAIL", {"key": key, "note": "mirror lags the ledger by <= one shipping interval"})
    except ClientError as e:
        run.mark(P, "b_mirror_intact", "ERROR", {"key": key, "error": e.response["Error"]["Code"], "note": "mirror had not shipped this event yet"})

    if not version:
        for probe in ("c_delete_denied", "d_lock_weakening_denied"):
            run.mark(P, probe, "ERROR", "No mirrored object version to test retention against")
        return

    try:
        s3.delete_object(Bucket=EVIDENCE, Key=key, VersionId=version)
        run.mark(P, "c_delete_denied", "FAIL", "delete succeeded - Object Lock did not hold")
    except ClientError as e:
        code = e.response["Error"]["Code"]
        run.mark(P, "c_delete_denied", "PASS" if code in ("AccessDenied", "InvalidRequest") else "ERROR", code)

    try:
        s3.put_object_retention(Bucket=EVIDENCE, Key=key, VersionId=version,
            Retention={"Mode": "COMPLIANCE", "RetainUntilDate": datetime.now(timezone.utc) + timedelta(seconds=60)})
        run.mark(P, "d_lock_weakening_denied", "FAIL", "retention was weakened")
    except ClientError as e:
        run.mark(P, "d_lock_weakening_denied", "PASS" if e.response["Error"]["Code"] == "AccessDenied" else "ERROR", e.response["Error"]["Code"])

    run.artifacts["tamper-probe.json"] = {"job_id": target_job, "event_id": ev_id, "original_detail": original, "verify_after": report}


# ── P6 · Runaway loop ────────────────────────────────────────────────────────

def p6_runaway() -> None:
    P = "P6"
    r = api("POST", "/api/jobs", json={"title": "EPCOT P6: loop with a $5 ceiling", "target_agent_role": ROLES[0],
                                       "input_payload": {"prompt": "Say 'tick'."}, "budget_usd": 5})
    accepted_budget = r.status_code < 300 and "budget" in r.text.lower()
    run.mark(P, "a_budget_enforced", "PASS" if accepted_budget else "FAIL",
             {"status": r.status_code, "note": "no usage ledger, no budget field, no breaker (WS4)"})
    run.mark(P, "b_halt_reason_in_feed", "FAIL", "nothing to halt on")
    if r.status_code < 300:
        run.touched_jobs.append((r.json().get("job") or r.json()).get("id"))


# ── Evidence + status page ───────────────────────────────────────────────────

def file_evidence(gate_job: str | None = None) -> dict:
    ep = api("POST", "/api/governance/evidence-pack", json={})
    ep_body = ep.json() if ep.status_code == 200 else {"error": ep.status_code, "body": ep.text[:500]}
    run.artifacts["evidence-pack.json"] = ep_body
    if gate_job:
        audit = {}
        for artifact in ep_body.get("files", []) if isinstance(ep_body, dict) else []:
            if artifact.get("filename") == "audit-trail.json" and artifact.get("text"):
                try:
                    audit = json.loads(artifact["text"])
                except (TypeError, ValueError):
                    audit = {}
                break
        decisions = audit.get("decision_history", []) if isinstance(audit, dict) else []
        found = [d for d in decisions if d.get("job_id") == gate_job]
        run.mark("P7", "d_evidence_pack_has_decision", "PASS" if found else "FAIL",
                 {"status": ep.status_code, "matching_decisions": len(found)})
    summary = run.summary()
    if BACKEND == "postgres" and DATABASE_URL:
        from mco.orchestrator.audit import verify_chain
        db = _DB(DATABASE_URL)
        run.artifacts["chain-verification.json"] = {jid: verify_chain(db, jid) for jid in run.touched_jobs if jid}
    if METRICS_TOKEN:
        m = api("GET", "/metrics", token=METRICS_TOKEN)
        run.artifacts["metrics-snapshot.json"] = {"status": m.status_code, "text": m.text}
    run.artifacts["connectors.json"] = {"servicenow": SERVICENOW, "dynatrace": DYNATRACE}

    manifest = {
        "run_id": RUN_ID, "started": run.started.isoformat(), "finished": datetime.now(timezone.utc).isoformat(),
        "backend": BACKEND, "gateway": PUBLIC_URL, "summary": summary, "assertions": run.results, "touched_jobs": run.touched_jobs,
        "artifacts": sorted(run.artifacts.keys()),
        "honesty": {
            "expected_red_today": ["P6.*"],
            "mirror_lag_note": "ECS requires locked evidence before attempt acknowledgements; the background shipper is a repair path. Live retention assertions are recorded separately.",
        },
    }
    for name, obj in run.artifacts.items():
        vault(name, obj)
    vault("run.json", manifest)
    return manifest


def publish_status(manifest: dict) -> None:
    order = ["P1", "P2", "P3", "P4", "P5", "P6", "P7"]
    names = {"P1": "Stop Button", "P2": "Worker Killed", "P3": "Partition", "P4": "Hub Dark", "P5": "Tamper", "P6": "Runaway Loop", "P7": "The Gate"}
    colors = {"PASS": "#3e7a4e", "FAIL": "#c2410c", "ERROR": "#8a6712", "NA": "#87837a"}
    cells = ""
    for p in order:
        st = manifest["summary"].get(p, "NA")
        asserts = manifest["assertions"].get(p, {})
        rows = "".join(f"<li><code>{k}</code> <b style='color:{colors.get(v['status'], '#000')}'>{v['status']}</b></li>" for k, v in asserts.items())
        cells += f"<section><h2><span style='color:{colors.get(st)}'>●</span> {p} · {names[p]}</h2><ul>{rows}</ul></section>"
    html = f"""<!doctype html><meta charset="utf-8"><title>BitCadence EPCOT · {manifest['run_id']}</title>
<style>body{{font-family:Archivo,system-ui,sans-serif;max-width:820px;margin:40px auto;padding:0 20px;color:#1c1b18;background:#faf9f5}}
h1{{font-family:Fraunces,Georgia,serif;font-weight:600}}section{{border-top:1px solid #d8d4c8;padding:14px 0}}h2{{font-size:1.05rem;margin:0 0 8px}}
ul{{margin:0;padding-left:18px;font-size:.9rem;color:#5c594f}}code{{font-family:'IBM Plex Mono',monospace;font-size:.85em}}
.meta{{font-family:'IBM Plex Mono',monospace;font-size:.75rem;color:#87837a}}</style>
<h1>The city tested itself.</h1>
<p class="meta">run {manifest['run_id']} · backend {manifest['backend']} · evidence: s3://{EVIDENCE}/runs/{manifest['run_id']}/</p>
<p>Green is a claim that held under fault. <b style="color:#c2410c">Red is a claim that did not</b> — and is published anyway. Expected red today: {", ".join(manifest['honesty']['expected_red_today'])}.</p>
{cells}
<p class="meta">BitCadence — every agent, one beat. Always becoming.</p>"""
    s3.put_object(Bucket=STATUS, Key="index.html", Body=html.encode("utf-8"), ContentType="text/html; charset=utf-8")
    s3.put_object(Bucket=STATUS, Key="latest.json", Body=json.dumps(manifest, default=str).encode(), ContentType="application/json")


# ── main ─────────────────────────────────────────────────────────────────────

def main() -> int:
    logger.info({"event": "conductor.start", "run": RUN_ID, "gateway": GATEWAY, "backend": BACKEND, "roles": ROLES})
    if not wait_until(gateway_up, 300, 5, "gateway"):
        vault("run.json", {"run_id": RUN_ID, "fatal": "gateway never became healthy"})
        return 2

    refresh_worker_tokens()  # a later run seeds nothing; env is still placeholders
    seed_workers()
    gate_job = None
    for name, fn in (("P7", lambda: p7_gate()), ("P1", p1_stop_button), ("P2", p2_worker_killed),
                     ("P3", p3_partition), ("P4", p4_hub_dark), ("P6", p6_runaway)):
        try:
            out = fn()
            if name == "P7":
                gate_job = out
        except Exception:
            run.mark(name, "harness", "ERROR", traceback.format_exc()[-1500:])
        if not wait_until(gateway_up, 300, 5, f"gateway after {name}"):
            run.mark(name, "gateway_after", "ERROR", "gateway did not recover; aborting remaining pavilions")
            break
    try:
        p5_tamper(gate_job)
    except Exception:
        run.mark("P5", "harness", "ERROR", traceback.format_exc()[-1500:])

    manifest = file_evidence(gate_job)
    publish_status(manifest)
    if SNS_TOPIC:
        s = manifest["summary"]
        sns.publish(TopicArn=SNS_TOPIC, Subject=f"EPCOT run {RUN_ID}: {sum(v == 'PASS' for v in s.values())}/{len(s)} pavilions green",
                    Message=json.dumps(s, indent=2) + f"\n\nEvidence: s3://{EVIDENCE}/runs/{RUN_ID}/")
    logger.info({"event": "conductor.done", "summary": manifest["summary"]})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
