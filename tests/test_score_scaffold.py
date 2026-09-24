"""The scaffolder must emit Score documents the live bridge actually accepts."""
from __future__ import annotations

import json

import pytest

from mco.orchestrator.score_conductor import open_bridge
from mco.orchestrator.score_scaffold import scaffold_score
from mco.orchestrator.scores import compile_score, load_score

SHA = "a" * 40
STEPS = [
    {"id": "S1", "title": "Build the scraper", "brief": "Build a robots-aware scraper for the client site."},
    {"id": "S2", "title": "Package the delivery", "brief": "Write the README and delivery report."},
]


def _score(**overrides):
    kwargs = dict(score_id="client-job-demo", objective="Deliver a tested scraper.", steps=STEPS,
                  worktree_path="C:/AI/clients/demo", target_branch="client/demo", expected_before_sha=SHA,
                  test_command="pytest -q")
    kwargs.update(overrides)
    return scaffold_score(**kwargs)


def test_scaffold_passes_the_score_validator_and_bridge_initialize(tmp_path):
    doc = _score()
    text = json.dumps(doc)
    compile_score(load_score(text))
    bridge = open_bridge(tmp_path / "runs.db", tmp_path / "artifacts", live_executor=object())
    bridge.initialize("dry", text, principal="local-operator", org="default",
                      targets={"codex": "codex-beast", "claude": "claude-beast", "antigravity": "antigravity-beast"},
                      credential_hash="x" * 64)


def test_each_step_gets_two_fixes_with_independent_reviewers():
    tasks = {task["id"]: task for task in _score()["tasks"]}
    assert list(tasks) == ["S1", "S1-fix", "S1-fix2", "S2", "S2-fix", "S2-fix2"]
    assert (tasks["S1"]["role"], tasks["S1"]["review_role"]) == ("codex", "claude")
    assert (tasks["S1-fix"]["role"], tasks["S1-fix"]["review_role"]) == ("claude", "codex")
    assert tasks["S1-fix2"]["review_role"] == "antigravity"
    assert (tasks["S2"]["role"], tasks["S2"]["review_role"]) == ("claude", "codex")
    assert tasks["S1"]["on_reject"] == "S1-fix" and tasks["S1-fix"]["on_reject"] == "S1-fix2"
    assert "on_reject" not in tasks["S1-fix2"]
    assert all(task["max_attempts"] == 1 and task["max_cost_cents"] == 0 for task in tasks.values())


def test_only_the_first_step_pins_the_starting_commit_and_rules_reach_the_worker():
    tasks = {task["id"]: task for task in _score()["tasks"]}
    assert tasks["S1"]["commit"]["expected_before_sha"] == SHA
    assert all("expected_before_sha" not in tasks[i]["commit"] for i in ("S1-fix", "S1-fix2", "S2", "S2-fix"))
    for task in tasks.values():
        text = task["instructions"]
        assert "Files you may commit:" in text and "src/*" in text
        assert '{"ready": true}' in text
        assert "Dead code is a rejection finding" in text
        assert "pytest -q" in text


@pytest.mark.parametrize("bad", [
    {"steps": []},
    {"builders": ("codex", "codex")},
    {"third_reviewer": "claude"},
    {"expected_before_sha": "abc"},
    {"steps": [STEPS[0], STEPS[0]]},
])
def test_scaffold_rejects_unsafe_shapes(bad):
    with pytest.raises(ValueError):
        _score(**bad)


def test_cli_scaffold_writes_a_valid_score(tmp_path):
    from typer.testing import CliRunner
    from mco import cli

    spec = tmp_path / "spec.json"
    spec.write_text(json.dumps({"score_id": "client-job-cli", "objective": "Deliver it.", "steps": STEPS,
                                "worktree_path": "C:/AI/clients/demo", "target_branch": "client/demo",
                                "expected_before_sha": SHA, "builders": ["codex", "claude"], "test_command": "pytest -q"}),
                    encoding="utf-8")
    out = tmp_path / "score.json"
    result = CliRunner().invoke(cli.app, ["score", "scaffold", str(spec), "--out", str(out)])
    assert result.exit_code == 0, result.output
    compile_score(load_score(out.read_text(encoding="utf-8")))
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"score_id": "x", "objective": "y", "steps": [], "worktree_path": "w",
                               "target_branch": "b", "expected_before_sha": SHA}), encoding="utf-8")
    assert CliRunner().invoke(cli.app, ["score", "scaffold", str(bad), "--out", str(tmp_path / "no.json")]).exit_code == 1
