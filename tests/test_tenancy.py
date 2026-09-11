"""Multi-tenancy isolation and platform-guardrail tests."""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import mco.notifiers.ntfy as ntfy_mod
import mco.orchestrator.routes as routes_mod
from mco.orchestrator.auth import require_agent
from mco.orchestrator.context_routes import context_router
from mco.orchestrator.routes import router as jobs_router, agents_router

from tests.test_routes import FakeDB

ACME = {"instance_id": "acme-1", "role": "codex", "status": "online", "org_id": "acme"}
GLOBEX = {"instance_id": "globex-1", "role": "codex", "status": "online", "org_id": "globex"}
ACME_HUMAN = {"instance_id": "acme-joe", "role": "human", "status": "online", "org_id": "acme"}


class _TenancyBase:
    @pytest.fixture(autouse=True)
    def setup(self, monkeypatch):
        monkeypatch.setattr(ntfy_mod, "notify", lambda *a, **k: True)
        self.db = FakeDB()
        monkeypatch.setattr(routes_mod, "get_db_client", lambda: self.db)
        app = FastAPI()
        app.include_router(jobs_router)
        app.include_router(agents_router)
        app.include_router(context_router)
        self.app = app
        self.http = TestClient(app)
        self._as(ACME)

    def _as(self, agent):
        self.app.dependency_overrides[require_agent] = lambda: agent


class TestOrgIsolation(_TenancyBase):
    def test_jobs_list_only_shows_own_org(self):
        self.db.add_job(id="a1", title="acme job", status="pending",
                        target_agent_role="codex", org_id="acme")
        self.db.add_job(id="g1", title="globex job", status="pending",
                        target_agent_role="codex", org_id="globex")
        ids = [j["id"] for j in self.http.get("/api/jobs").json()]
        assert ids == ["a1"]

    def test_pending_excludes_other_org(self):
        self.db.add_job(id="g2", status="pending", target_agent_role="codex", org_id="globex")
        resp = self.http.get("/api/jobs/pending", params={"role": "codex", "instance_id": "acme-1"})
        assert resp.json() == []

    def test_created_job_is_stamped_with_caller_org(self):
        resp = self.http.post("/api/jobs", json={"title": "x", "target_agent_role": "claude"})
        assert resp.json()["job"]["org_id"] == "acme"

    def test_cannot_lease_other_orgs_job(self):
        self.db.add_job(id="g3", status="pending", target_agent_role="codex", org_id="globex")
        resp = self.http.post("/api/jobs/lease", json={"task_id": "g3", "agent_instance_id": "acme-1"})
        assert resp.status_code == 404

    def test_cannot_update_other_orgs_job(self):
        self.db.add_job(id="g4", status="in_progress", target_agent_role="codex", org_id="globex")
        resp = self.http.put("/api/jobs/g4", json={"status": "completed"})
        assert resp.status_code == 404

    def test_cannot_approve_other_orgs_job(self):
        self.db.add_job(id="g5", status="needs_approval", target_agent_role="codex", org_id="globex")
        self._as(ACME_HUMAN)
        assert self.http.post("/api/jobs/g5/approve").status_code == 404

    def test_cannot_retry_other_orgs_job(self):
        self.db.add_job(id="g6", status="failed", target_agent_role="codex", org_id="globex")
        self._as(ACME_HUMAN)
        assert self.http.post("/api/jobs/g6/retry").status_code == 404

    def test_audit_trail_hidden_across_orgs(self):
        self.db.add_job(id="g7", status="pending", target_agent_role="codex", org_id="globex")
        self.db._events.append({"id": 1, "job_id": "g7", "event": "created",
                                "created_at": "2026-01-01T00:00:00Z"})
        assert self.http.get("/api/jobs/g7/events").json() == []

    def test_agents_list_only_shows_own_org(self):
        self.db.add_agent("acme-2", "claude", "t1")
        self.db._agents[-1]["org_id"] = "acme"
        self.db.add_agent("globex-2", "claude", "t2")
        self.db._agents[-1]["org_id"] = "globex"
        names = [a["instance_id"] for a in self.http.get("/api/agents").json()]
        assert "acme-2" in names and "globex-2" not in names

    def test_drumline_memory_isolated_per_org(self):
        self.http.post("/api/context", json={"title": "acme secret sauce", "content": "recipe"})
        self._as(GLOBEX)
        hits = self.http.get("/api/context", params={"query": "acme secret sauce"}).json()
        assert hits == []
        self._as(ACME)
        hits = self.http.get("/api/context", params={"query": "acme secret sauce"}).json()
        assert len(hits) == 1

    def test_default_org_jobs_visible_to_default_agents(self):
        """Single-tenant installs (no org_id anywhere) behave exactly as before."""
        self._as({"instance_id": "agent-1", "role": "codex", "status": "online"})
        self.db.add_job(id="d1", status="pending", target_agent_role="codex")
        ids = [j["id"] for j in self.http.get("/api/jobs").json()]
        assert ids == ["d1"]


class TestGuardrails(_TenancyBase):
    def test_gated_role_forces_approval(self, monkeypatch):
        monkeypatch.setattr(routes_mod, "get_gated_roles", lambda: {"servicenow"})
        resp = self.http.post("/api/jobs", json={"title": "write to prod ITSM",
                                                 "target_agent_role": "servicenow"})
        assert resp.json()["job"]["status"] == "needs_approval"

    def test_non_gated_role_unaffected(self, monkeypatch):
        monkeypatch.setattr(routes_mod, "get_gated_roles", lambda: {"servicenow"})
        resp = self.http.post("/api/jobs", json={"title": "x", "target_agent_role": "claude"})
        assert resp.json()["job"]["status"] == "pending"

    def test_kill_switch_blocks_create_and_lease(self, monkeypatch):
        monkeypatch.setattr(routes_mod, "kill_switch_active", lambda: True)
        assert self.http.post("/api/jobs", json={"title": "x", "target_agent_role": "claude"}).status_code == 503
        assert self.http.post("/api/jobs/lease",
                              json={"task_id": "j1", "agent_instance_id": "acme-1"}).status_code == 503

    def test_unowned_result_is_fenced_during_kill_switch(self, monkeypatch):
        monkeypatch.setattr(routes_mod, "kill_switch_active", lambda: True)
        self.db.add_job(id="k1", status="in_progress", target_agent_role="codex", org_id="acme")
        resp = self.http.put("/api/jobs/k1", json={"status": "completed"})
        assert resp.status_code == 409


class TestHealthz:
    def test_healthz_is_public_and_reports_state(self, monkeypatch):
        from mco.cli import create_app
        monkeypatch.setattr(routes_mod, "get_db_client", lambda: None)
        http = TestClient(create_app())
        resp = http.get("/healthz")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "ok"
        assert body == {"status": "ok"}
