import asyncio
import copy
import hashlib
import json
import uuid
from unittest.mock import AsyncMock

import pytest

from mco.localstore import LocalStore
from mco.orchestrator import routes
from mco.orchestrator.handlers import _unlock_dependents
from mco.orchestrator.score_bridge import ScoreBridge
from mco.orchestrator.score_dispatcher import ScoreOutboxDispatcher, score_job_id
from mco.orchestrator.scores import ScoreError


AGENT = {
    "instance_id": "score-conductor",
    "role": "conductor",
    "org_id": "default",
    "scopes": ["jobs:read", "jobs:write"],
}


def _payload():
    return {
        "title": "Score run-7 audit work",
        "description": "Produce the audit evidence.",
        "target_agent_role": "auditor",
        "target_agent_id": "auditor-1",
        "input_payload": {"prompt": "Inspect only."},
        "max_retries": 0,
        "requires_approval": False,
        "priority": 0,
    }


def _enqueue(dispatcher):
    return dispatcher.enqueue(
        org_id="default",
        score_id="via-cloud",
        run_id="run-7",
        digest="d" * 64,
        task_id="G01",
        attempt=2,
        phase="work",
        payload=_payload(),
    )


class RouteBoard:
    def __init__(self):
        self.creates = 0
        self.lose_ack = False

    def capabilities(self):
        return {"create_with_id": 1}

    def create(self, payload):
        self.creates += 1
        job = asyncio.run(routes.create_job(copy.deepcopy(payload), dict(AGENT)))["job"]
        if self.lose_ack:
            self.lose_ack = False
            raise TimeoutError("response lost after durable create")
        return job


def _score(tasks):
    return {
        "score_version": 1,
        "id": "audit-chain",
        "revision": 1,
        "objective": "Audit in order",
        "constraints": ["Read only"],
        "budget_cents": 0,
        "max_parallel": 2,
        "tasks": tasks,
        "launch_requires": [tasks[-1]["id"]],
    }


def _task(key, depends_on=()):
    return {
        "id": key,
        "goal": key,
        "title": key,
        "instructions": "Read-only",
        "role": "auditor",
        "review_role": "reviewer",
        "depends_on": list(depends_on),
        "resources": [key],
        "capabilities": ["cloud:inspect"],
        "evidence": ["report"],
        "max_attempts": 1,
        "timeout_seconds": 300,
        "max_cost_cents": 0,
        "checkpoint": None,
    }


def test_score_job_id_uses_exact_uuid5_formula():
    assert score_job_id(
        "default", "run-7", "d" * 64, "G01", "work"
    ) == "123ac833-c0ed-5b2b-9ece-9b7a4be5b3ac"


def test_outbox_stamps_score_run_task_attempt_digest_and_has_one_natural_key(tmp_path):
    store = LocalStore(tmp_path / "mco.db")
    dispatcher = ScoreOutboxDispatcher(store)

    first = _enqueue(dispatcher)
    replay = _enqueue(dispatcher)

    assert replay == first
    assert {
        key: first[key]
        for key in (
            "org_id",
            "score_id",
            "run_id",
            "digest",
            "task_id",
            "attempt",
            "phase",
        )
    } == {
        "org_id": "default",
        "score_id": "via-cloud",
        "run_id": "run-7",
        "digest": "d" * 64,
        "task_id": "G01",
        "attempt": 2,
        "phase": "work",
    }
    assert first["job_id"] == "123ac833-c0ed-5b2b-9ece-9b7a4be5b3ac"
    assert first["payload"]["id"] == first["job_id"]
    assert first["payload"]["depends_on"] == []
    assert first["payload"]["input_payload"]["score"] == {
        "protocol": "score-v1",
        "score_id": "via-cloud",
        "run_id": "run-7",
        "digest": "d" * 64,
        "task": "G01",
        "attempt": 2,
        "phase": "work",
    }
    assert len(store.table("score_outbox").select("*").execute().data) == 1
    store.close()


