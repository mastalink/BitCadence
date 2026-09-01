"""Enterprise human identity: OIDC federation, role mapping, and sessions.

OIDC providers share one Adapter contract through Authlib. Provider secrets
stay behind SecretVault, authorization transaction state stays server-side,
and the browser receives only signed transaction markers plus an opaque
session credential after successful login.
"""

from __future__ import annotations

import hashlib
import json
import re
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlparse

from authlib.integrations.starlette_client import OAuth, OAuthError
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse

from mco.config import get_config
from mco.editions import require_feature
from mco.orchestrator.auth import (
    KNOWN_SCOPES,
    enforce_session_csrf,
    require_agent,
    require_scopes,
)
from mco.orchestrator.routes import get_db_client
from mco.secret_vault import SecretRef, VaultError, build_secret_vault

_enterprise_identity = [Depends(require_feature("identity_federation"))]
identity_admin_router = APIRouter(
    prefix="/api/identity-providers",
    dependencies=_enterprise_identity,
)
auth_router = APIRouter(prefix="/api/auth", dependencies=_enterprise_identity)

BUILTIN_ROLE_SCOPES = {
    "owner": {"admin"},
    "admin": {"admin"},
    "operator": {
        "jobs:read", "jobs:write", "jobs:approve",
        "context:read", "context:write", "agents:read",
        "integrations:read", "integrations:manage",
    },
    "approver": {
        "jobs:read", "jobs:write", "jobs:approve",
        "context:read", "agents:read", "integrations:read",
    },
    "integration_manager": {
        "jobs:read", "jobs:write", "context:read", "agents:read",
        "integrations:read", "integrations:manage",
    },
    "worker": {
        "jobs:read", "jobs:write", "context:read", "context:write",
        "agents:read", "integrations:read",
    },
    "auditor": {"jobs:read", "context:read", "agents:read", "integrations:read"},
    "viewer": {"jobs:read", "agents:read"},
}
ROLE_PRIORITY = tuple(BUILTIN_ROLE_SCOPES)


def _db():
    db = get_db_client()
    if db is None:
        raise HTTPException(status_code=503, detail="Identity federation requires a database")
    return db


def _org(principal: dict) -> str:
    return principal.get("org_id") or "default"


def _provider_secret_ref(provider: dict) -> SecretRef:
    return SecretRef(
        org_id=provider.get("org_id") or "default",
        scope=provider["id"],
        name="oidc_client_secret",
    )


def _validate_issuer(issuer: str) -> str:
    value = str(issuer or "").strip().rstrip("/")
    parsed = urlparse(value)
    local = parsed.hostname in {"localhost", "127.0.0.1", "::1"}
    if (
        not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or (parsed.scheme != "https" and not (local and parsed.scheme == "http"))
    ):
        raise HTTPException(
            status_code=400,
            detail="OIDC issuer must be an HTTPS origin/tenant path without credentials, query, or fragment",
        )
    return value


def _provider_public(row: dict) -> dict:
    config = row.get("config") or {}
    return {
        "id": row.get("id"),
        "org_id": row.get("org_id") or "default",
        "name": row.get("name"),
        "protocol": row.get("protocol"),
        "issuer": row.get("issuer"),
        "client_id": row.get("client_id"),
        "enabled": bool(row.get("enabled", True)),
        "jit_enabled": bool(row.get("jit_enabled", True)),
        "group_claim": config.get("group_claim", "groups"),
        "scopes": config.get("scopes", "openid email profile groups"),
        "created_at": row.get("created_at"),
    }


def _get_provider(db: Any, provider_id: str, org_id: str | None = None) -> dict:
    result = db.table("identity_providers").select("*").eq("id", provider_id).execute()
    rows = result.data or []
    if not rows:
        raise HTTPException(status_code=404, detail="Identity provider not found")
    row = rows[0]
    if org_id is not None and (row.get("org_id") or "default") != org_id:
        raise HTTPException(status_code=404, detail="Identity provider not found")
    return row


def _mapping_payload(provider_id: str, org_id: str, external_group: str, role: str) -> dict:
    normalized_role = str(role or "").strip().lower()
    group = str(external_group or "").strip()
    if not group:
        raise HTTPException(status_code=400, detail="External group is required")
    if normalized_role not in BUILTIN_ROLE_SCOPES:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown role '{normalized_role}'. Valid: {', '.join(BUILTIN_ROLE_SCOPES)}",
        )
    # FROZEN WIRE CONSTANT - do not rebrand. This namespace fixes the
    # deterministic id of a role mapping; changing it makes every stored
    # mapping recompute to a new id, so saves duplicate instead of update and
    # an SSO user can log in without the role their group is meant to grant.
    mapping_id = str(uuid.uuid5(
        uuid.NAMESPACE_URL,
        f"batoncadence:role-mapping:{provider_id}:{group.casefold()}",
    ))
    return {
        "id": mapping_id,
        "org_id": org_id,
        "identity_provider_id": provider_id,
        "external_group": group,
        "role": normalized_role,
        "scopes": sorted(BUILTIN_ROLE_SCOPES[normalized_role]),
    }


