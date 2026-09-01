"""OIDC provider configuration, group mapping, and human browser sessions."""

import hashlib
import json

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from starlette.middleware.sessions import SessionMiddleware

import mco.orchestrator.identity_routes as identity_mod
import mco.orchestrator.routes as routes_mod
import mco.editions as editions_mod
from mco.localstore import LocalStore
from mco.orchestrator.auth import require_agent, verify_user_session
from mco.orchestrator.identity_routes import (
    DatabaseOIDCStateCache,
    auth_router,
    identity_admin_router,
)
from tests.test_admin_routes import ADMIN, FakeConfig

pytestmark = pytest.mark.filterwarnings("ignore")


class FakeOIDCClient:
    def __init__(self, claims):
        self.claims = claims

    async def authorize_access_token(self, request):
        return {"userinfo": self.claims}


@pytest.fixture
def ctx(tmp_path, monkeypatch):
    db = LocalStore(tmp_path / "identity.db")
    cfg = FakeConfig(
        MCO_SESSION_SECRET="session-signing-secret",
        MCO_SESSION_COOKIE_SECURE="false",
        MCO_EDITION="enterprise",
    )
    monkeypatch.setattr(identity_mod, "get_db_client", lambda: db)
    monkeypatch.setattr(identity_mod, "get_config", lambda: cfg)
    monkeypatch.setattr(editions_mod, "get_config", lambda: cfg)
    monkeypatch.setattr(routes_mod, "get_db_client", lambda: db)

    app = FastAPI()
    app.add_middleware(
        SessionMiddleware,
        secret_key="session-signing-secret",
        session_cookie="mco_oidc_state",
        https_only=False,
        same_site="lax",
    )
    app.include_router(identity_admin_router)
    app.include_router(auth_router)

    @app.post("/session-protected")
    async def session_protected(principal: dict = Depends(require_agent)):
        return {"role": principal["role"]}

    app.dependency_overrides[require_agent] = lambda: ADMIN
    client = TestClient(app)

    obj = type("Ctx", (), {})()
    obj.db, obj.cfg, obj.app, obj.http = db, cfg, app, client
    yield obj
    db.close()


def create_provider(ctx, mappings=None):
    response = ctx.http.post("/api/identity-providers", json={
        "name": "Acme Okta",
        "issuer": "https://acme.okta.com/oauth2/default",
        "client_id": "client-123",
        "client_secret": "oidc-client-secret",
        "group_mappings": mappings or {"BitCadence-Admins": "admin"},
    })
    assert response.status_code == 200, response.text
    return response.json()["provider"]


def test_provider_secret_is_encrypted_and_never_returned(ctx):
    provider = create_provider(ctx)

    assert "client_secret" not in provider
    assert "oidc-client-secret" not in ctx.http.get("/api/identity-providers").text
    secret_calls = [call for call in ctx.cfg.set_calls if call[0].startswith("MCO_SECRET_")]
    assert secret_calls and secret_calls[-1][2] is True
    row = ctx.db.table("identity_providers").select("*").eq("id", provider["id"]).execute().data[0]
    assert row["secret_ref"]
    assert "client_secret" not in row


def test_invalid_role_mapping_is_rejected_before_any_provider_or_secret_write(ctx):
    response = ctx.http.post("/api/identity-providers", json={
        "name": "Bad Mapping",
        "issuer": "https://acme.okta.com/oauth2/default",
        "client_id": "client-123",
        "client_secret": "must-not-land",
        "group_mappings": {"Everyone": "superuser"},
    })

    assert response.status_code == 400
    assert ctx.db.table("identity_providers").select("*").execute().data == []
    assert not any("must-not-land" in str(call) for call in ctx.cfg.set_calls)


@pytest.mark.parametrize("issuer", [
    "http://directory.internal",
    "https://user:pass@example.com",
    "https://example.com?redirect=evil",
])
def test_unsafe_issuer_rejected(ctx, issuer):
    response = ctx.http.post("/api/identity-providers", json={
        "name": "unsafe",
        "issuer": issuer,
        "client_id": "x",
        "client_secret": "y",
    })
    assert response.status_code == 400


