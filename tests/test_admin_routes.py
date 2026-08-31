"""Admin API tests: agent management, settings whitelist, workflow submission."""

import base64
import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import mco.editions as editions_mod
import mco.notifiers.ntfy as ntfy_mod
import mco.orchestrator.admin_routes as admin_mod
import mco.orchestrator.routes as routes_mod
from mco.orchestrator.auth import require_agent, verify_token
from mco.orchestrator.admin_routes import (
    agents_admin_router,
    governance_router,
    settings_router,
    workflows_router,
)
from mco.orchestrator.routes import (
    agents_router,
    decorate_presence,
    router as jobs_router,
)

from tests.test_routes import FakeDB

ADMIN = {"instance_id": "joe", "role": "human", "status": "online", "org_id": "default"}
WORKER = {"instance_id": "w1", "role": "codex", "status": "online", "org_id": "default"}
ORG_ADMIN = {"instance_id": "acme-admin", "role": "admin", "status": "online", "org_id": "acme"}


class FakeConfig:
    """In-memory ConfigManager stand-in recording set/delete calls."""

    def __init__(self, **values):
        self.values = dict(values)
        self.deleted = []
        self.set_calls = []

    def get(self, key, default=None):
        return self.values.get(key, default)

    def set(self, key, value, encrypt=False):
        self.values[key] = value
        self.set_calls.append((key, value, encrypt))

    def delete(self, key):
        self.values.pop(key, None)
        self.deleted.append(key)


@pytest.fixture(autouse=True)
def setup(monkeypatch):
    db = FakeDB()
    cfg = FakeConfig()
    monkeypatch.setattr(routes_mod, "get_db_client", lambda: db)
    monkeypatch.setattr(admin_mod, "get_config", lambda: cfg)
    monkeypatch.setattr(editions_mod, "get_config", lambda: cfg)
    monkeypatch.setattr(ntfy_mod, "notify", lambda *a, **k: True)

    app = FastAPI()
    app.include_router(jobs_router)
    app.include_router(agents_router)
    app.include_router(agents_admin_router)
    app.include_router(governance_router)
    app.include_router(settings_router)
    app.include_router(workflows_router)
    app.dependency_overrides[require_agent] = lambda: ADMIN

    ctx = type("Ctx", (), {})
    pytest.ctx = ctx
    ctx.db = db
    ctx.cfg = cfg
    ctx.app = app
    ctx.http = TestClient(app)
    yield


def _ctx():
    return pytest.ctx


def _as(agent):
    _ctx().app.dependency_overrides[require_agent] = lambda: agent


# ── Agent management ─────────────────────────────────────────────────────────

class TestRegisterAgent:
    def test_register_returns_token_once_and_persists_hash(self):
        resp = _ctx().http.post("/api/agents", json={"instance_id": "new-w", "role": "codex"})
        assert resp.status_code == 200
        body = resp.json()
        assert body["token"].startswith("mco_tok_")
        assert "auth_token_hash" not in body["agent"]
        # the token round-trips through real verification
        agent = verify_token(_ctx().db, body["token"])
        assert agent["instance_id"] == "new-w"

    def test_register_with_scopes(self):
        resp = _ctx().http.post("/api/agents", json={
            "instance_id": "ro", "role": "viewer", "scopes": ["jobs:read", "agents:read"]})
        assert resp.status_code == 200
        assert resp.json()["agent"]["scopes"] == ["agents:read", "jobs:read"]

    def test_duplicate_is_409_never_silent_rotation(self):
        _ctx().http.post("/api/agents", json={"instance_id": "dup", "role": "codex"})
        resp = _ctx().http.post("/api/agents", json={"instance_id": "dup", "role": "codex"})
        assert resp.status_code == 409

    def test_unknown_scope_400(self):
        resp = _ctx().http.post("/api/agents", json={
            "instance_id": "x", "role": "codex", "scopes": ["jobs:fly"]})
        assert resp.status_code == 400
        assert "jobs:fly" in resp.json()["detail"]

    def test_missing_fields_400(self):
        assert _ctx().http.post("/api/agents", json={"role": "codex"}).status_code == 400

    def test_worker_token_cannot_register(self):
        _as(WORKER)
        resp = _ctx().http.post("/api/agents", json={"instance_id": "x", "role": "codex"})
        assert resp.status_code == 403
        assert "agents:manage" in resp.json()["detail"]

    def test_org_admin_is_forced_into_own_org(self):
        _as(ORG_ADMIN)
        resp = _ctx().http.post("/api/agents", json={
            "instance_id": "acme-w", "role": "codex", "org": "other-org"})
        assert resp.status_code == 200
        assert resp.json()["agent"]["org_id"] == "acme"


