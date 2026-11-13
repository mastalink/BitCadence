"""A Score step is not addressed to an agent the board already knows is down.

Found 2026-11-13: a review sat for hours on an agent that was connected but
whose provider balance was exhausted, while healthy agents idled. A role may
now list fallbacks; the run uses the first one the fleet view does not report
unavailable. With no fleet view nothing changes.
"""
import hashlib

import pytest

from mco.jobs.filters import REASON_EXCLUDE_FINANCE_SECTOR, HardFilterEngine
from mco.jobs.models import JobPosting
from mco.orchestrator.score_bridge import ScoreBridge
from mco.orchestrator.score_conductor import Conductor

from tests.test_score_bridge import Board, score


def _bridge(tmp_path, targets, view=None):
    bridge = ScoreBridge(tmp_path / "state.db", tmp_path / "artifacts")
    board = Board()
    bridge.initialize("run", score(), principal="conductor", org="default",
                      targets=targets, credential_hash=board.identity)
    if view is not None:
        bridge.fleet = lambda: view
    path = bridge.root / "report.json"
    path.write_text('{"audit":"test"}', encoding="utf-8")
    evidence = {"report": {"path": "report.json", "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}}
    return bridge, board, evidence


def _events(bridge, name):
    return [e for e in bridge.status("run")["events"] if e["event"] == name]


def test_work_skips_a_broken_first_target(tmp_path):
    view = {"w1": {"role": "auditor", "available": False, "state": "broken"},
            "w2": {"role": "auditor", "available": True, "state": "standby"}}
    bridge, board, _ = _bridge(tmp_path, {"auditor": ["w1", "w2"], "reviewer": "independent"}, view)
    job = bridge.plan("run")[0]
    bridge.dispatch("run", board)
    assert board.get(job)["target_agent_id"] == "w2"
    fallback = _events(bridge, "target_fallback")
    assert len(fallback) == 1 and '"preferred":"w1"' in fallback[0]["detail"] and '"chosen":"w2"' in fallback[0]["detail"]


def test_review_skips_a_broken_first_reviewer_and_run_is_accepted(tmp_path):
    view = {"rev1": {"role": "reviewer", "available": False, "state": "broken"},
            "rev2": {"role": "reviewer", "available": True, "state": "standby"}}
    bridge, board, evidence = _bridge(tmp_path, {"auditor": "worker", "reviewer": ["rev1", "rev2"]}, view)
    work = bridge.plan("run")[0]
    bridge.dispatch("run", board)
    board.complete(work, {"artifacts": evidence})
    bridge.poll("run", board)
    review = bridge.plan("run")[0]
    bridge.dispatch("run", board)
    assert board.get(review)["target_agent_id"] == "rev2"
    board.complete(review, {"verdict": "pass", "review_of": evidence, "findings": []})
    bridge.poll("run", board)
    assert bridge.status("run")["status"] == "accepted"


def test_fallback_registered_under_another_role_is_addressed_by_its_own_role(tmp_path):
    """The board authenticates a completion by the agent's real role."""
    view = {"rev1": {"role": "reviewer", "available": False, "state": "offline"},
            "spare": {"role": "light", "available": True, "state": "standby"}}
    bridge, board, evidence = _bridge(tmp_path, {"auditor": "worker", "reviewer": ["rev1", "spare"]}, view)
    work = bridge.plan("run")[0]
    bridge.dispatch("run", board)
    board.complete(work, {"artifacts": evidence})
    bridge.poll("run", board)
    review = bridge.plan("run")[0]
    bridge.dispatch("run", board)
    job = board.get(review)
    assert (job["target_agent_id"], job["target_agent_role"]) == ("spare", "light")
    board.complete(review, {"verdict": "pass", "review_of": evidence, "findings": []})
    bridge.poll("run", board)
    assert bridge.status("run")["status"] == "accepted"


def test_healthy_first_target_is_kept_and_no_fallback_is_recorded(tmp_path):
    view = {"w1": {"role": "auditor", "available": True, "state": "standby"},
            "w2": {"role": "auditor", "available": True, "state": "standby"}}
    bridge, board, _ = _bridge(tmp_path, {"auditor": ["w1", "w2"], "reviewer": "independent"}, view)
    job = bridge.plan("run")[0]
    bridge.dispatch("run", board)
    assert board.get(job)["target_agent_id"] == "w1"
    assert _events(bridge, "target_fallback") == []


def test_all_targets_down_keeps_the_first_so_the_job_waits_as_before(tmp_path):
    view = {"w1": {"role": "auditor", "available": False, "state": "broken"},
            "w2": {"role": "auditor", "available": False, "state": "offline"}}
    bridge, board, _ = _bridge(tmp_path, {"auditor": ["w1", "w2"], "reviewer": "independent"}, view)
    job = bridge.plan("run")[0]
    bridge.dispatch("run", board)
    assert board.get(job)["target_agent_id"] == "w1"


@pytest.mark.parametrize("fleet", [None, "raises", "not-a-dict"])
def test_no_or_unreadable_fleet_view_uses_listed_order(tmp_path, fleet):
    bridge, board, _ = _bridge(tmp_path, {"auditor": ["w1", "w2"], "reviewer": "independent"})
    if fleet == "raises":
        def boom():
            raise RuntimeError("presence down")
        bridge.fleet = boom
    elif fleet == "not-a-dict":
        bridge.fleet = lambda: ["w1"]
    job = bridge.plan("run")[0]
    bridge.dispatch("run", board)
    assert board.get(job)["target_agent_id"] == "w1"
    assert bridge.status("run")["status"] == "running"


def test_conductor_reads_board_presence_broken_counts_as_unavailable(tmp_path):
    class PresenceBoard(Board):
        def agents(self):
            return [
                {"instance_id": "w1", "role": "auditor", "effective_status": "online", "state": "broken"},
                {"instance_id": "w2", "role": "auditor", "effective_status": "online", "state": "standby"},
                {"instance_id": "gone", "role": "auditor", "effective_status": "offline", "state": "offline"},
                {"role": "nameless"}, "junk",
            ]

    bridge = ScoreBridge(tmp_path / "state.db", tmp_path / "artifacts")
    board = PresenceBoard()
    bridge.initialize("run", score(), principal="conductor", org="default",
                      targets={"auditor": ["w1", "w2"], "reviewer": "independent"},
                      credential_hash=board.identity)
    conductor = Conductor(bridge, board)
    view = bridge.fleet()
    assert view["w1"]["available"] is False and view["w2"]["available"] is True
    assert view["gone"]["available"] is False and set(view) == {"w1", "w2", "gone"}
    result = conductor.tick("run")
    assert result.error is None
    assert board.get(result.dispatched[0])["target_agent_id"] == "w2"


def test_conductor_without_board_presence_leaves_the_bridge_alone(tmp_path):
    bridge = ScoreBridge(tmp_path / "state.db", tmp_path / "artifacts")
    Conductor(bridge, Board())
    assert bridge.fleet is None


def test_cli_repeated_target_lists_fallbacks_in_order():
    from mco.cli import _parse_targets
    assert _parse_targets(["grok=grok-beast", "claude=claude-code", "grok=reviewer-beast", "grok=grok-beast"]) == {
        "grok": ["grok-beast", "reviewer-beast"], "claude": "claude-code"}
    assert _parse_targets(["codex=codex-beast"]) == {"codex": "codex-beast"}


def test_cli_names_steps_the_sweep_would_never_reach():
    from mco.cli import _steps_outside_launch
    doc = {"launch_requires": ["a"], "tasks": [
        {"id": "a", "on_reject": "a-fix"}, {"id": "a-fix"}, {"id": "b", "on_reject": "b-fix"}, {"id": "b-fix"}]}
    assert _steps_outside_launch(doc) == ["b"]
    doc["launch_requires"] = ["a", "b"]
    assert _steps_outside_launch(doc) == []


def test_a_payments_business_is_excluded_but_taking_payments_is_not():
    def posting(title, description):
        return JobPosting(source="file", id=title, title=title, description=description,
                          budget_type="fixed", budget_min=900.0, budget_max=900.0)

    for title, desc in [
        ("Internal tool", "We are a payments company and need an internal dashboard."),
        ("Ops automation", "Automate onboarding for our payment\nplatform."),
        ("Reporting", "Reports for a merchant acquirer."),
        ("Data cleanup", "We are a card issuer cleaning up CSV exports."),
    ]:
        res = HardFilterEngine.evaluate(posting(title, desc))
        assert res.eligible is False, title
        assert REASON_EXCLUDE_FINANCE_SECTOR in res.reasons, title

    for title, desc in [
        ("Booking site", "Add a payment form so customers can pay a deposit."),
        ("Shop fix", "Our store should accept payments in two currencies."),
    ]:
        res = HardFilterEngine.evaluate(posting(title, desc))
        assert REASON_EXCLUDE_FINANCE_SECTOR not in res.reasons, title
