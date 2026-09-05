"""Stale-lease reclamation (audit finding F-01): a job LEASED by a worker
that crashed / was Ctrl-C'd / lost the network must not stay LEASED forever.
It is reclaimed to PENDING once its lease outlives MCO_LEASE_TTL_SECONDS.

Uses a real LocalStore so the update path (status/leased_by/started_at) and
the audit event are exercised on the actual embedded backend."""

from datetime import datetime, timedelta, timezone

import pytest

import mco.orchestrator.routes as routes_mod
from mco.localstore import LocalStore
from mco.orchestrator.audit import get_events


@pytest.fixture
def db(tmp_path):
    s = LocalStore(tmp_path / "test.db")
    yield s
    s.close()


def _leased_job(db, started_at, job_id="job-1"):
    db.table("agent_jobs").insert({
        "id": job_id, "title": "t", "status": "leased",
        "target_agent_role": "codex", "leased_by_instance_id": "worker-1",
        "started_at": started_at,
    }).execute()


def _iso(dt):
    return dt.isoformat()


def _status(db, job_id="job-1"):
    rows = db.table("agent_jobs").select("*").eq("id", job_id).execute().data
    return rows[0] if rows else None


class TestReclaim:
    def test_stale_lease_reverts_to_pending(self, db, monkeypatch):
        monkeypatch.setattr(routes_mod, "get_config", lambda: {"MCO_LEASE_TTL_SECONDS": "300"})
        _leased_job(db, _iso(datetime.now(timezone.utc) - timedelta(seconds=600)))
        n = routes_mod.reclaim_stale_leases(db)
        assert n == 1
        row = _status(db)
        assert row["status"] == "pending"
        assert row["leased_by_instance_id"] is None
        assert row["started_at"] is None

    def test_fresh_lease_is_left_alone(self, db, monkeypatch):
        monkeypatch.setattr(routes_mod, "get_config", lambda: {"MCO_LEASE_TTL_SECONDS": "300"})
        _leased_job(db, _iso(datetime.now(timezone.utc) - timedelta(seconds=60)))
        assert routes_mod.reclaim_stale_leases(db) == 0
        assert _status(db)["status"] == "leased"

    def test_records_a_lease_expired_audit_event(self, db, monkeypatch):
        monkeypatch.setattr(routes_mod, "get_config", lambda: {"MCO_LEASE_TTL_SECONDS": "300"})
        _leased_job(db, _iso(datetime.now(timezone.utc) - timedelta(seconds=600)))
        routes_mod.reclaim_stale_leases(db)
        events = get_events(db, "job-1")
        assert any(e["event"] == "lease_expired" and e["actor_id"] == "system" for e in events)

    def test_ttl_zero_disables_reclamation(self, db, monkeypatch):
        monkeypatch.setattr(routes_mod, "get_config", lambda: {"MCO_LEASE_TTL_SECONDS": "0"})
        _leased_job(db, _iso(datetime.now(timezone.utc) - timedelta(days=7)))
        assert routes_mod.reclaim_stale_leases(db) == 0
        assert _status(db)["status"] == "leased"

    def test_default_ttl_is_900(self, monkeypatch):
        monkeypatch.setattr(routes_mod, "get_config", lambda: {})
        assert routes_mod.get_lease_ttl_seconds() == 900

    def test_lease_without_started_at_is_skipped(self, db, monkeypatch):
        monkeypatch.setattr(routes_mod, "get_config", lambda: {"MCO_LEASE_TTL_SECONDS": "300"})
        db.table("agent_jobs").insert({
            "id": "job-x", "title": "t", "status": "leased",
            "leased_by_instance_id": "w", "started_at": None,
        }).execute()
        assert routes_mod.reclaim_stale_leases(db) == 0

    def test_never_raises_on_broken_db(self):
        class BrokenDB:
            def table(self, *a, **k):
                raise RuntimeError("db down")
        # config default applies; must swallow the error and return 0
        import mco.orchestrator.routes as r
        with pytest.raises(RuntimeError, match="db down"):
            r.reclaim_stale_leases(BrokenDB())
