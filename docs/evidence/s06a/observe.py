"""Read-only evidence dump for one score run: conductor DB + board job events."""
import json, os, sqlite3, sys, urllib.request

RUN = sys.argv[1]
DB = os.environ["MCO_SCORE_DB"]
GW = os.environ.get("MCO_GATEWAY_URL", "http://127.0.0.1:18997")
TOKEN = os.environ.get("MCO_LOCAL_TOKEN", "")

db = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
db.row_factory = sqlite3.Row
run = db.execute("SELECT id,digest,status,principal,org FROM runs WHERE id=?", (RUN,)).fetchone()
dispatch = [dict(r) for r in db.execute(
    "SELECT task,phase,job_id,status FROM dispatch WHERE run=? ORDER BY task,phase", (RUN,))]
events = [dict(r) for r in db.execute(
    "SELECT seq,event,detail,at FROM events WHERE run=? ORDER BY seq", (RUN,))]
db.close()

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

out = {"run": dict(run) if run else None, "dispatch": dispatch,
       "conductor_events": [{**e, "detail": json.loads(e["detail"])} for e in events],
       "board": board}
print(json.dumps(out, indent=2, sort_keys=True))
