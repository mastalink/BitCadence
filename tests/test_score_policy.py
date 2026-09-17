from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import secrets

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
    GATEWAY_RESTART_AUTHORIZATION,
    SPEND_ABOVE_CAP,
    TASK_CHECKPOINT,
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


def test_same_digest_can_be_granted_to_two_runs_in_one_org(store):
    service = GrantService(store, verification_key=KEY)
    first = service.issue({
        key: value for key, value in strict_grant(run_id="run-1").items()
        if key != "signature"
    })
    second = service.issue({
        key: value for key, value in strict_grant(run_id="run-2").items()
        if key != "signature"
    })

    assert first["id"] != second["id"]
    assert service.load(org_id="acme", run_id="run-1", digest="a" * 64) == first
    assert service.load(org_id="acme", run_id="run-2", digest="a" * 64) == second


def test_same_digest_grants_are_isolated_across_orgs(store):
    service = GrantService(store, verification_key=KEY)
    acme = service.issue({
        key: value for key, value in strict_grant(org_id="acme").items()
        if key != "signature"
    })
    globex = service.issue({
        key: value for key, value in strict_grant(org_id="globex").items()
        if key != "signature"
    })

    assert acme["id"] != globex["id"]
    assert service.load(org_id="acme", run_id="run-1", digest="a" * 64) == acme
    assert service.load(org_id="globex", run_id="run-1", digest="a" * 64) == globex
    with pytest.raises(AuthorityError, match="^issued_grant_not_found$"):
        service.load(org_id="initech", run_id="run-1", digest="a" * 64)


def test_grant_key_has_real_config_format_and_fails_closed():
    assert configured_grant_key(KEY.hex()) == KEY
    invalid_base64 = "not-base64!" * 4
    assert configured_grant_key(invalid_base64) == invalid_base64.encode()
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


def test_gate_service_rejects_unallowed_kind(store):
    service = GateService(store)
    with pytest.raises(ScoreError, match="Gate kind is not allowed by the VIA owner policy"):
        service.request(
            org_id="acme",
            run_id="run-1",
            digest="a" * 64,
            task_id="T01",
            kind="anything-i-want",
            evidence={"checkpoint": {"id": "gate_1", "reason": "test"}},
        )


def test_gate_service_accepts_task_checkpoint_kind(store):
    service = GateService(store)
    gate = service.request(
        org_id="acme",
        run_id="run-1",
        digest="a" * 64,
        task_id="T01",
        kind=TASK_CHECKPOINT,
        evidence={"checkpoint": {"id": "gate_1", "reason": "test"}},
    )
    assert gate["kind"] == TASK_CHECKPOINT
    assert gate["status"] == "pending"


