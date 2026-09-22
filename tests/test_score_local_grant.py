"""Interactive local-human Score grant coverage."""
from __future__ import annotations

import json

from typer.testing import CliRunner

from mco import cli
from mco.localstore import LocalStore
from mco.orchestrator import routes, score_authority


KEY = b"local-human-grant-test-key-material-0001"


def _score(path):
    path.write_text(json.dumps({
        "score_version": 1,
        "id": "local-grant",
        "revision": 1,
        "objective": "test local approval",
        "constraints": ["local"],
        "budget_cents": 0,
        "max_parallel": 1,
        "launch_requires": ["B02"],
        "tasks": [{
            "id": "B02", "goal": "persist", "title": "Persist pause state",
            "instructions": "test", "role": "codex", "review_role": "claude",
            "depends_on": [], "resources": ["C:/AI/worktree"],
            "capabilities": ["repository:write"], "evidence": ["test"],
            "max_attempts": 1, "timeout_seconds": 30, "max_cost_cents": 0,
            "checkpoint": None,
        }],
    }), encoding="utf-8")


def _enable_local_human(monkeypatch, store):
    monkeypatch.setattr(cli, "get_config", lambda: {"MCO_LOCAL_HUMAN_GRANTS": "true"})
    monkeypatch.setattr(cli, "_local_human_platform_supported", lambda: True)
    monkeypatch.setattr(cli, "_local_human_terminal_is_interactive", lambda: True)
    monkeypatch.setattr(cli.getpass, "getuser", lambda: "operator")
    monkeypatch.setattr(routes, "get_db_client", lambda: store)
    monkeypatch.setattr(score_authority, "get_config", lambda: {"MCO_SCORE_GRANT_KEY": KEY.hex()})


def test_local_grant_requires_explicit_enablement(monkeypatch):
    monkeypatch.setattr(cli, "get_config", lambda: {})
    try:
        cli._local_human_principal()
    except RuntimeError as exc:
        assert "disabled" in str(exc)
    else:
        raise AssertionError("local human grant must be disabled by default")


def test_local_grant_issues_signed_authority_after_exact_confirmation(tmp_path, monkeypatch):
    store = LocalStore(tmp_path / "local.db")
    score_file = tmp_path / "score.json"
    _score(score_file)
    _enable_local_human(monkeypatch, store)

    result = CliRunner().invoke(cli.app, [
        "score", "grant-local", str(score_file),
        "--run-id", "run-local-1",
        "--action", "repository:write",
        "--resource", "C:/AI/worktree",
    ], input="ISSUE LOCAL GRANT run-local-1\n")

    assert result.exit_code == 0, result.output
    saved = store.table("score_grants").select("*").execute().data
    assert len(saved) == 1
    assert saved[0]["human_principal"] == "local-windows:operator"
    assert saved[0]["actions"] == ["repository:write"]
    assert saved[0]["resources"] == ["C:/AI/worktree"]


def test_local_grant_refuses_wrong_confirmation(tmp_path, monkeypatch):
    store = LocalStore(tmp_path / "local.db")
    score_file = tmp_path / "score.json"
    _score(score_file)
    _enable_local_human(monkeypatch, store)

    result = CliRunner().invoke(cli.app, [
        "score", "grant-local", str(score_file),
        "--run-id", "run-local-2",
        "--action", "repository:write",
        "--resource", "C:/AI/worktree",
    ], input="yes\n")

    assert result.exit_code == 1
    assert "Confirmation did not match" in result.output
    assert store.table("score_grants").select("*").execute().data == []
