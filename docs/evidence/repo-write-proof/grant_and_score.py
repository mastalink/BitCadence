"""Helper to create canary score definition and issue signed grant."""
import argparse
import datetime
import json
import os
import sys
from pathlib import Path

# Fallback isolated environment if not set
os.environ.setdefault("MCO_SCORE_GRANT_KEY", "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef")
s6_default = Path("C:/AI/baton/wt/scratch-s06b").resolve()
os.environ.setdefault("HOME", str(s6_default / "home"))
os.environ.setdefault("USERPROFILE", str(s6_default / "home"))

wt = os.environ.get("WT", "C:/AI/baton/wt/score-repo-write-s06b")
sys.path.insert(0, str(Path(wt) / "src"))

from mco.orchestrator.scores import digest, load_score
from mco.orchestrator.score_authority import GrantService, configured_grant_key
from mco.localstore import get_local_store

def create_score_and_grant(run_id, worktree_path, target_branch, output_score_file, omit_resource=False):
    worktree_path = str(Path(worktree_path).resolve())
    resources = ["unrelated-resource-lock"] if omit_resource else [worktree_path]

    score = {
        "score_version": 1,
        "id": "via-score-repo-write-canary",
        "revision": 1,
        "objective": "Canary execution proving repository:write under conductor.",
        "constraints": [
            "Isolated repo-write canary execution only",
            "Fixed non-LLM handler execution only",
        ],
        "budget_cents": 0,
        "max_parallel": 1,
        "launch_requires": ["write-task-1"],
        "tasks": [
            {
                "id": "write-task-1",
                "goal": "write-task-1",
                "title": "Modify canary file and commit",
                "instructions": "Touch src/canary.txt and signal ready",
                "role": "canary-worker-repo-write",
                "review_role": "canary-review-repo-write",
                "depends_on": [],
                "resources": resources,
                "capabilities": ["repository:write", "evidence:write", "evidence:review"],
                "commit": {
                    "worktree_path": worktree_path,
                    "target_branch": target_branch,
                    "allowed_paths": ["src/*"],
                    "commit_message": f"Score canary {run_id}: update canary file",
                },
                "evidence": ["commit_sha"],
                "max_attempts": 1,
                "timeout_seconds": 300,
                "max_cost_cents": 0,
                "checkpoint": None,
            }
        ]
    }

    # Write score to file
    score_path = Path(output_score_file).resolve()
    score_path.parent.mkdir(parents=True, exist_ok=True)
    score_path.write_text(json.dumps(score, indent=2), encoding="utf-8")

    # Compute digest
    d = digest(score)

    # Issue grant in LocalStore
    store = get_local_store()
    # Clean up any stale grant for this run_id first
    try:
        store.table("score_grants").delete().eq("run_id", run_id).execute()
    except Exception:
        pass

    now = datetime.datetime.now(datetime.timezone.utc)
    grant_data = {
        "org_id": "default",
        "run_id": run_id,
        "digest": d,
        "actions": ["repository:write"],
        "resources": [worktree_path],
        "env": "test",
        "not_before": now.isoformat(),
        "expires_at": (now + datetime.timedelta(hours=2)).isoformat(),
        "budget_cents": 0,
        "human_principal": "local-operator",
    }
    svc = GrantService(store, verification_key=configured_grant_key())
    issued = svc.issue(grant_data)
    print(f"Issued grant {issued['id']} for run {run_id} digest {d}")
    return d

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--worktree", required=True)
    parser.add_argument("--branch", default="canary/s06b-target")
    parser.add_argument("--output", required=True)
    parser.add_argument("--omit-resource", action="store_true")
    args = parser.parse_args()
    create_score_and_grant(args.run_id, args.worktree, args.branch, args.output, args.omit_resource)
