"""Explicit-ID job creation is safe to retry across lost responses and races."""

import asyncio
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import mco.orchestrator.routes as routes
from mco.localstore import LocalStore
from mco.orchestrator.auth import require_agent


OPERATION_ID = "7c5b5858-64be-4a34-8f21-3e6ab2135df8"
AGENT = {
    "instance_id": "via-dispatcher",
    "role": "operator",
    "status": "online",
    "org_id": "via",
}
PAYLOAD = {
    "id": OPERATION_ID,
    "title": "Refresh Lorain bulletins",
    "description": "Collect and retain source evidence",
    "target_agent_role": "collector",
    "depends_on": [],
    "input_payload": {"county": "Lorain", "state": "OH"},
    "max_retries": 2,
    "priority": 80,
}


@pytest.fixture
def idempotent_api(monkeypatch, tmp_path):
    store = LocalStore(tmp_path / "mco.db")
    monkeypatch.setattr(routes, "get_db_client", lambda: store)
    monkeypatch.setattr(routes, "notify_job_created", lambda **_kw: None)
    monkeypatch.setattr(routes, "notify_job_needs_approval", lambda **_kw: None)
    broadcast = AsyncMock()
    monkeypatch.setattr(routes, "_broadcast_callback", broadcast)
    app = FastAPI()
    app.include_router(routes.router)
    app.dependency_overrides[require_agent] = lambda: AGENT
    with TestClient(app) as http:
        yield http, store, broadcast, app
    store.close()


def _rows(store, table):
    return store.table(table).select("*").execute().data


def test_explicit_uuid_is_persisted_and_identical_replay_returns_existing_job(idempotent_api):
    http, store, broadcast, _app = idempotent_api

    first = http.post("/api/jobs", json=PAYLOAD)
    replay = http.post("/api/jobs", json=PAYLOAD)

    assert first.status_code == 200
    assert replay.status_code == 200
    assert first.json()["job"]["id"] == OPERATION_ID
    assert replay.json()["job"] == first.json()["job"]
    assert len(_rows(store, "agent_jobs")) == 1
    assert len([e for e in _rows(store, "agent_job_events") if e["event"] == "created"]) == 1
    assert broadcast.await_count == 1


def test_replay_with_changed_immutable_intent_is_rejected(idempotent_api):
    http, store, broadcast, _app = idempotent_api
    assert http.post("/api/jobs", json=PAYLOAD).status_code == 200

    changed = {**PAYLOAD, "input_payload": {"county": "Cuyahoga", "state": "OH"}}
    response = http.post("/api/jobs", json=changed)

    assert response.status_code == 409
    assert response.json() == {"detail": "Job ID is already in use"}
    assert len(_rows(store, "agent_jobs")) == 1
    assert broadcast.await_count == 1


@pytest.mark.parametrize(
    "other_agent",
    [
        {**AGENT, "instance_id": "other-via-dispatcher"},
        {**AGENT, "org_id": "another-tenant"},
    ],
    ids=["sender", "tenant"],
)
def test_job_id_collision_does_not_reveal_or_mutate_another_owners_job(
    idempotent_api, other_agent
):
    http, store, broadcast, app = idempotent_api
    assert http.post("/api/jobs", json=PAYLOAD).status_code == 200
    original = _rows(store, "agent_jobs")[0]
    app.dependency_overrides[require_agent] = lambda: other_agent

    response = http.post("/api/jobs", json=PAYLOAD)

    assert response.status_code == 409
    assert response.json() == {"detail": "Job ID is already in use"}
    assert _rows(store, "agent_jobs") == [original]
    assert broadcast.await_count == 1


def test_retry_after_create_response_is_lost_does_not_repeat_side_effects(idempotent_api):
    http, store, broadcast, _app = idempotent_api
    http.post("/api/jobs", json=PAYLOAD)  # caller loses this response

    recovered = http.post("/api/jobs", json=PAYLOAD)

    assert recovered.status_code == 200
    assert recovered.json()["job"]["id"] == OPERATION_ID
    assert len([e for e in _rows(store, "agent_job_events") if e["event"] == "created"]) == 1
    assert broadcast.await_count == 1


def test_capabilities_advertise_explicit_id_before_dynamic_job_route(idempotent_api):
    http, _store, _broadcast, _app = idempotent_api

    response = http.get("/api/jobs/capabilities")

    assert response.status_code == 200
    assert response.json() == {"create_with_id": 1}


