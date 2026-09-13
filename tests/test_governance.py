"""Phase A governance tests: approval gates, immutable audit trail, escalation."""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import mco.notifiers.ntfy as ntfy_mod
import mco.orchestrator.routes as routes_mod
import mco.orchestrator.utils as utils_mod
from mco.orchestrator.auth import require_agent
from mco.orchestrator.routes import router, agents_router

from tests.test_routes import FakeDB


CODEX_AGENT = {"instance_id": "agent-1", "role": "codex", "status": "online"}
HUMAN_AGENT = {"instance_id": "joe", "role": "human", "status": "online"}


def _build_app() -> FastAPI:
    app = FastAPI()
    app.include_router(router)
    app.include_router(agents_router)
    return app


@pytest.fixture(autouse=True)
def _no_outbound_ntfy(monkeypatch):
    """Keep tests offline: ntfy pushes become no-ops."""
    monkeypatch.setattr(ntfy_mod, "notify", lambda *a, **k: True)


class _GovernanceBase:
    agent = CODEX_AGENT

    @pytest.fixture(autouse=True)
    def setup(self, monkeypatch):
        self.db = FakeDB()
        monkeypatch.setattr(routes_mod, "get_db_client", lambda: self.db)
        self.app = _build_app()
        self.app.dependency_overrides[require_agent] = lambda: self.agent
        self.http = TestClient(self.app)

    def _as(self, agent):
        self.app.dependency_overrides[require_agent] = lambda: agent


class TestApprovalGates(_GovernanceBase):
    def test_create_with_requires_approval_pauses_at_gate(self):
        resp = self.http.post("/api/jobs", json={
            "title": "Risky deploy",
            "target_agent_role": "codex",
            "requires_approval": True,
        })
        assert resp.status_code == 200
        assert resp.json()["job"]["status"] == "needs_approval"

    def test_create_with_deps_and_approval_starts_waiting(self):
        self.db.add_job(id="dep-1", status="pending", target_agent_role="codex")
        resp = self.http.post("/api/jobs", json={
            "title": "Gated downstream",
            "target_agent_role": "codex",
            "requires_approval": True,
            "depends_on": ["dep-1"],
        })
        assert resp.json()["job"]["status"] == "waiting"

    def _gated_job(self) -> str:
        return self.db.add_job(
            id="gated-1", title="Gated", status="needs_approval",
            target_agent_role="codex", requires_approval=True,
        )

    def test_approver_role_can_approve(self):
        job_id = self._gated_job()
        self._as(HUMAN_AGENT)
        resp = self.http.post(f"/api/jobs/{job_id}/approve")
        assert resp.status_code == 200
        job = resp.json()["job"]
        assert job["status"] == "pending"
        assert job["approved_by"] == "joe"

    def test_non_approver_role_gets_403(self):
        job_id = self._gated_job()
        resp = self.http.post(f"/api/jobs/{job_id}/approve")
        assert resp.status_code == 403

    def test_reject_is_terminal_with_reason(self):
        job_id = self._gated_job()
        self._as(HUMAN_AGENT)
        resp = self.http.post(f"/api/jobs/{job_id}/reject", json={"reason": "too risky"})
        assert resp.status_code == 200
        job = resp.json()["job"]
        assert job["status"] == "rejected"
        assert "too risky" in job["error_message"]

    def test_approve_non_gated_job_is_400(self):
        self.db.add_job(id="plain-1", status="pending", target_agent_role="codex")
        self._as(HUMAN_AGENT)
        resp = self.http.post("/api/jobs/plain-1/approve")
        assert resp.status_code == 400

    def test_approve_unknown_job_is_404(self):
        self._as(HUMAN_AGENT)
        resp = self.http.post("/api/jobs/no-such-job/approve")
        assert resp.status_code == 404

    def test_custom_approver_roles_config(self, monkeypatch):
        monkeypatch.setattr(utils_mod, "get_approver_roles", lambda: {"codex"})
        job_id = self._gated_job()
        resp = self.http.post(f"/api/jobs/{job_id}/approve")
        assert resp.status_code == 200