def test_gateway_restart_authorization_distinguishable_from_task_checkpoint_in_audit_trail(store):
    service = GateService(store)
    caller = {"org_id": "acme", "instance_id": "user:joe", "auth_method": "session"}

    checkpoint_gate = service.request(
        org_id="acme",
        run_id="run-1",
        digest="a" * 64,
        task_id="T01",
        kind=TASK_CHECKPOINT,
        evidence={"checkpoint": {"id": "gate_1", "reason": "checkpoint review"}},
    )
    restart_gate = service.request(
        org_id="acme",
        run_id="run-1",
        digest="a" * 64,
        task_id="T02",
        kind=GATEWAY_RESTART_AUTHORIZATION,
        evidence={"target_branch": "codex/score-v1-b01", "deploy_target": "deploy/local"},
    )

    # Both gates appear in list() for org with clearly different kind values
    listed = service.list(org_id="acme")
    listed_by_kind = {item["kind"]: item for item in listed}
    assert TASK_CHECKPOINT in listed_by_kind
    assert GATEWAY_RESTART_AUTHORIZATION in listed_by_kind
    assert listed_by_kind[TASK_CHECKPOINT]["id"] == checkpoint_gate["id"]
    assert listed_by_kind[GATEWAY_RESTART_AUTHORIZATION]["id"] == restart_gate["id"]
    assert listed_by_kind[TASK_CHECKPOINT]["status"] == "pending"
    assert listed_by_kind[GATEWAY_RESTART_AUTHORIZATION]["status"] == "pending"
    assert listed_by_kind[TASK_CHECKPOINT]["decision"] is None
    assert listed_by_kind[GATEWAY_RESTART_AUTHORIZATION]["decision"] is None

    # Deciding TASK_CHECKPOINT does not implicitly decide GATEWAY_RESTART_AUTHORIZATION
    service.decide(checkpoint_gate["id"], caller=caller, decision="approved", reason="checkpoint approved")

    assert service.approved(
        org_id="acme", run_id="run-1", digest="a" * 64, task_id="T01", kind=TASK_CHECKPOINT
    ) is True
    assert service.approved(
        org_id="acme", run_id="run-1", digest="a" * 64, task_id="T02", kind=GATEWAY_RESTART_AUTHORIZATION
    ) is False

    updated_listed = {item["kind"]: item for item in service.list(org_id="acme")}
    assert updated_listed[TASK_CHECKPOINT]["status"] == "approved"
    assert updated_listed[TASK_CHECKPOINT]["decision"]["human_principal"] == "user:joe"
    assert updated_listed[GATEWAY_RESTART_AUTHORIZATION]["status"] == "pending"
    assert updated_listed[GATEWAY_RESTART_AUTHORIZATION]["decision"] is None

    # Separate decide() call is required for GATEWAY_RESTART_AUTHORIZATION
    service.decide(restart_gate["id"], caller=caller, decision="approved", reason="restart authorized")

    assert service.approved(
        org_id="acme", run_id="run-1", digest="a" * 64, task_id="T02", kind=GATEWAY_RESTART_AUTHORIZATION
    ) is True

    final_listed = {item["kind"]: item for item in service.list(org_id="acme")}
    assert final_listed[GATEWAY_RESTART_AUTHORIZATION]["status"] == "approved"
    assert final_listed[GATEWAY_RESTART_AUTHORIZATION]["decision"]["human_principal"] == "user:joe"

    # Distinct records exist in score_checkpoint_decisions
    decisions = store.table("score_checkpoint_decisions").select("*").eq("org_id", "acme").execute().data or []
    assert len(decisions) == 2
    decisions_by_gate = {d["gate_id"]: d for d in decisions}
    assert checkpoint_gate["id"] in decisions_by_gate
    assert restart_gate["id"] in decisions_by_gate


def test_gateway_restart_authorization_evidence_shape_validation(store):
    service = GateService(store)
    valid_evidence = {
        "target_branch": "codex/score-v1-b01",
        "deploy_target": "deploy/local",
    }

    # Missing target_branch
    with pytest.raises(ScoreError, match="target_branch"):
        service.request(
            org_id="acme",
            run_id="run-1",
            digest="a" * 64,
            task_id="T01",
            kind=GATEWAY_RESTART_AUTHORIZATION,
            evidence={"deploy_target": "deploy/local"},
        )

    # Empty target_branch
    with pytest.raises(ScoreError, match="target_branch"):
        service.request(
            org_id="acme",
            run_id="run-1",
            digest="a" * 64,
            task_id="T01",
            kind=GATEWAY_RESTART_AUTHORIZATION,
            evidence={"target_branch": "   ", "deploy_target": "deploy/local"},
        )

    # Non-str target_branch
    with pytest.raises(ScoreError, match="target_branch"):
        service.request(
            org_id="acme",
            run_id="run-1",
            digest="a" * 64,
            task_id="T01",
            kind=GATEWAY_RESTART_AUTHORIZATION,
            evidence={"target_branch": 123, "deploy_target": "deploy/local"},
        )

    # Missing deploy_target
    with pytest.raises(ScoreError, match="deploy_target"):
        service.request(
            org_id="acme",
            run_id="run-1",
            digest="a" * 64,
            task_id="T01",
            kind=GATEWAY_RESTART_AUTHORIZATION,
            evidence={"target_branch": "codex/score-v1-b01"},
        )

    # Empty deploy_target
    with pytest.raises(ScoreError, match="deploy_target"):
        service.request(
            org_id="acme",
            run_id="run-1",
            digest="a" * 64,
            task_id="T01",
            kind=GATEWAY_RESTART_AUTHORIZATION,
            evidence={"target_branch": "codex/score-v1-b01", "deploy_target": ""},
        )

    # Non-str deploy_target
    with pytest.raises(ScoreError, match="deploy_target"):
        service.request(
            org_id="acme",
            run_id="run-1",
            digest="a" * 64,
            task_id="T01",
            kind=GATEWAY_RESTART_AUTHORIZATION,
            evidence={"target_branch": "codex/score-v1-b01", "deploy_target": {"target": "deploy/local"}},
        )

    # Valid request with both succeeds
    gate = service.request(
        org_id="acme",
        run_id="run-1",
        digest="a" * 64,
        task_id="T01",
        kind=GATEWAY_RESTART_AUTHORIZATION,
        evidence=valid_evidence,
    )
    assert gate["kind"] == GATEWAY_RESTART_AUTHORIZATION
    assert gate["status"] == "pending"
    assert gate["evidence"] == valid_evidence


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


