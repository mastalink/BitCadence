"""Standing authority is reusable across runs, never across policy changes."""
import copy
from datetime import datetime, timedelta, timezone

import pytest
from typer.testing import CliRunner

from mco.localstore import LocalStore
from mco.orchestrator.score_authority import AuthorityError, GrantService
from mco.orchestrator.scores import digest
from tests.test_score_local_grant import _score, _enable_local_human
from tests.test_score_adapters_live import git_worktree

KEY = b"standing-grant-test-material-00000001"
NOW = datetime.now(timezone.utc)


@pytest.fixture
def setup(tmp_path):
    import json
    path = tmp_path / "score.json"
    _score(path)
    score = json.loads(path.read_text())
    store = LocalStore(tmp_path / "authority.db")
    return GrantService(store, verification_key=KEY), store, score


def approve(service, score, **overrides):
    values = dict(score=score, org_id="default", human_principal="owner",
                  environment="test", not_before=(NOW - timedelta(minutes=1)).isoformat(),
                  expires_at=(NOW + timedelta(hours=1)).isoformat())
    values.update(overrides)
    return service.issue_standing(**values)


def use(service, score, run="run-1", **overrides):
    values = dict(org_id="default", run_id=run, digest=digest(score),
                  action="repository:write", resource="C:/AI/worktree", environment="test",
                  cost_cents=0, owner_principal="owner", now=NOW)
    values.update(overrides)
    return service.require(**values)


def test_same_class_runs_without_another_approval(setup):
    service, store, score = setup
    approved = approve(service, score)
    first, first_id = use(service, score)
    second, second_id = use(service, score, "run-2")
    assert first["standing_grant_id"] == second["standing_grant_id"] == approved["id"]
    assert first_id != second_id
    assert store.table("score_grants").select("*").execute().data == []


@pytest.mark.parametrize("change", ["constraint", "budget", "capability"])
def test_changed_class_requires_fresh_grant(setup, change):
    service, _, score = setup
    approve(service, score)
    changed = copy.deepcopy(score)
    if change == "constraint":
        changed["constraints"][0] = "widened"
    elif change == "budget":
        changed["budget_cents"] += 1
    else:
        changed["tasks"][0]["capabilities"].append("release:publish")
    with pytest.raises(AuthorityError, match="issued_grant_not_found"):
        use(service, changed)


def test_revoke_blocks_existing_and_future_runs_after_restart(setup):
    service, store, score = setup
    approved = approve(service, score)
    use(service, score)
    service.revoke_standing(approved["id"], org_id="default", human_principal="owner")
    restarted = GrantService(store, verification_key=KEY)
    for run in ("run-1", "run-2"):
        with pytest.raises(AuthorityError, match="revoked"):
            use(restarted, score, run)


@pytest.mark.parametrize("overrides,reason", [
    ({"now": NOW + timedelta(hours=2)}, "expired"),
    ({"now": NOW - timedelta(hours=2)}, "not_yet_valid"),
    ({"cost_cents": 1}, "budget_exceeded"),
    ({"environment": "production"}, "environment"),
    ({"resource": "other"}, "resource"),
    ({"action": "release:publish"}, "action"),
    ({"owner_principal": "agent"}, "owner"),
    ({"org_id": "other"}, "not_found"),
])
def test_scopes_and_time_fail_closed(setup, overrides, reason):
    service, _, score = setup
    approve(service, score)
    with pytest.raises(AuthorityError, match=reason):
        use(service, score, **overrides)


@pytest.mark.parametrize("capability", ["money:move", "release:publish", "deploy:production", "unknown:action"])
def test_standing_grants_never_authorize_restricted_or_unknown_capabilities(setup, capability):
    service, _, score = setup
    score["tasks"][0]["capabilities"].append(capability)
    with pytest.raises(AuthorityError, match="standing_capability_forbidden"):
        approve(service, score)


def test_standing_production_environment_forbidden(setup):
    service, _, score = setup
    with pytest.raises(AuthorityError, match="standing_environment_forbidden"):
        approve(service, score, environment="production")


def test_stop_blocks_use_but_allows_revocation(setup, monkeypatch):
    from mco.orchestrator import routes
    service, _, score = setup
    approved = approve(service, score)
    monkeypatch.setattr(routes, "kill_switch_active", lambda: True)
    with pytest.raises(AuthorityError, match="STOP"):
        use(service, score)
    service.revoke_standing(approved["id"], org_id="default", human_principal="owner")


def test_audit_is_durable_and_append_only(setup):
    service, store, score = setup
    approved = approve(service, score)
    use(service, score)
    with pytest.raises(AuthorityError):
        use(service, score, cost_cents=1)
    service.revoke_standing(approved["id"], org_id="default", human_principal="owner")
    rows = store.table("score_standing_events").select("*").execute().data
    assert {r["kind"] for r in rows} >= {"issued", "authorized", "denied", "revoked"}
    for table in ("score_standing_grants", "score_standing_revocations", "score_standing_events", "score_standing_runs"):
        with pytest.raises(Exception, match="[Aa]ppend.only"):
            store.table(table).delete().execute()


def test_local_cli_approve_once_and_revoke(tmp_path, monkeypatch):
    from mco import cli
    store = LocalStore(tmp_path / "local.db")
    path = tmp_path / "score.json"
    _score(path)
    _enable_local_human(monkeypatch, store)
    result = CliRunner().invoke(cli.app, ["score", "grant-standing-local", str(path)],
                                input="ISSUE STANDING GRANT local-grant\n")
    assert result.exit_code == 0, result.output
    rows = store.table("score_standing_grants").select("*").execute().data
    assert len(rows) == 1
    result = CliRunner().invoke(cli.app, ["score", "revoke-standing-local", rows[0]["id"]])
    assert result.exit_code == 0, result.output
    assert len(store.table("score_standing_revocations").select("*").execute().data) == 1


