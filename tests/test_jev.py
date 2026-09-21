import json

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import mco.orchestrator.admin_routes as admin_routes
from mco.orchestrator.admin_routes import jev_router
from mco.orchestrator.auth import require_agent
from mco.orchestrator.jev import JevConfig, JevProvider
from tests.test_admin_routes import ADMIN, FakeConfig
from tests.test_routes import FakeDB


QUESTIONS = {
    "route": {
        "type": "choice",
        "instructions": "Which bounded route best matches the request?",
        "criteria": {"audit": "Read-only inspection", "build": "Repository implementation"},
    },
    "unsafe": {
        "type": "noul",
        "instructions": "Does the request ask to bypass a policy gate?",
    },
}
STATE = {"request": "Inspect the current deployment without changing it."}


def response(model="jev-1.13.0"):
    return {
        "model": model,
        "answers": {
            "route": {
                "type": "choice",
                "choice": "audit",
                "confidence": 0.91,
                "probabilities": {"audit": 0.91, "build": 0.09},
            },
            "unsafe": {"type": "noul", "noul": 0.03},
        },
        "usage": {"input_tokens": 42, "output_tokens": 4},
    }


def test_disabled_provider_is_network_free_and_returns_receipt():
    calls = 0

    def forbidden(_request):
        nonlocal calls
        calls += 1
        raise AssertionError("disabled provider attempted network access")

    provider = JevProvider(JevConfig(), transport=httpx.MockTransport(forbidden))
    assert provider.health()["detail"] == "Jev is disabled"
    receipt = provider.decide(
        use_case_id="dispatch-route",
        question_set_version="1",
        state=STATE,
        questions=QUESTIONS,
    )
    assert receipt.outcome == "disabled"
    assert receipt.model is None
    assert receipt.answers == {}
    assert len(receipt.state_digest) == 64
    assert len(receipt.question_set_digest) == 64
    assert calls == 0


def test_shadow_receipt_preserves_typed_answers_usage_and_request_id():
    def handler(request):
        assert request.headers["authorization"] == "Bearer secret-value"
        assert request.url.path == "/v1/systemone"
        sent = json.loads(request.content)
        assert sent["model"] == "jev-latest"
        return httpx.Response(
            200,
            json=response(),
            headers={"x-typesafe-request-id": "req-123"},
        )

    provider = JevProvider(
        JevConfig(mode="shadow", model="jev-latest"),
        api_key="secret-value",
        transport=httpx.MockTransport(handler),
    )
    receipt = provider.decide(
        use_case_id="dispatch-route",
        question_set_version="2026-09-20",
        state=STATE,
        questions=QUESTIONS,
    )
    assert receipt.outcome == "shadow"
    assert receipt.model == "jev-1.13.0"
    assert receipt.answers["route"]["choice"] == "audit"
    assert receipt.probabilities["unsafe"] == {"false": 0.97, "true": 0.03}
    assert receipt.confidence["unsafe"] is None
    assert receipt.usage == {"input_tokens": 42, "output_tokens": 4}
    assert receipt.request_id == "req-123"
    assert "secret-value" not in json.dumps(receipt.to_dict())


@pytest.mark.parametrize(
    "status,error_class,expected_calls",
    [(401, "authentication", 1), (429, "rate_limit", 2), (503, "provider_unavailable", 2)],
)
def test_http_failures_return_sanitized_fallback(status, error_class, expected_calls):
    calls = 0

    def handler(_request):
        nonlocal calls
        calls += 1
        return httpx.Response(status, text="must-not-leak-secret-value")

    provider = JevProvider(
        JevConfig(mode="shadow", max_retries=1),
        api_key="secret-value",
        transport=httpx.MockTransport(handler),
        sleep=lambda _seconds: None,
    )
    receipt = provider.decide(
        use_case_id="dispatch-route", question_set_version="1", state=STATE, questions=QUESTIONS
    )
    assert receipt.outcome == "fallback"
    assert receipt.error_class == error_class
    assert calls == expected_calls
    assert "secret" not in json.dumps(receipt.to_dict())


