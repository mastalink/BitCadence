"""Tests for BitCadence improvements:
1. Default job board sort order configuration & querying.
2. Multi-select batch actions (/api/jobs/batch-action).
3. Autonomy observability & steering controls (/api/score/autonomy).
"""
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import mco.orchestrator.routes as routes_mod
from mco.orchestrator.auth import require_agent
from mco.orchestrator.routes import router as jobs_router
from mco.orchestrator.score_gate_routes import score_autonomy_router
from mco.orchestrator import score_sweep
from tests.test_routes import FakeDB


@pytest.fixture
def test_env(monkeypatch, tmp_path):
    db = FakeDB()
    db.add_agent("op-1", "operator", "token-op")
    db.add_agent("w-1", "codex", "token-w1")
    monkeypatch.setattr(routes_mod, "get_db_client", lambda: db)

    app = FastAPI()
    app.include_router(jobs_router)
    app.include_router(score_autonomy_router)

    app.dependency_overrides[require_agent] = lambda: {
        "instance_id": "op-1",
        "role": "operator",
        "scopes": ["admin", "jobs:read", "jobs:write", "jobs:approve"],
        "org_id": "default",
    }
    client = TestClient(app)
    return {"db": db, "client": client, "app": app}


def test_job_sorting_by_priority(test_env):
    db, client = test_env["db"], test_env["client"]
    db.add_job(id="j1", title="Low priority", priority=10, created_at="2026-01-01T10:00:00Z")
    db.add_job(id="j2", title="High priority", priority=90, created_at="2026-01-01T09:00:00Z")
    db.add_job(id="j3", title="Med priority", priority=50, created_at="2026-01-01T11:00:00Z")

    resp = client.get("/api/jobs?sort=priority_desc")
    assert resp.status_code == 200
    ids = [j["id"] for j in resp.json()]
    assert ids == ["j2", "j3", "j1"]


def test_job_sorting_by_created(test_env):
    db, client = test_env["db"], test_env["client"]
    db.add_job(id="j1", title="Job 1", created_at="2026-01-01T10:00:00Z")
    db.add_job(id="j2", title="Job 2", created_at="2026-01-01T08:00:00Z")
    db.add_job(id="j3", title="Job 3", created_at="2026-01-01T12:00:00Z")

    # Created asc
    resp = client.get("/api/jobs?sort=created_asc")
    assert resp.status_code == 200
    assert [j["id"] for j in resp.json()] == ["j2", "j1", "j3"]

    # Created desc
    resp = client.get("/api/jobs?sort=created_desc")
    assert resp.status_code == 200
    assert [j["id"] for j in resp.json()] == ["j3", "j1", "j2"]


def test_job_sorting_by_status(test_env):
    db, client = test_env["db"], test_env["client"]
    db.add_job(id="j_completed", title="Done", status="completed", created_at="2026-01-01T10:00:00Z")
    db.add_job(id="j_approval", title="Needs approval", status="needs_approval", created_at="2026-01-01T09:00:00Z")
    db.add_job(id="j_pending", title="Pending", status="pending", created_at="2026-01-01T08:00:00Z")

    resp = client.get("/api/jobs?sort=status")
    assert resp.status_code == 200
    assert [j["id"] for j in resp.json()] == ["j_approval", "j_pending", "j_completed"]


def test_batch_action_retry(test_env):
    db, client = test_env["db"], test_env["client"]
    j1 = db.add_job(title="Fail 1", status="failed")
    j2 = db.add_job(title="Fail 2", status="rejected")
    j3 = db.add_job(title="Already done", status="completed")

    resp = client.post("/api/jobs/batch-action", json={
        "job_ids": [j1, j2, j3],
        "action": "retry"
    })
    assert resp.status_code == 200
    data = resp.json()
    assert data["success"] is True
    assert data["action"] == "retry"
    assert sorted(data["succeeded"]) == sorted([j1, j2])
    assert j3 in data["failed"]
    assert "failed" in data["failed"][j3].lower()


def test_batch_action_cancel(test_env):
    db, client = test_env["db"], test_env["client"]
    j1 = db.add_job(title="Pending job", status="pending")
    j2 = db.add_job(title="Waiting job", status="waiting")

    resp = client.post("/api/jobs/batch-action", json={
        "job_ids": [j1, j2],
        "action": "cancel",
        "reason": "Batch cleanup"
    })
    assert resp.status_code == 200
    data = resp.json()
    assert sorted(data["succeeded"]) == sorted([j1, j2])
    assert db._jobs[j1]["status"] == "cancelled"
    assert db._jobs[j2]["status"] == "cancelled"


