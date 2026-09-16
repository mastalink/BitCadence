"""Adaptive lease TTL: a worker's own time estimate sets its lease, instead of
one flat window that a careful job routinely outlives.

Real failure this fixes: a code review with a full pytest re-run took ~16
minutes against a 15-minute lease, so mco_complete came back 409 on a fence
the worker never saw coming - see Drumline "Job leases are 15 min" (2026-09-15).

Two invariants must hold at every TTL this can produce, estimate or not:
  1. a live worker's lease lasts long enough to actually finish the work;
  2. an abandoned job still comes back to pending - reclaim_stale_leases reads
     whatever lease_expires_at a lease/renew wrote, so the guarantee holds
     regardless of who set the TTL or how.
"""

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import mco.orchestrator.routes as routes
from mco.localstore import LocalStore
from mco.orchestrator.auth import require_agent
from mco.orchestrator.leases import DEFAULT_TTL_SECONDS

AGENT = {"instance_id": "worker-1", "role": "codex", "status": "online", "org_id": "default"}


# ── Pure function: effective_lease_ttl / get_lease_ttl_bounds ────────────────

def _cfg(monkeypatch, **values):
    monkeypatch.setattr(routes, "get_config", lambda: values)


class TestEffectiveLeaseTtl:
    def test_no_estimate_is_the_flat_default(self, monkeypatch):
        _cfg(monkeypatch)
        assert routes.effective_lease_ttl(None) == routes.get_lease_ttl_seconds() == 3600

    def test_zero_negative_and_non_numeric_are_treated_as_no_estimate(self, monkeypatch):
        _cfg(monkeypatch)
        default = routes.get_lease_ttl_seconds()
        for bad in (0, -30, "not a number", None, [], {}):
            assert routes.effective_lease_ttl(bad) == default

    def test_estimate_gets_the_greater_of_floor_or_fractional_buffer(self, monkeypatch):
        _cfg(monkeypatch)  # defaults: floor 300s, fraction 0.5
        # Small estimate: floor buffer wins (200 * 0.5 = 100 < 300).
        assert routes.effective_lease_ttl(200) == 200 + 300
        # Large estimate: fractional buffer wins (2000 * 0.5 = 1000 > 300).
        assert routes.effective_lease_ttl(2000) == 2000 + 1000

    def test_an_estimate_never_produces_a_shorter_lease_than_the_flat_floor(self, monkeypatch):
        _cfg(monkeypatch)
        # A 10s estimate plus its floor buffer still clears the configured minimum.
        assert routes.effective_lease_ttl(10) >= routes.get_lease_ttl_bounds()[0]

    def test_a_huge_or_hostile_estimate_is_capped_not_honoured(self, monkeypatch):
        _cfg(monkeypatch)
        lo, hi = routes.get_lease_ttl_bounds()
        assert routes.effective_lease_ttl(10 ** 9) == hi
        assert hi == 4 * 3600  # documented default ceiling

    def test_bounds_and_buffer_are_configurable(self, monkeypatch):
        _cfg(monkeypatch,
             MCO_LEASE_MIN_TTL_SECONDS="120", MCO_LEASE_MAX_TTL_SECONDS="600",
             MCO_LEASE_BUFFER_FLOOR_SECONDS="60", MCO_LEASE_BUFFER_FRACTION="0.1")
        assert routes.get_lease_ttl_bounds() == (120, 600)
        assert routes.effective_lease_ttl(50) == 120          # 50+60=110, clamped up to lo
        assert routes.effective_lease_ttl(50000) == 600        # clamped down to hi

    def test_max_can_never_be_configured_below_min(self, monkeypatch):
        _cfg(monkeypatch, MCO_LEASE_MIN_TTL_SECONDS="500", MCO_LEASE_MAX_TTL_SECONDS="100")
        lo, hi = routes.get_lease_ttl_bounds()
        assert lo == 500 and hi == 500

    def test_unparseable_bounds_fall_back_to_documented_defaults(self, monkeypatch):
        _cfg(monkeypatch, MCO_LEASE_MIN_TTL_SECONDS="lots", MCO_LEASE_MAX_TTL_SECONDS="also lots")
        assert routes.get_lease_ttl_bounds() == (300, 4 * 3600)

    def test_leases_module_default_matches_the_route_default(self):
        # Two independent constants (leases.py has no import of routes.py, by
        # design); they must still agree or a direct leases.py caller and an
        # HTTP caller would silently disagree about "no estimate given".
        assert DEFAULT_TTL_SECONDS == 3600


