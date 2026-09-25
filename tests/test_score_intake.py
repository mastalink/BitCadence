from __future__ import annotations

import json
import subprocess

import pytest
from typer.testing import CliRunner

from mco import cli
from mco.orchestrator.score_intake import approve_scope, build_delivery, draft_scope
from mco.orchestrator.scores import compile_score, load_score

SHA = "b" * 40
BRIEF = "Build a CLI that converts CSV invoices to JSON. Add a README with usage. Include unit tests."
BAD = "Scrape the member directory behind a login and export every profile."


def test_intake_makes_scope_with_steps_and_acceptance_tests():
    scope = draft_scope(BRIEF, client="Acme")
    assert scope["status"] == "draft" and scope["flags"] == []
    assert len(scope["steps"]) == 4 and scope["acceptance_tests"]
    assert any("Delete all client data" in c for c in scope["constraints"])


@pytest.mark.parametrize("brief,flag", [
    (BAD, "scraping_behind_login"),
    ("Send bulk email to 50k scraped addresses.", "spam_or_automated_messaging"),
    ("Write my essay for the class.", "academic_dishonesty"),
    ("Dashboard for a regional bank.", "financial_institution_client"),
    ("Collect the passwords of our competitors' users.", "third_party_credentials"),
])
def test_guardrails_flag_and_block_approval(brief, flag):
    scope = draft_scope(brief)
    assert flag in [f["id"] for f in scope["flags"]]
    with pytest.raises(ValueError, match="blocking"):
        approve_scope(scope, worktree="C:/w", branch="client/x", before_sha=SHA)


def test_approve_emits_compiling_score_with_client_constraints():
    scope = draft_scope(BRIEF, client="Acme")
    doc = approve_scope(scope, worktree="C:/w", branch="client/acme", before_sha=SHA)
    compile_score(load_score(json.dumps(doc)))
    assert scope["status"] == "approved" and scope["approved_at"]
    assert any("Delete all client data" in c for c in doc["constraints"])
    with pytest.raises(ValueError, match="draft"):
        approve_scope(scope, worktree="C:/w", branch="client/acme", before_sha=SHA)


def test_deliver_builds_bundle_from_fixture_worktree(tmp_path):
    wt = tmp_path / "wt"
    wt.mkdir()

    def run(*a):
        subprocess.run(["git", "-C", str(wt), *a], check=True, capture_output=True)

    run("init", "-q", "-b", "client/acme")
    (wt / "a.py").write_text("print(1)\n")
    run("add", ".")
    run("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "x")
    ev = tmp_path / "ev"
    ev.mkdir()
    (ev / "S1-review.md").write_text("approved")
    names = build_delivery(wt, tmp_path / "out", evidence_dir=ev, test_output="3 passed", client="Acme")
    assert {"code.zip", "README.md", "test-output.txt", "reviews", "client-message.DRAFT.md"} <= set(names)
    assert "DRAFT - NOT SENT" in (tmp_path / "out" / "client-message.DRAFT.md").read_text()


def test_cli_flow_and_failures(tmp_path):
    r = CliRunner()
    brief = tmp_path / "b.txt"
    brief.write_text(BRIEF)
    scope = tmp_path / "scope.json"
    assert r.invoke(cli.app, ["score", "intake", str(brief), "--out", str(scope), "--client", "Acme"]).exit_code == 0
    score = tmp_path / "score.json"
    args = ["score", "intake", "approve", str(scope), "--worktree", "C:/w", "--branch", "client/acme",
            "--before-sha", SHA, "--out", str(score)]
    res = r.invoke(cli.app, args)
    assert res.exit_code == 0, res.output
    compile_score(load_score(score.read_text()))
    assert r.invoke(cli.app, args).exit_code == 1  # no longer draft
    bad = tmp_path / "bad.txt"
    bad.write_text(BAD)
    bscope = tmp_path / "bscope.json"
    assert r.invoke(cli.app, ["score", "intake", str(bad), "--out", str(bscope)]).exit_code == 0
    args[3] = str(bscope)
    assert r.invoke(cli.app, args).exit_code == 1
    empty = tmp_path / "e.txt"
    empty.write_text("  ")
    assert r.invoke(cli.app, ["score", "intake", str(empty), "--out", str(tmp_path / "x.json")]).exit_code == 1
    assert r.invoke(cli.app, ["score", "deliver", str(tmp_path), "--out", str(tmp_path / "d")]).exit_code == 1