def seed_human_session(
    store,
    *,
    user_id: str = "usr-human-1",
    email: str = "human@example.com",
    role: str = "approver",
    scopes: list[str] | None = None,
    org_id: str = "default",
    raw_token: str | None = None,
    user_active: bool = True,
    membership_active: bool = True,
    expires_delta: timedelta = timedelta(hours=8),
    revoked: bool = False,
) -> str:
    if raw_token is None:
        raw_token = f"session-token-{user_id}-{secrets.token_hex(8)}"
    store.table("users").insert({
        "id": user_id,
        "email": email,
        "display_name": "Test Approver",
        "active": user_active,
    }).execute()
    store.table("org_memberships").insert({
        "id": f"mem-{user_id}",
        "org_id": org_id,
        "user_id": user_id,
        "role": role,
        "scopes": ["jobs:read", "jobs:write", "jobs:approve"] if scopes is None else scopes,
        "active": membership_active,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }).execute()
    now = datetime.now(timezone.utc)
    sess = {
        "id": f"sess-{user_id}",
        "org_id": org_id,
        "user_id": user_id,
        "session_token_hash": hash_token(raw_token),
        "expires_at": (now + expires_delta).isoformat(),
    }
    if revoked:
        sess["revoked_at"] = now.isoformat()
    store.table("user_sessions").insert(sess).execute()
    return raw_token