def test_standing_run_blocks_before_dispatch_after_revoke(tmp_path, setup):
    from mco.orchestrator.score_bridge import ScoreBridge, ScoreAuthorityError
    service, _, score = setup
    score["tasks"][0]["capabilities"] = ["evidence:write"]
    approved = approve(service, score)
    bridge = ScoreBridge(tmp_path / "runs.db", tmp_path / "artifacts", authority_service=service)
    bridge.initialize("run-1", score, principal="worker", org="default",
                      targets={"codex": "builder", "claude": "reviewer"}, credential_hash="token",
                      standing_grant_id=approved["id"])
    assert bridge.plan("run-1")
    service.revoke_standing(approved["id"], org_id="default", human_principal="owner")
    restarted = ScoreBridge(tmp_path / "runs.db", tmp_path / "artifacts", authority_service=service)
    with pytest.raises(ScoreAuthorityError, match="revoked"):
        restarted.dispatch("run-1", object())


@pytest.mark.parametrize("change", ["constraints", "budget_cents", "capabilities"])
def test_standing_start_refuses_policy_drift_before_creating_run(tmp_path, setup, change):
    from mco.orchestrator.score_bridge import ScoreBridge, ScoreAuthorityError
    service, _, score = setup
    approved = approve(service, score)
    changed = copy.deepcopy(score)
    if change == "constraints":
        changed[change].append("changed")
    elif change == "budget_cents":
        changed[change] += 1
    else:
        changed["tasks"][0][change].append("cloud:inspect")
    bridge = ScoreBridge(tmp_path / "runs.db", tmp_path / "artifacts", authority_service=service)
    with pytest.raises(ScoreAuthorityError):
        bridge.initialize("changed", changed, principal="worker", org="default",
                          targets={"codex": "builder", "claude": "reviewer"}, credential_hash="token",
                          standing_grant_id=approved["id"])
    with bridge.tx() as db:
        assert db.execute("SELECT count(*) FROM runs").fetchone()[0] == 0


def test_signed_standing_template_cannot_be_tampered(setup):
    from mco.orchestrator.score_authority import verify_grant_signature
    service, _, score = setup
    approved = approve(service, score)
    changed = copy.deepcopy(approved["grant"])
    changed["budget_cents"] += 1
    with pytest.raises(AuthorityError, match="signature_invalid"):
        verify_grant_signature(changed, KEY)
    derived, _ = use(service, score)
    derived["standing_grant_id"] = "other-parent"
    with pytest.raises(AuthorityError, match="signature_invalid"):
        verify_grant_signature(derived, KEY)


def test_cli_wrong_phrase_cannot_issue_standing_grant(tmp_path, monkeypatch):
    from mco import cli
    store = LocalStore(tmp_path / "local.db")
    path = tmp_path / "score.json"
    _score(path)
    _enable_local_human(monkeypatch, store)
    result = CliRunner().invoke(cli.app, ["score", "grant-standing-local", str(path)], input="yes\n")
    assert result.exit_code == 1
    assert store.table("score_standing_grants").select("*").execute().data == []


def test_explicit_run_grant_keeps_its_existing_authority(setup):
    service, _, score = setup
    approved = approve(service, score)
    explicit = dict(approved["grant"], run_id="run-1")
    service.issue(explicit)
    service.revoke_standing(approved["id"], org_id="default", human_principal="owner")
    grant, _ = use(service, score)
    assert "standing_grant_id" not in grant


def test_standing_cannot_be_copied_into_durable_run_grant(setup):
    service, _, score = setup
    approve(service, score)
    grant, _ = use(service, score)
    with pytest.raises(AuthorityError, match="cannot_be_materialized"):
        service.issue(grant)


def test_real_commit_uses_standing_authority_then_revocation_prevents_next_commit(setup, git_worktree):
    import subprocess
    from mco.orchestrator.score_adapters_live import commit_worktree_changes
    service, _, score = setup
    worktree = git_worktree["worktree_dir"]
    score["tasks"][0]["resources"] = [str(worktree)]
    approved = approve(service, score)
    path = worktree / "standing.txt"
    path.write_text("first\n")
    args = dict(worktree_path=worktree, target_branch=git_worktree["branch"],
                allowed_paths=["standing.txt"], commit_message="test standing authority",
                expected_before_sha=git_worktree["initial_sha"], run_id="first-run", task_id="B02",
                digest=digest(score), grant_service=service, org_id="default", resource=str(worktree),
                owner_principal="owner", now=NOW)
    committed = commit_worktree_changes(**args)
    assert len(committed) == 40 and committed != git_worktree["initial_sha"]
    service.revoke_standing(approved["id"], org_id="default", human_principal="owner")
    path.write_text("second\n")
    args.update(run_id="second-run", expected_before_sha=committed)
    with pytest.raises(AuthorityError, match="revoked"):
        commit_worktree_changes(**args)
    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=worktree,
                          check=True, text=True, capture_output=True).stdout.strip()
    assert head == committed


def test_new_approval_cannot_revive_a_run_using_revoked_authority(setup):
    service, _, score = setup
    first = approve(service, score)
    use(service, score, "old-run")
    service.revoke_standing(first["id"], org_id="default", human_principal="owner")
    second = approve(service, score)
    with pytest.raises(AuthorityError, match="revoked"):
        use(service, score, "old-run")
    grant, _ = use(service, score, "new-run")
    assert grant["standing_grant_id"] == second["id"]

