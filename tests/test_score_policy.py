from datetime import datetime, timezone
import json
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from mco.localstore import LocalStore
from mco.orchestrator.auth import hash_token, require_agent
from mco.orchestrator.score_authority import (
    AuthorityError,
    GrantService,
    configured_grant_key,
    grant_identity,
    require_grant,
    sign_grant,
)
from mco.orchestrator.score_gate_routes import score_gates_router, score_grants_router
from mco.orchestrator.score_policy import (
    G08_LAUNCH_SIGNOFF,
    SPEND_ABOVE_CAP,
    GateService,
    authenticated_human,
    required_gate,
    required_gates,
)
from mco.orchestrator.scores import ScoreError, SandboxRun, load_score


NOW = datetime(2026, 9, 15, 12, tzinfo=timezone.utc)
KEY = b"owner-policy-test-key-32-bytes!!"


def strict_grant(**overrides):
    value = {
        "org_id": "acme", "run_id": "run-1", "digest": "a" * 64,
        "actions": ["deploy"], "resources": ["via-api"], "env": "production",
        "not_before": "2026-09-15T00:00:00Z", "expires_at": "2026-09-16T00:00:00Z",
        "budget_cents": 15000, "human_principal": "user:joe",
    }
    value.update(overrides)
    return sign_grant(value, KEY)


def test_signed_grant_is_bound_to_run_action_resource_environment_time_and_budget():
    value = strict_grant()
    identity = require_grant(value, org_id="acme", run_id="run-1", digest="a" * 64,
                             action="deploy", resource="via-api", environment="production",
                             cost_cents=15000, owner_principal="user:joe",
                             verification_key=KEY, now=NOW)
    assert identity == grant_identity(value)
    checks = [
        ({"run_id": "run-2"}, "run"), ({"resource": "database"}, "resource"),
        ({"environment": "staging"}, "environment"), ({"cost_cents": 15001}, "budget"),
    ]
    for changed, message in checks:
        kwargs = dict(org_id="acme", run_id="run-1", digest="a" * 64, action="deploy",
                      resource="via-api", environment="production", cost_cents=1,
                      owner_principal="user:joe", verification_key=KEY, now=NOW)
        kwargs.update(changed)
        with pytest.raises(AuthorityError, match=message):
            require_grant(value, **kwargs)
    forged = dict(value, budget_cents=20000)
    with pytest.raises(AuthorityError, match="signature"):
        require_grant(forged, org_id="acme", run_id="run-1", digest="a" * 64,
                      action="deploy", resource="via-api", environment="production",
                      cost_cents=1, owner_principal="user:joe",
                      verification_key=KEY, now=NOW)


def test_grant_service_persists_reads_and_rejects_tampering(store):
    service = GrantService(store, verification_key=KEY)
    unsigned = {key: value for key, value in strict_grant().items() if key != "signature"}
    saved = service.issue(unsigned)
    assert service.load(org_id="acme", run_id="run-1", digest="a" * 64) == saved
    store.table("score_grants").update({"signature": "deadbeef"}).eq("digest", "a" * 64).execute()
    with pytest.raises(AuthorityError, match="signature"):
        service.load(org_id="acme", run_id="run-1", digest="a" * 64)


def test_grant_key_has_real_config_format_and_fails_closed():
    assert configured_grant_key(KEY.hex()) == KEY
    with pytest.raises(AuthorityError, match="not_configured"):
        configured_grant_key("")


def test_owner_policy_has_exactly_g08_signoff_and_over_cap_spend():
    assert required_gate(task_id="G01", projected_monthly_cents=15000) is None
    assert required_gate(task_id="G09", projected_monthly_cents=15000) == G08_LAUNCH_SIGNOFF
    assert required_gate(task_id="G04", projected_monthly_cents=15001) == SPEND_ABOVE_CAP
    assert required_gates(task_id="G09", projected_monthly_cents=15001) == (
        G08_LAUNCH_SIGNOFF, SPEND_ABOVE_CAP,
    )