class TestAuditTrail(_GovernanceBase):
    def _events(self, job_id):
        resp = self.http.get(f"/api/jobs/{job_id}/events")
        assert resp.status_code == 200
        return [e["event"] for e in resp.json()]

    def test_create_is_audited(self):
        resp = self.http.post("/api/jobs", json={"title": "x", "target_agent_role": "codex"})
        job_id = resp.json()["job"]["id"]
        assert "created" in self._events(job_id)

    def test_lease_is_audited(self):
        self.db.add_job(id="jl1", status="pending", target_agent_role="codex")
        self.db.set_rpc(True)
        self.http.post("/api/jobs/lease", json={"task_id": "jl1", "agent_instance_id": "agent-1"})
        assert "leased" in self._events("jl1")

    def test_status_change_is_audited_with_actor(self):
        self.db.add_job(id="ju1", status="in_progress", leased_by_instance_id="agent-1", target_agent_role="codex")
        self.http.put("/api/jobs/ju1", json={"status": "completed"})
        events = self.http.get("/api/jobs/ju1/events").json()
        assert any(e["event"] == "status:completed" and e["actor_id"] == "agent-1" for e in events)

    def test_approval_decision_is_audited(self):
        self.db.add_job(id="ga1", status="needs_approval", target_agent_role="codex")
        self._as(HUMAN_AGENT)
        self.http.post("/api/jobs/ga1/approve")
        assert "approved" in self._events("ga1")


class TestManualRetry(_GovernanceBase):
    def test_approver_requeues_failed_job(self):
        self.db.add_job(id="mr1", status="failed", target_agent_role="codex",
                        leased_by_instance_id="agent-1", error_message="boom")
        self._as(HUMAN_AGENT)
        resp = self.http.post("/api/jobs/mr1/retry")
        assert resp.status_code == 200
        job = resp.json()["job"]
        assert job["status"] == "pending"
        assert job["leased_by_instance_id"] is None
        events = self.http.get("/api/jobs/mr1/events").json()
        assert any(e["event"] == "retried" and e["actor_id"] == "joe" for e in events)

    def test_rejected_job_can_be_retried(self):
        self.db.add_job(id="mr2", status="rejected", target_agent_role="codex")
        self._as(HUMAN_AGENT)
        resp = self.http.post("/api/jobs/mr2/retry")
        assert resp.status_code == 200
        assert resp.json()["job"]["status"] == "pending"

    def test_non_approver_403(self):
        self.db.add_job(id="mr3", status="failed", target_agent_role="codex")
        resp = self.http.post("/api/jobs/mr3/retry")
        assert resp.status_code == 403

    def test_non_terminal_job_400(self):
        self.db.add_job(id="mr4", status="in_progress", leased_by_instance_id="agent-1", target_agent_role="codex")
        self._as(HUMAN_AGENT)
        resp = self.http.post("/api/jobs/mr4/retry")
        assert resp.status_code == 400

    def test_unknown_job_404(self):
        self._as(HUMAN_AGENT)
        resp = self.http.post("/api/jobs/missing/retry")
        assert resp.status_code == 404


class TestCancel(_GovernanceBase):
    def test_approver_cancels_pending_job(self):
        self.db.add_job(id="c1", status="pending", target_agent_role="codex")
        self._as(HUMAN_AGENT)
        resp = self.http.post("/api/jobs/c1/cancel", json={"reason": "wrong target"})
        assert resp.status_code == 200
        job = resp.json()["job"]
        assert job["status"] == "cancelled"
        assert "wrong target" in job["error_message"]
        events = self.http.get("/api/jobs/c1/events").json()
        assert any(e["event"] == "cancelled" and e["actor_id"] == "joe" for e in events)

    def test_can_cancel_leased_and_in_progress_jobs(self):
        self.db.add_job(id="c2", status="leased", target_agent_role="codex",
                        leased_by_instance_id="agent-1")
        self._as(HUMAN_AGENT)
        resp = self.http.post("/api/jobs/c2/cancel")
        assert resp.status_code == 200
        assert resp.json()["job"]["leased_by_instance_id"] is None

    def test_non_approver_403(self):
        self.db.add_job(id="c3", status="pending", target_agent_role="codex")
        resp = self.http.post("/api/jobs/c3/cancel")
        assert resp.status_code == 403

    def test_terminal_job_400(self):
        self.db.add_job(id="c4", status="completed", target_agent_role="codex")
        self._as(HUMAN_AGENT)
        resp = self.http.post("/api/jobs/c4/cancel")
        assert resp.status_code == 400

    def test_unknown_job_404(self):
        self._as(HUMAN_AGENT)
        resp = self.http.post("/api/jobs/missing/cancel")
        assert resp.status_code == 404

    def test_state_change_during_cancel_returns_conflict(self, monkeypatch):
        self.db.add_job(id="c5", status="in_progress", leased_by_instance_id="agent-1", target_agent_role="codex")
        self._as(HUMAN_AGENT)
        original_load = routes_mod._load_job_in_org

        def load_then_complete(db_client, job_id, agent):
            snapshot = original_load(db_client, job_id, agent)
            self.db._jobs[job_id]["status"] = "completed"
            return snapshot

        monkeypatch.setattr(routes_mod, "_load_job_in_org", load_then_complete)
        resp = self.http.post("/api/jobs/c5/cancel")

        assert resp.status_code == 409
        assert self.db._jobs["c5"]["status"] == "completed"


