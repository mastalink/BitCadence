"""
Tests for the per-token / per-IP rate limiting middleware (src/mco/ratelimit.py).

Covers:
  - under-limit requests pass (200)
  - over-limit requests are rejected with 429
  - /healthz is always exempt, even when over-limit
  - Bearer-token identity is used when present
  - IP fallback identity when no bearer token
  - MCO_RATE_LIMIT=0 disables rate limiting entirely
  - Two distinct identities have independent buckets
"""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from mco.ratelimit import (
    RateLimitMiddleware,
    RateLimitStore,
    build_rate_limit_store,
    _parse_limit,
    _extract_identity,
)
from starlette.requests import Request as StarletteRequest


# ── Helpers ───────────────────────────────────────────────────────────────────

def _app_with_limit(limit: int) -> FastAPI:
    """Build a minimal FastAPI app with rate limiting at *limit* req/min."""
    store = RateLimitStore(capacity=float(limit), refill_rate=float(limit) / 60.0)
    app = FastAPI()
    app.add_middleware(RateLimitMiddleware, store=store)

    @app.get("/healthz")
    def healthz():
        return {"status": "ok"}

    @app.get("/api/test")
    def api_test():
        return {"ok": True}

    return app


# ── Under-limit: requests pass ────────────────────────────────────────────────

class TestUnderLimit:
    def test_single_request_is_200(self):
        client = TestClient(_app_with_limit(10))
        resp = client.get("/api/test", headers={"Authorization": "Bearer tok-abc"})
        assert resp.status_code == 200

    def test_requests_up_to_limit_all_pass(self):
        limit = 5
        client = TestClient(_app_with_limit(limit))
        for i in range(limit):
            resp = client.get("/api/test", headers={"Authorization": "Bearer tok-xyz"})
            assert resp.status_code == 200, f"request {i + 1} of {limit} should pass"


# ── Over-limit: 429 returned ──────────────────────────────────────────────────

class TestOverLimit:
    def test_request_over_limit_is_429(self):
        limit = 3
        client = TestClient(_app_with_limit(limit))
        headers = {"Authorization": "Bearer tok-limited"}
        for _ in range(limit):
            client.get("/api/test", headers=headers)
        # The (limit+1)th request should be rejected.
        resp = client.get("/api/test", headers=headers)
        assert resp.status_code == 429

    def test_429_body_has_error_field(self):
        limit = 1
        client = TestClient(_app_with_limit(limit))
        headers = {"Authorization": "Bearer tok-body"}
        client.get("/api/test", headers=headers)
        resp = client.get("/api/test", headers=headers)
        assert resp.status_code == 429
        body = resp.json()
        assert body.get("error") == "rate_limit_exceeded"
        assert "detail" in body


# ── /healthz is always exempt ─────────────────────────────────────────────────

class TestHealthzExempt:
    def test_healthz_never_rate_limited(self):
        # Use a limit of 1 so every other route hits 429 immediately on 2nd call.
        limit = 1
        client = TestClient(_app_with_limit(limit))
        headers = {"Authorization": "Bearer tok-health"}
        # Exhaust the bucket.
        client.get("/api/test", headers=headers)
        assert client.get("/api/test", headers=headers).status_code == 429
        # /healthz must still return 200 no matter how many times we call it.
        for _ in range(5):
            assert client.get("/healthz", headers=headers).status_code == 200


# ── Identity: token vs IP ─────────────────────────────────────────────────────

class TestIdentityIsolation:
    def test_two_tokens_have_independent_buckets(self):
        limit = 2
        client = TestClient(_app_with_limit(limit))
        for _ in range(limit):
            r = client.get("/api/test", headers={"Authorization": "Bearer tok-A"})
            assert r.status_code == 200
        # tok-A is exhausted; tok-B should still have a full bucket.
        r = client.get("/api/test", headers={"Authorization": "Bearer tok-B"})
        assert r.status_code == 200

    def test_ip_fallback_used_when_no_bearer(self):
        # Without a bearer token the identity falls back to the remote IP.
        # Both requests from the same "IP" share a bucket.
        limit = 1
        client = TestClient(_app_with_limit(limit))
        client.get("/api/test")                      # consume the single token
        resp = client.get("/api/test")               # should be 429
        assert resp.status_code == 429


# ── Configuration: MCO_RATE_LIMIT env var ────────────────────────────────────

class TestConfig:
    def test_parse_limit_default_is_120(self, monkeypatch):
        monkeypatch.delenv("MCO_RATE_LIMIT", raising=False)
        assert _parse_limit() == 120

    def test_parse_limit_reads_env(self, monkeypatch):
        monkeypatch.setenv("MCO_RATE_LIMIT", "60")
        assert _parse_limit() == 60

    def test_parse_limit_zero_disables(self, monkeypatch):
        monkeypatch.setenv("MCO_RATE_LIMIT", "0")
        assert _parse_limit() is None

    def test_parse_limit_invalid_falls_back_to_default(self, monkeypatch):
        monkeypatch.setenv("MCO_RATE_LIMIT", "not-a-number")
        assert _parse_limit() == 120

    def test_build_store_returns_none_when_disabled(self, monkeypatch):
        monkeypatch.setenv("MCO_RATE_LIMIT", "0")
        assert build_rate_limit_store() is None

    def test_build_store_returns_store_by_default(self, monkeypatch):
        monkeypatch.delenv("MCO_RATE_LIMIT", raising=False)
        store = build_rate_limit_store()
        assert store is not None

    def test_rate_limit_zero_disables_middleware(self, monkeypatch):
        """When MCO_RATE_LIMIT=0, create_app adds no rate limiting at all."""
        monkeypatch.setenv("MCO_RATE_LIMIT", "0")
        import mco.orchestrator.routes as routes_mod
        import mco.orchestrator.integration_routes as int_routes
        import mco.orchestrator.context_routes as ctx_routes
        import mco.orchestrator.admin_routes as admin_routes
        import mco.orchestrator.metrics_routes as metrics_mod
        import mco.cli as cli_mod

        # We only need a minimal fake DB for create_app not to crash.
        class _FakeDB:
            backend = "local"
            def table(self, *a): return self
            def select(self, *a): return self
            def execute(self):
                class R: data = []
                return R()

        monkeypatch.setattr(routes_mod, "get_db_client", lambda: _FakeDB())

        app = cli_mod.create_app()
        # Verify middleware stack does NOT include RateLimitMiddleware.
        from mco.ratelimit import RateLimitMiddleware
        mw_types = [type(m) for m in app.middleware_stack.__class__.__mro__]
        # Simpler: just confirm requests all succeed without 429.
        from fastapi.testclient import TestClient
        client = TestClient(app, raise_server_exceptions=False)
        # Hit an unauthenticated route (401 is fine — no 429).
        for _ in range(200):
            resp = client.get("/api/jobs")
            assert resp.status_code != 429


# ── Bucket GC smoke test ──────────────────────────────────────────────────────

def test_gc_does_not_break_subsequent_requests():
    """Trigger GC by faking last_gc far in the past; confirm normal operation."""
    import time
    store = RateLimitStore(capacity=5.0, refill_rate=5.0 / 60.0)
    store._last_gc = time.monotonic() - 120  # force GC on next is_allowed
    assert store.is_allowed("tok:gc-test") is True
