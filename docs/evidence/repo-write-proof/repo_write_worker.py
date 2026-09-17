"""Canary worker for repository:write tasks using GatewayClient."""
import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

# Add worktree src to sys.path
wt = os.environ.get("WT", "C:/AI/baton/wt/score-repo-write-s06b")
sys.path.insert(0, str(Path(wt) / "src"))

from mco.orchestrator.client import GatewayClient

def main():
    parser = argparse.ArgumentParser(description="Canary Repo Write Worker")
    parser.add_argument("--role", default="canary-worker-repo-write")
    parser.add_argument("--instance", default="worker-1")
    parser.add_argument("--token", required=True)
    parser.add_argument("--gateway", default="http://127.0.0.1:18997")
    parser.add_argument("--worktree", required=True)
    parser.add_argument("--run-id", default="")
    parser.add_argument("--mode", default="normal", choices=["normal", "crash", "claim_sha", "drift"])
    parser.add_argument("--timeout", type=int, default=20)
    args = parser.parse_args()

    worktree = Path(args.worktree).resolve()
    print(f"[{args.instance}] Worker started. Role: {args.role}, Mode: {args.mode}, Run: {args.run_id}, Worktree: {worktree}", flush=True)

    client = GatewayClient(base_url=args.gateway, instance_id=args.instance, role=args.role, token=args.token)

    start_time = time.time()
    while time.time() - start_time < args.timeout:
        try:
            lease_res = client.lease_next()
            job = lease_res.get("job")
            if not job or not job.get("id"):
                time.sleep(1)
                continue

            job_id = job["id"]
            contract = (job.get("input_payload") or {}).get("score") or {}
            job_run_id = contract.get("run_id", "unknown-run")
            task_id = contract.get("task", "unknown-task")

            if args.run_id and job_run_id != args.run_id:
                print(f"[{args.instance}] Ignoring job {job_id} for other run {job_run_id} (wanted {args.run_id})", flush=True)
                time.sleep(1)
                continue

            print(f"[{args.instance}] Leased job {job_id} for run {job_run_id}, task {task_id}", flush=True)

            if args.mode == "crash":
                print(f"[{args.instance}] INJECTION 2: Crashing worker process immediately after leasing...", flush=True)
                sys.stdout.flush()
                # Hard crash with 137
                os._exit(137)

            # Normal or drift mode: write change to allowed_paths
            canary_file = worktree / "src" / "canary.txt"
            canary_file.parent.mkdir(parents=True, exist_ok=True)
            ts = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            canary_file.write_text(f"Canary write at {ts} for run {job_run_id} by {args.instance}\n", encoding="utf-8")
            print(f"[{args.instance}] Wrote {canary_file}", flush=True)

            if args.mode == "drift":
                print(f"[{args.instance}] INJECTION 4: Injecting out-of-band commit before completion...", flush=True)
                subprocess.run(["git", "add", "README.md"], cwd=str(worktree), check=True)
                subprocess.run(["git", "commit", "--allow-empty", "-m", f"Out-of-band drift commit {ts}"], cwd=str(worktree), check=True)
                print(f"[{args.instance}] Out-of-band commit created on worktree HEAD", flush=True)

            if args.mode == "claim_sha":
                result_str = json.dumps({"ready": True, "commit_sha": "deadbeef1234"})
            else:
                result_str = json.dumps({"ready": True, "status": "ready"})

            comp_res = client.complete(job_id, result_str)
            print(f"[{args.instance}] Completed job {job_id}: {comp_res}", flush=True)
            return
        except Exception as exc:
            print(f"[{args.instance}] Error: {exc}", flush=True)
            time.sleep(1)

    print(f"[{args.instance}] Timeout waiting for job after {args.timeout}s", flush=True)
    sys.exit(1)

if __name__ == "__main__":
    main()
