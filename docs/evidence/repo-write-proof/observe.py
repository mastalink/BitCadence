"""Read-only evidence dumper for repository:write S06b adversarial proof."""
import json
import os
import sqlite3
import subprocess
import sys
import urllib.request
from pathlib import Path

RUN = sys.argv[1]
DB = os.environ["MCO_SCORE_DB"]
GW = os.environ.get("MCO_GATEWAY_URL", "http://127.0.0.1:18997")
TOKEN = os.environ.get("MCO_LOCAL_TOKEN", "")
WT = os.environ.get("THROWAWAY_WT", "C:/AI/baton/wt/scratch-s06b/throwaway-wt")

# Conductor database
db = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
db.row_factory = sqlite3.Row
run = db.execute("SELECT id,digest,status,principal,org FROM runs WHERE id=?", (RUN,)).fetchone()
dispatch = [dict(r) for r in db.execute(
    "SELECT task,phase,job_id,status,evidence FROM dispatch WHERE run=? ORDER BY task,phase", (RUN,))]
events = [dict(r) for r in db.execute(
    "SELECT seq,event,detail,at FROM events WHERE run=? ORDER BY seq", (RUN,))]
db.close()

# Board API
def api(path):
    req = urllib.request.Request(GW + path, headers={"Authorization": f"Bearer {TOKEN}"})
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.load(r)

board = {}
for d in dispatch:
    try:
        j = api(f"/api/jobs/{d['job_id']}")
        job = j.get("job", j)
        ev = api(f"/api/jobs/{d['job_id']}/events")
        board[d["job_id"]] = {
            "status": job.get("status"),
            "leased_by": job.get("leased_by_instance_id"),
            "lease_epoch": job.get("lease_epoch"),
            "events": [{"event": e.get("event"), "actor": e.get("actor_id"),
                        "role": e.get("actor_role"), "at": e.get("created_at"),
                        "detail": e.get("detail")}
                       for e in (ev.get("events") if isinstance(ev, dict) else ev)],
        }
    except Exception as exc:
        board[d["job_id"]] = {"error": str(exc)}

# LocalStore (score_events and score_recovery)
home = os.environ.get("HOME", "")
store_db = Path(home) / ".mco" / "store.db"
adapter_events = []
recovery_claims = []
if store_db.exists():
    try:
        sdb = sqlite3.connect(f"file:{store_db}?mode=ro", uri=True)
        sdb.row_factory = sqlite3.Row
        ev_rows = sdb.execute("SELECT org_id, run_id, task_id, kind, actor, payload, created_at FROM score_events WHERE run_id=? ORDER BY created_at", (RUN,)).fetchall()
        for r in ev_rows:
            d = dict(r)
            if isinstance(d.get("payload"), str):
                try:
                    d["payload"] = json.loads(d["payload"])
                except Exception:
                    pass
            adapter_events.append(d)

        rec_rows = sdb.execute("SELECT approval_or_attempt_id, decision, uncertain_effect, created_at FROM score_recovery").fetchall()
        for r in rec_rows:
            d = dict(r)
            if isinstance(d.get("uncertain_effect"), str):
                try:
                    d["uncertain_effect"] = json.loads(d["uncertain_effect"])
                except Exception:
                    pass
            recovery_claims.append(d)
        sdb.close()
    except Exception as exc:
        adapter_events = [{"error": str(exc)}]

# Throwaway Git state
git_state = {}
if Path(WT).exists():
    try:
        head_sha = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=WT, text=True).strip()
        branch = subprocess.check_output(["git", "branch", "--show-current"], cwd=WT, text=True).strip()
        log_out = subprocess.check_output(["git", "log", "-n", "3", "--format=%H %an %s"], cwd=WT, text=True).strip().splitlines()
        git_state = {
            "head_sha": head_sha,
            "branch": branch,
            "recent_commits": log_out,
        }
    except Exception as exc:
        git_state = {"error": str(exc)}

out = {
    "run": dict(run) if run else None,
    "dispatch": [
        {**d, "evidence": json.loads(d["evidence"]) if d.get("evidence") else None}
        for d in dispatch
    ],
    "conductor_events": [
        {**e, "detail": json.loads(e["detail"]) if isinstance(e.get("detail"), str) else e.get("detail")}
        for e in events
    ],
    "board": board,
    "adapter_events": adapter_events,
    "recovery_claims": recovery_claims,
    "git_state": git_state,
}
print(json.dumps(out, indent=2, sort_keys=True))
