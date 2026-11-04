import json

from fastapi import FastAPI
from fastapi.testclient import TestClient

from mco.localstore import LocalStore
from mco.orchestrator.auth import require_agent
from mco.orchestrator.portfolio_routes import portfolio_router


def app_for(store, caller=None):
    import mco.orchestrator.portfolio_routes as routes

    routes.get_db_client = lambda: store
    app = FastAPI()
    app.include_router(portfolio_router)
    if caller is not None:
        app.dependency_overrides[require_agent] = lambda: caller
    return TestClient(app)


def write_config(path):
    path.write_text(
        """
version: 1
projects:
  - id: alpha
    name: Project Alpha
    status: active
    priority: 1
    done_definition: The pilot workflow is dependable.
    aliases: [alpha-app]
    milestones:
      - {title: Foundation, status: done}
      - {title: Pilot, status: next}
publishing: {}
gpu: {stage_terms: []}
thresholds: {pending_hours: 2}
""",
        encoding="utf-8",
    )


def test_portfolio_routes_require_authentication(tmp_path, monkeypatch):
    monkeypatch.setenv("MCO_PORTFOLIO_CONFIG", str(tmp_path / "missing.yaml"))
    http = app_for(LocalStore(tmp_path / "board.db"))

    assert http.get("/api/portfolio").status_code == 401
    assert http.get("/api/portfolio/flags").status_code == 401


def test_missing_config_returns_safe_setup_state(tmp_path, monkeypatch):
    private_path = tmp_path / "private" / "portfolio.yaml"
    monkeypatch.setenv("MCO_PORTFOLIO_CONFIG", str(private_path))
    http = app_for(
        LocalStore(tmp_path / "board.db"),
        {"org_id": "ours", "role": "viewer", "scopes": ["jobs:read"]},
    )

    response = http.get("/api/portfolio")

    assert response.status_code == 200
    assert response.json()["configured"] is False
    assert str(tmp_path) not in response.text
    assert response.json()["controls"] == {"read_only": True}


def test_invalid_config_degrades_without_leaking_contents(tmp_path, monkeypatch):
    path = tmp_path / "portfolio.yaml"
    path.write_text("private-secret: [", encoding="utf-8")
    monkeypatch.setenv("MCO_PORTFOLIO_CONFIG", str(path))
    http = app_for(
        LocalStore(tmp_path / "board.db"),
        {"org_id": "ours", "role": "viewer", "scopes": ["jobs:read"]},
    )

    response = http.get("/api/portfolio")

    assert response.status_code == 200
    assert response.json()["configured"] is False
    assert response.json()["warning"] == "Portfolio configuration is invalid."
    assert "private-secret" not in response.text


def test_semantically_invalid_optional_config_degrades_without_500(tmp_path, monkeypatch):
    path = tmp_path / "portfolio.yaml"
    write_config(path)
    path.write_text(path.read_text(encoding="utf-8") + "\ngpu:\n  stage_terms: null\n")
    monkeypatch.setenv("MCO_PORTFOLIO_CONFIG", str(path))
    http = app_for(
        LocalStore(tmp_path / "board.db"),
        {"org_id": "ours", "role": "viewer", "scopes": ["jobs:read"]},
    )

    response = http.get("/api/portfolio")

    assert response.status_code == 200
    assert response.json()["configured"] is False
    assert response.json()["warning"] == "Portfolio configuration is invalid."


def test_portfolio_aggregate_is_org_scoped_and_flags_endpoint_matches(tmp_path, monkeypatch):
    path = tmp_path / "portfolio.yaml"
    write_config(path)
    monkeypatch.setenv("MCO_PORTFOLIO_CONFIG", str(path))
    store = LocalStore(tmp_path / "board.db")
    store.table("agent_jobs").insert(
        {
            "id": "ours-old",
            "org_id": "ours",
            "title": "Pilot work",
            "status": "pending",
            "created_at": "2026-11-03T17:00:00Z",
            "input_payload": {"project": "alpha-app", "prompt": "not returned"},
        }
    ).execute()
    store.table("agent_jobs").insert(
        {
            "id": "theirs",
            "org_id": "theirs",
            "title": "Other org private work",
            "status": "failed",
            "created_at": "2026-11-03T19:00:00Z",
            "input_payload": {"project": "alpha-app"},
        }
    ).execute()
    store.table("agent_registry").insert(
        {"instance_id": "ours-worker", "org_id": "ours", "role": "codex", "status": "online"}
    ).execute()
    store.table("agent_registry").insert(
        {"instance_id": "theirs-worker", "org_id": "theirs", "role": "claude", "status": "broken"}
    ).execute()
    store.table("agent_job_events").insert(
        {
            "id": "event-ours",
            "job_id": "ours-old",
            "org_id": "ours",
            "event": "chain_stalled",
            "created_at": "2026-11-03T19:00:00Z",
            "payload": {},
        }
    ).execute()
    store.table("agent_job_events").insert(
        {
            "id": "event-theirs",
            "job_id": "theirs",
            "org_id": "theirs",
            "event": "chain_stalled",
            "created_at": "2026-11-03T19:00:00Z",
            "payload": {},
        }
    ).execute()
    monkeypatch.setattr(
        "mco.orchestrator.portfolio_routes._now",
        lambda: __import__("datetime").datetime(2026, 11, 3, 20, 0, tzinfo=__import__("datetime").timezone.utc),
    )
    http = app_for(
        store,
        {"org_id": "ours", "role": "viewer", "scopes": ["jobs:read"]},
    )

    response = http.get("/api/portfolio")
    flags_response = http.get("/api/portfolio/flags")

    assert response.status_code == 200
    body = response.json()
    assert body["configured"] is True
    assert [job["id"] for job in body["projects"][0]["recent_jobs"]] == ["ours-old"]
    assert body["workers"]["workers"] == [
        {"instance_id": "ours-worker", "role": "codex", "status": "online", "last_seen_at": None}
    ]
    assert "Other org private work" not in response.text
    assert "not returned" not in response.text
    assert {flag["kind"] for flag in body["flags"]} == {"job_pending", "chain_stalled"}
    assert flags_response.status_code == 200
    assert flags_response.json()["flags"] == body["flags"]
    assert flags_response.json()["generated_at"] == body["generated_at"]


