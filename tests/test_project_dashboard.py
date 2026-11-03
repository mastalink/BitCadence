"""Behavioral acceptance tests for the human-facing Projects projection."""

import json
from pathlib import Path
import subprocess

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
import mco.orchestrator.routes as routes_mod
from mco.orchestrator.auth import require_agent
from mco.orchestrator.routes import router as jobs_router
from mco.localstore import LocalStore


ROOT = Path(__file__).parents[1]
VIEW_SOURCE = (
    ROOT / "src" / "mco" / "console_src" / "5adac14f-6645-4e02-866b-22c4e571989b.js"
).read_text(encoding="utf-8")
SHELL_SOURCE = (
    ROOT / "src" / "mco" / "console_src" / "8ec84a72-7b37-434d-a68b-2ac1188e3e6d.js"
).read_text(encoding="utf-8")
OPERATOR = {"instance_id": "alex", "role": "human", "status": "online", "org_id": "default"}


@pytest.fixture
def project_api(monkeypatch, tmp_path):
    db = LocalStore(tmp_path / "projects.db")
    monkeypatch.setattr(routes_mod, "get_db_client", lambda: db)
    app = FastAPI()
    app.include_router(jobs_router)
    app.dependency_overrides[require_agent] = lambda: OPERATOR
    return db, TestClient(app)


