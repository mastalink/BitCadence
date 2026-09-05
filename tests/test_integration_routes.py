"""Integration route tests: auth, webhook ingestion, sync, action gating,
and the ITSM escalation bridge."""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import mco.connectors.base as base_mod
import mco.editions as editions_mod
import mco.notifiers.ntfy as ntfy_mod
import mco.orchestrator.integration_routes as integ_mod
import mco.orchestrator.routes as routes_mod
from mco.connectors import register_connector, reset_connectors
from mco.connectors.base import BaseConnector
from mco.orchestrator.auth import require_agent
from mco.orchestrator.integration_routes import integrations_router
from mco.orchestrator.routes import router as jobs_router

from tests.test_routes import FakeDB

CODEX_AGENT = {"instance_id": "agent-1", "role": "codex", "status": "online"}
HUMAN_AGENT = {"instance_id": "joe", "role": "human", "status": "online"}


class StubConnector(BaseConnector):
    name = "stub"

    def __init__(self):
        self.actions_run = []
        self.escalations = []

    def health(self):
        return {"ok": True, "detail": "stub"}

    def pull_events(self):
        return [{
            "external_id": "stub:1", "title": "Stub incident", "description": "d",
            "target_agent_role": "claude",
            "input_payload": {"external_id": "stub:1", "connector": "stub", "prompt": "p"},
        }]

    def actions(self):
        return ["ping"]

    def execute_action(self, action, params):
        self.actions_run.append((action, params))
        return {"pong": True}

    def escalate(self, job, error):
        self.escalations.append((job.get("id"), error))
        return {"ticket": "TICK-1"}


class FakeConfig:
    def __init__(self, **values):
        self._values = values

    def get(self, key, default=None):
        return self._values.get(key, default)


@pytest.fixture(autouse=True)
def setup(monkeypatch):
    reset_connectors()
    # Mark the registry as built so build_connectors() doesn't hit real config.
    monkeypatch.setattr(base_mod, "_built", True)
    self_db = FakeDB()
    self_stub = StubConnector()
    register_connector(self_stub)
    monkeypatch.setattr(routes_mod, "get_db_client", lambda: self_db)
    monkeypatch.setattr(ntfy_mod, "notify", lambda *a, **k: True)
    # Connectors are an enterprise surface; pin the edition so these tests
    # exercise the connector logic, not the edition gate (gate tests live in
    # tests/test_rbac.py).
    monkeypatch.setattr(editions_mod, "current_edition", lambda: "enterprise")

    app = FastAPI()
    app.include_router(jobs_router)
    app.include_router(integrations_router)
    app.dependency_overrides[require_agent] = lambda: CODEX_AGENT

    self = type("Ctx", (), {})
    pytest.ctx = self
    self.db = self_db
    self.stub = self_stub
    self.app = app
    self.http = TestClient(app)
    yield
    reset_connectors()


def _ctx():
    return pytest.ctx


class TestIntegrationListing:
    def test_lists_connectors_with_health(self):
        resp = _ctx().http.get("/api/integrations")
        assert resp.status_code == 200
        rows = resp.json()
        assert rows[0]["name"] == "stub"
        assert rows[0]["health"]["ok"] is True
        assert rows[0]["actions"] == ["ping"]


class TestSyncRoute:
    def test_sync_creates_jobs(self):
        resp = _ctx().http.post("/api/integrations/stub/sync")
        assert resp.status_code == 200
        assert len(resp.json()["created"]) == 1
        assert len(_ctx().db._jobs) == 1

    def test_sync_unknown_connector_404(self):
        resp = _ctx().http.post("/api/integrations/nope/sync")
        assert resp.status_code == 404


class TestActionRoute:
    def test_non_approver_403(self):
        resp = _ctx().http.post("/api/integrations/stub/action", json={"action": "ping"})
        assert resp.status_code == 403
        assert _ctx().stub.actions_run == []

    def test_approver_runs_action(self):
        _ctx().app.dependency_overrides[require_agent] = lambda: HUMAN_AGENT
        resp = _ctx().http.post("/api/integrations/stub/action",
                                json={"action": "ping", "params": {"a": 1}})
        assert resp.status_code == 200
        assert resp.json()["result"] == {"pong": True}
        assert _ctx().stub.actions_run == [("ping", {"a": 1})]

    def test_missing_action_400(self):
        _ctx().app.dependency_overrides[require_agent] = lambda: HUMAN_AGENT
        resp = _ctx().http.post("/api/integrations/stub/action", json={})
        assert resp.status_code == 400