def test_outbox_rejects_random_uuid_collision_for_the_same_score_dispatch(tmp_path):
    store = LocalStore(tmp_path / "mco.db")
    store.table("score_outbox").insert(
        {
            "org_id": "default",
            "score_id": "via-cloud",
            "run_id": "run-7",
            "digest": "d" * 64,
            "task_id": "G01",
            "attempt": 2,
            "phase": "work",
            "job_id": str(uuid.uuid4()),
            "payload": _payload(),
        }
    ).execute()

    with pytest.raises(ScoreError, match="outbox dispatch key collision"):
        _enqueue(ScoreOutboxDispatcher(store))

    assert len(store.table("score_outbox").select("*").execute().data) == 1
    store.close()


def test_dispatch_fails_closed_when_score_stamp_was_removed(tmp_path):
    store = LocalStore(tmp_path / "mco.db")
    dispatcher = ScoreOutboxDispatcher(store)
    row = _enqueue(dispatcher)
    payload = copy.deepcopy(row["payload"])
    payload["input_payload"]["score"]["score_id"] = ""
    store.table("score_outbox").update(
        {"score_id": "", "payload": payload}
    ).eq("id", row["id"]).execute()

    class Board:
        def capabilities(self):
            return {"create_with_id": 1}

        def create(self, payload):
            return payload

    with pytest.raises(ScoreError, match="score_id must be a nonempty string"):
        dispatcher.dispatch(Board())

    store.close()


def test_lost_ack_replays_outbox_without_second_agent_job(tmp_path, monkeypatch):
    store = LocalStore(tmp_path / "mco.db")
    monkeypatch.setattr(routes, "get_db_client", lambda: store)
    monkeypatch.setattr(routes, "notify_job_created", lambda **_kwargs: None)
    monkeypatch.setattr(routes, "_broadcast_callback", AsyncMock())
    dispatcher = ScoreOutboxDispatcher(store)
    row = _enqueue(dispatcher)
    board = RouteBoard()
    board.lose_ack = True

    with pytest.raises(TimeoutError, match="response lost"):
        dispatcher.dispatch(board)

    assert store.table("score_outbox").select("*").execute().data[0]["status"] == "sending"
    assert len(store.table("agent_jobs").select("*").execute().data) == 1

    sent = ScoreOutboxDispatcher(store).dispatch(board)

    assert sent == [row["job_id"]]
    assert board.creates == 2
    assert len(store.table("agent_jobs").select("*").execute().data) == 1
    assert store.table("score_outbox").select("*").execute().data[0]["status"] == "submitted"
    store.close()


def test_completed_worker_job_does_not_mark_score_task_accepted(tmp_path, monkeypatch):
    store = LocalStore(tmp_path / "mco.db")
    monkeypatch.setattr(routes, "get_db_client", lambda: store)
    monkeypatch.setattr(routes, "notify_job_created", lambda **_kwargs: None)
    monkeypatch.setattr(routes, "_broadcast_callback", AsyncMock())
    dispatcher = ScoreOutboxDispatcher(store)
    row = _enqueue(dispatcher)
    dispatcher.dispatch(RouteBoard())
    score_task = store.table("score_tasks").insert(
        {
            "org_id": "default",
            "run_id": "run-7",
            "task_id": "G01",
            "attempt": 2,
            "status": "running",
        }
    ).execute().data[0]

    store.table("agent_jobs").update({"status": "completed"}).eq(
        "id", row["job_id"]
    ).execute()
    dispatcher.dispatch(RouteBoard())

    persisted = store.table("score_tasks").select("*").eq(
        "id", score_task["id"]
    ).execute().data[0]
    assert persisted["status"] == "running"
    assert len(store.table("score_outbox").select("*").execute().data) == 1
    store.close()