class TestInstanceIdValidation:
    """Stored-XSS defense-in-depth: instance_id/role are rendered into the
    Control Panel, so the register endpoint must reject anything outside a safe
    identifier charset before it can reach an HTML/JS sink (security finding #1).
    """

    @pytest.mark.parametrize("bad", [
        "x' onmouseover='alert(1)",          # single-quote attribute breakout
        'x"><script>alert(1)</script>',      # tag injection
        "a/b",                                # path-ish / slash
        "has space",                          # whitespace
        "x" * 65,                             # over the 64-char bound
    ])
    def test_unsafe_instance_id_rejected(self, bad):
        resp = _ctx().http.post("/api/agents", json={"instance_id": bad, "role": "codex"})
        assert resp.status_code == 400
        assert _ctx().db._agents == []        # nothing persisted

    def test_unsafe_role_rejected(self):
        resp = _ctx().http.post("/api/agents", json={
            "instance_id": "ok-worker", "role": "co'dex"})
        assert resp.status_code == 400
        assert _ctx().db._agents == []

    @pytest.mark.parametrize("good", ["codex-worker-2", "claude.research_01", "sso:joe", "A1"])
    def test_safe_identifiers_accepted(self, good):
        resp = _ctx().http.post("/api/agents", json={"instance_id": good, "role": "codex"})
        assert resp.status_code == 200
        assert resp.json()["agent"]["instance_id"] == good


class TestOrgAllowlist:
    def test_unknown_org_is_rejected_not_minted(self):
        """The Baton-worker accident: a typo'd org must never create a tenant."""
        resp = _ctx().http.post("/api/agents", json={
            "instance_id": "oops", "role": "codex", "org": "Baton-worker"})
        assert resp.status_code == 400
        assert "not configured" in resp.json()["detail"]
        assert "Settings" in resp.json()["detail"]
        assert _ctx().db._agents == []  # nothing created

    def test_configured_org_is_accepted(self):
        _ctx().cfg.values["MCO_ORGS"] = "acme, beta-team"
        resp = _ctx().http.post("/api/agents", json={
            "instance_id": "acme-w", "role": "codex", "org": "acme"})
        assert resp.status_code == 200
        assert resp.json()["agent"]["org_id"] == "acme"

    def test_default_always_allowed(self):
        resp = _ctx().http.post("/api/agents", json={
            "instance_id": "plain", "role": "codex"})
        assert resp.status_code == 200

    def test_orgs_endpoint_host_view(self):
        _ctx().cfg.values["MCO_ORGS"] = "acme"
        _ctx().db._agents.append({"instance_id": "legacy", "role": "codex",
                                  "status": "offline", "org_id": "grandfathered",
                                  "auth_token_hash": "x"})
        body = _ctx().http.get("/api/agents/orgs").json()
        assert body["host_operator"] is True
        assert body["orgs"] == ["acme", "default"]
        assert "grandfathered" in body["in_use"]  # existing tenants stay visible

    def test_orgs_endpoint_org_admin_sees_only_home(self):
        _as(ORG_ADMIN)
        body = _ctx().http.get("/api/agents/orgs").json()
        assert body == {"orgs": ["acme"], "host_operator": False}


class TestTokenLifecycle:
    def test_reset_rotates_token(self):
        old = _ctx().http.post("/api/agents", json={"instance_id": "rot", "role": "codex"}).json()
        resp = _ctx().http.post("/api/agents/rot/reset-token")
        assert resp.status_code == 200
        new_token = resp.json()["token"]
        assert new_token != old["token"]
        assert verify_token(_ctx().db, old["token"]) is None      # old dead
        assert verify_token(_ctx().db, new_token)["instance_id"] == "rot"

    def test_reset_unknown_agent_404(self):
        assert _ctx().http.post("/api/agents/ghost/reset-token").status_code == 404

    def test_cross_org_reset_is_404(self):
        _ctx().http.post("/api/agents", json={"instance_id": "default-w", "role": "codex"})
        _as(ORG_ADMIN)
        assert _ctx().http.post("/api/agents/default-w/reset-token").status_code == 404