class TestWebhookRoute:
    def _enable_secret(self, monkeypatch, secret="hook-secret"):
        monkeypatch.setattr(integ_mod, "get_config", lambda: FakeConfig(MCO_WEBHOOK_SECRET=secret))

    def test_disabled_without_secret_config(self, monkeypatch):
        monkeypatch.setattr(integ_mod, "get_config", lambda: FakeConfig())
        resp = _ctx().http.post("/api/integrations/generic/webhook", json={"title": "x"})
        assert resp.status_code == 403

    def test_wrong_secret_401(self, monkeypatch):
        self._enable_secret(monkeypatch)
        resp = _ctx().http.post("/api/integrations/generic/webhook", json={"title": "x"},
                                headers={"X-MCO-Webhook-Secret": "wrong"})
        assert resp.status_code == 401

    def test_valid_webhook_creates_job(self, monkeypatch):
        self._enable_secret(monkeypatch)
        resp = _ctx().http.post(
            "/api/integrations/servicenow/webhook",
            json={"sys_id": "w1", "number": "INC0007", "short_description": "Mail down"},
            headers={"X-MCO-Webhook-Secret": "hook-secret"},
        )
        assert resp.status_code == 200
        assert len(resp.json()["created"]) == 1
        job = list(_ctx().db._jobs.values())[0]
        assert job["source_agent_id"] == "webhook:servicenow"
        assert "INC0007" in job["title"]

    def test_webhook_is_idempotent(self, monkeypatch):
        self._enable_secret(monkeypatch)
        body = {"sys_id": "w2", "short_description": "Same event"}
        for _ in range(2):
            resp = _ctx().http.post("/api/integrations/servicenow/webhook", json=body,
                                    headers={"X-MCO-Webhook-Secret": "hook-secret"})
            assert resp.status_code == 200
        assert len(_ctx().db._jobs) == 1

    def test_bad_payload_400(self, monkeypatch):
        self._enable_secret(monkeypatch)
        resp = _ctx().http.post("/api/integrations/generic/webhook", json={"nope": 1},
                                headers={"X-MCO-Webhook-Secret": "hook-secret"})
        assert resp.status_code == 400


class TestEscalationBridge:
    def test_exhausted_job_opens_external_ticket(self, monkeypatch):
        import mco.config as config_mod
        import mco.connectors as connectors_pkg

        monkeypatch.setattr(config_mod, "get_config",
                            lambda *a, **k: FakeConfig(MCO_ESCALATION_CONNECTOR="stub"))
        monkeypatch.setattr(connectors_pkg, "get_connector",
                            lambda name: _ctx().stub if name == "stub" else None)

        _ctx().db.add_job(id="es1", title="Broken job", status="in_progress", leased_by_instance_id="agent-1",
                          target_agent_role="codex", max_retries=0,
                          escalate_to_role="human")
        resp = _ctx().http.put("/api/jobs/es1", json={"status": "failed", "error_message": "kaput"})
        assert resp.status_code == 200
        assert _ctx().stub.escalations == [("es1", "kaput")]
        events = [e["event"] for e in _ctx().db._events if e["job_id"] == "es1"]
        assert "escalated_external" in events
        assert "escalated" in events  # internal escalation job still created

    def test_bridge_failure_does_not_break_update(self, monkeypatch):
        import mco.config as config_mod
        import mco.connectors as connectors_pkg

        class ExplodingConnector(StubConnector):
            def escalate(self, job, error):
                raise RuntimeError("platform down")

        monkeypatch.setattr(config_mod, "get_config",
                            lambda *a, **k: FakeConfig(MCO_ESCALATION_CONNECTOR="stub"))
        monkeypatch.setattr(connectors_pkg, "get_connector", lambda name: ExplodingConnector())

        _ctx().db.add_job(id="es2", title="Broken", status="in_progress", leased_by_instance_id="agent-1",
                          target_agent_role="codex")
        resp = _ctx().http.put("/api/jobs/es2", json={"status": "failed", "error_message": "x"})
        assert resp.status_code == 200
        assert _ctx().db._jobs["es2"]["status"] == "failed"