def test_capabilities_require_authentication(monkeypatch, tmp_path):
    store = LocalStore(tmp_path / "mco.db")
    monkeypatch.setattr(routes, "get_db_client", lambda: store)
    app = FastAPI()
    app.include_router(routes.router)
    with TestClient(app) as http:
        response = http.get("/api/jobs/capabilities")
    store.close()

    assert response.status_code == 401


def test_creator_can_reconcile_single_job_by_id(idempotent_api):
    http, _store, _broadcast, _app = idempotent_api
    assert http.post("/api/jobs", json=PAYLOAD).status_code == 200

    response = http.get(f"/api/jobs/{OPERATION_ID}")

    assert response.status_code == 200
    assert response.json()["id"] == OPERATION_ID
    assert response.json()["input_payload"] == PAYLOAD["input_payload"]


@pytest.mark.parametrize(
    "other_agent",
    [
        {**AGENT, "instance_id": "same-org-stranger", "role": "observer"},
        {**AGENT, "org_id": "another-tenant"},
    ],
    ids=["unrelated-sender", "tenant"],
)
def test_single_job_reconciliation_hides_jobs_from_unrelated_callers(
    idempotent_api, other_agent
):
    http, _store, _broadcast, app = idempotent_api
    assert http.post("/api/jobs", json=PAYLOAD).status_code == 200
    app.dependency_overrides[require_agent] = lambda: other_agent

    response = http.get(f"/api/jobs/{OPERATION_ID}")

    assert response.status_code == 404
    assert response.json() == {"detail": "Job not found"}


def test_single_job_reconciliation_returns_same_404_when_absent(idempotent_api):
    http, _store, _broadcast, _app = idempotent_api

    response = http.get("/api/jobs/aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")

    assert response.status_code == 404
    assert response.json() == {"detail": "Job not found"}


def test_concurrent_identical_creates_produce_one_job_and_one_broadcast(monkeypatch, tmp_path):
    store = LocalStore(tmp_path / "mco.db")
    monkeypatch.setattr(routes, "get_db_client", lambda: store)
    monkeypatch.setattr(routes, "notify_job_created", lambda **_kw: None)
    monkeypatch.setattr(routes, "_broadcast_callback", AsyncMock())

    def create():
        return asyncio.run(routes.create_job(dict(PAYLOAD), dict(AGENT)))

    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(lambda _index: create(), range(2)))

    assert [r["job"]["id"] for r in responses] == [OPERATION_ID, OPERATION_ID]
    assert len(_rows(store, "agent_jobs")) == 1
    assert len([e for e in _rows(store, "agent_job_events") if e["event"] == "created"]) == 1
    assert routes._broadcast_callback.await_count == 1
    store.close()


def test_non_uuid_explicit_id_is_rejected_without_creating_work(idempotent_api):
    http, store, broadcast, _app = idempotent_api

    response = http.post("/api/jobs", json={**PAYLOAD, "id": "via-operation-42"})

    assert response.status_code == 400
    assert _rows(store, "agent_jobs") == []
    assert broadcast.await_count == 0


def test_legacy_creation_without_id_still_creates_distinct_jobs(idempotent_api):
    http, store, broadcast, _app = idempotent_api
    payload = {key: value for key, value in PAYLOAD.items() if key != "id"}
    first = http.post("/api/jobs", json=payload).json()["job"]
    second = http.post("/api/jobs", json=payload).json()["job"]
    assert first["id"] != second["id"]
    assert len(_rows(store, "agent_jobs")) == 2
    assert broadcast.await_count == 2


def test_replay_preserves_progress_and_does_not_requeue_completed_job(idempotent_api):
    http, store, broadcast, _app = idempotent_api
    assert http.post("/api/jobs", json=PAYLOAD).status_code == 200
    store.table("agent_jobs").update({"status": "completed", "output_payload": {"receipt": "retained"}}).eq("id", OPERATION_ID).execute()
    replay = http.post("/api/jobs", json=PAYLOAD)
    assert replay.status_code == 200
    assert replay.json()["job"]["status"] == "completed"
    assert replay.json()["job"]["output_payload"] == {"receipt": "retained"}
    assert broadcast.await_count == 1


@pytest.mark.parametrize("instance,expected", [("collector-a", 200), ("collector-b", 404)])
def test_single_job_read_respects_explicit_addressee(idempotent_api, instance, expected):
    http, _store, _broadcast, app = idempotent_api
    assert http.post("/api/jobs", json={**PAYLOAD, "target_agent_id": "collector-a"}).status_code == 200
    app.dependency_overrides[require_agent] = lambda: {**AGENT, "instance_id": instance, "role": "collector"}
    assert http.get(f"/api/jobs/{OPERATION_ID}").status_code == expected