@identity_admin_router.get("")
async def list_identity_providers(caller: dict = Depends(require_scopes("admin"))):
    result = _db().table("identity_providers").select("*").execute()
    return [
        _provider_public(row)
        for row in (result.data or [])
        if (row.get("org_id") or "default") == _org(caller)
    ]


@identity_admin_router.post("")
async def create_identity_provider(
    payload: dict,
    caller: dict = Depends(require_scopes("admin")),
):
    name = str(payload.get("name") or "").strip()
    issuer = _validate_issuer(payload.get("issuer"))
    client_id = str(payload.get("client_id") or "").strip()
    client_secret = str(payload.get("client_secret") or "").strip()
    if not name or not client_id or not client_secret:
        raise HTTPException(status_code=400, detail="name, issuer, client_id, and client_secret are required")
    if not re.fullmatch(r"[A-Za-z0-9._ :/-]{1,100}", name):
        raise HTTPException(status_code=400, detail="Identity Provider name contains unsupported characters")
    group_claim = str(payload.get("group_claim") or "groups").strip()
    if not re.fullmatch(r"[A-Za-z0-9._:-]{1,100}", group_claim):
        raise HTTPException(status_code=400, detail="group_claim contains unsupported characters")
    requested_scopes = {
        part for part in str(payload.get("scopes") or "openid email profile groups").split()
        if part
    }
    requested_scopes.update({"openid", "email"})

    db = _db()
    org_id = _org(caller)
    provider_id = str(uuid.uuid4())
    raw_mappings = payload.get("group_mappings") or {}
    if not isinstance(raw_mappings, dict):
        raise HTTPException(status_code=400, detail="group_mappings must be an object")
    mapping_rows = [
        _mapping_payload(provider_id, org_id, group, role)
        for group, role in raw_mappings.items()
    ]
    row = {
        "id": provider_id,
        "org_id": org_id,
        "name": name,
        "protocol": "oidc",
        "issuer": issuer,
        "client_id": client_id,
        "enabled": True,
        "jit_enabled": bool(payload.get("jit_enabled", True)),
        "config": {
            "group_claim": group_claim,
            "scopes": " ".join(sorted(requested_scopes)),
        },
    }
    inserted = db.table("identity_providers").insert(row).execute()
    if not inserted.data:
        raise HTTPException(status_code=500, detail="Failed to create identity provider")
    secret_ref = _provider_secret_ref(row)
    vault = None
    try:
        vault = build_secret_vault(get_config(), db)
        vault.put(secret_ref, client_secret)
        db.table("identity_providers").update({
            "secret_ref": secret_ref.record_id,
        }).eq("id", provider_id).execute()
        for mapping in mapping_rows:
            db.table("role_mappings").upsert(mapping).execute()
    except Exception as exc:
        if vault is not None:
            try:
                vault.delete(secret_ref)
            except Exception:
                pass
        db.table("role_mappings").delete().eq("identity_provider_id", provider_id).execute()
        db.table("identity_providers").delete().eq("id", provider_id).execute()
        if isinstance(exc, HTTPException):
            raise
        status = 503 if isinstance(exc, VaultError) else 500
        raise HTTPException(status_code=status, detail=str(exc)) from exc
    return {"success": True, "provider": _provider_public(row)}


@identity_admin_router.put("/{provider_id}/role-mappings")
async def replace_role_mappings(
    provider_id: str,
    payload: dict,
    caller: dict = Depends(require_scopes("admin")),
):
    db = _db()
    org_id = _org(caller)
    _get_provider(db, provider_id, org_id)
    mappings = payload.get("group_mappings")
    if not isinstance(mappings, dict) or not mappings:
        raise HTTPException(status_code=400, detail="group_mappings must be a non-empty object")
    rows = [_mapping_payload(provider_id, org_id, group, role) for group, role in mappings.items()]
    db.table("role_mappings").delete().eq("identity_provider_id", provider_id).execute()
    for row in rows:
        db.table("role_mappings").insert(row).execute()
    return {"success": True, "mappings": rows}


