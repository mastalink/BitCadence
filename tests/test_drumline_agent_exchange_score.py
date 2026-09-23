"""Governance proofs for the Drumline Agent Exchange Score."""

from pathlib import Path

from mco.orchestrator.scores import SandboxRun, load_score


ROOT = Path(__file__).parents[1]
SCORE_PATH = ROOT / "examples" / "scores" / "drumline-agent-exchange.score.json"


def _score():
    return load_score(SCORE_PATH.read_text(encoding="utf-8"))


def test_score_has_serial_bounded_phase_chain():
    score = _score()
    assert score["revision"] == 2
    assert score["budget_cents"] == 0
    assert score["max_parallel"] == 1
    assert [task["id"] for task in score["tasks"]] == [
        "D01", "R01", "B01", "R02", "M01", "D02"
    ]
    assert score["launch_requires"] == ["D02"]
    assert {task["id"]: task["depends_on"] for task in score["tasks"]} == {
        "D01": [],
        "R01": ["D01"],
        "B01": ["R01"],
        "R02": ["B01"],
        "M01": ["R02"],
        "D02": ["M01"],
    }


def test_every_phase_declares_bounds_evidence_independence_and_failure_behavior():
    score = _score()
    for task in score["tasks"]:
        assert task["role"] != task["review_role"]
        assert task["resources"]
        assert task["evidence"]
        assert task["max_attempts"] in (1, 2)
        assert "ALLOWED PATHS:" in task["instructions"]
        assert "block" in task["instructions"].lower() or "terminal" in task["instructions"].lower()
        assert "escalat" in task["instructions"].lower()
    assert next(t for t in score["tasks"] if t["id"] == "R01")["max_attempts"] == 1
    assert next(t for t in score["tasks"] if t["id"] == "R02")["max_attempts"] == 1


def test_score_grants_no_cloud_authority_and_merge_requires_security_codeql():
    score = _score()
    capabilities = {cap for task in score["tasks"] for cap in task["capabilities"]}
    assert not any("cloud" in cap.lower() or "aws" in cap.lower() for cap in capabilities)
    merge = next(task for task in score["tasks"] if task["id"] == "M01")
    assert merge["depends_on"] == ["R02"]
    assert {"ci_receipt", "security_receipt", "codeql_receipt"} <= set(merge["evidence"])
    assert "no source edits" in merge["instructions"].lower()
    assert "merge-around" in merge["instructions"].lower()


def test_r01_rejection_blocks_b01_and_m01():
    """Cheapest fail-closed proof: rejected R01 can release no downstream work."""
    score = _score()
    grants = {cap for task in score["tasks"] for cap in task["capabilities"]}
    run = SandboxRun(
        score,
        "drumline-r01-rejection-proof",
        grants=sorted(grants),
        authorized_budget_cents=0,
    )

    # Model D01 as independently accepted; R01 is now the only ready phase.
    run.state["D01"]["status"] = "accepted"
    assert run.ready() == ["R01"]

    token = run.start("R01", actor="antigravity-beast", role="antigravity", now=1)
    run.finish(
        "R01",
        token,
        actor="antigravity-beast",
        evidence={
            "verdict": "fail",
            "review_of_commit": "d01-exact-head",
            "blocking_findings": "Unsafe authority boundary",
            "tests": "focused score tests",
        },
        now=2,
    )
    run.review(
        "R01",
        token,
        actor="chief-beast",
        role="chief",
        passed=False,
        now=3,
    )

    report = run.report()
    assert report["tasks"]["R01"]["status"] == "blocked"
    assert "dependencies" in report["blockers"]["B01"]
    assert "dependencies" in report["blockers"]["M01"]
    assert not report["launch_accepted"]
    assert run.ready() == []


def test_d01_commit_is_limited_to_design_score_and_focused_test():
    score = _score()
    d01 = next(task for task in score["tasks"] if task["id"] == "D01")
    assert d01["commit"]["expected_before_sha"] == (
        "0b9104d2056432726fedeb268a500057d42abc03"
    )
    assert d01["commit"]["worktree_path"] == (
        "C:/AI/baton/wt/drumline-d01-codex-beast-r3"
    )
    assert d01["commit"]["target_branch"] == (
        "score/drumline-agent-exchange-d01-r3"
    )
    assert d01["commit"]["allowed_paths"] == [
        "docs/DRUMLINE-AGENT-EXCHANGE-DESIGN.md",
        "examples/scores/drumline-agent-exchange.score.json",
        "tests/test_drumline_agent_exchange_score.py",
    ]


def test_stale_revision_receipts_cannot_release_b01():
    score = _score()
    r01 = next(task for task in score["tasks"] if task["id"] == "R01")
    b01 = next(task for task in score["tasks"] if task["id"] == "B01")
    assert "earlier D01/R01/B01/R02 receipts as stale" in r01["instructions"]
    assert "exact D01 revision 2 commit" in b01["instructions"]
    assert "scripts/build_console.py" in b01["commit"]["allowed_paths"]
    assert "LF and CRLF" in b01["instructions"]
    assert "new governed Score revision/run" in b01["instructions"]
