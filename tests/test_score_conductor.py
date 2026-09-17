"""The conductor loop: one tick advances a run by whatever is possible now.

Proven end to end against the live board on 2026-09-16 (run canary-20260916-0043,
initialized -> planned -> submitted -> validated -> reviewed -> accepted in ~50s).
These tests pin that behaviour with a fake board so it keeps working."""

import json
from pathlib import Path

import pytest

from mco.orchestrator.score_canary_worker import CanaryContractError, review, work
from mco.orchestrator.score_conductor import Conductor, TickResult, start_run, open_bridge
from mco.orchestrator.scores import ScoreError

CANARY = Path(__file__).resolve().parents[1] / "examples" / "scores" / "via-score-conductor-canary.score.json"
TARGETS = {"score-canary-worker": "canary-worker-1", "score-canary-review": "canary-review-1"}


class FakeBoard:
    """A board that records what the conductor asked it to do."""

    def __init__(self, identity="cred-hash"):
        self.identity = identity
        self.jobs: dict = {}
        self.event_log: dict = {}

    def capabilities(self):
        return {"create_with_id": 1}

    def create(self, payload):
        job = {**payload, "status": "pending", "source_agent_id": "score-conductor-1",
               "org_id": "default", "output_payload": None, "leased_by_instance_id": None}
        self.jobs[payload["id"]] = job
        return job

    def get(self, job_id):
        return self.jobs[job_id]

    def events(self, job_id):
        return self.event_log.get(job_id, [])

    # ── helpers the tests use to play the part of a worker ───────────────
    def finish(self, job_id, result: str, *, actor=None):
        job = self.jobs[job_id]
        actor = actor or job["target_agent_id"]
        job.update(status="completed", leased_by_instance_id=actor,
                   output_payload={"result": result})
        self.event_log.setdefault(job_id, []).append(
            {"job_id": job_id, "event": "status:completed", "actor_id": actor,
             "actor_role": job["target_agent_role"]})

    def fail(self, job_id):
        self.jobs[job_id]["status"] = "failed"


@pytest.fixture
def conductor(tmp_path):
    bridge = open_bridge(tmp_path / "runs.db", tmp_path / "artifacts")
    board = FakeBoard()
    return Conductor(bridge, board, sleep=lambda _s: None)


def _start(conductor, run_id="run-1"):
    start_run(conductor.bridge, run_id=run_id, score_path=CANARY, principal="score-conductor-1",
              org="default", targets=TARGETS, credential_hash=conductor.board.identity)
    return run_id


def _do_work(conductor, run_id, job_id, *, actor="canary-worker-1"):
    job = conductor.board.get(job_id)
    conductor.board.finish(job_id, work(job, conductor.bridge.root), actor=actor)


def _stage_of(conductor, run_id):
    """The stage named by the run's tick_blocked event, if a tick blocked it."""
    for event in conductor.bridge.status(run_id)["events"]:
        if event["event"] == "tick_blocked":
            return json.loads(event["detail"])["stage"]
    return None


def _do_review(conductor, job_id, reviewer="canary-review-1"):
    job = conductor.board.get(job_id)
    conductor.board.finish(job_id, review(job, reviewer, conductor.bridge.root))