class DatabaseOIDCStateCache:
    """Authlib cache Adapter keeping PKCE verifier and nonce off the browser."""

    def __init__(self, db: Any):
        self._db = db

    @staticmethod
    def _id(key: str) -> str:
        return hashlib.sha256(str(key).encode("utf-8")).hexdigest()

    async def set(self, key: str, value: str, expires_in: int) -> None:
        expires_at = (datetime.now(timezone.utc) + timedelta(seconds=int(expires_in))).isoformat()
        self._db.table("oidc_transactions").upsert({
            "id": self._id(key),
            "value": value,
            "expires_at": expires_at,
        }).execute()

    async def get(self, key: str):
        result = (
            self._db.table("oidc_transactions")
            .select("*")
            .eq("id", self._id(key))
            .execute()
        )
        rows = result.data or []
        if not rows:
            return None
        row = rows[0]
        if datetime.fromisoformat(str(row["expires_at"]).replace("Z", "+00:00")) <= datetime.now(timezone.utc):
            await self.delete(key)
            return None
        return row.get("value")

    async def delete(self, key: str) -> None:
        self._db.table("oidc_transactions").delete().eq("id", self._id(key)).execute()


def _oidc_client(provider: dict, db: Any):
    try:
        client_secret = build_secret_vault(get_config(), db).get(_provider_secret_ref(provider))
    except VaultError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    config = provider.get("config") or {}
    oauth = OAuth(cache=DatabaseOIDCStateCache(db))
    name = f"oidc_{provider['id'].replace('-', '_')}"
    oauth.register(
        name,
        client_id=provider["client_id"],
        client_secret=client_secret,
        server_metadata_url=provider["issuer"].rstrip("/") + "/.well-known/openid-configuration",
        client_kwargs={
            "scope": config.get("scopes", "openid email profile groups"),
            "code_challenge_method": "S256",
        },
    )
    return oauth.create_client(name)


def _require_oidc_session_middleware(request: Request) -> None:
    if "session" not in request.scope:
        raise HTTPException(
            status_code=503,
            detail="OIDC login requires MCO_SESSION_SECRET and the session middleware",
        )


@auth_router.get("/oidc/{provider_id}/login", name="oidc_login")
async def oidc_login(provider_id: str, request: Request):
    _require_oidc_session_middleware(request)
    db = _db()
    provider = _get_provider(db, provider_id)
    if provider.get("protocol") != "oidc" or not provider.get("enabled", True):
        raise HTTPException(status_code=404, detail="OIDC provider is not enabled")
    redirect_uri = request.url_for("oidc_callback", provider_id=provider_id)
    return await _oidc_client(provider, db).authorize_redirect(request, redirect_uri)


def _mapped_access(db: Any, provider: dict, claims: dict) -> tuple[str, list[str]]:
    config = provider.get("config") or {}
    raw_groups = claims.get(config.get("group_claim", "groups")) or []
    groups = {str(group).casefold() for group in (raw_groups if isinstance(raw_groups, list) else [raw_groups])}
    result = (
        db.table("role_mappings")
        .select("*")
        .eq("identity_provider_id", provider["id"])
        .execute()
    )
    matched = [
        row for row in (result.data or [])
        if str(row.get("external_group") or "").casefold() in groups
    ]
    if not matched:
        raise HTTPException(status_code=403, detail="No BitCadence role mapping matched this identity")
    roles = {row["role"] for row in matched}
    role = next((candidate for candidate in ROLE_PRIORITY if candidate in roles), "viewer")
    scopes = set()
    for row in matched:
        scopes.update(row.get("scopes") or BUILTIN_ROLE_SCOPES.get(row["role"], set()))
    normalized = sorted({scope for scope in scopes if scope in KNOWN_SCOPES})
    return role, normalized


