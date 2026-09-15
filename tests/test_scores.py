"""Offline policy proofs, not authenticated gateway/cloud acceptance."""
import copy
import json
from pathlib import Path

import pytest

from mco.orchestrator.scores import ScoreError, SandboxRun, compile_score, digest, load_score


def task(key, deps=(), checkpoint=None):
    return dict(id=key, goal=key, title=key, instructions="Deliver tested artifact", role="builder", review_role="reviewer", depends_on=list(deps), resources=[key], capabilities=["sandbox:write"], evidence=["sha", "tests"], max_attempts=2, timeout_seconds=10, max_cost_cents=5, checkpoint=checkpoint)


def score():
    return dict(score_version=1, id="demo", revision=1, objective="Test launch", constraints=["No external effects"], budget_cents=100, max_parallel=2, tasks=[task("build"), task("launch", ["build"], {"id": "release", "reason": "Explicit demo gate"})], launch_requires=["launch"])


def run(value=None, **kwargs):
    return SandboxRun(value or score(), "run1", grants=["sandbox:write"], authorized_budget_cents=100, **kwargs)


def finish(r, key, now):
    token = r.start(key, actor="author", role="builder", now=now)
    r.finish(key, token, actor="author", evidence={"sha": "artifact1", "tests": "passing-report"}, now=now + 1)
    return token


def accept(r, key, token, now):
    r.review(key, token, actor="independent", role="reviewer", passed=True, verified_evidence=True, now=now)


def test_full_flow_failure_retry_review_human_gate_launch():
    r = run()
    first = r.start("build", actor="crashed", role="builder", now=0)
    r.expire(now=10)
    second = finish(r, "build", 11)
    with pytest.raises(ScoreError):
        r.finish("build", first, actor="crashed", evidence={"sha": "old", "tests": "old"}, now=12)
    assert "dependencies" in r.blockers("launch")
    accept(r, "build", second, 13)
    assert r.blockers("launch") == ["human_checkpoint"]
    with pytest.raises(ScoreError):
        r.approve("launch", actor="agent", actor_kind="agent")
    r.approve("launch", actor="owner", actor_kind="human")
    third = finish(r, "launch", 14)
    accept(r, "launch", third, 16)
    assert r.report()["launch_accepted"]
    assert r.ready() == []
    assert r.reserved == 15


@pytest.mark.parametrize("change", [
    lambda s: s.update(score_version=True),
    lambda s: s.update(score_version=2),
    lambda s: s.update(budget_cents=-1),
    lambda s: s.update(max_parallel=0),
    lambda s: s.update(tasks=[]),
    lambda s: s.update(unknown=True),
    lambda s: s.update(launch_requires=["missing"]),
    lambda s: s["tasks"][0].update(id="launch"),
    lambda s: s["tasks"][0].update(depends_on=["missing"]),
    lambda s: s["tasks"][0].update(depends_on=["launch"]),
    lambda s: s["tasks"][0].update(review_role="builder"),
    lambda s: s["tasks"][0].update(max_attempts=True),
    lambda s: s["tasks"][0].update(evidence=[]),
    lambda s: s["tasks"][0].update(resources=["x", "x"]),
    lambda s: s["tasks"][0].update(checkpoint={"id": "x"}),
    lambda s: s["tasks"][0].update(capabilities="all"),
    lambda s: s["tasks"][0].update(max_cost_cents=1.5),
    lambda s: s["tasks"][0].update(role=" "),
])
def test_invalid_contracts(change):
    value = score()
    change(value)
    with pytest.raises(ScoreError):
        load_score(value)


def test_duplicate_json_keys_and_paths_rejected():
    for text in ('{"id":"one","id":"two"}', __file__, '[]', '{oops'):
        with pytest.raises(ScoreError):
            load_score(text)


def test_score_is_copied_and_digest_binds_every_field():
    original = score()
    loaded = load_score(original)
    original["objective"] = "Changed"
    assert loaded["objective"] != original["objective"]
    assert digest(loaded) != digest(original)
    assert digest(loaded) == digest(json.loads(json.dumps(loaded)))


