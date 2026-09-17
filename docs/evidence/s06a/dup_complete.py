"""Duplicate delivery, worker side.

A worker whose response is lost retries the same completion; a confused or
malicious one sends a different result under the same lease. Both arrive on
the wire as the same PUT, so the board has to tell them apart.
"""
import json, os, sys, time, httpx
from mco.orchestrator.client import GatewayClient
from mco.orchestrator.score_canary_worker import work

TOK = open(sys.argv[1]).read().strip()
INSTANCE = sys.argv[2]
client = GatewayClient(base_url="http://127.0.0.1:18997", token=TOK,
                       role="score-canary-worker", instance_id=INSTANCE)
deadline = time.time() + 90
job = None
while time.time() < deadline and job is None:
    for j in client.inbox() or []:
        job = j
        break
    if job is None:
        time.sleep(2)
if job is None:
    print("no job appeared"); raise SystemExit(1)

lease = client.lease(job["id"])["lease"]
result = work(job)
print(f"job {job['id']}  lease {lease['lease_id']} epoch {lease['lease_epoch']}")

def put(payload, label):
    r = httpx.put(f"http://127.0.0.1:18997/api/jobs/{job['id']}",
                  json={**payload, **lease},
                  headers={"Authorization": f"Bearer {TOK}"}, timeout=20)
    body = r.json() if r.headers.get("content-type","").startswith("application/json") else {"text": r.text}
    replayed = body.get("job", {}).get("_replayed") if isinstance(body.get("job"), dict) else body.get("_replayed")
    print(f"  {label}")
    print(f"    HTTP {r.status_code}   _replayed={replayed}")
    if r.status_code >= 400:
        print(f"    detail: {json.dumps(body)[:200]}")
    return r

same = {"status": "completed", "output_payload": {"result": result}}
put(same, "DELIVERY 1 - the honest completion")
time.sleep(1)
put(same, "DELIVERY 2 - byte-identical retry after a lost response (SAME lease)")
time.sleep(1)
divergent = {"status": "completed", "output_payload": {"result": json.dumps(
    {"artifacts": {"canary_artifact": {"path": "forged.json", "sha256": "0"*64}}})}}
put(divergent, "DELIVERY 3 - a DIFFERENT result under the SAME lease")
