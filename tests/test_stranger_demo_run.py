"""Stranger Install Demo test: 2 agents, 1 human gate approval, 1 reviewer rejection, and audit verification."""
import hashlib
import json
import time
from datetime import datetime, timezone
from pathlib import Path
import pytest

from mco.localstore import LocalStore
from mco.orchestrator.scores import load_score
from mco.orchestrator.score_conductor import Conductor, start_run, open_bridge
from mco.orchestrator.score_policy import GateService

DEMO_SCORE_FILE = Path(__file__).parents[1] / "examples" / "scores" / "stranger-demo.score.json"


class StrangerAuditedBoard:
    def __init__(self, identity="stranger-cred-hash-01"):
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
            "source_agent_id": payload.get("source_agent_id", "stranger-operator"),
            "output_payload": None,
            "leased_by_instance_id": None,
        }
        self.jobs[payload["id"]] = job
        self.event_log.setdefault(payload["id"], []).append({
            "job_id": payload["id"],
            "event": "job:created",
            "actor_id": payload.get("source_agent_id", "stranger-operator"),
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


def test_stranger_demo_run_lifecycle(tmp_path):
    runs_db = tmp_path / "runs.db"
    gates_db = tmp_path / "gates.db"
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()

    board = StrangerAuditedBoard()
    gate_service = GateService(LocalStore(gates_db))
    bridge = open_bridge(runs_db, artifacts, gate_service=gate_service)
    conductor = Conductor(bridge, board, sleep=lambda _s: None, gate_service=gate_service)

    targets = {
        "score-stranger-worker": "stranger-worker-1",
        "score-stranger-review": "stranger-review-1",
    }
    run_id = "test-stranger-run-01"
    info = start_run(
        bridge,
        run_id=run_id,
        score_path=DEMO_SCORE_FILE,
        principal="stranger-operator",
        org="default",
        targets=targets,
        credential_hash=board.identity,
    )
    assert info["score_id"] == "stranger-demo-install"

    # 1. Tick 1 evaluates checkpoint gate -> enters waiting_on_gate
    t1 = conductor.tick(run_id)
    assert t1.status == "waiting_on_gate"
    assert conductor.status(run_id)["waiting_on_gate"] == ["DEMO-step-1"]

    # 2. Operator approves gate
    pending = gate_service.list(org_id="default")
    assert len(pending) == 1
    caller = {"org_id": "default", "instance_id": "stranger-operator", "auth_method": "session"}
    gate_service.decide(pending[0]["id"], caller=caller, decision="approve", reason="Operator stranger approval")

    # 3. Next tick plans and dispatches work
    t2 = conductor.tick(run_id)
    assert len(t2.dispatched) == 1
    work_id = t2.dispatched[0]

    # Worker finishes work
    rel_path = f"{run_id}/demo_artifact.json"
    art_file = artifacts / rel_path
    art_file.parent.mkdir(parents=True, exist_ok=True)
    art_file.write_text('{"build": "v1"}', encoding="utf-8")
    art_sha = hashlib.sha256(art_file.read_bytes()).hexdigest()
    board.finish(work_id, json.dumps({"artifacts": {"demo_artifact": {"path": rel_path, "sha256": art_sha}}}))

    # 4. Tick validates work, next tick dispatches review
    conductor.tick(run_id)
    t3 = conductor.tick(run_id)
    assert len(t3.dispatched) == 1
    review_id = t3.dispatched[0]
    review_job = board.get(review_id)
    assert review_job["target_agent_id"] == "stranger-review-1"

    # Reviewer REJECTS with findings
    review_rejection = json.dumps({
        "verdict": "fail",
        "review_of": review_job["input_payload"]["score"]["review_of"],
        "findings": ["Schema verification failed: initial build rejected for stranger install check"],
    })
    board.finish(review_id, review_rejection)

    # 5. Conductor polls rejection -> records rejected event
    conductor.tick(run_id)
    final_status = conductor.status(run_id)
    assert final_status["status"] == "blocked"

    # 6. Audit Trail verification
    events = [e["event"] for e in final_status.get("recent_events", [])]
    assert "waiting_on_gate" in events
    assert "gate_approved" in events
    assert "validated" in events
    assert "rejected" in events
