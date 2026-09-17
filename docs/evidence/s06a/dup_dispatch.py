"""Duplicate delivery, conductor side.

A conductor that dies between `board.create(...)` and the row that records
`submitted` comes back with the dispatch still marked `sending` and re-creates
the same job. This reproduces exactly that window and counts what the board
ends up holding.
"""
import json, os, sqlite3, sys, urllib.request
from mco.orchestrator.score_sweep import open_conductor
from mco.config import get_config

RUN = sys.argv[1]
DB = os.environ["MCO_SCORE_DB"]
TOK = os.environ["MCO_AGENT_TOKEN"]

def api(path):
    req = urllib.request.Request("http://127.0.0.1:18997" + path,
                                 headers={"Authorization": f"Bearer {TOK}"})
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.load(r)

db = sqlite3.connect(DB, timeout=30, isolation_level=None)
db.row_factory = sqlite3.Row
row = db.execute("SELECT * FROM dispatch WHERE run=? AND phase='work'", (RUN,)).fetchone()
job_id = row["job_id"]
before = api(f"/api/jobs/{job_id}/events")
before = before["events"] if isinstance(before, dict) else before
print(f"job {job_id}")
print(f"  status in conductor db : {row['status']}")
print(f"  board 'created' events : {sum(1 for e in before if e.get('event')=='created')}")

# Rewind to the crash window: created on the board, not yet recorded as sent.
db.execute("UPDATE dispatch SET status='sending' WHERE job_id=?", (job_id,))
db.close()
print("  -> rewound dispatch row to 'sending' (the conductor-crash window)")

cond = open_conductor(get_config())
sent = cond.bridge.dispatch(RUN, cond.board)
print(f"  -> re-dispatch returned: {sent}")

after = api(f"/api/jobs/{job_id}/events")
after = after["events"] if isinstance(after, dict) else after
allj = api("/api/jobs")
jobs = allj["jobs"] if isinstance(allj, dict) else allj
same = [j for j in jobs if j.get("id") == job_id]
titles = [j for j in jobs if j.get("title") == f"Score {RUN} C01 work"]
print(f"  board 'created' events after re-dispatch : {sum(1 for e in after if e.get('event')=='created')}")
print(f"  board jobs with this id                  : {len(same)}")
print(f"  board jobs titled 'Score {RUN} C01 work' : {len(titles)}")
print(f"  job status / leased_by                   : {same[0].get('status')} / {same[0].get('leased_by_instance_id')}")
