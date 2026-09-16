"""A canary worker that leases the C01 work job and dies mid-task.

It does exactly what a SIGKILLed worker does: takes the lease, records the
claim it was holding, and exits without completing. os._exit skips every
cleanup path, so nothing tidies the lease on the way out.
"""
import json, os, sys, time
from mco.orchestrator.client import GatewayClient

token = open(sys.argv[1]).read().strip()
instance = sys.argv[2]
claimfile = sys.argv[3]
client = GatewayClient(base_url="http://127.0.0.1:18997", token=token,
                       role="score-canary-worker", instance_id=instance)
deadline = time.time() + 120
while time.time() < deadline:
    jobs = client.inbox()
    for job in jobs or []:
        claim = client.lease(job["id"])
        record = {"job_id": job["id"], "claim": claim,
                  "leased_at": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime()) + "Z",
                  "pid": os.getpid()}
        open(claimfile, "w").write(json.dumps(record, indent=2, default=str))
        print("LEASED", job["id"], "-> dying without completing", flush=True)
        os._exit(137)          # SIGKILL-equivalent: no unwinding, no completion
    time.sleep(2)
print("no job appeared", flush=True)