def test_e2e_real_session_decides_score_gate_happy_path(store, monkeypatch):
    from mco.orchestrator import routes
    monkeypatch.setattr(routes, "get_db_client", lambda: store)
    raw_token = seed_human_session(store)
    gate = GateService(store).request(
        org_id="default", run_id="run-e2e-1", digest="b" * 64,
        task_id="G09", kind=G08_LAUNCH_SIGNOFF,
        evidence={"report": "sha256:verified"},
    )
    app = FastAPI()
    app.include_router(score_gates_router)
    # Crucial: NO dependency_overrides on require_agent! The entire real auth chain runs.
    client = TestClient(app, cookies={"mco_session": raw_token})
    response = client.post(
        f"/api/score/gates/{gate['id']}/decision",
        headers={"Origin": "http://testserver"},
        json={"decision": "approved", "reason": "Approved by human operator"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert body["decision"]["decision"] == "approved"
    assert body["decision"]["human_principal"] == "user:usr-human-1"
    assert body["decision"]["reason"] == "Approved by human operator"

    # Verify database persistence
    decisions = store.table("score_checkpoint_decisions").select("*").execute().data
    assert len(decisions) == 1
    assert decisions[0]["human_principal"] == "user:usr-human-1"
    assert decisions[0]["decision"] == "approved"

    # Verify gate request status updated
    gate_row = store.table("score_gate_requests").select("*").eq("id", gate["id"]).execute().data[0]
    assert gate_row["status"] == "approved"


def test_e2e_real_session_issues_score_grant_happy_path(store, monkeypatch):
    from mco.orchestrator import routes, score_authority
    monkeypatch.setattr(routes, "get_db_client", lambda: store)
    monkeypatch.setattr(score_authority, "get_config", lambda: {"MCO_SCORE_GRANT_KEY": KEY.hex()})
    raw_token = seed_human_session(store, user_id="usr-approver-2")

    app = FastAPI()
    app.include_router(score_grants_router)
    # Crucial: NO dependency overrides on require_agent!
    client = TestClient(app, cookies={"mco_session": raw_token})
    response = client.post(
        "/api/score/grants",
        headers={"Origin": "http://testserver"},
        json={
            "run_id": "run-grant-e2e",
            "digest": "c" * 64,
            "actions": ["deploy"],
            "resources": ["via-api"],
            "environment": "production",
            "not_before": "2026-09-15T00:00:00Z",
            "expires_at": "2026-09-16T00:00:00Z",
            "budget_cents": 15000,
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert body["grant"]["human_principal"] == "user:usr-approver-2"

    saved = store.table("score_grants").select("*").eq("digest", "c" * 64).execute().data[0]
    assert saved["human_principal"] == "user:usr-approver-2"
    assert len(saved["signature"]) == 64


def test_e2e_real_session_via_oidc_callback_decides_score_gate(store, monkeypatch):
    from starlette.middleware.sessions import SessionMiddleware
    from mco.orchestrator import routes
    import mco.orchestrator.identity_routes as identity_mod
    import mco.editions as editions_mod
    from tests.test_admin_routes import FakeConfig

    cfg = FakeConfig(
        MCO_SESSION_SECRET="test-session-secret-key-12345",
        MCO_SESSION_COOKIE_SECURE="false",
        MCO_EDITION="enterprise",
    )
    monkeypatch.setattr(identity_mod, "get_db_client", lambda: store)
    monkeypatch.setattr(identity_mod, "get_config", lambda: cfg)
    monkeypatch.setattr(editions_mod, "get_config", lambda: cfg)
    monkeypatch.setattr(routes, "get_db_client", lambda: store)

    class FakeOIDCClient:
        def __init__(self, claims):
            self.claims = claims

        async def authorize_access_token(self, request):
            return {"userinfo": self.claims}

    # Seed OIDC provider and role mapping directly in store
    provider_id = "oidc-test-provider"
    store.table("identity_providers").insert({
        "id": provider_id,
        "org_id": "default",
        "name": "Test IdP",
        "protocol": "oidc",
        "issuer": "https://auth.example.com",
        "client_id": "client-abc",
        "enabled": True,
        "jit_enabled": True,
        "config": {"group_claim": "groups", "scopes": "openid email profile groups"},
    }).execute()
    store.table("role_mappings").insert({
        "id": "rm-1",
        "org_id": "default",
        "identity_provider_id": provider_id,
        "external_group": "Platform-Engineers",
        "role": "approver",
        "scopes": ["jobs:read", "jobs:write", "jobs:approve"],
    }).execute()

    monkeypatch.setattr(
        identity_mod,
        "_oidc_client",
        lambda provider_row, db: FakeOIDCClient({
            "sub": "oidc-sub-999",
            "email": "auditor-alice@example.com",
            "name": "Alice Operator",
            "groups": ["Platform-Engineers"],
        }),
    )

    app = FastAPI()
    app.add_middleware(
        SessionMiddleware,
        secret_key="test-session-secret-key-12345",
        session_cookie="mco_oidc_state",
        https_only=False,
        same_site="lax",
    )
    app.include_router(identity_mod.auth_router)
    app.include_router(score_gates_router)

    gate = GateService(store).request(
        org_id="default", run_id="run-oidc-e2e", digest="d" * 64,
        task_id="G09", kind=G08_LAUNCH_SIGNOFF,
        evidence={"report": "sha256:oidc-evidence"},
    )

    # NO dependency overrides! Real cookie issued by oidc_callback.
    client = TestClient(app)

    # 1. Real login callback issues real session cookie
    cb_resp = client.get(
        f"/api/auth/oidc/{provider_id}/callback",
        follow_redirects=False,
    )
    assert cb_resp.status_code == 303
    assert cb_resp.headers["location"] == "/console"
    raw_cookie = client.cookies.get("mco_session")
    assert raw_cookie

    # 2. Real Score gate decision using that cookie
    dec_resp = client.post(
        f"/api/score/gates/{gate['id']}/decision",
        headers={"Origin": "http://testserver"},
        json={"decision": "approved", "reason": "OIDC human approved"},
    )
    assert dec_resp.status_code == 200
    body = dec_resp.json()
    assert body["success"] is True
    # Verify the human principal matches the JIT-provisioned user
    user_row = store.table("users").select("*").eq("email", "auditor-alice@example.com").execute().data[0]
    expected_principal = f"user:{user_row['id']}"
    assert body["decision"]["human_principal"] == expected_principal
    assert body["decision"]["decision"] == "approved"


def test_e2e_real_session_negative_expired(store, monkeypatch):
    from mco.orchestrator import routes
    monkeypatch.setattr(routes, "get_db_client", lambda: store)
    raw_token = seed_human_session(store, expires_delta=timedelta(hours=-1))
    gate = GateService(store).request(
        org_id="default", run_id="run-neg-1", digest="e" * 64,
        task_id="G09", kind=G08_LAUNCH_SIGNOFF,
        evidence={"report": "sha256:exp"},
    )
    app = FastAPI()
    app.include_router(score_gates_router)
    client = TestClient(app, cookies={"mco_session": raw_token})
    response = client.post(
        f"/api/score/gates/{gate['id']}/decision",
        headers={"Origin": "http://testserver"},
        json={"decision": "approved"},
    )
    assert response.status_code == 401
    assert store.table("score_checkpoint_decisions").select("*").execute().data == []


def test_e2e_real_session_negative_revoked(store, monkeypatch):
    from mco.orchestrator import routes
    monkeypatch.setattr(routes, "get_db_client", lambda: store)
    raw_token = seed_human_session(store, revoked=True)
    gate = GateService(store).request(
        org_id="default", run_id="run-neg-2", digest="f" * 64,
        task_id="G09", kind=G08_LAUNCH_SIGNOFF,
        evidence={"report": "sha256:rev"},
    )
    app = FastAPI()
    app.include_router(score_gates_router)
    client = TestClient(app, cookies={"mco_session": raw_token})
    response = client.post(
        f"/api/score/gates/{gate['id']}/decision",
        headers={"Origin": "http://testserver"},
        json={"decision": "approved"},
    )
    assert response.status_code == 401
    assert store.table("score_checkpoint_decisions").select("*").execute().data == []


def test_e2e_real_session_negative_inactive_org_membership(store, monkeypatch):
    from mco.orchestrator import routes
    monkeypatch.setattr(routes, "get_db_client", lambda: store)
    raw_token = seed_human_session(store, membership_active=False)
    gate = GateService(store).request(
        org_id="default", run_id="run-neg-3", digest="1" * 64,
        task_id="G09", kind=G08_LAUNCH_SIGNOFF,
        evidence={"report": "sha256:inact-mem"},
    )
    app = FastAPI()
    app.include_router(score_gates_router)
    client = TestClient(app, cookies={"mco_session": raw_token})
    response = client.post(
        f"/api/score/gates/{gate['id']}/decision",
        headers={"Origin": "http://testserver"},
        json={"decision": "approved"},
    )
    assert response.status_code == 401
    assert store.table("score_checkpoint_decisions").select("*").execute().data == []


def test_e2e_real_session_negative_inactive_user(store, monkeypatch):
    from mco.orchestrator import routes
    monkeypatch.setattr(routes, "get_db_client", lambda: store)
    raw_token = seed_human_session(store, user_active=False)
    gate = GateService(store).request(
        org_id="default", run_id="run-neg-4", digest="2" * 64,
        task_id="G09", kind=G08_LAUNCH_SIGNOFF,
        evidence={"report": "sha256:inact-usr"},
    )
    app = FastAPI()
    app.include_router(score_gates_router)
    client = TestClient(app, cookies={"mco_session": raw_token})
    response = client.post(
        f"/api/score/gates/{gate['id']}/decision",
        headers={"Origin": "http://testserver"},
        json={"decision": "approved"},
    )
    assert response.status_code == 401
    assert store.table("score_checkpoint_decisions").select("*").execute().data == []


def test_e2e_real_session_negative_csrf_cross_site(store, monkeypatch):
    from mco.orchestrator import routes
    monkeypatch.setattr(routes, "get_db_client", lambda: store)
    raw_token = seed_human_session(store)
    gate = GateService(store).request(
        org_id="default", run_id="run-neg-5", digest="3" * 64,
        task_id="G09", kind=G08_LAUNCH_SIGNOFF,
        evidence={"report": "sha256:csrf"},
    )
    app = FastAPI()
    app.include_router(score_gates_router)
    client = TestClient(app, cookies={"mco_session": raw_token})
    response = client.post(
        f"/api/score/gates/{gate['id']}/decision",
        headers={
            "sec-fetch-site": "cross-site",
            "Origin": "http://testserver",
        },
        json={"decision": "approved"},
    )
    assert response.status_code == 403
    assert "Cross-site session request denied" in response.text
    assert store.table("score_checkpoint_decisions").select("*").execute().data == []


def test_e2e_real_session_negative_csrf_origin_mismatch(store, monkeypatch):
    from mco.orchestrator import routes
    monkeypatch.setattr(routes, "get_db_client", lambda: store)
    raw_token = seed_human_session(store)
    gate = GateService(store).request(
        org_id="default", run_id="run-neg-6", digest="4" * 64,
        task_id="G09", kind=G08_LAUNCH_SIGNOFF,
        evidence={"report": "sha256:mismatch"},
    )
    app = FastAPI()
    app.include_router(score_gates_router)
    client = TestClient(app, cookies={"mco_session": raw_token})

    # 1. Origin header from untrusted third party
    resp_evil = client.post(
        f"/api/score/gates/{gate['id']}/decision",
        headers={"Origin": "https://evil-attacker.com"},
        json={"decision": "approved"},
    )
    assert resp_evil.status_code == 403
    assert "Session request origin did not match" in resp_evil.text

    # 2. Missing Origin header on mutation
    resp_missing = client.post(
        f"/api/score/gates/{gate['id']}/decision",
        json={"decision": "approved"},
    )
    assert resp_missing.status_code == 403
    assert "Session request origin did not match" in resp_missing.text

    assert store.table("score_checkpoint_decisions").select("*").execute().data == []


def test_e2e_real_session_negative_insufficient_scope(store, monkeypatch):
    from mco.orchestrator import routes
    monkeypatch.setattr(routes, "get_db_client", lambda: store)
    raw_token = seed_human_session(
        store,
        role="viewer",
        scopes=["jobs:read", "agents:read"],
    )
    gate = GateService(store).request(
        org_id="default", run_id="run-neg-7", digest="5" * 64,
        task_id="G09", kind=G08_LAUNCH_SIGNOFF,
        evidence={"report": "sha256:viewer"},
    )
    app = FastAPI()
    app.include_router(score_gates_router)
    client = TestClient(app, cookies={"mco_session": raw_token})
    response = client.post(
        f"/api/score/gates/{gate['id']}/decision",
        headers={"Origin": "http://testserver"},
        json={"decision": "approved"},
    )
    assert response.status_code == 403
    assert "Token lacks required scope(s): jobs:approve" in response.text
    assert store.table("score_checkpoint_decisions").select("*").execute().data == []


def test_e2e_real_session_negative_org_tenant_isolation(store, monkeypatch):
    from mco.orchestrator import routes
    monkeypatch.setattr(routes, "get_db_client", lambda: store)
    raw_token = seed_human_session(store, org_id="tenant-a")
    gate = GateService(store).request(
        org_id="tenant-b", run_id="run-neg-8", digest="6" * 64,
        task_id="G09", kind=G08_LAUNCH_SIGNOFF,
        evidence={"report": "sha256:tenant"},
    )
    app = FastAPI()
    app.include_router(score_gates_router)
    client = TestClient(app, cookies={"mco_session": raw_token})
    response = client.post(
        f"/api/score/gates/{gate['id']}/decision",
        headers={"Origin": "http://testserver"},
        json={"decision": "approved"},
    )
    assert response.status_code == 409
    assert "Gate not found" in response.text
    assert store.table("score_checkpoint_decisions").select("*").execute().data == []


def test_e2e_real_session_issue_grant_negative_csrf_and_scopes(store, monkeypatch):
    from mco.orchestrator import routes, score_authority
    monkeypatch.setattr(routes, "get_db_client", lambda: store)
    monkeypatch.setattr(score_authority, "get_config", lambda: {"MCO_SCORE_GRANT_KEY": KEY.hex()})

    app = FastAPI()
    app.include_router(score_grants_router)

    grant_payload = {
        "run_id": "run-neg-grant",
        "digest": "7" * 64,
        "actions": ["deploy"],
        "resources": ["via-api"],
        "environment": "production",
        "not_before": "2026-09-15T00:00:00Z",
        "expires_at": "2026-09-16T00:00:00Z",
        "budget_cents": 5000,
    }

    # 1. CSRF cross-site rejected
    valid_token = seed_human_session(store, user_id="usr-csrf-grant")
    client_csrf = TestClient(app, cookies={"mco_session": valid_token})
    resp_csrf = client_csrf.post(
        "/api/score/grants",
        headers={"sec-fetch-site": "cross-site", "Origin": "http://testserver"},
        json=grant_payload,
    )
    assert resp_csrf.status_code == 403
    assert "Cross-site session request denied" in resp_csrf.text

    # 2. Insufficient scope rejected
    viewer_token = seed_human_session(store, user_id="usr-viewer-grant", role="viewer", scopes=["jobs:read"])
    client_viewer = TestClient(app, cookies={"mco_session": viewer_token})
    resp_viewer = client_viewer.post(
        "/api/score/grants",
        headers={"Origin": "http://testserver"},
        json=grant_payload,
    )
    assert resp_viewer.status_code == 403
    assert "Token lacks required scope(s): jobs:approve" in resp_viewer.text

    # Assert no grant was persisted
    assert store.table("score_grants").select("*").execute().data == []