# ── Route-level: the estimate actually becomes lease_expires_at ─────────────

@pytest.fixture
def api(monkeypatch, tmp_path):
    store = LocalStore(tmp_path / "mco.db")
    monkeypatch.setattr(routes, "get_db_client", lambda: store)
    monkeypatch.setattr(routes, "notify_job_leased", lambda *a, **k: None)
    monkeypatch.setattr(routes, "_broadcast_callback", AsyncMock())
    app = FastAPI()
    app.include_router(routes.router)
    app.dependency_overrides[require_agent] = lambda: AGENT
    with TestClient(app) as http:
        yield http, store
    store.close()


def _job(store, **overrides):
    row = {"id": "job-1", "title": "t", "status": "pending", "target_agent_role": "codex",
           "org_id": "default", "created_at": datetime.now(timezone.utc).isoformat()}
    row.update(overrides)
    store.table("agent_jobs").insert(row).execute()
    return row["id"]


def _expires_at(store, job_id="job-1"):
    row = store.table("agent_jobs").select("*").eq("id", job_id).execute().data[0]
    return datetime.fromisoformat(row["lease_expires_at"].replace("Z", "+00:00"))


class TestLeaseRoute:
    def test_no_estimate_uses_the_flat_default(self, api, monkeypatch):
        http, store = api
        monkeypatch.setattr(routes, "get_config", lambda: {})
        _job(store)
        before = datetime.now(timezone.utc)

        resp = http.post("/api/jobs/lease", json={"task_id": "job-1", "agent_instance_id": "worker-1"})

        assert resp.status_code == 200 and resp.json()["success"]
        assert resp.json()["renew_after_seconds"] == 3600 // 3
        delta = (_expires_at(store) - before).total_seconds()
        assert 3590 <= delta <= 3610

    def test_a_stated_estimate_sets_a_correspondingly_sized_lease(self, api, monkeypatch):
        http, store = api
        monkeypatch.setattr(routes, "get_config", lambda: {})
        _job(store)
        before = datetime.now(timezone.utc)

        resp = http.post("/api/jobs/lease", json={
            "task_id": "job-1", "agent_instance_id": "worker-1", "estimated_seconds": 2000})

        assert resp.status_code == 200 and resp.json()["success"]
        expected = 2000 + 1000  # fractional buffer dominates at this size
        assert resp.json()["renew_after_seconds"] == expected // 3
        delta = (_expires_at(store) - before).total_seconds()
        assert expected - 5 <= delta <= expected + 5

    def test_a_reckless_estimate_is_still_capped(self, api, monkeypatch):
        http, store = api
        monkeypatch.setattr(routes, "get_config", lambda: {})
        _job(store)
        before = datetime.now(timezone.utc)

        resp = http.post("/api/jobs/lease", json={
            "task_id": "job-1", "agent_instance_id": "worker-1", "estimated_seconds": 999999})

        assert resp.status_code == 200 and resp.json()["success"]
        delta = (_expires_at(store) - before).total_seconds()
        assert 4 * 3600 - 5 <= delta <= 4 * 3600 + 5

    def test_leased_events_records_what_ttl_was_used(self, api, monkeypatch):
        from mco.orchestrator.audit import get_events
        http, store = api
        monkeypatch.setattr(routes, "get_config", lambda: {})
        _job(store)

        http.post("/api/jobs/lease", json={
            "task_id": "job-1", "agent_instance_id": "worker-1", "estimated_seconds": 500})

        leased = next(e for e in get_events(store, "job-1") if e["event"] == "leased")
        assert leased["detail"]["estimated_seconds"] == 500
        assert leased["detail"]["lease_ttl_seconds"] == 500 + 300