def test_agent_role_cannot_impersonate_human_principal():
    with pytest.raises(ScoreError, match="agent bearer"):
        authenticated_human({"instance_id": "agent", "role": "human", "auth_method": "bearer"})
    assert authenticated_human({"instance_id": "user:joe", "auth_method": "session"}) == "user:joe"


@pytest.fixture
def store(tmp_path):
    value = LocalStore(tmp_path / "gates.db")
    yield value
    value.close()


def test_gate_view_has_evidence_and_separate_immutable_decision(store):
    service = GateService(store)
    gate = service.request(org_id="acme", run_id="run-1", digest="a" * 64,
                           task_id="G09", kind=G08_LAUNCH_SIGNOFF,
                           evidence={"g08_report": "sha256:abc"})
    decision = service.decide(gate["id"], caller={"org_id": "acme", "instance_id": "user:joe", "auth_method": "session"},
                              decision="approved", reason="Lorain MVP accepted")
    view = service.list(org_id="acme")[0]
    assert view["evidence"] == {"g08_report": "sha256:abc"}
    assert view["status"] == "approved"
    assert view["decision"] == decision
    assert store.table("score_grants").select("*").execute().data == []
    with pytest.raises(PermissionError):
        store.table("score_checkpoint_decisions").update({"decision": "rejected"}).eq("id", decision["id"]).execute()


def test_human_issuance_route_persists_signed_grant(store, monkeypatch):
    from mco.orchestrator import routes, score_authority
    monkeypatch.setattr(routes, "get_db_client", lambda: store)
    monkeypatch.setattr(score_authority, "get_config", lambda: {"MCO_SCORE_GRANT_KEY": KEY.hex()})
    app = FastAPI()
    app.include_router(score_grants_router)
    app.dependency_overrides[require_agent] = lambda: {
        "org_id": "acme", "instance_id": "user:joe", "auth_method": "session",
        "scopes": ["jobs:approve"],
    }
    response = TestClient(app).post("/api/score/grants", json={
        "run_id": "run-1", "digest": "a" * 64, "actions": ["deploy"],
        "resources": ["via-api"], "environment": "production",
        "not_before": "2026-09-15T00:00:00Z",
        "expires_at": "2026-09-16T00:00:00Z", "budget_cents": 15000,
    })
    assert response.status_code == 200
    saved = store.table("score_grants").select("*").execute().data[0]
    assert saved["human_principal"] == "user:joe"
    assert len(saved["signature"]) == 64


def test_missing_authority_pauses_only_affected_path():
    score = {"score_version": 1, "id": "paths", "revision": 1, "objective": "paths",
             "constraints": ["test"], "budget_cents": 0, "max_parallel": 2,
             "launch_requires": ["a"], "tasks": []}
    for task_id, capability in (("a", "repo:read"), ("b", "cloud:change")):
        score["tasks"].append({"id": task_id, "goal": task_id, "title": task_id, "instructions": "test",
            "role": "worker", "review_role": "reviewer", "depends_on": [], "resources": [task_id],
            "capabilities": [capability], "evidence": ["report"], "max_attempts": 1,
            "timeout_seconds": 10, "max_cost_cents": 0, "checkpoint": None})
    run = SandboxRun(score, "run", grants=["repo:read"], authorized_budget_cents=0)
    assert run.ready() == ["a"]
    assert run.blockers("b") == ["authority"]