def test_legacy_dependency_unlock_does_not_advance_score_owned_job(tmp_path):
    store = LocalStore(tmp_path / "mco.db")
    parent = store.table("agent_jobs").insert(
        {"title": "legacy parent", "target_agent_role": "worker", "status": "completed"}
    ).execute().data[0]
    score_job = store.table("agent_jobs").insert(
        {
            "title": "score child",
            "target_agent_role": "worker",
            "status": "waiting",
            "depends_on": [parent["id"]],
            "input_payload": {
                "score": {
                    "protocol": "score-v1",
                    "score_id": "via-cloud",
                    "run_id": "run-7",
                    "digest": "d" * 64,
                    "task": "G02",
                    "attempt": 1,
                    "phase": "work",
                }
            },
        }
    ).execute().data[0]

    asyncio.run(_unlock_dependents(store, parent["id"], AsyncMock()))

    persisted = store.table("agent_jobs").select("*").eq(
        "id", score_job["id"]
    ).execute().data[0]
    assert persisted["status"] == "waiting"
    store.close()


def test_legacy_dependency_unlock_tolerates_non_object_input_payload(tmp_path):
    store = LocalStore(tmp_path / "mco.db")
    parent = store.table("agent_jobs").insert(
        {"title": "legacy parent", "target_agent_role": "worker", "status": "completed"}
    ).execute().data[0]
    child = store.table("agent_jobs").insert(
        {
            "title": "legacy child",
            "target_agent_role": "worker",
            "status": "waiting",
            "depends_on": [parent["id"]],
            "input_payload": "legacy-text-payload",
        }
    ).execute().data[0]

    asyncio.run(_unlock_dependents(store, parent["id"], AsyncMock()))

    persisted = store.table("agent_jobs").select("*").eq(
        "id", child["id"]
    ).execute().data[0]
    assert persisted["status"] == "pending"
    store.close()


def test_next_score_packet_stays_locked_until_review_is_accepted(tmp_path):
    score = _score([_task("G01"), _task("G02", ["G01"])])
    bridge = ScoreBridge(tmp_path / "score.db", tmp_path / "artifacts")

    class Board:
        identity = "credential-fingerprint"

        def __init__(self):
            self.jobs = {}

        def capabilities(self):
            return {"create_with_id": 1}

        def create(self, payload):
            self.jobs.setdefault(
                payload["id"],
                {
                    **copy.deepcopy(payload),
                    "source_agent_id": "conductor",
                    "org_id": "default",
                    "status": "pending",
                },
            )
            return self.jobs[payload["id"]]

        def get(self, job_id):
            return self.jobs[job_id]

        def events(self, job_id):
            job = self.jobs[job_id]
            return [
                {
                    "job_id": job_id,
                    "event": "status:completed",
                    "actor_id": job["target_agent_id"],
                    "actor_role": job["target_agent_role"],
                }
            ]

        def complete(self, job_id, result):
            self.jobs[job_id].update(
                status="completed",
                leased_by_instance_id=self.jobs[job_id]["target_agent_id"],
                output_payload={"result": json.dumps(result)},
            )

    board = Board()
    bridge.initialize(
        "run",
        score,
        principal="conductor",
        org="default",
        targets={"auditor": "worker", "reviewer": "independent"},
        credential_hash=board.identity,
    )
    report = bridge.root / "report.json"
    report.write_text('{"audit":"test"}', encoding="utf-8")
    evidence = {
        "report": {
            "path": "report.json",
            "sha256": hashlib.sha256(report.read_bytes()).hexdigest(),
        }
    }

    work = bridge.plan("run")[0]
    bridge.dispatch("run", board)
    board.complete(work, {"artifacts": evidence})
    bridge.poll("run", board)
    review = bridge.plan("run")[0]

    assert all(row["task"] != "G02" for row in bridge.status("run")["dispatches"])

    bridge.dispatch("run", board)
    board.complete(review, {"verdict": "pass", "review_of": evidence, "findings": []})
    bridge.poll("run", board)

    next_job = bridge.plan("run")[0]
    next_row = next(
        row for row in bridge.status("run")["dispatches"] if row["job_id"] == next_job
    )
    assert next_row["task"] == "G02"
    assert next_row["phase"] == "work"
