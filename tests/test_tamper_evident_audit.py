"""Tamper-evident audit tests: hash-chaining, verification, optional HMAC, CLI.

Mirrors tests/test_governance.py: a FastAPI app wired to the in-memory FakeDB,
plus direct unit tests of the chain primitives in mco.orchestrator.audit. The
chain is exercised through the real recording path (record_event) so what tests
verify is exactly what production writes.
"""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import mco.notifiers.ntfy as ntfy_mod
import mco.orchestrator.routes as routes_mod
from mco.orchestrator import audit as audit_mod
from mco.orchestrator.audit import (
    compute_hash,
    get_events,
    record_event,
    verify_chain,
)
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


@pytest.fixture(autouse=True)
def _no_audit_key(monkeypatch):
    """Default: no HMAC key configured (pure hash-chain mode).

    Signing tests opt in explicitly. This keeps the secret store off the hot
    path and the tests hermetic on machines that happen to have a vault.
    """
    monkeypatch.setattr(audit_mod, "_audit_hmac_key", lambda: None)


# ── Unit-level chain primitives ──────────────────────────────────────────────


class TestHashChainPrimitives:
    def test_compute_hash_is_deterministic_and_order_independent(self):
        a = {"job_id": "j1", "event": "created", "detail": {"x": 1, "y": 2}}
        b = {"event": "created", "job_id": "j1", "detail": {"y": 2, "x": 1}}
        assert compute_hash("", a) == compute_hash("", b)

    def test_compute_hash_depends_on_prev_hash(self):
        content = {"job_id": "j1", "event": "created"}
        assert compute_hash("", content) != compute_hash("deadbeef", content)

    def test_compute_hash_depends_on_content(self):
        assert compute_hash("p", {"event": "a"}) != compute_hash("p", {"event": "b"})


# ── Recording path: every append is chained ──────────────────────────────────


class TestChainedRecording:
    def setup_method(self):
        self.db = FakeDB()

    def test_first_event_links_to_genesis(self):
        record_event(self.db, "j1", "created", "agent-1", "codex")
        rows = get_events(self.db, "j1")
        assert len(rows) == 1
        assert rows[0]["prev_hash"] == ""
        assert rows[0]["hash"]  # non-empty

    def test_each_event_links_to_the_prior_hash(self):
        record_event(self.db, "j1", "created", "agent-1", "codex")
        record_event(self.db, "j1", "leased", "agent-1", "codex")
        record_event(self.db, "j1", "status:completed", "agent-1", "codex")
        rows = get_events(self.db, "j1")
        assert rows[1]["prev_hash"] == rows[0]["hash"]
        assert rows[2]["prev_hash"] == rows[1]["hash"]

    def test_chains_are_independent_per_job(self):
        record_event(self.db, "j1", "created", "agent-1", "codex")
        record_event(self.db, "j2", "created", "agent-1", "codex")
        assert get_events(self.db, "j2")[0]["prev_hash"] == ""

    def test_unsigned_rows_have_no_signature(self):
        record_event(self.db, "j1", "created", "agent-1", "codex")
        assert "signature" not in get_events(self.db, "j1")[0]


# ── Verification: intact vs. tampered ────────────────────────────────────────


class TestVerifyChain:
    def setup_method(self):
        self.db = FakeDB()
        for ev in ("created", "leased", "status:completed"):
            record_event(self.db, "j1", ev, "agent-1", "codex")

    def test_empty_chain_is_ok(self):
        report = verify_chain(self.db, "no-such-job")
        assert report["ok"] is True
        assert report["count"] == 0
        assert report["broken_at"] is None

    def test_intact_chain_verifies(self):
        report = verify_chain(self.db, "j1")
        assert report["ok"] is True
        assert report["count"] == 3
        assert report["broken_at"] is None

    def test_edited_content_breaks_the_link(self):
        # Tamper directly with stored state, bypassing the recording path.
        self.db._events[1]["event"] = "status:approved-by-attacker"
        report = verify_chain(self.db, "j1")
        assert report["ok"] is False
        assert report["broken_at"] == 2
        assert "content hash mismatch" in report["reason"]

    def test_edited_detail_breaks_the_link(self):
        self.db._events[0]["detail"] = {"injected": True}
        report = verify_chain(self.db, "j1")
        assert report["ok"] is False
        assert report["broken_at"] == 1

    def test_rewritten_hash_breaks_prev_linkage(self):
        # Forge a self-consistent hash on row 0; row 1 still points at the old one.
        self.db._events[0]["hash"] = "f" * 64
        report = verify_chain(self.db, "j1")
        assert report["ok"] is False
        # Row 0's own content hash no longer matches its forged hash.
        assert report["broken_at"] == 1

    def test_deleted_middle_event_breaks_linkage(self):
        # Append-only storage forbids this in production; we simulate the result
        # of a backend-level deletion to prove the chain would catch it.
        del self.db._events[1]
        report = verify_chain(self.db, "j1")
        assert report["ok"] is False
        assert report["broken_at"] == 2
        assert "prev_hash mismatch" in report["reason"]

    def test_reordered_events_break_linkage(self):
        self.db._events[1], self.db._events[2] = self.db._events[2], self.db._events[1]
        report = verify_chain(self.db, "j1")
        assert report["ok"] is False