class TestArchive(_GovernanceBase):
    def test_archive_terminal_job_hides_it_from_default_list(self):
        self.db.add_job(id="a1", status="completed", target_agent_role="codex")
        resp = self.http.post("/api/jobs/a1/archive")
        assert resp.status_code == 200
        job = resp.json()["job"]
        assert job["archived"] is True
        assert job["archived_by"] == "agent-1"

        listed = self.http.get("/api/jobs").json()
        assert not any(j["id"] == "a1" for j in listed)

        listed_all = self.http.get("/api/jobs?include_archived=true").json()
        assert any(j["id"] == "a1" for j in listed_all)

    def test_cannot_archive_non_terminal_job(self):
        self.db.add_job(id="a2", status="in_progress", leased_by_instance_id="agent-1", target_agent_role="codex")
        resp = self.http.post("/api/jobs/a2/archive")
        assert resp.status_code == 400

    def test_unarchive_restores_default_visibility(self):
        self.db.add_job(id="a3", status="failed", target_agent_role="codex")
        self.http.post("/api/jobs/a3/archive")
        resp = self.http.post("/api/jobs/a3/unarchive")
        assert resp.status_code == 200
        assert resp.json()["job"]["archived"] is False
        listed = self.http.get("/api/jobs").json()
        assert any(j["id"] == "a3" for j in listed)

    def test_archive_is_idempotent(self):
        self.db.add_job(id="a4", status="completed", target_agent_role="codex")
        self.http.post("/api/jobs/a4/archive")
        resp = self.http.post("/api/jobs/a4/archive")
        assert resp.status_code == 200


class TestReassign(_GovernanceBase):
    def test_approver_reassigns_failed_job_to_new_role(self):
        self.db.add_job(id="r1", title="Build the thing", status="failed",
                        target_agent_role="codex", input_payload={"prompt": "do it"})
        self._as(HUMAN_AGENT)
        resp = self.http.post("/api/jobs/r1/reassign", json={"target_agent_role": "claude"})
        assert resp.status_code == 200
        body = resp.json()
        new_job = body["job"]
        old_job = body["superseded_job"]

        assert new_job["status"] == "pending"
        assert new_job["target_agent_role"] == "claude"
        assert new_job["title"] == "Build the thing"
        assert new_job["reassigned_from_job_id"] == "r1"

        assert old_job["archived"] is True
        assert old_job["reassigned_to_job_id"] == new_job["id"]

        events = self.http.get("/api/jobs/r1/events").json()
        assert any(e["event"] == "reassigned" for e in events)

    def test_reassign_with_new_instructions_overrides_prompt(self):
        self.db.add_job(id="r2", title="Old title", status="rejected",
                        target_agent_role="codex", input_payload={"prompt": "old"})
        self._as(HUMAN_AGENT)
        resp = self.http.post("/api/jobs/r2/reassign", json={
            "target_agent_role": "codex", "instructions": "new approach", "title": "New title",
        })
        new_job = resp.json()["job"]
        assert new_job["title"] == "New title"
        assert new_job["input_payload"]["prompt"] == "new approach"

    def test_non_approver_403(self):
        self.db.add_job(id="r3", status="failed", target_agent_role="codex")
        resp = self.http.post("/api/jobs/r3/reassign", json={"target_agent_role": "claude"})
        assert resp.status_code == 403

    def test_cannot_reassign_completed_job(self):
        self.db.add_job(id="r4", status="completed", target_agent_role="codex")
        self._as(HUMAN_AGENT)
        resp = self.http.post("/api/jobs/r4/reassign", json={"target_agent_role": "claude"})
        assert resp.status_code == 400

    def test_empty_payload_reassigns_to_the_same_role(self):
        """A bare reassign (no target given) is a plain retry-elsewhere: it
        reuses the original job's target role rather than failing."""
        self.db.add_job(id="r5", status="failed", target_agent_role="codex")
        self._as(HUMAN_AGENT)
        resp = self.http.post("/api/jobs/r5/reassign", json={})
        assert resp.status_code == 200
        assert resp.json()["job"]["target_agent_role"] == "codex"

    def test_missing_target_role_400_when_source_job_has_none(self):
        self.db.add_job(id="r6", status="failed", target_agent_role=None)
        self._as(HUMAN_AGENT)
        resp = self.http.post("/api/jobs/r6/reassign", json={})
        assert resp.status_code == 400

    def test_reassign_preserves_approval_gate(self):
        self.db.add_job(
            id="r7",
            status="rejected",
            target_agent_role="codex",
            requires_approval=True,
        )
        self._as(HUMAN_AGENT)
        resp = self.http.post("/api/jobs/r7/reassign", json={})

        assert resp.status_code == 200
        new_job = resp.json()["job"]
        assert new_job["requires_approval"] is True
        assert new_job["status"] == "needs_approval"