def test_batch_action_archive(test_env):
    db, client = test_env["db"], test_env["client"]
    j1 = db.add_job(title="Done 1", status="completed")
    j2 = db.add_job(title="Pending 1", status="pending")

    resp = client.post("/api/jobs/batch-action", json={
        "job_ids": [j1, j2],
        "action": "archive"
    })
    assert resp.status_code == 200
    data = resp.json()
    assert data["succeeded"] == [j1]
    assert j2 in data["failed"]


def test_batch_action_unauthorized_role(test_env):
    client, app = test_env["client"], test_env["app"]
    app.dependency_overrides[require_agent] = lambda: {
        "instance_id": "worker-1",
        "role": "codex",
        "scopes": ["jobs:read", "jobs:write"],
        "org_id": "default",
    }
    resp = client.post("/api/jobs/batch-action", json={
        "job_ids": ["any-id"],
        "action": "cancel"
    })
    assert resp.status_code == 403


def test_autonomy_status_and_pause_resume(test_env, monkeypatch):
    client = test_env["client"]
    score_sweep.set_sweep_paused(False)

    # Status check
    resp = client.get("/api/score/autonomy")
    assert resp.status_code == 200
    data = resp.json()
    assert "status" in data
    assert data["paused"] is False

    # Pause
    resp_pause = client.post("/api/score/autonomy/pause")
    assert resp_pause.status_code == 200
    assert resp_pause.json()["paused"] is True
    assert score_sweep.is_sweep_paused() is True

    # Status reflects paused
    resp_status2 = client.get("/api/score/autonomy")
    assert resp_status2.json()["paused"] is True
    assert resp_status2.json()["status"] == "paused"

    # Resume
    resp_resume = client.post("/api/score/autonomy/resume")
    assert resp_resume.status_code == 200
    assert resp_resume.json()["paused"] is False
    assert score_sweep.is_sweep_paused() is False


def test_autonomy_manual_tick(test_env, monkeypatch):
    client = test_env["client"]

    class FakeConductor:
        pass

    class FakeResult:
        ticked = ["run-1"]
        advanced = ["run-1"]
        blocked = {}
        skipped = {}
        errors = {}
        failing = {}

    monkeypatch.setattr(score_sweep, "open_conductor", lambda: FakeConductor())
    monkeypatch.setattr(score_sweep, "sweep", lambda conductor, force=False: FakeResult())

    resp = client.post("/api/score/autonomy/tick")
    assert resp.status_code == 200
    res = resp.json()
    assert res["success"] is True
    assert res["result"]["ticked"] == ["run-1"]


def test_console_bundle_roundtrips_and_includes_improvements():
    """Verify scripts/build_console.py roundtrips with 0 mismatches, and console.html contains new features."""
    import scripts.build_console as builder
    from mco.console import get_console_html

    # Roundtrip verification: checks all console_src/ match console.html
    builder.build(check_only=True)

    html = get_console_html()
    assert html is not None
    # Verify manifest contains all updated assets
    manifest = builder._read_manifest(html)
    assert len(manifest) > 0

    # Verify that extracted source files contain the new features
    store_src = (builder.SRC / "47e66145-9c4d-41a1-acbb-42b12848f160.js").read_text(encoding="utf-8")
    assert "batchAction" in store_src
    assert "getAutonomy" in store_src
    assert "pauseAutonomy" in store_src
    assert "resumeAutonomy" in store_src
    assert "tickAutonomy" in store_src
    assert "abortRun" in store_src

    board_src = (builder.SRC / "5adac14f-6645-4e02-866b-22c4e571989b.js").read_text(encoding="utf-8")
    assert "bitcadence_job_sort" in board_src
    assert "mco_job_sort" in board_src
    assert "runBatch" in board_src
    assert "toggleSelectAll" in board_src

    overview_src = (builder.SRC / "43b328d0-9d0e-4fce-a105-be0a939d7e48.js").read_text(encoding="utf-8")
    assert "AutonomyLiveLookModal" in overview_src
    assert "AutonomyControlCard" in overview_src
    assert "MemoryDetailDrawer" in overview_src
    assert "handleBatchApprove" in overview_src