class TestEditDelete:
    def test_patch_role_status_scopes(self):
        _ctx().http.post("/api/agents", json={"instance_id": "ed", "role": "codex"})
        resp = _ctx().http.patch("/api/agents/ed", json={
            "role": "claude", "status": "disabled", "scopes": ["jobs:read"]})
        assert resp.status_code == 200
        agent = resp.json()["agent"]
        assert agent["role"] == "claude"
        assert agent["status"] == "disabled"
        assert agent["scopes"] == ["jobs:read"]

    def test_patch_empty_scopes_restores_role_defaults(self):
        _ctx().http.post("/api/agents", json={
            "instance_id": "ed2", "role": "codex", "scopes": ["jobs:read"]})
        resp = _ctx().http.patch("/api/agents/ed2", json={"scopes": []})
        assert resp.status_code == 200
        assert resp.json()["agent"]["scopes"] is None

    def test_patch_nothing_400(self):
        _ctx().http.post("/api/agents", json={"instance_id": "ed3", "role": "codex"})
        assert _ctx().http.patch("/api/agents/ed3", json={}).status_code == 400

    def test_patch_org_moves_agent(self):
        _ctx().cfg.values["MCO_ORGS"] = "acme"
        _ctx().http.post("/api/agents", json={"instance_id": "mover", "role": "codex"})
        resp = _ctx().http.patch("/api/agents/mover", json={"org": "acme"})
        assert resp.status_code == 200
        assert resp.json()["agent"]["org_id"] == "acme"

    def test_patch_org_unknown_is_400(self):
        _ctx().http.post("/api/agents", json={"instance_id": "mover2", "role": "codex"})
        assert _ctx().http.patch("/api/agents/mover2", json={"org": "nope"}).status_code == 400

    def test_patch_org_by_org_admin_is_403(self):
        _ctx().cfg.values["MCO_ORGS"] = "acme"
        _as(ORG_ADMIN)
        _ctx().http.post("/api/agents", json={"instance_id": "acme-w", "role": "codex"})
        assert _ctx().http.patch("/api/agents/acme-w", json={"org": "default"}).status_code == 403

    def test_delete_kills_the_token(self):
        body = _ctx().http.post("/api/agents", json={"instance_id": "del", "role": "codex"}).json()
        assert _ctx().http.delete("/api/agents/del").status_code == 200
        assert verify_token(_ctx().db, body["token"]) is None
        assert _ctx().http.post("/api/agents/del/reset-token").status_code == 404


# ── Settings ─────────────────────────────────────────────────────────────────