# ── Optional HMAC signing ────────────────────────────────────────────────────


class TestHmacSignatures:
    KEY = b"super-secret-audit-key"

    def setup_method(self):
        self.db = FakeDB()

    def test_signed_rows_carry_signature_and_verify(self, monkeypatch):
        monkeypatch.setattr(audit_mod, "_audit_hmac_key", lambda: self.KEY)
        record_event(self.db, "j1", "created", "agent-1", "codex")
        record_event(self.db, "j1", "leased", "agent-1", "codex")
        rows = get_events(self.db, "j1")
        assert all(r.get("signature") for r in rows)
        assert verify_chain(self.db, "j1")["ok"] is True
        assert verify_chain(self.db, "j1")["signed"] is True

    def test_tampered_signature_fails_verification(self, monkeypatch):
        monkeypatch.setattr(audit_mod, "_audit_hmac_key", lambda: self.KEY)
        record_event(self.db, "j1", "created", "agent-1", "codex")
        self.db._events[0]["signature"] = "0" * 64
        report = verify_chain(self.db, "j1")
        assert report["ok"] is False
        assert "signature mismatch" in report["reason"]

    def test_wrong_key_fails_verification(self, monkeypatch):
        monkeypatch.setattr(audit_mod, "_audit_hmac_key", lambda: self.KEY)
        record_event(self.db, "j1", "created", "agent-1", "codex")
        # Verifier now holds a different key.
        monkeypatch.setattr(audit_mod, "_audit_hmac_key", lambda: b"other-key")
        report = verify_chain(self.db, "j1")
        assert report["ok"] is False
        assert "signature mismatch" in report["reason"]

    def test_missing_signature_when_key_required_fails(self, monkeypatch):
        # Row written WITHOUT a key, then a key appears at verify time: the
        # unsigned row must fail closed rather than pass.
        record_event(self.db, "j1", "created", "agent-1", "codex")
        monkeypatch.setattr(audit_mod, "_audit_hmac_key", lambda: self.KEY)
        report = verify_chain(self.db, "j1")
        assert report["ok"] is False
        assert "signature mismatch" in report["reason"]


# ── End-to-end through the orchestration routes ──────────────────────────────


class _AuditRouteBase:
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


class TestRoutesProduceVerifiableChain(_AuditRouteBase):
    def test_job_lifecycle_chain_verifies(self):
        # Create -> the create event is recorded through the real route.
        resp = self.http.post("/api/jobs", json={"title": "x", "target_agent_role": "codex"})
        job_id = resp.json()["job"]["id"]
        # Drive a status change so there is more than one link.
        self.db.add_job(id=job_id, status="in_progress", target_agent_role="codex")
        self.http.put(f"/api/jobs/{job_id}", json={"status": "completed"})

        rows = get_events(self.db, job_id)
        assert len(rows) >= 2
        assert verify_chain(self.db, job_id)["ok"] is True

    def test_tampering_after_the_fact_is_detected(self):
        resp = self.http.post("/api/jobs", json={"title": "x", "target_agent_role": "codex"})
        job_id = resp.json()["job"]["id"]
        self.db.add_job(id=job_id, status="in_progress", target_agent_role="codex")
        self.http.put(f"/api/jobs/{job_id}", json={"status": "completed"})

        # An attacker flips a recorded status in place.
        events = [e for e in self.db._events if e["job_id"] == job_id]
        events[-1]["event"] = "status:failed"
        assert verify_chain(self.db, job_id)["ok"] is False


# ── CLI: `mco audit --verify <job_id>` ───────────────────────────────────────


class TestAuditVerifyCli:
    def _run(self, monkeypatch, db, job_id):
        from typer.testing import CliRunner
        import mco.cli as cli
        import mco.orchestrator.routes as routes

        monkeypatch.setattr(routes, "get_db_client", lambda *a, **k: db)
        monkeypatch.setattr(audit_mod, "_audit_hmac_key", lambda: None)
        return CliRunner().invoke(cli.app, ["audit", job_id, "--verify"])

    def test_verify_ok_exits_zero(self, monkeypatch):
        db = FakeDB()
        record_event(db, "j1", "created", "agent-1", "codex")
        record_event(db, "j1", "leased", "agent-1", "codex")
        result = self._run(monkeypatch, db, "j1")
        assert result.exit_code == 0
        assert "intact" in result.stdout

    def test_verify_broken_exits_nonzero(self, monkeypatch):
        db = FakeDB()
        record_event(db, "j1", "created", "agent-1", "codex")
        record_event(db, "j1", "leased", "agent-1", "codex")
        db._events[0]["event"] = "tampered"
        result = self._run(monkeypatch, db, "j1")
        assert result.exit_code == 1
        assert "BROKEN" in result.stdout