class TestLeaseNextRoute:
    def test_estimate_flows_through_lease_next_too(self, api, monkeypatch):
        http, store = api
        monkeypatch.setattr(routes, "get_config", lambda: {})
        _job(store)
        before = datetime.now(timezone.utc)

        resp = http.post("/api/jobs/lease_next", json={
            "agent_instance_id": "worker-1", "estimated_seconds": 400})

        assert resp.status_code == 200 and resp.json()["success"]
        expected = 400 + 300  # floor buffer dominates at this size
        delta = (_expires_at(store) - before).total_seconds()
        assert expected - 5 <= delta <= expected + 5


class TestRenewRoute:
    def test_renew_can_ask_for_more_time_than_the_original_lease(self, api, monkeypatch):
        http, store = api
        monkeypatch.setattr(routes, "get_config", lambda: {})
        _job(store)
        lease = http.post("/api/jobs/lease", json={
            "task_id": "job-1", "agent_instance_id": "worker-1", "estimated_seconds": 100}).json()["lease"]

        before = datetime.now(timezone.utc)
        resp = http.post("/api/jobs/job-1/renew", json={**lease, "estimated_seconds": 3000})

        assert resp.status_code == 200 and resp.json()["success"]
        expected = 3000 + 1500  # fractional buffer dominates
        delta = (_expires_at(store) - before).total_seconds()
        assert expected - 5 <= delta <= expected + 5

    def test_renew_without_a_new_estimate_falls_back_to_the_flat_default(self, api, monkeypatch):
        http, store = api
        monkeypatch.setattr(routes, "get_config", lambda: {})
        _job(store)
        lease = http.post("/api/jobs/lease", json={
            "task_id": "job-1", "agent_instance_id": "worker-1", "estimated_seconds": 100}).json()["lease"]

        before = datetime.now(timezone.utc)
        resp = http.post("/api/jobs/job-1/renew", json=lease)

        assert resp.status_code == 200
        delta = (_expires_at(store) - before).total_seconds()
        assert 3590 <= delta <= 3610


# ── The invariant: whatever the TTL, an abandoned lease still comes back ────

class TestReclaimStillWorksAtAnyTtl:
    def test_a_large_estimate_does_not_defeat_reclamation_once_it_truly_expires(self, api, monkeypatch):
        http, store = api
        monkeypatch.setattr(routes, "get_config", lambda: {})
        _job(store)
        http.post("/api/jobs/lease", json={
            "task_id": "job-1", "agent_instance_id": "worker-1", "estimated_seconds": 500})

        # The worker vanishes. Move the wall clock past its (500+300=800s) lease
        # by writing an already-expired lease_expires_at directly, the way real
        # time passing would - then the reaper must still reclaim it.
        past = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
        store.table("agent_jobs").update({"lease_expires_at": past}).eq("id", "job-1").execute()

        reclaimed = routes.reclaim_stale_leases(store)

        assert reclaimed == 1
        row = store.table("agent_jobs").select("*").eq("id", "job-1").execute().data[0]
        assert row["status"] == "pending"
        assert row["leased_by_instance_id"] is None

    def test_a_job_that_renews_in_time_is_never_reclaimed(self, api, monkeypatch):
        http, store = api
        monkeypatch.setattr(routes, "get_config", lambda: {})
        _job(store)
        lease = http.post("/api/jobs/lease", json={
            "task_id": "job-1", "agent_instance_id": "worker-1", "estimated_seconds": 60}).json()["lease"]

        http.post("/api/jobs/job-1/renew", json={**lease, "estimated_seconds": 600})

        assert routes.reclaim_stale_leases(store) == 0
        row = store.table("agent_jobs").select("*").eq("id", "job-1").execute().data[0]
        assert row["status"] == "leased"