class TestSettings:
    def test_get_returns_groups_edition_and_scopes(self):
        resp = _ctx().http.get("/api/settings")
        assert resp.status_code == 200
        body = resp.json()
        assert set(body["groups"]) == {"governance", "memory", "presence", "tenancy",
                                       "observability", "edition", "security", "notifications",
                                       "connectors"}
        assert body["edition"]["edition"] == "community"
        assert "jobs:approve" in body["known_scopes"]

    def test_secret_values_never_echoed(self):
        _ctx().cfg.values["MCO_WEBHOOK_SECRET"] = "super-secret"
        resp = _ctx().http.get("/api/settings")
        security = {i["key"]: i for i in resp.json()["groups"]["security"]}
        assert security["MCO_WEBHOOK_SECRET"]["value"] is True   # set, but masked
        assert "super-secret" not in resp.text

    def test_put_whitelisted_bool_coerces(self):
        resp = _ctx().http.put("/api/settings", json={"MCO_KILL_SWITCH": True})
        assert resp.status_code == 200
        assert _ctx().cfg.values["MCO_KILL_SWITCH"] == "true"

    def test_put_secret_requires_encrypted_storage(self):
        resp = _ctx().http.put(
            "/api/settings",
            json={"MCO_TRUSTED_HEADER_SECRET": "proxy-secret"},
        )
        assert resp.status_code == 200
        assert _ctx().cfg.set_calls[-1] == (
            "MCO_TRUSTED_HEADER_SECRET",
            "proxy-secret",
            True,
        )

    def test_locked_secret_store_fails_closed(self):
        def locked_set(key, value, encrypt=False):
            if encrypt:
                raise RuntimeError("locked")
            _ctx().cfg.values[key] = value

        _ctx().cfg.set = locked_set
        resp = _ctx().http.put(
            "/api/settings",
            json={"MCO_WEBHOOK_SECRET": "must-not-be-plaintext"},
        )

        assert resp.status_code == 503
        assert "MCO_WEBHOOK_SECRET" not in _ctx().cfg.values

    def test_put_unknown_key_rejected(self):
        resp = _ctx().http.put("/api/settings", json={"SUPABASE_KEY": "sneaky"})
        assert resp.status_code == 400
        assert "SUPABASE_KEY" in resp.json()["detail"]
        assert "SUPABASE_KEY" not in _ctx().cfg.values

    def test_put_invalid_choice_rejected(self):
        resp = _ctx().http.put("/api/settings", json={"MCO_EDITION": "galactic"})
        assert resp.status_code == 400

    def test_put_empty_clears_key(self):
        _ctx().cfg.values["NTFY_TOPIC"] = "mytopic"
        resp = _ctx().http.put("/api/settings", json={"NTFY_TOPIC": ""})
        assert resp.status_code == 200
        assert "NTFY_TOPIC" in _ctx().cfg.deleted

    def test_worker_token_gets_403(self):
        _as(WORKER)
        assert _ctx().http.get("/api/settings").status_code == 403
        assert _ctx().http.put("/api/settings", json={"MCO_KILL_SWITCH": True}).status_code == 403

    def test_connectors_group_exposes_credential_fields(self):
        conn = {i["key"]: i for i in _ctx().http.get("/api/settings").json()["groups"]["connectors"]}
        assert conn["SERVICENOW_INSTANCE_URL"]["type"] == "text"
        assert conn["SERVICENOW_PASSWORD"]["type"] == "secret"
        assert conn["DYNATRACE_API_TOKEN"]["type"] == "secret"

    def test_put_connector_key_resets_registry(self, monkeypatch):
        import mco.orchestrator.admin_routes as admin
        called = {"n": 0}
        monkeypatch.setattr(admin, "reset_connectors", lambda: called.__setitem__("n", called["n"] + 1),
                            raising=False)
        # patch the import target so `from mco.connectors import reset_connectors` resolves to our spy
        monkeypatch.setattr("mco.connectors.reset_connectors",
                            lambda: called.__setitem__("n", called["n"] + 1))
        resp = _ctx().http.put("/api/settings", json={"SERVICENOW_INSTANCE_URL": "https://dev1.service-now.com"})
        assert resp.status_code == 200
        assert _ctx().cfg.values["SERVICENOW_INSTANCE_URL"] == "https://dev1.service-now.com"
        assert called["n"] >= 1   # registry invalidated so the next test reflects new creds

    def test_test_connector_validates_name(self):
        resp = _ctx().http.post("/api/settings/test-connector", json={"name": "splunk"})
        assert resp.status_code == 400

    def test_test_connector_reports_unconfigured(self, monkeypatch):
        monkeypatch.setattr("mco.connectors.build_connectors", lambda force=False: [])
        monkeypatch.setattr("mco.connectors.reset_connectors", lambda: None)
        monkeypatch.setattr("mco.connectors.get_connector", lambda name: None)
        resp = _ctx().http.post("/api/settings/test-connector", json={"name": "dynatrace"})
        assert resp.status_code == 200
        body = resp.json()
        assert body["ok"] is False
        assert "configured" in body["detail"]

    def test_test_connector_returns_health(self, monkeypatch):
        class _Conn:
            def health(self):
                return {"ok": True, "detail": "connected to https://dev1.service-now.com"}
        monkeypatch.setattr("mco.connectors.build_connectors", lambda force=False: [_Conn()])
        monkeypatch.setattr("mco.connectors.reset_connectors", lambda: None)
        monkeypatch.setattr("mco.connectors.get_connector", lambda name: _Conn())
        resp = _ctx().http.post("/api/settings/test-connector", json={"name": "servicenow"})
        assert resp.status_code == 200
        body = resp.json()
        assert body["ok"] is True
        assert "connected" in body["detail"]


# ── Presence / health checks ─────────────────────────────────────────────────