def test_compiler_cannot_be_mistaken_for_legacy_workflow():
    plan = compile_score(score())
    assert plan["live_submission_supported"] is False
    assert "steps" not in plan
    assert plan["packets"][1]["checkpoint"]["id"] == "release"
    assert plan["packets"][0]["downstream_unlock"] == "accepted_not_worker_completed"


def test_empty_external_authority_and_budget_fail_closed():
    r = SandboxRun(score(), "no-grants")
    assert "authority" in r.blockers("build")
    assert "budget" in r.blockers("build")
    assert r.ready() == []


def test_budget_reservations_retries_and_limit():
    value = score()
    value["budget_cents"] = 5
    r = run(value)
    r.start("build", actor="author", role="builder", now=0)
    r.expire(now=10)
    assert "budget" in r.blockers("build")


def test_retries_exhaust_and_do_not_release_dependencies():
    r = run()
    for now in (0, 10):
        r.start("build", actor="author", role="builder", now=now)
        r.expire(now=now + 10)
    assert r.state["build"]["status"] == "blocked"
    assert "dependencies" in r.blockers("launch")


def test_reviewer_cannot_be_author_or_wrong_role():
    r = run()
    token = finish(r, "build", 0)
    for actor, role in (("author", "reviewer"), ("independent", "builder")):
        with pytest.raises(ScoreError):
            r.review("build", token, actor=actor, role=role, passed=True, verified_evidence=True, now=2)
    with pytest.raises(ScoreError):
        r.review("build", token, actor="independent", role="reviewer", passed=True, verified_evidence=False, now=2)
    accept(r, "build", token, 2)
    with pytest.raises(ScoreError):
        accept(r, "build", token, 2)


def test_evidence_is_complete_and_bound_to_author():
    r = run()
    token = r.start("build", actor="author", role="builder", now=0)
    for evidence in ({}, {"sha": "a"}, {"sha": "a", "tests": ""}, {"sha": "a", "tests": "b", "extra": "x"}):
        with pytest.raises(ScoreError):
            r.finish("build", token, actor="author", evidence=evidence, now=1)
    with pytest.raises(ScoreError):
        r.finish("build", token, actor="other", evidence={"sha": "a", "tests": "b"}, now=1)


def test_review_rejection_retries_and_review_expiry_is_bounded():
    r = run()
    token = finish(r, "build", 0)
    r.review("build", token, actor="independent", role="reviewer", passed=False, verified_evidence=False, now=2)
    finish(r, "build", 3)
    r.expire(now=14)
    assert r.state["build"]["status"] == "blocked"


def test_resources_lock_through_review_and_unlock_after_acceptance():
    value = score()
    value["tasks"][1].update(depends_on=[], checkpoint=None, resources=["build"])
    r = run(value)
    token = finish(r, "build", 0)
    assert "resource_lock" in r.blockers("launch")
    accept(r, "build", token, 2)
    assert r.ready() == ["launch"]


def test_parallel_limit():
    value = score()
    value["max_parallel"] = 1
    value["tasks"][1].update(depends_on=[], checkpoint=None)
    r = run(value)
    finish(r, "build", 0)
    assert "parallel_limit" in r.blockers("launch")


def test_deadline_and_clock_enforced():
    r = run()
    token = r.start("build", actor="author", role="builder", now=5)
    with pytest.raises(ScoreError):
        r.expire(now=4)
    with pytest.raises(ScoreError):
        r.finish("build", token, actor="author", evidence={"sha": "a", "tests": "b"}, now=15)


def test_checkpoint_does_not_grant_capabilities():
    r = SandboxRun(score(), "no-authority")
    r.approve("launch", actor="owner", actor_kind="human")
    assert "authority" in r.blockers("launch")


def test_report_cannot_mutate_internal_state():
    r = run()
    r.report()["tasks"]["build"]["status"] = "accepted"
    assert r.state["build"]["status"] == "pending"


def test_via_score_has_all_goals_no_invented_human_checkpoints():
    path = Path(__file__).parents[1] / "examples/scores/via-cloud.score.json"
    value = load_score(path.read_text(encoding="utf-8"))
    assert len(value["tasks"]) == 14
    assert value["launch_requires"] == ["G08"]
    assert all(t["checkpoint"] is None for t in value["tasks"])
    assert SandboxRun(value, "via-preview").ready() == []
