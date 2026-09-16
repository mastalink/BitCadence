"""Unit and integration tests for C01 canary score and fixed non-LLM handlers."""
import hashlib
import json
from pathlib import Path
import pytest

from mco.orchestrator.scores import ScoreError, SandboxRun, compile_score, digest, load_score
from mco.orchestrator.score_evidence import EvidenceBinding, VerifiedEvidence
from mco.orchestrator.score_canary import (
    CANARY_SCORE_ID,
    CANARY_TASK_ID,
    CANARY_ROLE_WORKER,
    CANARY_ROLE_REVIEW,
    CANARY_RESOURCE_LANE,
    default_canary_body,
    hash_artifact,
    verify_artifact,
)

CANARY_SCORE_FILE = Path(__file__).parents[1] / "examples/scores/via-score-conductor-canary.score.json"
VIA_CLOUD_LAUNCH_DIGEST = "77387d1a453356f6d6f3e22cabfdf31fce56fcef930a2b6991d8f2c8b24eded9"
AUDIT_RUNNER_DIGEST = "2448e860acb838c2b614b7185d0abef798de88abb7b3f63213ef8bb8e5867dd5"


@pytest.fixture
def canary_score_dict():
    raw = CANARY_SCORE_FILE.read_text(encoding="utf-8")
    return load_score(raw)


def test_canary_score_validation_and_compilation(canary_score_dict):
    """Prove canary score validates, compiles, and produces a distinct new digest."""
    score = canary_score_dict
    assert score["id"] == CANARY_SCORE_ID
    assert score["revision"] == 1
    assert score["budget_cents"] == 0
    assert score["max_parallel"] == 1
    assert score["launch_requires"] == [CANARY_TASK_ID]

    # Task verification
    assert len(score["tasks"]) == 1
    task = score["tasks"][0]
    assert task["id"] == CANARY_TASK_ID
    assert task["role"] == CANARY_ROLE_WORKER
    assert task["review_role"] == CANARY_ROLE_REVIEW
    assert task["role"] != task["review_role"]
    assert task["resources"] == [CANARY_RESOURCE_LANE]
    assert "via-release-lane" not in task["resources"]
    assert set(task["capabilities"]).issubset({"evidence:write", "evidence:review"})
    # One ARTIFACT NAME, not the fields inside it: the bridge treats each
    # evidence entry as an artifact it must fetch and hash, so ["path","sha256"]
    # demanded two artifacts literally named "path" and "sha256" and the canary
    # could never be executed. Corrected when the conductor first ran it.
    assert task["evidence"] == ["canary_artifact"]
    # The durable bridge executes exactly one work attempt and rejects a score
    # that asks for more; lease recovery is the gateway's job, not the score's.
    assert task["max_attempts"] == 1
    assert task["checkpoint"] is None

    # Compilation & digest acceptance criteria
    compiled = compile_score(score)
    assert compiled["live_submission_supported"] is False
    score_digest = compiled["score_digest"]
    assert score_digest == digest(score)
    assert score_digest != VIA_CLOUD_LAUNCH_DIGEST
    assert score_digest != AUDIT_RUNNER_DIGEST


def test_hash_artifact_deterministic_write(tmp_path):
    """Prove worker handler writes deterministic body and computes correct sha256."""
    run_id = "run-canary-001"
    evidence = hash_artifact(tmp_path, run_id)

    expected_rel_path = f"score-runs/{run_id}/canary_artifact.json"
    assert evidence["path"] == expected_rel_path

    artifact_file = tmp_path / expected_rel_path
    assert artifact_file.is_file()

    actual_bytes = artifact_file.read_bytes()
    expected_sha = hashlib.sha256(actual_bytes).hexdigest()
    assert evidence["sha256"] == expected_sha

    # Verify JSON content matches known deterministic structure
    doc = json.loads(actual_bytes.decode("utf-8"))
    assert doc == default_canary_body(run_id)


def test_verify_artifact_pass_on_matching_sha(tmp_path):
    """Prove review handler passes iff exact bytes and distinct author/reviewer."""
    run_id = "run-canary-002"
    evidence = hash_artifact(tmp_path, run_id)

    review_res = verify_artifact(
        tmp_path,
        run_id,
        evidence,
        author="canary-worker-instance",
        reviewer="canary-review-instance",
    )
    assert review_res["passed"] is True
    assert review_res["verified_evidence"] is True
    assert review_res["sha256"] == evidence["sha256"]


def test_verify_artifact_fails_when_author_equals_reviewer(tmp_path):
    """Prove review handler strictly fails if author identity equals reviewer."""
    run_id = "run-canary-003"
    evidence = hash_artifact(tmp_path, run_id)

    review_res = verify_artifact(
        tmp_path,
        run_id,
        evidence,
        author="same-agent-instance",
        reviewer="same-agent-instance",
    )
    assert review_res["passed"] is False
    assert review_res["verified_evidence"] is False
    assert "author identity" in review_res["reason"].lower()


