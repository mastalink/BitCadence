"""Canary reviewer for repository:write tasks using GatewayClient."""
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
    parser = argparse.ArgumentParser(description="Canary Repo Write Reviewer")
    parser.add_argument("--role", default="canary-review-repo-write")
    parser.add_argument("--instance", default="reviewer-1")
    parser.add_argument("--token", required=True)
    parser.add_argument("--gateway", default="http://127.0.0.1:18997")
    parser.add_argument("--worktree", required=True)
    parser.add_argument("--run-id", default="")
    parser.add_argument("--mode", default="pass", choices=["pass", "fail"])
    parser.add_argument("--timeout", type=int, default=20)
    args = parser.parse_args()

    worktree = Path(args.worktree).resolve()
    print(f"[{args.instance}] Reviewer started. Role: {args.role}, Mode: {args.mode}, Run: {args.run_id}, Worktree: {worktree}", flush=True)

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
                print(f"[{args.instance}] Ignoring review job {job_id} for other run {job_run_id} (wanted {args.run_id})", flush=True)
                time.sleep(1)
                continue

            review_of = contract.get("review_of") or {}
            commit_sha = review_of.get("commit_sha")
            print(f"[{args.instance}] Leased review job {job_id} for run {job_run_id}, review_of commit_sha: {commit_sha}", flush=True)

            if not commit_sha:
                verdict = "fail"
                findings = ["No commit_sha in contract review_of"]
            else:
                proc = subprocess.run(["git", "cat-file", "-t", commit_sha], cwd=str(worktree), capture_output=True, text=True)
                if proc.returncode != 0 or proc.stdout.strip() != "commit":
                    verdict = "fail"
                    findings = [f"Commit {commit_sha} does not exist in worktree git history"]
                elif args.mode == "fail":
                    verdict = "fail"
                    findings = ["INJECTION 5: Canary reviewer deliberate rejection"]
                else:
                    verdict = "pass"
                    findings = []

            result_str = json.dumps({
                "verdict": verdict,
                "review_of": {"commit_sha": commit_sha},
                "findings": findings,
            })
            comp_res = client.complete(job_id, result_str)
            print(f"[{args.instance}] Completed review job {job_id} with verdict {verdict}: {comp_res}", flush=True)
            return
        except Exception as exc:
            print(f"[{args.instance}] Error: {exc}", flush=True)
            time.sleep(1)

    print(f"[{args.instance}] Timeout waiting for review job after {args.timeout}s", flush=True)
    sys.exit(1)

if __name__ == "__main__":
    main()