class TestTick:
    def test_a_run_reaches_accepted_without_anyone_typing_a_prompt(self, conductor):
        run = _start(conductor)
        first = conductor.tick(run)
        assert len(first.planned) == 1 and len(first.dispatched) == 1
        work_job = first.dispatched[0]

        _do_work(conductor, run, work_job)
        conductor.tick(run)                       # validates the evidence
        planned_review = conductor.tick(run)      # plans + dispatches the review
        review_job = planned_review.dispatched[0]
        assert review_job != work_job

        _do_review(conductor, review_job)
        final = conductor.tick(run)
        assert final.accepted == ["C01"]
        status = conductor.status(run)
        assert status["status"] == "accepted"
        assert status["launched"] is True

    def test_tick_is_idle_while_a_worker_is_still_working(self, conductor):
        run = _start(conductor)
        conductor.tick(run)
        assert conductor.tick(run).idle

    def test_the_review_goes_to_a_different_identity(self, conductor):
        run = _start(conductor)
        work_job = conductor.tick(run).dispatched[0]
        _do_work(conductor, run, work_job)
        conductor.tick(run)
        review_job = conductor.tick(run).dispatched[0]
        assert conductor.board.get(work_job)["target_agent_id"] == "canary-worker-1"
        assert conductor.board.get(review_job)["target_agent_id"] == "canary-review-1"

    def test_a_failed_job_blocks_the_run_instead_of_advancing_it(self, conductor):
        run = _start(conductor)
        job_id = conductor.tick(run).dispatched[0]
        conductor.board.fail(job_id)
        conductor.tick(run)
        assert conductor.status(run)["status"] == "blocked"

    def test_forged_completion_identity_is_refused(self, conductor):
        run = _start(conductor)
        job_id = conductor.tick(run).dispatched[0]
        _do_work(conductor, run, job_id, actor="somebody-else")
        result = conductor.tick(run)
        assert result.error and "identity" in result.error.lower()
        assert conductor.status(run)["status"] == "blocked"

    def test_a_dispatch_failure_blocks_the_run_rather_than_only_reporting_it(self, conductor):
        run = _start(conductor)
        conductor.board.capabilities = lambda: {}          # a gateway without create_with_id
        result = conductor.tick(run)
        assert result.error == "Retry-safe gateway protocol unavailable"
        assert result.status == "blocked"
        assert _stage_of(conductor, run) == "dispatch"
        assert not conductor.board.jobs, "nothing may be submitted through a refused protocol"
        # The block is durable: a fresh conductor over the same database agrees.
        fresh = Conductor(open_bridge(conductor.bridge.database, conductor.bridge.root), FakeBoard())
        assert fresh.status(run)["status"] == "blocked"

    def test_a_plan_failure_blocks_the_run_too(self, conductor, monkeypatch):
        run = _start(conductor)

        def refuse(_run_id):
            raise ScoreError("planner refused")

        monkeypatch.setattr(conductor.bridge, "plan", refuse)
        result = conductor.tick(run)
        assert result.error == "planner refused"
        assert result.status == "blocked"
        assert _stage_of(conductor, run) == "plan"
        assert not conductor.board.jobs

    def test_a_poll_failure_keeps_its_own_reason_and_is_not_reported_twice(self, conductor):
        run = _start(conductor)
        job_id = conductor.tick(run).dispatched[0]
        _do_work(conductor, run, job_id, actor="somebody-else")
        assert conductor.tick(run).status == "blocked"
        events = [e["event"] for e in conductor.bridge.status(run)["events"]]
        assert events.count("validation_blocked") == 1
        assert "tick_blocked" not in events, "poll already recorded the specific reason"

    def test_run_until_settled_stops_at_acceptance(self, conductor):
        run = _start(conductor)
        seen = []

        def advance(_result):
            for job_id, job in list(conductor.board.jobs.items()):
                if job["status"] != "pending":
                    continue
                if job["target_agent_role"] == "score-canary-worker":
                    _do_work(conductor, run, job_id)
                else:
                    _do_review(conductor, job_id)
            seen.append(_result)

        status = conductor.run_until_settled(run, interval=0, timeout=30, on_tick=advance)
        assert status["status"] == "accepted" and status["launched"] is True
        assert seen, "on_tick should see every tick"

    def test_it_reports_a_timeout_rather_than_claiming_success(self, conductor):
        run = _start(conductor)
        status = conductor.run_until_settled(run, interval=0, timeout=-1)
        assert status.get("timed_out") is True
        assert status["status"] == "running"


class TestStartRun:
    def test_restarting_the_same_run_resumes_instead_of_duplicating(self, conductor):
        run = _start(conductor)
        conductor.tick(run)
        _start(conductor, run)                    # same policy and identities
        assert len(conductor.board.jobs) == 1

    def test_changed_identities_are_refused(self, conductor):
        run = _start(conductor)
        with pytest.raises(ScoreError):
            start_run(conductor.bridge, run_id=run, score_path=CANARY,
                      principal="someone-else", org="default", targets=TARGETS,
                      credential_hash=conductor.board.identity)

    def test_author_cannot_also_be_the_reviewer(self, conductor):
        with pytest.raises(ScoreError):
            start_run(conductor.bridge, run_id="same", score_path=CANARY,
                      principal="score-conductor-1", org="default",
                      targets={"score-canary-worker": "solo", "score-canary-review": "solo"},
                      credential_hash=conductor.board.identity)