def test_policy_gate_is_durable_and_pauses_only_g09_path(store):
    score = {"score_version": 1, "id": "policy-paths", "revision": 1,
             "objective": "policy paths", "constraints": ["test"],
             "budget_cents": 0, "max_parallel": 2,
             "launch_requires": ["G09"], "tasks": []}
    for task_id in ("G09", "G14"):
        score["tasks"].append({"id": task_id, "goal": task_id, "title": task_id,
            "instructions": "test", "role": "worker", "review_role": "reviewer",
            "depends_on": [], "resources": [task_id], "capabilities": ["repo:read"],
            "evidence": ["report"], "max_attempts": 1, "timeout_seconds": 10,
            "max_cost_cents": 0, "checkpoint": None})
    service = GateService(store)
    run = SandboxRun(score, "run-policy", grants=["repo:read"],
                     gate_service=service, org_id="acme")
    assert run.blockers("G09") == ["human_checkpoint"]
    assert run.blockers("G14") == []
    gate = service.list(org_id="acme")[0]
    service.decide(gate["id"], caller={"org_id": "acme", "instance_id": "user:joe", "auth_method": "session"},
                   decision="approved", reason="launch accepted")
    assert run.blockers("G09") == []

    without_store = SandboxRun(score, "run-without-store", grants=["repo:read"])
    with pytest.raises(ScoreError, match="Durable gate service"):
        without_store.approve("G09", actor="user:joe", actor_kind="human")


def test_via_expansion_transits_g09_but_g14_is_maintenance():
    score = load_score(json.loads(Path("examples/scores/via-cloud.score.json").read_text(encoding="utf-8")))
    dependencies = {task["id"]: set(task["depends_on"]) for task in score["tasks"]}

    def transitively_depends(task_id, dependency):
        pending = list(dependencies[task_id])
        seen = set()
        while pending:
            current = pending.pop()
            if current == dependency:
                return True
            if current not in seen:
                seen.add(current)
                pending.extend(dependencies[current])
        return False

    # G09 is the expansion choke point, so one sign-off gates G09-G13.
    assert all(transitively_depends(task_id, "G09") for task_id in ("G10", "G11", "G12", "G13"))
    # G14 is post-G08 maintenance, not expansion; it intentionally bypasses G09.
    assert dependencies["G14"] == {"G08"}
    assert not transitively_depends("G14", "G09")


def test_gate_api_rejects_agent_bearer_even_with_human_role(store, monkeypatch):
    from mco.orchestrator import routes
    gate = GateService(store).request(org_id="default", run_id="run", digest="a" * 64,
                                      task_id="G09", kind=G08_LAUNCH_SIGNOFF, evidence={"report": "sha256:x"})
    monkeypatch.setattr(routes, "get_db_client", lambda: store)
    app = FastAPI()
    app.include_router(score_gates_router)
    app.dependency_overrides[require_agent] = lambda: {
        "org_id": "default", "instance_id": "fake-human-agent", "role": "human",
        "auth_method": "bearer", "scopes": ["jobs:read", "jobs:approve"],
    }
    response = TestClient(app).post(f"/api/score/gates/{gate['id']}/decision", json={"decision": "approved"})
    assert response.status_code == 403


def test_registry_row_cannot_forge_session_auth_over_http(store, monkeypatch):
    from mco.orchestrator import routes
    token = "forged-agent-token"
    store.table("agent_registry").insert({
        "instance_id": "forged-agent", "role": "human", "status": "online",
        "org_id": "default", "auth_method": "session",
        "scopes": ["jobs:read", "jobs:approve"],
        "auth_token_hash": hash_token(token),
    }).execute()
    gate = GateService(store).request(
        org_id="default", run_id="run", digest="a" * 64,
        task_id="G09", kind=G08_LAUNCH_SIGNOFF,
        evidence={"report": "sha256:x"},
    )
    monkeypatch.setattr(routes, "get_db_client", lambda: store)
    app = FastAPI()
    app.include_router(score_gates_router)
    response = TestClient(app).post(
        f"/api/score/gates/{gate['id']}/decision",
        headers={"Authorization": f"Bearer {token}"},
        json={"decision": "approved"},
    )
    assert response.status_code == 403
    assert store.table("score_checkpoint_decisions").select("*").execute().data == []