def test_portfolio_scope_is_enforced(tmp_path, monkeypatch):
    path = tmp_path / "portfolio.yaml"
    write_config(path)
    monkeypatch.setenv("MCO_PORTFOLIO_CONFIG", str(path))
    http = app_for(
        LocalStore(tmp_path / "board.db"),
        {"org_id": "ours", "role": "viewer", "scopes": ["agents:read"]},
    )

    assert http.get("/api/portfolio").status_code == 403


def test_chain_stall_query_filters_before_the_hundred_event_limit(tmp_path, monkeypatch):
    path = tmp_path / "portfolio.yaml"
    write_config(path)
    monkeypatch.setenv("MCO_PORTFOLIO_CONFIG", str(path))
    store = LocalStore(tmp_path / "board.db")
    store.table("agent_jobs").insert(
        {
            "id": "job",
            "org_id": "ours",
            "title": "Pilot work",
            "status": "in_progress",
            "created_at": "2026-11-03T18:00:00Z",
            "input_payload": {"project": "alpha-app"},
        }
    ).execute()
    store.table("agent_job_events").insert(
        {
            "id": "stall",
            "job_id": "job",
            "org_id": "ours",
            "event": "chain_stalled",
            "created_at": "2026-11-03T19:00:00Z",
            "payload": {},
        }
    ).execute()
    for index in range(101):
        store.table("agent_job_events").insert(
            {
                "id": f"routine-{index}",
                "job_id": "job",
                "org_id": "ours",
                "event": "heartbeat",
                "created_at": "2026-11-03T19:30:00Z",
                "payload": {},
            }
        ).execute()
    monkeypatch.setattr(
        "mco.orchestrator.portfolio_routes._now",
        lambda: __import__("datetime").datetime(
            2026, 11, 3, 20, 0, tzinfo=__import__("datetime").timezone.utc
        ),
    )
    http = app_for(
        store,
        {"org_id": "ours", "role": "viewer", "scopes": ["jobs:read"]},
    )

    response = http.get("/api/portfolio")

    assert response.status_code == 200
    assert "chain_stalled" in {flag["kind"] for flag in response.json()["flags"]}


def test_core_board_query_failure_returns_service_unavailable(tmp_path, monkeypatch):
    path = tmp_path / "portfolio.yaml"
    write_config(path)
    monkeypatch.setenv("MCO_PORTFOLIO_CONFIG", str(path))

    class BrokenStore:
        def table(self, name):
            raise RuntimeError("private database detail")

    http = app_for(
        BrokenStore(),
        {"org_id": "ours", "role": "viewer", "scopes": ["jobs:read"]},
    )

    response = http.get("/api/portfolio")

    assert response.status_code == 503
    assert response.json() == {"detail": "Portfolio board data is unavailable."}
    assert "private database detail" not in response.text


def test_chain_event_query_failure_returns_service_unavailable(tmp_path, monkeypatch):
    path = tmp_path / "portfolio.yaml"
    write_config(path)
    monkeypatch.setenv("MCO_PORTFOLIO_CONFIG", str(path))
    store = LocalStore(tmp_path / "board.db")
    store.table("agent_jobs").insert(
        {
            "id": "job",
            "org_id": "ours",
            "title": "Pilot work",
            "status": "in_progress",
            "created_at": "2026-11-03T18:00:00Z",
            "input_payload": {"project": "alpha-app"},
        }
    ).execute()

    class EventBrokenStore:
        def table(self, name):
            if name == "agent_job_events":
                raise RuntimeError("private event database detail")
            return store.table(name)

    http = app_for(
        EventBrokenStore(),
        {"org_id": "ours", "role": "viewer", "scopes": ["jobs:read"]},
    )

    response = http.get("/api/portfolio")

    assert response.status_code == 503
    assert response.json() == {"detail": "Portfolio board data is unavailable."}
    assert "private event database detail" not in response.text
