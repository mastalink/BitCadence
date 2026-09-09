"""
Tests for /healthz (pure liveness) and /readyz (readiness).

Requirements:
- /healthz answers 'is this process alive' (returns 200, does not query the database)
- /readyz answers 'can this gateway safely accept work':
    - store answers a trivial query; fails with 503 if store is unreachable
    - scheduler heartbeat (if configured) is fresh; fails with 503 if missing or stale
    - fleet liveness: zero workers online reports as DEGRADED health in body, NEVER a non-200 status code
- Body names each check and its result for operator visibility
"""
from __future__ import annotations

import json
from datetime import datetime, timezone, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from mco.cli import create_app
import mco.orchestrator.routes as routes_mod
import mco.launcher as launcher_mod
import mco.scheduler as scheduler_mod
from mco.localstore import LocalStore


@pytest.fixture
def test_app():
    return create_app()


def test_healthz_is_pure_liveness_even_without_db(monkeypatch, test_app):
    monkeypatch.setattr(routes_mod, "get_db_client", lambda: None)
    client = TestClient(test_app)
    resp = client.get("/healthz")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert data["process"] == "alive"
    assert data["database"] is False


def test_readyz_fails_503_when_database_is_none(monkeypatch, test_app):
    monkeypatch.setattr(routes_mod, "get_db_client", lambda: None)
    client = TestClient(test_app)
    resp = client.get("/readyz")
    assert resp.status_code == 503
    data = resp.json()
    assert data["status"] == "unavailable"
    assert data["checks"]["store"]["status"] == "failed"
    assert "Database not configured" in data["checks"]["store"]["error"]


def test_readyz_fails_503_when_store_query_raises(monkeypatch, test_app):
    class BrokenStore:
        backend = "broken"

        def table(self, name):
            raise RuntimeError("Database connection timed out")

    monkeypatch.setattr(routes_mod, "get_db_client", lambda: BrokenStore())
    client = TestClient(test_app)
    resp = client.get("/readyz")
    assert resp.status_code == 503
    data = resp.json()
    assert data["status"] == "unavailable"
    assert data["checks"]["store"]["status"] == "failed"
    assert "Database connection timed out" in data["checks"]["store"]["error"]


def test_readyz_returns_200_degraded_with_zero_workers(tmp_path, monkeypatch, test_app):
    """CRITICAL requirement: Zero workers online must report as DEGRADED in body, NEVER non-200."""
    store = LocalStore(tmp_path / "readyz_test.db")
    monkeypatch.setattr(routes_mod, "get_db_client", lambda: store)
    monkeypatch.setattr(scheduler_mod, "SCHEDULES_CONFIG_PATH", tmp_path / "no-schedules.yaml")
    monkeypatch.delenv("MCO_REQUIRE_SCHEDULER", raising=False)

    client = TestClient(test_app)
    resp = client.get("/readyz")

    # Status code MUST be 200 so load balancers do not drop the control plane
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "degraded"
    assert data["checks"]["store"]["status"] == "ok"
    assert data["checks"]["store"]["backend"] == "local"
    assert data["checks"]["fleet"]["status"] == "degraded"
    assert data["checks"]["fleet"]["online_workers"] == 0
    assert data["checks"]["fleet"]["detail"] == "No workers currently online"
    assert data["checks"]["scheduler"]["status"] == "ok"
    assert data["checks"]["scheduler"]["configured"] is False


def test_readyz_returns_200_ok_when_worker_online(tmp_path, monkeypatch, test_app):
    store = LocalStore(tmp_path / "readyz_test.db")
    # Register an active worker
    store.table("agent_registry").insert({
        "instance_id": "codex-1",
        "role": "codex",
        "status": "online",
        "last_seen_at": datetime.now(timezone.utc).isoformat(),
    }).execute()

    monkeypatch.setattr(routes_mod, "get_db_client", lambda: store)
    monkeypatch.setattr(scheduler_mod, "SCHEDULES_CONFIG_PATH", tmp_path / "no-schedules.yaml")
    monkeypatch.delenv("MCO_REQUIRE_SCHEDULER", raising=False)

    client = TestClient(test_app)
    resp = client.get("/readyz")

    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert data["checks"]["store"]["status"] == "ok"
    assert data["checks"]["fleet"]["status"] == "ok"
    assert data["checks"]["fleet"]["online_workers"] == 1
    assert data["checks"]["fleet"]["registered_workers"] == 1


def test_readyz_scheduler_stale_heartbeat_fails_503(tmp_path, monkeypatch, test_app):
    store = LocalStore(tmp_path / "readyz_test.db")
    monkeypatch.setattr(routes_mod, "get_db_client", lambda: store)

    state_file = tmp_path / "schedule-state.json"
    stale_time = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    state_file.write_text(json.dumps({"version": 1, "updated_at": stale_time, "schedules": {}}), encoding="utf-8")

    monkeypatch.setenv("MCO_REQUIRE_SCHEDULER", "true")
    monkeypatch.setattr(launcher_mod, "SCHEDULE_STATE_PATH", state_file)

    client = TestClient(test_app)
    resp = client.get("/readyz")

    assert resp.status_code == 503
    data = resp.json()
    assert data["status"] == "unavailable"
    assert data["checks"]["scheduler"]["status"] == "stale"
    assert data["checks"]["scheduler"]["configured"] is True
    assert "stale" in data["checks"]["scheduler"]["error"]


def test_readyz_scheduler_fresh_heartbeat_passes(tmp_path, monkeypatch, test_app):
    store = LocalStore(tmp_path / "readyz_test.db")
    store.table("agent_registry").insert({
        "instance_id": "claude-1",
        "role": "claude",
        "status": "online",
        "last_seen_at": datetime.now(timezone.utc).isoformat(),
    }).execute()
    monkeypatch.setattr(routes_mod, "get_db_client", lambda: store)

    state_file = tmp_path / "schedule-state.json"
    fresh_time = (datetime.now(timezone.utc) - timedelta(seconds=5)).isoformat()
    state_file.write_text(json.dumps({"version": 1, "updated_at": fresh_time, "schedules": {}}), encoding="utf-8")

    monkeypatch.setenv("MCO_REQUIRE_SCHEDULER", "true")
    monkeypatch.setattr(launcher_mod, "SCHEDULE_STATE_PATH", state_file)

    client = TestClient(test_app)
    resp = client.get("/readyz")

    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert data["checks"]["scheduler"]["status"] == "ok"
    assert data["checks"]["scheduler"]["configured"] is True
    assert data["checks"]["scheduler"]["last_heartbeat_seconds"] <= 10


def test_readyz_scheduler_configured_missing_state_fails_503(tmp_path, monkeypatch, test_app):
    store = LocalStore(tmp_path / "readyz_test.db")
    monkeypatch.setattr(routes_mod, "get_db_client", lambda: store)

    missing_state_file = tmp_path / "does-not-exist.json"
    monkeypatch.setenv("MCO_REQUIRE_SCHEDULER", "true")
    monkeypatch.setattr(launcher_mod, "SCHEDULE_STATE_PATH", missing_state_file)

    client = TestClient(test_app)
    resp = client.get("/readyz")

    assert resp.status_code == 503
    data = resp.json()
    assert data["status"] == "unavailable"
    assert data["checks"]["scheduler"]["status"] == "missing"
    assert data["checks"]["scheduler"]["configured"] is True