def test_verify_artifact_fails_on_tampered_bytes(tmp_path):
    """Prove review handler rejects tampered file content."""
    run_id = "run-canary-004"
    evidence = hash_artifact(tmp_path, run_id)

    # Tamper with file on disk
    artifact_path = tmp_path / evidence["path"]
    artifact_path.write_bytes(b'{"tampered": true}')

    review_res = verify_artifact(
        tmp_path,
        run_id,
        evidence,
        author="canary-worker",
        reviewer="canary-reviewer",
    )
    assert review_res["passed"] is False
    assert review_res["verified_evidence"] is False
    assert "sha-256 mismatch" in review_res["reason"].lower()


def test_verify_artifact_fails_on_tampered_sha(tmp_path):
    """Prove review handler rejects forged sha256 in evidence payload."""
    run_id = "run-canary-005"
    evidence = hash_artifact(tmp_path, run_id)
    evidence["sha256"] = "0" * 64

    review_res = verify_artifact(
        tmp_path,
        run_id,
        evidence,
        author="canary-worker",
        reviewer="canary-reviewer",
    )
    assert review_res["passed"] is False
    assert review_res["verified_evidence"] is False


def test_verify_artifact_rejects_path_traversal(tmp_path):
    """Prove review handler rejects evidence referencing paths outside run dir."""
    run_id = "run-canary-006"
    evidence = {
        "path": f"score-runs/{run_id}/../../outside.json",
        "sha256": "abc",
    }
    review_res = verify_artifact(
        tmp_path,
        run_id,
        evidence,
        author="canary-worker",
        reviewer="canary-reviewer",
    )
    assert review_res["passed"] is False
    assert review_res["verified_evidence"] is False
    assert "traversal" in review_res["reason"].lower()


def test_canary_e2e_sandbox_lifecycle_and_acceptance(canary_score_dict, tmp_path):
    """End-to-end integration: wire handlers directly into SandboxRun state machine."""
    score = canary_score_dict
    run_id = "canary-e2e-run"
    sandbox = SandboxRun(
        score,
        run_id,
        grants=["evidence:write", "evidence:review"],
        authorized_budget_cents=0,
    )

    assert sandbox.ready() == [CANARY_TASK_ID]

    # 1. Start work with worker role
    token = sandbox.start(
        CANARY_TASK_ID,
        actor="worker-beast",
        role=CANARY_ROLE_WORKER,
        now=10,
    )
    assert sandbox.state[CANARY_TASK_ID]["status"] == "running"

    # 2. Worker executes fixed hash_artifact handler
    artifact = hash_artifact(tmp_path, run_id)
    # Evidence is keyed by the ARTIFACT NAME the score declares. NOTE: the two
    # layers disagree on the value - SandboxRun.finish requires a string per
    # name, while ScoreBridge.artifacts requires {"path", "sha256"} so it can
    # re-fetch and re-hash the bytes. The sandbox is the policy model; the
    # bridge is what actually executes. Tracked for the next packet.
    evidence = {"canary_artifact": artifact["sha256"]}

    # 3. Worker finishes task
    sandbox.finish(
        CANARY_TASK_ID,
        token,
        actor="worker-beast",
        evidence=evidence,
        now=11,
    )
    assert sandbox.state[CANARY_TASK_ID]["status"] == "review"

    # 4. Attempt self-review (author reviewing own work) -> Must FAIL
    self_review = verify_artifact(
        tmp_path,
        run_id,
        evidence,
        author="worker-beast",
        reviewer="worker-beast",
    )
    assert self_review["passed"] is False

    # Also prove SandboxRun kernel rejects self-review even if attempted
    with pytest.raises(ScoreError, match="Independent reviewer required"):
        sandbox.review(
            CANARY_TASK_ID,
            token,
            actor="worker-beast",
            role=CANARY_ROLE_REVIEW,
            passed=True,
            verified_evidence=True,
            now=12,
        )

    # 5. Independent reviewer verifies artifact (the reference itself, not the
    # name->string map the sandbox records)
    valid_review = verify_artifact(
        tmp_path,
        run_id,
        artifact,
        author="worker-beast",
        reviewer="reviewer-grok",
    )
    assert valid_review["passed"] is True
    assert valid_review["verified_evidence"] is True

    # 6. Submit valid review
    binding = EvidenceBinding("default", sandbox.fingerprint, run_id, "C01", 1, "head", "build", "deployment")
    verification = VerifiedEvidence(
        binding,
        {},
        (
            {"event_type": "test_receipt_ingested", "receipt": {"event_type": "test_receipt"}},
            {"event_type": "code_review_ingested", "receipt": {"event_type": "code_review"}},
        ),
        "reviewer-grok",
    )
    sandbox.review(
        CANARY_TASK_ID,
        token,
        actor="reviewer-grok",
        role=CANARY_ROLE_REVIEW,
        passed=valid_review["passed"],
        verification=verification,
        now=13,
    )

    assert sandbox.state[CANARY_TASK_ID]["status"] == "accepted"
    report = sandbox.report()
    assert report["launch_accepted"] is True
    assert [event["kind"] for event in report["events"]] == [
        "started",
        "work_completed",
        "test_receipt_ingested",
        "code_review_ingested",
        "accepted",
    ]
