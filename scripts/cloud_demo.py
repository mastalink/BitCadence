"""Create bounded lab demo jobs and check their actual outcomes through an SSM tunnel.

Requires an authorized AWS profile; tokens are captured privately and never printed.
Run with --action delegate or halt, then use the console to approve or stop work.
Run --action verify after both interactions. Evidence JSON contains no credentials.
"""
import argparse
import json
from pathlib import Path
import shutil
import subprocess
import time

import httpx


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--action", choices=["delegate", "halt", "verify"], required=True)
    parser.add_argument("--profile", default="batoncadence")
    args = parser.parse_args()
    aws = shutil.which("aws") or "C:/Program Files/Amazon/AWSCLIV2/aws.exe"

    def aws_json(*cmd):
        result = subprocess.run([aws, *cmd, "--profile", args.profile, "--region", "us-east-1",
            "--output", "json", "--no-cli-pager"], capture_output=True, text=True, check=True)
        return json.loads(result.stdout)

    assert aws_json("sts", "get-caller-identity")["Account"] == "314086896527", "Wrong AWS account"

    def token(role):
        return aws_json("secretsmanager", "get-secret-value", "--secret-id", f"bitcadence-lab/{role}")["SecretString"]

    client = httpx.Client(base_url="http://127.0.0.1:18891", timeout=30,
        headers={"Authorization": "Bearer " + token("operator")})
    state_path = Path(".codex/review/delegation-halt-demo.json")
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state = json.loads(state_path.read_text()) if state_path.exists() else {}

    def jobs():
        r = client.get("/api/jobs")
        r.raise_for_status()
        return r.json()

    if args.action != "verify":
        mode = "delegate" if args.action == "delegate" else "kill-demo"
        title = "Delegation demo: worker prepares a reviewer handoff" if mode == "delegate" else "Kill switch demo: bounded work with ownership checkpoints"
        r = client.post("/api/jobs", json={"title": title, "description":
            "Demonstrate governed delegation." if mode == "delegate" else
            "Run harmless work units for up to 180 seconds. Stop at an ownership checkpoint when the operator halts work.",
            "target_agent_role": "worker", "target_agent_id": "worker-lab", "input_payload": {"lab_mode": mode}})
        r.raise_for_status()
        job = r.json().get("job", r.json())
        state[args.action] = job["id"]
        state_path.write_text(json.dumps(state))
        print(json.dumps({"created": job["id"], "title": title}), flush=True)
        if args.action == "halt":
            for _ in range(60):
                current = next(j for j in jobs() if j["id"] == job["id"])
                if current["status"] in {"leased", "in_progress"}:
                    state["old_claim"] = {k: current[k] for k in ("lease_id", "lease_epoch", "lease_incarnation")}
                    state["old_claim"]["agent_instance_id"] = "worker-lab"
                    state_path.write_text(json.dumps(state))
                    print("PASS worker-lab owns the active attempt; ready for the operator kill switch")
                    return
                time.sleep(1)
            raise RuntimeError("Worker did not start within 60 seconds")
        return

    current_jobs = jobs()
    parent = next(j for j in current_jobs if j["id"] == state["delegate"])
    child = next(j for j in current_jobs if (j.get("input_payload") or {}).get("parent_job_id") == parent["id"])
    assert parent["status"] == child["status"] == "completed"
    assert child["source_agent_id"] == "worker-lab" and child["target_agent_id"] == "reviewer-lab"
    assert child.get("approved_by"), "No recorded approval"
    halted = next(j for j in current_jobs if j["id"] == state["halt"])
    assert halted["status"] == "halted" and not halted.get("output_payload")
    late = httpx.put(f"http://127.0.0.1:18891/api/jobs/{halted['id']}", timeout=30,
        headers={"Authorization": "Bearer " + token("worker")},
        json={**state["old_claim"], "status": "completed", "output_payload": {"result": "late demo result that must be rejected"}})
    assert late.status_code == 409, f"Expected fence rejection, received {late.status_code}"
    assert next(j for j in jobs() if j["id"] == halted["id"])["status"] == "halted"
    report = {"environment": "AWS us-east-1 through authenticated SSM tunnel", "delegation": {
        "parent": parent["id"], "child": child["id"], "source": child["source_agent_id"],
        "target": child["target_agent_id"], "parent_status": parent["status"], "child_status": child["status"],
        "approved_by": child["approved_by"]}, "kill_switch": {"job": halted["id"], "status": "halted",
        "late_completion_http": late.status_code, "output_accepted": False,
        "probe": "Operator replayed the old worker claim after halt to verify rejection"}}
    output = Path("output/playwright/demo-pack/delegation-kill-evidence.json")
    output.write_text(json.dumps(report, indent=2))
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