class TestCanaryHandlers:
    def test_work_reports_the_artifact_under_its_evidence_name(self, tmp_path):
        job = {"input_payload": {"score": {"protocol": "score-v1", "score_id": "via-score-conductor-canary", "run_id": "r1", "task": "C01", "phase": "work",
                                           "artifact_root": str(tmp_path),
                                           "required_evidence": ["canary_artifact"]}}}
        result = json.loads(work(job, tmp_path))
        reference = result["artifacts"]["canary_artifact"]
        written = tmp_path / reference["path"]
        assert written.is_file()
        assert reference["sha256"] == __import__("hashlib").sha256(written.read_bytes()).hexdigest()

    def test_review_passes_on_matching_bytes_and_fails_on_tampering(self, tmp_path):
        contract = {"protocol": "score-v1", "score_id": "via-score-conductor-canary", "run_id": "r1", "task": "C01", "phase": "work", "artifact_root": str(tmp_path),
                    "required_evidence": ["canary_artifact"]}
        evidence = json.loads(work({"input_payload": {"score": contract}}, tmp_path))["artifacts"]
        review_job = {"source_agent_id": "canary-worker-1",
                      "input_payload": {"score": {**contract, "phase": "review", "review_of": evidence}}}
        assert json.loads(review(review_job, "canary-review-1", tmp_path))["verdict"] == "pass"

        (tmp_path / evidence["canary_artifact"]["path"]).write_text("tampered", encoding="utf-8")
        failed = json.loads(review(review_job, "canary-review-1", tmp_path))
        assert failed["verdict"] == "fail" and failed["findings"]

    def test_author_cannot_review_their_own_artifact(self, tmp_path):
        contract = {"protocol": "score-v1", "score_id": "via-score-conductor-canary", "run_id": "r1", "task": "C01", "phase": "work", "artifact_root": str(tmp_path),
                    "required_evidence": ["canary_artifact"]}
        evidence = json.loads(work({"input_payload": {"score": contract}}, tmp_path))["artifacts"]
        job = {"source_agent_id": "canary-worker-1",
               "input_payload": {"score": {**contract, "phase": "review", "review_of": evidence}}}
        verdict = json.loads(review(job, "canary-worker-1", tmp_path))
        assert verdict["verdict"] == "fail"
        assert "independent" in verdict["findings"][0].lower()

    def test_a_job_without_the_score_contract_is_refused(self):
        with pytest.raises(CanaryContractError):
            work({"input_payload": {}})

    def test_multiple_evidence_names_are_refused_with_a_useful_message(self, tmp_path):
        job = {"input_payload": {"score": {"protocol": "score-v1", "score_id": "via-score-conductor-canary", "run_id": "r1", "task": "C01", "phase": "work",
                                           "artifact_root": str(tmp_path),
                                           "required_evidence": ["path", "sha256"]}}}
        with pytest.raises(CanaryContractError, match="one required evidence name"):
            work(job, tmp_path)

    def test_job_cannot_choose_artifact_root_or_escape_run_directory(self, tmp_path):
        contract = {"protocol": "score-v1", "score_id": "via-score-conductor-canary",
                    "run_id": "../../escaped", "task": "C01", "phase": "work",
                    "artifact_root": str(tmp_path / "attacker"),
                    "required_evidence": ["canary_artifact"]}
        with pytest.raises(CanaryContractError):
            work({"input_payload": {"score": contract}}, tmp_path)

    def test_contract_identity_and_protocol_are_bound(self, tmp_path):
        contract = {"protocol": "other", "score_id": "not-the-canary", "run_id": "r1",
                    "task": "C01", "phase": "work", "artifact_root": str(tmp_path),
                    "required_evidence": ["canary_artifact"]}
        with pytest.raises(CanaryContractError, match="protocol"):
            work({"input_payload": {"score": contract}}, tmp_path)


def test_tick_result_describes_itself():
    assert "planned=2" in TickResult("r", "running", planned=["a", "b"]).describe()
    assert TickResult("r", "running").idle is True
