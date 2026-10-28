"""Scoped-token RBAC, trusted-header SSO delegation, and edition gating."""

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

import mco.editions as editions_mod
import mco.orchestrator.auth as auth_mod
from mco.editions import (
    COMMUNITY,
    ENTERPRISE,
    TEAM,
    current_edition,
    edition_summary,
    has_feature,
    infer_edition,
    require_feature,
)
from mco.orchestrator.auth import (
    WORKER_DEFAULT_SCOPES,
    has_scope,
    normalize_scopes,
    require_scopes,
    resolve_scopes,
    trusted_header_agent,
    verify_token,
    hash_token,
)


class FakeConfig:
    def __init__(self, **values):
        self._values = values

    def get(self, key, default=None):
        return self._values.get(key, default)


class FakeRequest:
    """Request stub: just enough .headers for trusted_header_agent."""

    def __init__(self, headers=None):
        self.headers = {k.lower(): v for k, v in (headers or {}).items()}


# ── Scope resolution ─────────────────────────────────────────────────────────

class TestScopes:
    def test_normalize_accepts_list_and_comma_string(self):
        assert normalize_scopes(["Jobs:Read", " context:write "]) == ["context:write", "jobs:read"]
        assert normalize_scopes("jobs:read, jobs:write") == ["jobs:read", "jobs:write"]
        assert normalize_scopes(None) == []
        assert normalize_scopes("") == []

    def test_explicit_scopes_win_over_role(self):
        agent = {"role": "human", "scopes": ["jobs:read"]}
        assert resolve_scopes(agent) == ["jobs:read"]
        assert not has_scope(agent, "jobs:write")

    def test_worker_role_gets_worker_defaults(self):
        agent = {"role": "codex"}
        assert set(resolve_scopes(agent)) == set(WORKER_DEFAULT_SCOPES)
        assert has_scope(agent, "jobs:write")
        assert has_scope(agent, "context:read")
        assert not has_scope(agent, "jobs:approve")
        assert not has_scope(agent, "integrations:manage")

    def test_approver_roles_get_admin(self):
        for role in ("human", "admin", "operator"):
            agent = {"role": role}
            assert resolve_scopes(agent) == ["admin"]
            assert has_scope(agent, "jobs:approve")
            assert has_scope(agent, "anything:at-all")  # admin is the wildcard

    def test_admin_scope_is_wildcard(self):
        agent = {"role": "codex", "scopes": ["admin"]}
        assert has_scope(agent, "integrations:manage")


class TestRequireScopes:
    @pytest.mark.asyncio
    async def test_passes_when_scope_present(self):
        dep = require_scopes("jobs:read")
        agent = dep(agent={"role": "codex"})
        assert agent["role"] == "codex"

    @pytest.mark.asyncio
    async def test_403_names_missing_scopes(self):
        dep = require_scopes("jobs:approve", "integrations:manage")
        with pytest.raises(HTTPException) as exc:
            dep(agent={"role": "codex"})
        assert exc.value.status_code == 403
        assert "jobs:approve" in exc.value.detail
        assert "integrations:manage" in exc.value.detail


class TestVerifyTokenScopes:
    def test_scopes_column_flows_through_and_hash_never_leaks(self):
        tok = "mco_tok_x"

        class DB:
            def table(self, name):
                return self

            def select(self, *a, **k):
                return self

            def eq(self, col, val):
                self._hash = val
                return self

            def execute(self):
                class R:
                    data = [{
                        "instance_id": "w1", "role": "codex", "status": "online",
                        "auth_token_hash": hash_token(tok),
                        "scopes": ["jobs:read"],
                    }]
                return R()

        agent = verify_token(DB(), tok)
        assert agent["scopes"] == ["jobs:read"]
        assert "auth_token_hash" not in agent
        assert agent["org_id"] == "default"


# ── Trusted-header SSO delegation ────────────────────────────────────────────

