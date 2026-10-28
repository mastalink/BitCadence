"""Reproduce gateway writer-lock contention on a production-sized LocalStore.

Seeds audit events and outbox rows with realistic document sizes, then times the
operations that ran inside the writer lock (job update, lease, audit append) and
the maintenance drain, plus 30 threads polling concurrently.

    PYTHONPATH=src python bench/lock_contention_bench.py [events] [jobs]
"""
import json
import sqlite3
import sys
import tempfile
import threading
import time
import uuid
from pathlib import Path

from mco.localstore import LocalStore
from mco.orchestrator import audit, leases


def seed(path: Path, events: int, jobs: int) -> list[str]:
    store = LocalStore(path)
    store.table("agent_jobs").select("id").limit(1).execute()
    store.close()
    conn = sqlite3.connect(str(path))
    conn.execute('CREATE TABLE IF NOT EXISTS agent_job_events (pk TEXT PRIMARY KEY, data TEXT NOT NULL)')
    conn.execute('CREATE TABLE IF NOT EXISTS mco_audit_outbox (pk TEXT PRIMARY KEY, data TEXT NOT NULL)')
    conn.execute('CREATE TABLE IF NOT EXISTS agent_jobs (pk TEXT PRIMARY KEY, data TEXT NOT NULL)')
    fat = {"text": "x" * 3500}
    ids = []
    for _ in range(jobs):
        jid = str(uuid.uuid4())
        ids.append(jid)
        row = {"id": jid, "title": "t", "status": "pending", "org_id": "default",
               "created_at": "2026-10-28T00:00:00+00:00", "input_payload": fat}
        conn.execute("INSERT INTO agent_jobs VALUES (?,?)", (jid, json.dumps(row)))
    for i in range(events):
        eid, oid = str(uuid.uuid4()), str(uuid.uuid4())
        job_id = ids[i % len(ids)]
        ev = {"id": eid, "job_id": job_id, "event": "state_committed", "outbox_id": oid,
              "created_at": f"2026-10-28T00:{i // 3600 % 60:02d}:{i % 60:02d}.{i:06d}+00:00",
              "org_id": "default", "detail": fat, "hash": "h", "prev_hash": "p"}
        conn.execute("INSERT INTO agent_job_events VALUES (?,?)", (eid, json.dumps(ev)))
        ob = {"id": oid, "job_id": job_id, "event": "state_committed", "created_at": ev["created_at"],
              "org_id": "default", "detail": fat}
        conn.execute("INSERT INTO mco_audit_outbox VALUES (?,?)", (oid, json.dumps(ob)))
    conn.commit()
    conn.close()
    return ids


def timed(fn):
    start = time.perf_counter()
    fn()
    return (time.perf_counter() - start) * 1000


def main(events=3000, jobs=500):
    tmp = Path(tempfile.mkdtemp()) / "bench.db"
    ids = seed(tmp, events, jobs)
    store = LocalStore(tmp)
    results = {}
    results["update_job_ms"] = timed(lambda: store.table("agent_jobs").update({"title": "u"}).eq("id", ids[0]).execute())
    results["lease_ms"] = timed(lambda: leases.acquire_lease(store, ids[1], "bench"))
    results["audit_append_ms"] = timed(lambda: audit.record_event(store, ids[2], "bench", "a", "r", {}))
    results["drain_outbox_ms"] = timed(lambda: audit.drain_outbox(store))
    stop = threading.Event()
    lat = []
    drain_lat = []

    def poll(n):
        while not stop.is_set():
            lat.append(timed(lambda: store.table("agent_jobs").select("*").eq("status", "pending").limit(5).execute()))
            lat.append(timed(lambda: store.table("agent_jobs").update({"title": n}).eq("id", ids[3 + n % 20]).execute()))
            time.sleep(2)

    def maintain():
        while not stop.is_set():
            drain_lat.append(timed(lambda: audit.drain_outbox(store)))
            time.sleep(0.5)

    threads = [threading.Thread(target=poll, args=(n,)) for n in range(30)] + [threading.Thread(target=maintain)]
    for t in threads:
        t.start()
    time.sleep(20)
    stop.set()
    for t in threads:
        t.join()
    lat.sort()
    results["agent_ops_p50_ms"] = lat[len(lat) // 2]
    results["agent_ops_p95_ms"] = lat[int(len(lat) * 0.95)]
    results["agent_ops_max_ms"] = lat[-1]
    results["drain_max_ms"] = max(drain_lat) if drain_lat else None
    print(json.dumps({k: round(v, 1) for k, v in results.items() if v is not None}, indent=1))
    store.close()


if __name__ == "__main__":
    main(*[int(a) for a in sys.argv[1:3]])