class TestPresence:
    def test_decorate_marks_stale_online_agent_offline(self):
        row = {"status": "online", "last_seen_at": "2026-01-01T00:00:00Z"}
        out = decorate_presence(row, threshold=300)
        assert out["effective_status"] == "offline"
        assert out["last_seen_seconds"] > 300

    def test_decorate_keeps_fresh_agent_online(self):
        from datetime import datetime, timezone
        row = {"status": "online",
               "last_seen_at": datetime.now(timezone.utc).isoformat()}
        out = decorate_presence(row, threshold=300)
        assert out["effective_status"] == "online"
        assert out["last_seen_seconds"] <= 5

    def test_decorate_never_seen_keeps_stored_status(self):
        out = decorate_presence({"status": "online"}, threshold=300)
        assert out["effective_status"] == "online"   # no heartbeat data: don't guess
        assert out["last_seen_seconds"] is None
        assert decorate_presence({"status": "offline"}, 300)["effective_status"] == "offline"

    def test_response_status_is_the_derived_truth_not_the_stale_value(self):
        """The "board says online but the agent died days ago" bug.

        A consumer reading `status` (the MCP mco_agents tool, the console, an
        ad-hoc client) must NOT get a stale 'online'. The response `status` is
        collapsed onto the derived value.
        """
        row = decorate_presence(
            {"status": "online", "last_seen_at": "2026-01-01T00:00:00Z"}, threshold=300
        )
        assert row["status"] == "offline"          # the field consumers read
        assert row["effective_status"] == "offline"

    def test_fresh_agent_reads_online_on_both_fields(self):
        from datetime import datetime, timezone
        row = decorate_presence(
            {"status": "online", "last_seen_at": datetime.now(timezone.utc).isoformat()},
            threshold=300,
        )
        assert row["status"] == "online"

    def test_disabled_state_is_not_clobbered(self):
        # Only stale 'online' is demoted; 'disabled' must survive.
        row = decorate_presence(
            {"status": "disabled", "last_seen_at": "2026-01-01T00:00:00Z"}, threshold=300
        )
        assert row["status"] == "disabled"
        assert row["effective_status"] == "disabled"

    def test_stored_row_is_not_mutated_only_the_response_copy(self):
        # The endpoint hands decorate_presence a per-read dict copy; the fix
        # must not depend on that being the store, and must not flip the DB.
        original = {"status": "online", "last_seen_at": "2026-01-01T00:00:00Z"}
        copy = dict(original)
        decorate_presence(copy, threshold=300)
        assert original["status"] == "online"      # untouched
        assert copy["status"] == "offline"         # derived on the copy

    def test_polling_is_the_heartbeat(self):
        """GET /api/jobs/pending stamps last_seen_at and flips the poller online."""
        _ctx().db.add_agent("w1", "codex", "tok-w1", status="offline")
        _as({"instance_id": "w1", "role": "codex", "status": "offline", "org_id": "default"})
        resp = _ctx().http.get("/api/jobs/pending", params={"role": "codex", "instance_id": "w1"})
        assert resp.status_code == 200
        row = _ctx().db._agents[0]
        assert row["status"] == "online"
        assert row["last_seen_at"] != "2026-01-01T00:00:00Z"  # stamped fresh

    def test_agents_endpoint_returns_derived_presence(self):
        _ctx().db.add_agent("stale", "codex", "tok-s", status="online")  # last seen 2026-01-01
        resp = _ctx().http.get("/api/agents")
        assert resp.status_code == 200
        agent = resp.json()[0]
        # The response status IS the derived truth now: a client reading the
        # plain `status` field can no longer be told a dead agent is online.
        # (The DB row is unchanged; only this read-time response reflects it.)
        assert agent["status"] == "offline"
        assert agent["effective_status"] == "offline"   # derived: silent too long
        assert agent["last_seen_seconds"] > 300

    def test_host_operator_sees_all_orgs_org_admin_sees_own(self):
        _ctx().db.add_agent("default-w", "codex", "t1")
        _ctx().db._agents.append({"instance_id": "acme-w", "role": "codex",
                                  "status": "online", "org_id": "acme",
                                  "auth_token_hash": "x"})
        # Host operator (default org): everything, with org visible.
        names = {a["instance_id"] for a in _ctx().http.get("/api/agents").json()}
        assert names == {"default-w", "acme-w"}
        # Org admin: own fleet only.
        _as(ORG_ADMIN)
        names = {a["instance_id"] for a in _ctx().http.get("/api/agents").json()}
        assert names == {"acme-w"}


# ── Workflows ────────────────────────────────────────────────────────────────

