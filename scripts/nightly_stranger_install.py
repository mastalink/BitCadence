#!/usr/bin/env python3
"""Nightly Stranger Install and Governed Score Check.

Runs a clean, automated stranger rehearsal following public README patterns:
  1. Verifies cold preflight
  2. Simulates 2 connected agents
  3. Executes stranger-demo.score.json exercising:
     - 1 human gate checkpoint
     - 1 reviewer rejection
  4. Reads and validates the immutable audit trail
  5. Enforces total run time under 20 minutes (1200 seconds)

Exits 0 on success, 1 on failure.
"""
import hashlib
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

# Add src to python path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from mco.localstore import LocalStore
from mco.orchestrator.score_conductor import Conductor, start_run, open_bridge
from mco.orchestrator.score_policy import GateService

DEMO_SCORE = ROOT / "examples" / "scores" / "stranger-demo.score.json"
MAX_TIME_SECONDS = 1200  # 20 minutes threshold


class StrangerAuditedBoard:
    def __init__(self, identity="stranger-cred-hash-nightly"):
        self.identity = identity
        self.jobs = {}
        self.event_log = {}

    def capabilities(self):
        return {"create_with_id": 1}

    def create(self, payload):
        job = {
            **payload,
            "status": "pending",
            "org_id": payload.get("org_id", "default"),
            "source_agent_id": payload.get("source_agent_id", "stranger-nightly-runner"),
            "output_payload": None,
            "leased_by_instance_id": None,
        }
        self.jobs[payload["id"]] = job
        self.event_log.setdefault(payload["id"], []).append({
            "job_id": payload["id"],
            "event": "job:created",
            "actor_id": payload.get("source_agent_id", "stranger-nightly-runner"),
            "actor_role": "conductor",
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })
        return job

    def get(self, jid):
        return self.jobs[jid]

    def events(self, jid):
        return self.event_log.get(jid, [])

    def finish(self, jid, result, actor=None, actor_role=None):
        job = self.jobs[jid]
        actor = actor or job["target_agent_id"]
        actor_role = actor_role or job["target_agent_role"]
        job.update(status="completed", leased_by_instance_id=actor, output_payload={"result": result})
        self.event_log.setdefault(jid, []).append({
            "job_id": jid,
            "event": "status:completed",
            "actor_id": actor,
            "actor_role": actor_role,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })


def run_nightly_check(sandbox_dir: Path) -> dict:
    start_time = time.perf_counter()
    timings = {}

    runs_db = sandbox_dir / "nightly_runs.db"
    gates_db = sandbox_dir / "nightly_gates.db"
    artifacts = sandbox_dir / "artifacts"
    artifacts.mkdir(parents=True, exist_ok=True)

    for db_path in [runs_db, gates_db]:
        if db_path.exists():
            db_path.unlink()

    # Preflight
    t0 = time.perf_counter()
    assert DEMO_SCORE.is_file(), f"Demo score missing at {DEMO_SCORE}"
    timings["preflight_seconds"] = round(time.perf_counter() - t0, 3)

    # Conductor & Board setup
    t0 = time.perf_counter()
    board = StrangerAuditedBoard()
    gate_service = GateService(LocalStore(gates_db))
    bridge = open_bridge(runs_db, artifacts, gate_service=gate_service)
    conductor = Conductor(bridge, board, sleep=lambda _s: None, gate_service=gate_service)
    timings["setup_seconds"] = round(time.perf_counter() - t0, 3)

    # Run initialization
    t0 = time.perf_counter()
    run_id = f"nightly-stranger-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}"
    targets = {
        "score-stranger-worker": "stranger-worker-1",
        "score-stranger-review": "stranger-review-1",
    }
    start_run(
        bridge,
        run_id=run_id,
        score_path=DEMO_SCORE,
        principal="stranger-nightly-runner",
        org="default",
        targets=targets,
        credential_hash=board.identity,
    )
    timings["start_run_seconds"] = round(time.perf_counter() - t0, 3)

    # Step 1: Human Gate Checkpoint
    t0 = time.perf_counter()
    t1 = conductor.tick(run_id)
    assert t1.status == "waiting_on_gate", f"Expected waiting_on_gate, got {t1.status}"
    pending = gate_service.list(org_id="default")
    assert len(pending) == 1, f"Expected 1 pending gate, got {len(pending)}"

    # Human Gate Approval
    caller = {"org_id": "default", "instance_id": "stranger-nightly-runner", "auth_method": "session"}
    decision = gate_service.decide(
        pending[0]["id"],
        caller=caller,
        decision="approve",
        reason="Nightly automated verification gate approval",
    )
    assert decision["decision"] == "approved"
    timings["human_gate_seconds"] = round(time.perf_counter() - t0, 3)

    # Step 2: Work Dispatch & Execution
    t0 = time.perf_counter()
    t2 = conductor.tick(run_id)
    assert len(t2.dispatched) == 1, "Work job was not dispatched"
    work_id = t2.dispatched[0]

    rel_path = f"{run_id}/demo_artifact.json"
    art_file = artifacts / rel_path
    art_file.parent.mkdir(parents=True, exist_ok=True)
    art_file.write_text('{"build": "nightly_pass"}', encoding="utf-8")
    art_sha = hashlib.sha256(art_file.read_bytes()).hexdigest()
    board.finish(work_id, json.dumps({"artifacts": {"demo_artifact": {"path": rel_path, "sha256": art_sha}}}))
    timings["work_execution_seconds"] = round(time.perf_counter() - t0, 3)

    # Step 3: Review Dispatch & Intentional Rejection Test
    t0 = time.perf_counter()
    conductor.tick(run_id)
    t3 = conductor.tick(run_id)
    assert len(t3.dispatched) == 1, "Review job was not dispatched"
    review_id = t3.dispatched[0]
    review_job = board.get(review_id)

    review_rejection = json.dumps({
        "verdict": "fail",
        "review_of": review_job["input_payload"]["score"]["review_of"],
        "findings": ["Nightly stranger test rejection: deliberate failure proving rejection gate enforcement"],
    })
    board.finish(review_id, review_rejection)

    conductor.tick(run_id)
    final_status = conductor.status(run_id)
    assert final_status["status"] == "blocked", f"Expected blocked status on terminal rejection, got {final_status['status']}"
    timings["review_rejection_seconds"] = round(time.perf_counter() - t0, 3)

    # Step 4: Audit Verification
    t0 = time.perf_counter()
    events = [e["event"] for e in final_status.get("recent_events", [])]
    assert "waiting_on_gate" in events, "Missing waiting_on_gate in audit trail"
    assert "gate_approved" in events, "Missing gate_approved in audit trail"
    assert "validated" in events, "Missing validated in audit trail"
    assert "rejected" in events, "Missing rejected in audit trail"
    timings["audit_verification_seconds"] = round(time.perf_counter() - t0, 3)

    total_time = round(time.perf_counter() - start_time, 3)
    timings["total_run_seconds"] = total_time
    assert total_time < MAX_TIME_SECONDS, f"Run exceeded maximum threshold of {MAX_TIME_SECONDS}s: took {total_time}s"

    return {
        "status": "passed",
        "run_id": run_id,
        "timings": timings,
        "events": events,
    }


def main():
    sandbox = Path(os.environ.get("MCO_SANDBOX_DIR", ROOT / ".tmp" / "nightly_stranger"))
    sandbox.mkdir(parents=True, exist_ok=True)
    print(f"Starting Nightly Stranger Install Check in {sandbox}...")
    try:
        report = run_nightly_check(sandbox)
        print("Nightly Stranger Install Check PASSED:")
        print(json.dumps(report, indent=2))
        sys.exit(0)
    except Exception as exc:
        print(f"Nightly Stranger Install Check FAILED: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
