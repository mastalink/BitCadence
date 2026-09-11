"""LLM provider connections: CRUD, key storage/masking, and the test-ping.

Uses a real LocalStore (not FakeDB, which hardcodes per-table dispatch and
doesn't know about the new llm_connections table) so inserts/queries exercise
the actual dual-backend-compatible query builder.
"""

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import mco.editions as editions_mod
import mco.orchestrator.admin_routes as admin_mod
import mco.orchestrator.routes as routes_mod
from mco.localstore import LocalStore
from mco.orchestrator import llm_connections
from mco.orchestrator.admin_routes import llm_connections_router
from mco.orchestrator.auth import require_agent

from tests.test_admin_routes import ADMIN, WORKER, FakeConfig

pytestmark = pytest.mark.filterwarnings("ignore")


@pytest.fixture
def ctx(tmp_path, monkeypatch):
    db = LocalStore(tmp_path / "test.db")
    cfg = FakeConfig()
    monkeypatch.setattr(routes_mod, "get_db_client", lambda: db)
    monkeypatch.setattr(admin_mod, "get_config", lambda: cfg)
    monkeypatch.setattr(editions_mod, "get_config", lambda: cfg)

    app = FastAPI()
    app.include_router(llm_connections_router)
    app.dependency_overrides[require_agent] = lambda: ADMIN

    obj = type("Ctx", (), {})()
    obj.db, obj.cfg, obj.app = db, cfg, app
    obj.http = TestClient(app)
    yield obj
    db.close()


def _as(ctx, agent):
    ctx.app.dependency_overrides[require_agent] = lambda: agent


class TestProviders:
    def test_lists_known_providers(self, ctx):
        body = ctx.http.get("/api/llm-connections/providers").json()
        assert set(body) == {"anthropic", "openai", "gemini", "custom"}
        assert body["custom"]["base_url_editable"] is True
        assert body["anthropic"]["base_url_editable"] is False


class TestCreate:
    def test_create_persists_metadata_and_key_separately(self, ctx):
        resp = ctx.http.post("/api/llm-connections", json={
            "name": "prod-anthropic", "provider": "anthropic",
            "model": "claude-opus-4", "api_key": "sk-ant-secret123",
        })
        assert resp.status_code == 200
        conn = resp.json()["connection"]
        assert conn["name"] == "prod-anthropic"
        assert conn["provider"] == "anthropic"
        assert conn["key_set"] is True
        assert "api_key" not in conn  # never echoed

        # The key lives in config, not the table row.
        row = ctx.db.table("llm_connections").select("*").eq("id", conn["id"]).execute().data[0]
        assert "api_key" not in row
        assert ctx.cfg.get(llm_connections.config_key_for(conn["id"])) == "sk-ant-secret123"
        assert ctx.cfg.set_calls[-1] == (
            llm_connections.config_key_for(conn["id"]),
            "sk-ant-secret123",
            True,
        )

    def test_locked_local_vault_returns_503_and_removes_metadata(self, ctx):
        def locked_set(key, value, encrypt=False):
            raise RuntimeError("locked")

        ctx.cfg.set = locked_set
        resp = ctx.http.post("/api/llm-connections", json={
            "name": "cannot-save", "provider": "openai", "api_key": "sk-secret",
        })

        assert resp.status_code == 503
        assert ctx.db.table("llm_connections").select("*").execute().data == []

    def test_list_never_echoes_key(self, ctx):
        ctx.http.post("/api/llm-connections", json={
            "name": "c1", "provider": "openai", "api_key": "sk-openai-abc"})
        body = ctx.http.get("/api/llm-connections").json()
        assert len(body) == 1
        assert body[0]["key_set"] is True
        assert "sk-openai-abc" not in ctx.http.get("/api/llm-connections").text

    def test_custom_provider_requires_base_url(self, ctx):
        resp = ctx.http.post("/api/llm-connections", json={"name": "c1", "provider": "custom"})
        assert resp.status_code == 400

    def test_custom_provider_base_url_accepted(self, ctx):
        resp = ctx.http.post("/api/llm-connections", json={
            "name": "local-ollama", "provider": "custom",
            "base_url": "http://localhost:11434/v1"})
        assert resp.status_code == 200
        assert resp.json()["connection"]["base_url"] == "http://localhost:11434/v1"

    def test_builtin_provider_ignores_client_supplied_base_url(self, ctx):
        """SSRF guard: a client can't redirect a built-in provider's requests
        by supplying its own base_url alongside provider=anthropic."""
        resp = ctx.http.post("/api/llm-connections", json={
            "name": "c1", "provider": "anthropic",
            "base_url": "http://169.254.169.254/latest/meta-data/"})
        assert resp.status_code == 200
        assert resp.json()["connection"]["base_url"] is None

    def test_unknown_provider_rejected(self, ctx):
        resp = ctx.http.post("/api/llm-connections", json={"name": "c1", "provider": "made-up"})
        assert resp.status_code == 400

    def test_unsafe_name_rejected(self, ctx):
        resp = ctx.http.post("/api/llm-connections", json={
            "name": "x' onmouseover='alert(1)", "provider": "openai"})
        assert resp.status_code == 400

    def test_worker_token_forbidden(self, ctx):
        _as(ctx, WORKER)
        resp = ctx.http.post("/api/llm-connections", json={"name": "c1", "provider": "openai"})
        assert resp.status_code == 403