def _provision_user(db: Any, provider: dict, claims: dict, role: str, scopes: list[str]) -> dict:
    subject = str(claims.get("sub") or "").strip()
    email = str(claims.get("email") or "").strip().lower()
    if not subject or not email:
        raise HTTPException(status_code=403, detail="OIDC identity must include sub and email claims")
    identities = (
        db.table("external_identities")
        .select("*")
        .eq("identity_provider_id", provider["id"])
        .eq("subject", subject)
        .execute()
    ).data or []
    if identities:
        user_id = identities[0]["user_id"]
        current_users = db.table("users").select("*").eq("id", user_id).execute().data or []
        if not current_users or not current_users[0].get("active", True):
            raise HTTPException(status_code=403, detail="This user has been deactivated")
    else:
        if not provider.get("jit_enabled", True):
            raise HTTPException(status_code=403, detail="This identity has not been provisioned")
        users = db.table("users").select("*").execute().data or []
        user = next((row for row in users if str(row.get("email") or "").casefold() == email.casefold()), None)
        if user is None:
            created = db.table("users").insert({
                "email": email,
                "display_name": claims.get("name") or email,
                "active": True,
            }).execute()
            user = created.data[0]
        elif not user.get("active", True):
            raise HTTPException(status_code=403, detail="This user has been deactivated")
        user_id = user["id"]
        db.table("external_identities").insert({
            "user_id": user_id,
            "identity_provider_id": provider["id"],
            "subject": subject,
            "last_login_at": datetime.now(timezone.utc).isoformat(),
        }).execute()

    org_id = provider.get("org_id") or "default"
    # FROZEN WIRE CONSTANT - do not rebrand. This namespace fixes the
    # deterministic primary key of a membership. Changing it makes the upsert
    # below insert a duplicate row per (org_id, user_id) instead of updating,
    # and the active-check above reads only the first row - so a deactivated
    # member could be re-admitted through a still-active duplicate.
    membership_id = str(uuid.uuid5(
        uuid.NAMESPACE_URL,
        f"batoncadence:membership:{org_id}:{user_id}",
    ))
    membership = {
        "id": membership_id,
        "org_id": org_id,
        "user_id": user_id,
        "role": role,
        "scopes": scopes,
        "active": True,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    existing_membership = (
        db.table("org_memberships")
        .select("*")
        .eq("org_id", org_id)
        .eq("user_id", user_id)
        .execute()
    ).data or []
    if existing_membership and not existing_membership[0].get("active", True):
        raise HTTPException(status_code=403, detail="This Organization Membership has been deactivated")
    db.table("org_memberships").upsert(membership).execute()
    return membership


def _secure_cookie() -> bool:
    value = str(get_config().get("MCO_SESSION_COOKIE_SECURE", "true") or "true").lower()
    return value in {"1", "true", "yes", "on"}


@auth_router.get("/oidc/{provider_id}/callback", name="oidc_callback")
async def oidc_callback(provider_id: str, request: Request):
    _require_oidc_session_middleware(request)
    db = _db()
    provider = _get_provider(db, provider_id)
    try:
        token = await _oidc_client(provider, db).authorize_access_token(request)
    except OAuthError as exc:
        raise HTTPException(status_code=401, detail=f"OIDC login failed: {exc.error}") from exc
    claims = dict(token.get("userinfo") or {})
    role, scopes = _mapped_access(db, provider, claims)
    membership = _provision_user(db, provider, claims, role, scopes)

    raw_session = secrets.token_urlsafe(32)
    expires_at = datetime.now(timezone.utc) + timedelta(hours=8)
    session = db.table("user_sessions").insert({
        "org_id": membership["org_id"],
        "user_id": membership["user_id"],
        "session_token_hash": hashlib.sha256(raw_session.encode("utf-8")).hexdigest(),
        "device_name": request.headers.get("user-agent", "browser")[:255],
        "user_agent": request.headers.get("user-agent", "")[:1000],
        "expires_at": expires_at.isoformat(),
    }).execute().data[0]
    response = RedirectResponse("/console", status_code=303)
    response.set_cookie(
        "mco_session",
        raw_session,
        max_age=8 * 60 * 60,
        httponly=True,
        secure=_secure_cookie(),
        samesite="lax",
        path="/",
    )
    response.headers["Cache-Control"] = "no-store"
    return response


@auth_router.get("/me")
async def auth_me(principal: dict = Depends(require_agent)):
    return {
        "instance_id": principal.get("instance_id"),
        "user_id": principal.get("user_id"),
        "org_id": principal.get("org_id") or "default",
        "role": principal.get("role"),
        "scopes": principal.get("scopes") or [],
        "auth_method": principal.get("auth_method", "bearer"),
    }


@auth_router.post("/logout")
async def auth_logout(request: Request):
    response = JSONResponse({"success": True})
    raw = request.cookies.get("mco_session")
    if raw:
        enforce_session_csrf(request)
        db = _db()
        token_hash = hashlib.sha256(raw.encode("utf-8")).hexdigest()
        db.table("user_sessions").update({
            "revoked_at": datetime.now(timezone.utc).isoformat(),
        }).eq("session_token_hash", token_hash).execute()
    response.delete_cookie("mco_session", path="/")
    response.headers["Cache-Control"] = "no-store"
    return response