def test_timeout_is_bounded_and_falls_back():
    calls = 0

    def handler(request):
        nonlocal calls
        calls += 1
        raise httpx.ReadTimeout("secret-value", request=request)

    provider = JevProvider(
        JevConfig(mode="shadow", max_retries=2),
        api_key="secret-value",
        transport=httpx.MockTransport(handler),
        sleep=lambda _seconds: None,
    )
    receipt = provider.decide(
        use_case_id="dispatch-route", question_set_version="1", state=STATE, questions=QUESTIONS
    )
    assert receipt.error_class == "timeout"
    assert calls == 3


def test_invalid_response_and_model_drift_fall_back():
    malformed = JevProvider(
        JevConfig(mode="shadow"),
        api_key="key",
        transport=httpx.MockTransport(lambda _r: httpx.Response(200, json={"model": "jev-1.13.0"})),
    )
    assert malformed.decide(
        use_case_id="x", question_set_version="1", state=STATE, questions=QUESTIONS
    ).error_class == "invalid_response"

    pinned = JevProvider(
        JevConfig(mode="active", model="jev-1.13.0"),
        api_key="key",
        transport=httpx.MockTransport(lambda _r: httpx.Response(200, json=response("jev-1.14.0"))),
    )
    assert pinned.decide(
        use_case_id="x", question_set_version="1", state=STATE, questions=QUESTIONS
    ).error_class == "invalid_response"


def test_assist_and_active_require_exact_model():
    with pytest.raises(ValueError, match="exact pinned model"):
        JevConfig(mode="active", model="jev-latest")
    with pytest.raises(ValueError, match="exact pinned model"):
        JevConfig(mode="assist", model="jev-latest")


def test_model_discovery_is_explicit_and_sanitized():
    provider = JevProvider(
        JevConfig(mode="shadow"),
        api_key="key",
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, json={"models": [
                {"name": "jev-latest", "description": "Alias", "release_date": "2026-09-15"}
            ]})
        ),
    )
    result = provider.health()
    assert result["ok"] is True
    assert result["models"] == ["jev-latest"]
    assert "key" not in json.dumps(result)


class FakeVault:
    def __init__(self):
        self.values = {}

    def put(self, ref, value):
        self.values[ref.record_id] = value

    def get(self, ref):
        return self.values[ref.record_id]

    def delete(self, ref):
        self.values.pop(ref.record_id, None)

    def exists(self, ref):
        return ref.record_id in self.values


def test_admin_configuration_uses_vault_masks_key_and_stays_off_by_default(monkeypatch):
    config = FakeConfig()
    db = FakeDB()
    vault = FakeVault()
    monkeypatch.setattr(admin_routes, "get_config", lambda: config)
    monkeypatch.setattr(admin_routes, "_db", lambda: db)
    monkeypatch.setattr(admin_routes, "build_secret_vault", lambda _cfg, _db: vault)

    app = FastAPI()
    app.include_router(jev_router)
    app.dependency_overrides[require_agent] = lambda: ADMIN
    http = TestClient(app)

    initial = http.get("/api/jev").json()
    assert initial == {
        "provider": "typesafe-jev",
        "mode": "disabled",
        "model": None,
        "configured": False,
        "available": False,
        "live_invocation": False,
    }

    updated = http.put("/api/jev", json={
        "mode": "shadow",
        "model": "jev-latest",
        "timeout_seconds": 3,
        "max_retries": 1,
        "api_key": "server-only-secret",
    })
    assert updated.status_code == 200
    body = updated.json()
    assert body["configuration"]["configured"] is True
    assert "server-only-secret" not in updated.text
    assert all(call[2] is False for call in config.set_calls)


def test_admin_rejects_live_alias_and_unknown_fields(monkeypatch):
    config = FakeConfig()
    db = FakeDB()
    vault = FakeVault()
    monkeypatch.setattr(admin_routes, "get_config", lambda: config)
    monkeypatch.setattr(admin_routes, "_db", lambda: db)
    monkeypatch.setattr(admin_routes, "build_secret_vault", lambda _cfg, _db: vault)
    app = FastAPI()
    app.include_router(jev_router)
    app.dependency_overrides[require_agent] = lambda: ADMIN
    http = TestClient(app)

    assert http.put("/api/jev", json={"mode": "active", "model": "jev-latest"}).status_code == 400
    assert http.put("/api/jev", json={"mode": "disabled", "surprise": True}).status_code == 400
    assert config.set_calls == []