class TestUpdateDelete:
    def _create(self, ctx, **overrides):
        payload = {"name": "c1", "provider": "openai", "api_key": "sk-1"}
        payload.update(overrides)
        return ctx.http.post("/api/llm-connections", json=payload).json()["connection"]

    def test_rotate_key(self, ctx):
        conn = self._create(ctx)
        ctx.http.patch(f"/api/llm-connections/{conn['id']}", json={"api_key": "sk-2"})
        assert ctx.cfg.get(llm_connections.config_key_for(conn["id"])) == "sk-2"

    def test_blank_key_leaves_existing_key_untouched(self, ctx):
        conn = self._create(ctx)
        ctx.http.patch(f"/api/llm-connections/{conn['id']}", json={"model": "gpt-5"})
        assert ctx.cfg.get(llm_connections.config_key_for(conn["id"])) == "sk-1"

    def test_delete_removes_row_and_key(self, ctx):
        conn = self._create(ctx)
        resp = ctx.http.delete(f"/api/llm-connections/{conn['id']}")
        assert resp.status_code == 200
        assert ctx.http.get("/api/llm-connections").json() == []
        assert ctx.cfg.get(llm_connections.config_key_for(conn["id"])) is None

    def test_unknown_id_404(self, ctx):
        assert ctx.http.patch("/api/llm-connections/ghost", json={"model": "x"}).status_code == 404
        assert ctx.http.delete("/api/llm-connections/ghost").status_code == 404


class TestTenantIsolation:
    def test_org_admin_cannot_see_default_org_connection(self, ctx):
        ctx.http.post("/api/llm-connections", json={"name": "c1", "provider": "openai"})
        ORG_ADMIN = {"instance_id": "acme-admin", "role": "admin", "status": "online", "org_id": "acme"}
        _as(ctx, ORG_ADMIN)
        assert ctx.http.get("/api/llm-connections").json() == []


class TestConnectionPing:
    def test_ok_response(self):
        def handler(request):
            return httpx.Response(200, json={"data": []})
        transport = httpx.MockTransport(handler)
        result = llm_connections.test_connection("openai", "sk-x", transport=transport)
        assert result["ok"] is True

    def test_auth_rejected(self):
        def handler(request):
            return httpx.Response(401, json={"error": "invalid key"})
        transport = httpx.MockTransport(handler)
        result = llm_connections.test_connection("anthropic", "bad-key", transport=transport)
        assert result["ok"] is False
        assert "401" in result["detail"]

    def test_missing_key(self):
        result = llm_connections.test_connection("openai", "")
        assert result["ok"] is False

    def test_unknown_provider(self):
        result = llm_connections.test_connection("made-up", "key")
        assert result["ok"] is False

    def test_custom_without_base_url(self):
        result = llm_connections.test_connection("custom", "key")
        assert result["ok"] is False
        assert "base_url" in result["detail"]

    def test_anthropic_sends_x_api_key_header(self):
        seen = {}
        def handler(request):
            seen["headers"] = dict(request.headers)
            return httpx.Response(200, json={})
        transport = httpx.MockTransport(handler)
        llm_connections.test_connection("anthropic", "sk-ant-x", transport=transport)
        assert seen["headers"].get("x-api-key") == "sk-ant-x"

    def test_gemini_sends_key_as_query_param(self):
        seen = {}
        def handler(request):
            seen["url"] = str(request.url)
            return httpx.Response(200, json={})
        transport = httpx.MockTransport(handler)
        llm_connections.test_connection("gemini", "AIza-secret", transport=transport)
        assert "key=AIza-secret" in seen["url"]


class TestApiEndpointCallsTestConnection:
    def test_test_endpoint_returns_ping_result(self, ctx, monkeypatch):
        conn = ctx.http.post("/api/llm-connections", json={
            "name": "c1", "provider": "openai", "api_key": "sk-1"}).json()["connection"]

        def fake_test(provider, api_key, base_url=None):
            assert provider == "openai" and api_key == "sk-1"
            return {"ok": True, "detail": "Connection OK", "latency_ms": 42}
        monkeypatch.setattr(admin_mod.llm_connections, "test_connection", fake_test)

        resp = ctx.http.post(f"/api/llm-connections/{conn['id']}/test")
        assert resp.status_code == 200
        assert resp.json()["ok"] is True


class TestSharedDatabaseVaultRoute:
    def test_key_is_shared_as_ciphertext_and_resolves_server_side(self, ctx, monkeypatch):
        import base64

        ctx.cfg.values["MCO_SECRET_VAULT_BACKEND"] = "database"
        ctx.cfg.values["MCO_VAULT_MASTER_KEY"] = base64.urlsafe_b64encode(b"K" * 32).decode()
        conn = ctx.http.post("/api/llm-connections", json={
            "name": "shared-openai",
            "provider": "openai",
            "api_key": "sk-shared-secret",
        }).json()["connection"]

        records = ctx.db.table("secret_records").select("*").execute().data
        assert len(records) == 1
        assert "sk-shared-secret" not in str(records)
        assert ctx.http.get("/api/llm-connections").json()[0]["key_set"] is True

        def fake_test(provider, api_key, base_url=None):
            assert provider == "openai"
            assert api_key == "sk-shared-secret"
            return {"ok": True, "detail": "Connection OK", "latency_ms": 1}

        monkeypatch.setattr(admin_mod.llm_connections, "test_connection", fake_test)
        assert ctx.http.post(f"/api/llm-connections/{conn['id']}/test").json()["ok"] is True
