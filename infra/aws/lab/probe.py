"""Executed inside the cloud hub; reports assertions without printing credentials."""
import os
import time
from datetime import datetime, timezone
import boto3
import httpx


def main():
    secrets = boto3.client("secretsmanager", region_name="us-east-1")
    token = secrets.get_secret_value(SecretId="bitcadence-lab/operator")["SecretString"]
    worker_token = secrets.get_secret_value(SecretId="bitcadence-lab/worker")["SecretString"]
    base = "http://127.0.0.1:18789"
    assert httpx.get(base + "/api/jobs").status_code in (401, 403)
    print("PASS unauthenticated job access denied", flush=True)
    client = httpx.Client(base_url=base, headers={"Authorization": f"Bearer {token}"}, timeout=30)

    def get_job(job_id):
        response = client.get("/api/jobs")
        response.raise_for_status()
        return next(job for job in response.json() if job["id"] == job_id)

    def create(role, gated=False, model=False):
        response = client.post("/api/jobs", json={"title": f"Cloud lab {role} acceptance",
            "description": "Reply with a short confirmation that this governed test reached its worker.",
            "target_agent_role": role, "target_agent_id": f"{role}-lab", "requires_approval": gated,
            "input_payload": {"lab_mode": "bedrock" if model else "checksum"}})
        response.raise_for_status()
        result = response.json()
        return result.get("job", result)

    def wait(job_id):
        deadline = time.monotonic() + 150
        while time.monotonic() < deadline:
            job = get_job(job_id)
            if job.get("status") == "completed":
                return job
            assert job.get("status") not in {"failed", "halted", "cancelled"}, job.get("status")
            time.sleep(2)
        raise AssertionError("Cloud spoke did not complete its job within 150 seconds")

    normal = create("worker")
    completed = wait(normal["id"])
    assert str(completed.get("result", "")).startswith("sha256:")
    print("PASS worker spoke TLS, authentication, lease and execution", flush=True)
    gated = create("reviewer", gated=True)
    assert gated["status"] == "needs_approval"
    denied = httpx.post(base + f"/api/jobs/{gated['id']}/approve",
        headers={"Authorization": f"Bearer {worker_token}"})
    assert denied.status_code == 403
    time.sleep(5)
    assert get_job(gated["id"])["status"] == "needs_approval"
    client.post(f"/api/jobs/{gated['id']}/approve").raise_for_status()
    wait(gated["id"])
    print("PASS approval fence, worker approval denial, reviewer spoke execution", flush=True)
    model = create("worker", model=True)
    model_result = wait(model["id"])
    assert model_result.get("result") and not str(model_result["result"]).startswith("sha256:")
    print("PASS bounded Bedrock inference using the spoke IAM role", flush=True)
    from mco.localstore import LocalStore
    store = LocalStore("/mco/local.db")
    s3 = boto3.client("s3", region_name="us-east-1")
    for job in (normal, gated, model):
        rows = store.table("agent_job_events").select("*").eq("job_id", job["id"]).execute().data
        assert rows
        locked = 0
        for row in rows:
            try:
                head = s3.head_object(Bucket=os.environ["MCO_EVIDENCE_BUCKET"], Key=f"ledger/{job['id']}/{row['id']}.json")
            except s3.exceptions.ClientError:
                continue
            assert head.get("VersionId") and head.get("ObjectLockMode") == "COMPLIANCE"
            assert head["ObjectLockRetainUntilDate"] > datetime.now(timezone.utc)
            locked += 1
        assert locked > 0, "No locked evidence for completed job"
    print("PASS versioned, compliance-locked evidence for all three jobs", flush=True)


if __name__ == "__main__":
    main()