def test_provider_registration_enables_pkce_and_discovery(ctx, monkeypatch):
    provider = create_provider(ctx)
    captured = {}

    class FakeOAuth:
        def __init__(self, cache=None):
            captured["cache"] = cache

        def register(self, name, **kwargs):
            captured["name"] = name
            captured["kwargs"] = kwargs

        def create_client(self, name):
            return object()

    monkeypatch.setattr(identity_mod, "OAuth", FakeOAuth)
    row = ctx.db.table("identity_providers").select("*").eq("id", provider["id"]).execute().data[0]
    identity_mod._oidc_client(row, ctx.db)

    assert captured["kwargs"]["server_metadata_url"].endswith(
        "/.well-known/openid-configuration"
    )
    assert captured["kwargs"]["client_kwargs"]["code_challenge_method"] == "S256"
    assert isinstance(captured["cache"], DatabaseOIDCStateCache)


@pytest.mark.asyncio
async def test_oidc_transaction_cache_is_server_side(ctx):
    cache = DatabaseOIDCStateCache(ctx.db)
    await cache.set("state-key", json.dumps({"code_verifier": "verifier"}), 60)

    row = ctx.db.table("oidc_transactions").select("*").execute().data[0]
    assert row["id"] == hashlib.sha256(b"state-key").hexdigest()
    assert await cache.get("state-key") == json.dumps({"code_verifier": "verifier"})

    await cache.delete("state-key")
    assert await cache.get("state-key") is None


def test_callback_provisions_mapped_user_and_revocable_session(ctx, monkeypatch):
    provider = create_provider(ctx, {"Change-Approvers": "approver"})
    monkeypatch.setattr(
        identity_mod,
        "_oidc_client",
        lambda provider_row, db: FakeOIDCClient({
            "sub": "okta-user-1",
            "email": "joe@example.com",
            "name": "Joe",
            "groups": ["Change-Approvers", "Everyone"],
        }),
    )

    callback = ctx.http.get(
        f"/api/auth/oidc/{provider['id']}/callback",
        follow_redirects=False,
    )
    assert callback.status_code == 303
    assert callback.headers["location"] == "/console"
    assert "mco_session=" in callback.headers["set-cookie"]
    assert "HttpOnly" in callback.headers["set-cookie"]
    assert "oidc-client-secret" not in callback.text

    memberships = ctx.db.table("org_memberships").select("*").execute().data
    assert memberships[0]["role"] == "approver"
    assert "jobs:approve" in memberships[0]["scopes"]
    sessions = ctx.db.table("user_sessions").select("*").execute().data
    raw_cookie = ctx.http.cookies.get("mco_session")
    assert raw_cookie
    assert sessions[0]["session_token_hash"] == hashlib.sha256(raw_cookie.encode()).hexdigest()
    assert raw_cookie not in str(sessions)

    ctx.app.dependency_overrides.pop(require_agent)
    me = ctx.http.get("/api/auth/me")
    assert me.status_code == 200
    assert me.json()["auth_method"] == "session"
    assert me.json()["role"] == "approver"

    assert ctx.http.post("/session-protected").status_code == 403
    allowed = ctx.http.post(
        "/session-protected",
        headers={"Origin": "http://testserver"},
    )
    assert allowed.status_code == 200

    logout = ctx.http.post(
        "/api/auth/logout",
        headers={"Origin": "http://testserver"},
    )
    assert logout.status_code == 200
    assert ctx.db.table("user_sessions").select("*").execute().data[0]["revoked_at"]
    assert ctx.http.get("/api/auth/me").status_code == 401


def test_unmapped_group_is_denied_without_creating_session(ctx, monkeypatch):
    provider = create_provider(ctx, {"BitCadence-Admins": "admin"})
    monkeypatch.setattr(
        identity_mod,
        "_oidc_client",
        lambda provider_row, db: FakeOIDCClient({
            "sub": "outsider",
            "email": "outside@example.com",
            "groups": ["Everyone"],
        }),
    )

    response = ctx.http.get(f"/api/auth/oidc/{provider['id']}/callback")

    assert response.status_code == 403
    assert ctx.db.table("user_sessions").select("*").execute().data == []


def test_inactive_membership_invalidates_session(ctx):
    now = identity_mod.datetime.now(identity_mod.timezone.utc)
    user = ctx.db.table("users").insert({
        "email": "off@example.com",
        "active": True,
    }).execute().data[0]
    ctx.db.table("org_memberships").insert({
        "org_id": "default",
        "user_id": user["id"],
        "role": "viewer",
        "scopes": ["jobs:read"],
        "active": False,
    }).execute()
    raw = "session-token"
    ctx.db.table("user_sessions").insert({
        "org_id": "default",
        "user_id": user["id"],
        "session_token_hash": hashlib.sha256(raw.encode()).hexdigest(),
        "expires_at": (now + identity_mod.timedelta(hours=1)).isoformat(),
    }).execute()

    assert verify_user_session(ctx.db, raw) is None