def _project(js_jobs: list[dict]) -> list[dict]:
    """Execute the console's real projection helpers in Node, excluding JSX."""
    start = VIEW_SOURCE.index("const jobTime =")
    end = VIEW_SOURCE.index("function ProjectDashboard(")
    helpers = VIEW_SOURCE[start:end]
    script = (
        helpers
        + "\nconst result = projectGroups("
        + json.dumps(js_jobs)
        + ").map(p => ({id:p.id,name:p.name,source:p.source,state:p.state,status:p.status,"
        + "next:p.next,completed:p.completed,total:p.total,jobs:p.jobs.map(j => j.id)}));"
        + "\nconsole.log(JSON.stringify(result));"
    )
    proc = subprocess.run(
        ["node", "-e", script], cwd=ROOT, capture_output=True, text=True,
        encoding="utf-8", check=False,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    return json.loads(proc.stdout)


def test_projects_is_a_distinct_console_route_from_job_board():
    assert '{ id: "projects", label: "Projects"' in SHELL_SOURCE
    assert "projects: <ProjectDashboard" in SHELL_SOURCE
    assert "jobs: <JobBoard" in SHELL_SOURCE


def test_truncated_project_coverage_is_wired_to_a_visible_warning():
    adapter = (ROOT / "src" / "mco" / "console_src" / "47e66145-9c4d-41a1-acbb-42b12848f160.js").read_text(encoding="utf-8")
    assert "truncated: !!(projectView && projectView.truncated)" in adapter
    assert "getProjectCoverage:" in adapter
    assert "coverage={projectCoverage}" in SHELL_SOURCE
    assert "Project counts are partial" in VIEW_SOURCE


def test_full_project_dataset_is_lazy_and_not_part_of_fleet_poll():
    adapter = (ROOT / "src" / "mco" / "console_src" / "47e66145-9c4d-41a1-acbb-42b12848f160.js").read_text(encoding="utf-8")
    assert 'Promise.all([api("/api/jobs?limit=200"), api("/api/agents")])' in adapter
    assert adapter.count('api("/api/jobs/project-view")') == 1
    assert "Date.now() - projectFetchedAt < 60000" in adapter
    assert "await refreshProjectView(true)" in adapter
    assert "page !== \"projects\"" in SHELL_SOURCE
    assert "60000" in SHELL_SOURCE


def test_projection_prefers_declared_project_then_workflow_and_preserves_unassigned():
    groups = _project([
        {"id": "p1", "title": "Plan", "status": "completed", "input_payload": {"project": {"id": "little-saints", "name": "Little Saints"}}, "created_at": "2026-01-01"},
        {"id": "p2", "title": "Build", "status": "pending", "project_name": "Little Saints", "created_at": "2026-01-02"},
        {"id": "w1", "title": "Research", "status": "completed", "input_payload": {"workflow": {"name": "payment-latency-fix", "run": "r1", "step": "research"}}, "created_at": "2026-01-03"},
        {"id": "u1", "title": "One-off", "status": "pending", "created_at": "2026-01-04"},
    ])
    by_id = {g["id"]: g for g in groups}
    assert by_id["project:little-saints"]["jobs"] == ["p2", "p1"]
    assert by_id["project:little-saints"]["name"] == "Little Saints"
    assert by_id["workflow:payment-latency-fix"]["name"] == "Payment Latency Fix"
    assert by_id["workflow:payment-latency-fix"]["source"] == "workflow"
    assert by_id["unassigned"]["jobs"] == ["u1"]
    assert by_id["unassigned"]["source"] == "unassigned"


def test_status_progress_and_next_action_use_human_priority_order():
    groups = _project([
        {"id": "done", "title": "Plan", "status": "completed", "input_payload": {"project": "alpha"}},
        {"id": "work", "title": "Implement", "status": "in_progress", "input_payload": {"project": "alpha"}},
        {"id": "gate", "title": "Ship", "status": "needs_approval", "input_payload": {"project": "alpha"}},
        {"id": "bad", "title": "Broken", "status": "failed", "input_payload": {"project": "beta"}},
        {"id": "all-done", "title": "Finished", "status": "completed", "input_payload": {"project": "gamma"}},
    ])
    by_id = {g["id"]: g for g in groups}
    alpha = by_id["project:alpha"]
    assert (alpha["state"], alpha["status"], alpha["completed"], alpha["total"]) == (
        "Needs decision", "needs_approval", 1, 3
    )
    assert alpha["next"] == "Review “Ship”"
    assert by_id["project:beta"]["next"] == "Resolve “Broken”"
    assert by_id["project:gamma"]["next"] == "No action needed"


def test_assignment_is_separate_from_execution_payload_and_overlaid(project_api):
    db, http = project_api
    original = {"prompt": "do not change"}
    db.table("agent_jobs").insert({"id": "j1", "title": "Existing", "status": "pending", "org_id": "default", "input_payload": original}).execute()
    response = http.post("/api/jobs/j1/project", json={"name": "Little Saints"})
    assert response.status_code == 200
    stored_job = db.table("agent_jobs").select("*").eq("id", "j1").execute().data[0]
    assert stored_job["input_payload"] == original
    row = db.table("job_project_assignments").select("*").eq("job_id", "j1").execute().data[0]
    assert row["project_name"] == "Little Saints"
    shown = http.get("/api/jobs").json()
    assert shown[0]["project"] == {"id": "little-saints", "name": "Little Saints"}
    assert stored_job.get("project") is None


def test_assignment_validates_and_respects_org_boundary(project_api):
    db, http = project_api
    db.table("agent_jobs").insert({"id": "other", "title": "Other", "status": "pending", "org_id": "acme"}).execute()
    assert http.post("/api/jobs/other/project", json={"name": "Secret"}).status_code == 404
    db.table("agent_jobs").insert({"id": "mine", "title": "Mine", "status": "pending", "org_id": "default"}).execute()
    assert http.post("/api/jobs/mine/project", json={"name": 42}).status_code == 400
    assert http.post("/api/jobs/mine/project", json={"name": "x" * 81}).status_code == 400


def test_project_view_pages_beyond_job_board_limit(project_api):
    db, http = project_api
    for i in range(135):
        db.table("agent_jobs").insert({
            "id": f"job-{i}", "title": f"Job {i}", "status": "completed",
            "org_id": "default", "created_at": f"2026-01-{(i % 28) + 1:02d}T00:00:00Z",
        }).execute()
    assert len(http.get("/api/jobs?limit=100").json()) == 100
    project_view = http.get("/api/jobs/project-view").json()
    assert project_view["count"] == 135
    assert len(project_view["jobs"]) == 135
    assert project_view["truncated"] is False
