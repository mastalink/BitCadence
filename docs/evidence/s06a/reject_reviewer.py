"""A reviewer that looks at valid evidence and says no.

Not a malformed review and not a tampered artifact: a well-formed verdict of
`fail`, bound to the exact evidence map, which is the one thing a governed run
must never treat as an acceptance.
"""
import json, sys, time
from mco.orchestrator.client import GatewayClient

TOK = open(sys.argv[1]).read().strip()
INSTANCE = sys.argv[2]
client = GatewayClient(base_url="http://127.0.0.1:18997", token=TOK,
                       role="score-canary-review", instance_id=INSTANCE)
deadline = time.time() + 180
while time.time() < deadline:
    for job in client.inbox() or []:
        contract = (job.get("input_payload") or {}).get("score") or {}
        if contract.get("phase") != "review":
            continue
        client.lease(job["id"])
        payload = {"verdict": "fail",
                   "review_of": contract.get("review_of"),
                   "findings": ["Artifact bytes and digest verify, but the run is "
                                "rejected on review: injected S06a failure event."]}
        client.complete(job["id"], json.dumps(payload, sort_keys=True))
        print("REJECTED", job["id"], flush=True)
        print(json.dumps(payload, indent=2), flush=True)
        raise SystemExit(0)
    time.sleep(2)
print("no review job appeared", flush=True)