WF_YAML = """
name: panel-test
steps:
  - id: a
    role: claude
    title: First
    instructions: do a
  - id: b
    role: codex
    title: Second
    instructions: do b
    depends_on: [a]
    requires_approval: true
"""


class TestWorkflowSubmit:
    def test_yaml_becomes_governed_jobs_with_run_stamp(self):
        resp = _ctx().http.post("/api/workflows", json={"yaml": WF_YAML})
        assert resp.status_code == 200
        body = resp.json()
        assert set(body["jobs"]) == {"a", "b"}
        jobs = _ctx().db._jobs
        job_a = jobs[body["jobs"]["a"]]
        job_b = jobs[body["jobs"]["b"]]
        assert job_b["depends_on"] == [body["jobs"]["a"]]
        assert job_b["status"] == "waiting"                     # gated behind a
        stamp = job_a["input_payload"]["workflow"]
        assert stamp["name"] == "panel-test" and stamp["run"] == body["run"]

    def test_invalid_yaml_400(self):
        resp = _ctx().http.post("/api/workflows", json={"yaml": "name: x\nsteps: []"})
        assert resp.status_code == 400

    def test_worker_may_submit(self):
        _as(WORKER)  # jobs:write is a worker default scope
        assert _ctx().http.post("/api/workflows", json={"yaml": WF_YAML}).status_code == 200


class TestDemoPipeline:
    def test_demo_pipeline_seeds_three_job_dag(self):
        resp = _ctx().http.post("/api/workflows/demo-pipeline")
        assert resp.status_code == 200
        body = resp.json()
        assert body["workflow"] == "jde-demo-live-pipeline"
        assert set(body["jobs"]) == {"plan", "build", "review"}

        plan = _ctx().db._jobs[body["jobs"]["plan"]]
        build = _ctx().db._jobs[body["jobs"]["build"]]
        review = _ctx().db._jobs[body["jobs"]["review"]]
        assert plan["target_agent_role"] == "claude"
        assert build["target_agent_role"] == "codex"
        assert review["target_agent_role"] == "reviewer"
        assert build["depends_on"] == [plan["id"]]
        assert review["depends_on"] == [build["id"]]
        assert plan["input_payload"]["workflow"]["run"] == body["run"]
        assert len(_ctx().db._events) == 3


class TestGovernanceEvidencePack:
    def test_evidence_pack_returns_pdf_cover_and_audit_json(self):
        pending = _ctx().http.post("/api/jobs", json={
            "title": "Release approval",
            "target_agent_role": "codex",
            "requires_approval": True,
        }).json()["job"]
        decided = _ctx().http.post("/api/jobs", json={
            "title": "Firewall change",
            "target_agent_role": "codex",
            "requires_approval": True,
        }).json()["job"]
        assert _ctx().http.post(f"/api/jobs/{decided['id']}/approve").status_code == 200

        resp = _ctx().http.post("/api/governance/evidence-pack", json={
            "start_date": "2026-01-01",
            "end_date": "2026-12-31",
        })
        assert resp.status_code == 200
        body = resp.json()
        assert body["summary"]["pending_approvals"] == 1
        assert body["summary"]["decisions"] == 1

        files = {f["filename"]: f for f in body["files"]}
        assert base64.b64decode(files["cover.pdf"]["base64"]).startswith(b"%PDF-1.4")
        audit = json.loads(files["audit-trail.json"]["text"])
        assert audit["regulatory_basis"]["eu_ai_act_article_12"].startswith("Record-keeping")
        assert audit["regulatory_basis"]["eu_ai_act_article_14"].startswith("Human oversight")
        assert audit["pending_approvals"][0]["id"] == pending["id"]

    def test_evidence_pack_date_only_end_includes_selected_day(self):
        job = _ctx().http.post("/api/jobs", json={
            "title": "Year end approval",
            "target_agent_role": "codex",
        }).json()["job"]
        for event in _ctx().db._events:
            if event.get("job_id") == job["id"]:
                event["created_at"] = "2026-12-31T15:30:00Z"

        resp = _ctx().http.post("/api/governance/evidence-pack", json={
            "start_date": "2026-12-31",
            "end_date": "2026-12-31",
        })
        assert resp.status_code == 200
        files = {f["filename"]: f for f in resp.json()["files"]}
        audit = json.loads(files["audit-trail.json"]["text"])

        assert audit["summary"]["audit_events"] == 1
        assert audit["audit_events"][0]["job_id"] == job["id"]
