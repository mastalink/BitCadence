"""A passing review that leaves out an empty findings list does not kill the run.

Found 2026-11-13: a reviewer returned {"verdict": "pass", "review_of": <exact
map>} with no findings key. The run was blocked for good at 4 of 16 steps.
"""
import pytest

from mco.orchestrator.score_bridge import ScoreError

from tests.test_score_bridge import setup  # noqa: F401  (pytest fixture)


def _to_review(b, g, e):
    work = b.plan("run")[0]
    b.dispatch("run", g)
    g.complete(work, {"artifacts": e})
    b.poll("run", g)
    review = b.plan("run")[0]
    b.dispatch("run", g)
    return review


@pytest.mark.parametrize("extra", [{}, {"findings": None}])
def test_pass_without_findings_is_accepted(setup, extra):  # noqa: F811
    b, g, e = setup
    review = _to_review(b, g, e)
    g.complete(review, {"verdict": "pass", "review_of": e, **extra})
    b.poll("run", g)
    assert b.status("run")["status"] == "accepted"


def test_pass_without_findings_still_needs_the_exact_evidence(setup):  # noqa: F811
    b, g, e = setup
    review = _to_review(b, g, e)
    g.complete(review, {"verdict": "pass", "review_of": {}})
    with pytest.raises(ScoreError):
        b.poll("run", g)
    assert b.status("run")["status"] != "accepted"


def test_fail_without_findings_is_still_refused(setup):  # noqa: F811
    b, g, e = setup
    review = _to_review(b, g, e)
    g.complete(review, {"verdict": "fail", "review_of": e})
    with pytest.raises(ScoreError):
        b.poll("run", g)
    assert b.status("run")["status"] != "accepted"


def test_findings_of_the_wrong_type_are_still_refused(setup):  # noqa: F811
    b, g, e = setup
    review = _to_review(b, g, e)
    g.complete(review, {"verdict": "pass", "review_of": e, "findings": "looks fine"})
    with pytest.raises(ScoreError):
        b.poll("run", g)
    assert b.status("run")["status"] != "accepted"


def test_a_blocked_run_can_be_checked_again_and_is_not_waved_through(setup):  # noqa: F811
    b, g, e = setup
    review = _to_review(b, g, e)
    g.complete(review, {"verdict": "pass", "review_of": e, "findings": "looks fine"})
    with pytest.raises(ScoreError):
        b.poll("run", g)
    assert b.status("run")["status"] == "blocked"

    # Still wrong: resuming re-validates and blocks again.
    assert b.resume("run", g) is True
    with pytest.raises(ScoreError):
        b.poll("run", g)
    assert b.status("run")["status"] == "blocked"

    # The cause is fixed (here: the stored result is now well formed).
    g.complete(review, {"verdict": "pass", "review_of": e, "findings": []})
    assert b.resume("run", g) is True
    b.poll("run", g)
    assert b.status("run")["status"] == "accepted"
    assert [ev["event"] for ev in b.status("run")["events"]].count("resumed") == 2


def test_resume_leaves_a_run_that_is_not_blocked_alone(setup):  # noqa: F811
    b, g, e = setup
    assert b.resume("run", g) is False
    assert b.status("run")["status"] == "running"


def _blocked_past_deadline(tmp_path, completed_at):
    """A review whose result was unreadable, then a run left blocked past the review's deadline."""
    import hashlib
    from datetime import datetime, timezone

    from mco.orchestrator.score_bridge import ScoreBridge
    from tests.test_score_bridge import Board, score

    now = [1_000_000.0]
    b = ScoreBridge(tmp_path / "state.db", tmp_path / "artifacts", clock=lambda: now[0])
    g = Board()
    b.initialize("run", score(), principal="conductor", org="default",
                 targets={"auditor": "worker", "reviewer": "independent"}, credential_hash=g.identity)
    path = b.root / "report.json"
    path.write_text('{"audit":"test"}', encoding="utf-8")
    e = {"report": {"path": "report.json", "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}}
    review = _to_review(b, g, e)
    g.complete(review, {"verdict": "pass", "review_of": e, "findings": "looks fine"})
    with pytest.raises(ScoreError):
        b.poll("run", g)
    g.complete(review, {"verdict": "pass", "review_of": e, "findings": []})
    g.jobs[review]["completed_at"] = datetime.fromtimestamp(now[0] + completed_at, timezone.utc).isoformat()
    now[0] += 10_000_000  # far past every deadline
    return b, g


def test_a_result_that_arrived_on_time_is_not_late_after_a_long_block(tmp_path):
    b, g = _blocked_past_deadline(tmp_path, completed_at=5)
    assert b.resume("run", g) is True
    assert b.plan("run") == []
    b.poll("run", g)
    assert b.status("run")["status"] == "accepted"


def test_a_result_that_arrived_after_its_deadline_is_still_late(tmp_path):
    b, g = _blocked_past_deadline(tmp_path, completed_at=9_000_000)
    assert b.resume("run", g) is True
    b.plan("run")
    assert b.status("run")["status"] == "blocked"
    assert "deadline_exceeded" in [ev["event"] for ev in b.status("run")["events"]]