class TestTrustedHeaderAuth:
    def _enable(self, monkeypatch, **extra):
        values = {"MCO_TRUSTED_HEADER_AUTH": "true", **extra}
        monkeypatch.setattr(auth_mod, "get_config", lambda: FakeConfig(**values))
        monkeypatch.setattr(editions_mod, "current_edition", lambda: ENTERPRISE)

    def test_disabled_by_default(self, monkeypatch):
        monkeypatch.setattr(auth_mod, "get_config", lambda: FakeConfig())
        req = FakeRequest({"X-Forwarded-User": "alice@corp.example"})
        assert trusted_header_agent(req) is None

    def test_none_without_request(self, monkeypatch):
        self._enable(monkeypatch)
        assert trusted_header_agent(None) is None

    def test_identity_from_proxy_headers(self, monkeypatch):
        self._enable(monkeypatch)
        req = FakeRequest({"X-Forwarded-User": "alice@corp.example"})
        agent = trusted_header_agent(req)
        assert agent["instance_id"] == "sso:alice@corp.example"
        assert agent["role"] == "human"  # default role
        assert agent["auth_method"] == "trusted_header"
        assert has_scope(agent, "jobs:approve")  # human -> approver -> admin

    def test_role_header_respected(self, monkeypatch):
        self._enable(monkeypatch)
        req = FakeRequest({"X-Forwarded-User": "bob", "X-Forwarded-Role": "Viewer"})
        assert trusted_header_agent(req)["role"] == "viewer"

    def test_custom_header_names(self, monkeypatch):
        self._enable(monkeypatch, MCO_TRUSTED_HEADER_USER="Cf-Access-Authenticated-User-Email")
        req = FakeRequest({"Cf-Access-Authenticated-User-Email": "carol@corp.example"})
        assert trusted_header_agent(req)["instance_id"] == "sso:carol@corp.example"

    def test_proxy_secret_must_match(self, monkeypatch):
        self._enable(monkeypatch, MCO_TRUSTED_HEADER_SECRET="s3cret")
        good = FakeRequest({"X-Forwarded-User": "alice", "X-MCO-Proxy-Secret": "s3cret"})
        bad = FakeRequest({"X-Forwarded-User": "alice", "X-MCO-Proxy-Secret": "wrong"})
        missing = FakeRequest({"X-Forwarded-User": "alice"})
        assert trusted_header_agent(good) is not None
        assert trusted_header_agent(bad) is None
        assert trusted_header_agent(missing) is None

    def test_blocked_below_enterprise_edition(self, monkeypatch):
        monkeypatch.setattr(
            auth_mod, "get_config",
            lambda: FakeConfig(MCO_TRUSTED_HEADER_AUTH="true"),
        )
        monkeypatch.setattr(editions_mod, "current_edition", lambda: COMMUNITY)
        req = FakeRequest({"X-Forwarded-User": "alice"})
        assert trusted_header_agent(req) is None

    @pytest.mark.asyncio
    async def test_require_agent_prefers_proxy_identity(self, monkeypatch):
        self._enable(monkeypatch)
        req = FakeRequest({"X-Forwarded-User": "alice"})
        agent = auth_mod.require_agent(request=req, authorization="")
        assert agent["instance_id"] == "sso:alice"


# ── Editions ─────────────────────────────────────────────────────────────────

class TestEditions:
    def _config(self, monkeypatch, **values):
        monkeypatch.setattr(editions_mod, "get_config", lambda: FakeConfig(**values))

    def test_default_is_community(self, monkeypatch):
        self._config(monkeypatch)
        assert infer_edition() == COMMUNITY
        assert current_edition() == COMMUNITY

    def test_cloud_db_infers_team(self, monkeypatch):
        self._config(monkeypatch, SUPABASE_URL="https://x.supabase.co")
        assert infer_edition() == TEAM

    def test_connector_config_infers_enterprise(self, monkeypatch):
        self._config(monkeypatch, SERVICENOW_INSTANCE_URL="https://corp.service-now.com")
        assert infer_edition() == ENTERPRISE

    def test_explicit_pin_wins(self, monkeypatch):
        self._config(monkeypatch, MCO_EDITION="community",
                     SERVICENOW_INSTANCE_URL="https://corp.service-now.com")
        assert current_edition() == COMMUNITY

    def test_feature_ordering(self, monkeypatch):
        self._config(monkeypatch)
        assert has_feature("drumline", COMMUNITY)       # Drumline in EVERY edition
        assert has_feature("drumline", ENTERPRISE)
        assert not has_feature("connectors", COMMUNITY)
        assert not has_feature("connectors", TEAM)
        assert has_feature("connectors", ENTERPRISE)
        assert has_feature("unknown_feature", COMMUNITY)  # typos never lock a surface

    @pytest.mark.asyncio
    async def test_require_feature_403_below_minimum(self, monkeypatch):
        self._config(monkeypatch, MCO_EDITION="community")
        with pytest.raises(HTTPException) as exc:
            await require_feature("connectors")()
        assert exc.value.status_code == 403
        assert "enterprise" in exc.value.detail

    def test_summary_shape(self, monkeypatch):
        self._config(monkeypatch, MCO_EDITION="team")
        summary = edition_summary()
        assert summary["edition"] == TEAM
        assert summary["source"] == "explicit"
        assert summary["features"]["drumline"]["available"] is True
        assert summary["features"]["connectors"]["available"] is False


class TestEditionGateOnRoutes:
    def test_integrations_router_403_in_community(self, monkeypatch):
        from mco.orchestrator.auth import require_agent
        from mco.orchestrator.integration_routes import integrations_router

        monkeypatch.setattr(editions_mod, "current_edition", lambda: COMMUNITY)
        app = FastAPI()
        app.include_router(integrations_router)
        app.dependency_overrides[require_agent] = lambda: {"role": "human", "instance_id": "alex"}
        resp = TestClient(app).get("/api/integrations")
        assert resp.status_code == 403
        assert "MCO_EDITION=enterprise" in resp.json()["detail"]