class TestDuplicates(_GovernanceBase):
    def test_finds_same_title_same_role(self):
        self.db.add_job(id="d1", title="Fix the flaky test", status="failed", target_agent_role="codex")
        self.db.add_job(id="d2", title="Fix the flaky test", status="pending", target_agent_role="codex")
        self.db.add_job(id="d3", title="Fix the flaky test", status="pending", target_agent_role="claude")

        resp = self.http.get("/api/jobs/d1/duplicates")
        assert resp.status_code == 200
        ids = {d["id"] for d in resp.json()}
        assert "d2" in ids
        assert "d3" not in ids  # different target role, not a duplicate

    def test_finds_reassignment_link_even_with_different_title(self):
        self.db.add_job(id="d4", title="Original", status="failed", target_agent_role="codex",
                        reassigned_to_job_id="d5")
        self.db.add_job(id="d5", title="Redone differently", status="pending", target_agent_role="claude",
                        reassigned_from_job_id="d4")
        resp = self.http.get("/api/jobs/d4/duplicates")
        rel = {d["id"]: d["relation"] for d in resp.json()}
        assert rel.get("d5") == "reassignment_link"


class TestEscalation(_GovernanceBase):
    def test_failed_job_with_retry_budget_is_requeued(self):
        self.db.add_job(id="rt1", status="in_progress", target_agent_role="codex",
                        max_retries=2, retry_count=0, leased_by_instance_id="agent-1")
        self.http.put("/api/jobs/rt1", json={"status": "failed", "error_message": "boom"})
        job = self.db._jobs["rt1"]
        assert job["status"] == "pending"
        assert job["retry_count"] == 1
        assert job["leased_by_instance_id"] is None
        events = [e["event"] for e in self.db._events if e["job_id"] == "rt1"]
        assert "retried" in events

    def test_exhausted_retries_escalates_to_role(self):
        self.db.add_job(id="es1", title="Flaky task", status="in_progress", leased_by_instance_id="agent-1",
                        target_agent_role="codex", max_retries=1, retry_count=1,
                        escalate_to_role="human", description="orig instructions")
        self.http.put("/api/jobs/es1", json={"status": "failed", "error_message": "still broken"})
        assert self.db._jobs["es1"]["status"] == "failed"
        escalations = [j for j in self.db._jobs.values()
                       if j.get("target_agent_role") == "human" and "ESCALATION" in (j.get("title") or "")]
        assert len(escalations) == 1
        assert "still broken" in escalations[0]["description"]
        assert escalations[0]["input_payload"]["escalated_from"] == "es1"
        events = [e["event"] for e in self.db._events if e["job_id"] == "es1"]
        assert "escalated" in events

    def test_failed_job_without_policy_stays_failed(self):
        self.db.add_job(id="pl1", status="in_progress", leased_by_instance_id="agent-1", target_agent_role="codex")
        self.http.put("/api/jobs/pl1", json={"status": "failed", "error_message": "x"})
        assert self.db._jobs["pl1"]["status"] == "failed"
        assert len(self.db._jobs) == 1

    def test_dependency_unlock_respects_approval_gate(self):
        self.db.add_job(id="up1", status="in_progress", leased_by_instance_id="agent-1", target_agent_role="codex")
        self.db.add_job(id="dn1", status="waiting", target_agent_role="claude",
                        depends_on=["up1"], requires_approval=True)
        self.db.add_job(id="dn2", status="waiting", target_agent_role="claude",
                        depends_on=["up1"])
        self.http.put("/api/jobs/up1", json={"status": "completed"})
        assert self.db._jobs["dn1"]["status"] == "needs_approval"
        assert self.db._jobs["dn2"]["status"] == "pending"
