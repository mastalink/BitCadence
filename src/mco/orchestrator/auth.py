"""
Gateway authentication & authorization.

Four layers, evaluated in order:

1. **Trusted-header identity (enterprise)** - when BitCadence sits behind an
   SSO reverse proxy (Cloudflare Access, oauth2-proxy, Authelia, ...), the
   proxy authenticates the human and asserts identity via headers. We don't
   run SAML/OIDC inside this compatibility path; it delegates to infrastructure
   enterprises already run. Disabled unless MCO_TRUSTED_HEADER_AUTH is explicitly on.
2. **Human sessions** - OIDC-authenticated users receive an opaque,
   short-lived cookie whose hash is resolved through an active Organization
   Membership on every request.
3. **Bearer-token authentication** - agents and workers authenticate with
   tokens, reusing the same SHA-256 token-hash scheme the WebSocket handshake
   uses (one source of truth).
4. **Scope-based authorization (RBAC)** - every endpoint declares the scopes
   it needs via `require_scopes`. A registry row may carry an explicit
   `scopes` list; rows without one get role-derived defaults that match
   pre-RBAC behavior exactly (workers work, approvers approve).

Authorization additionally follows the "dropbox" model enforced in routes:
any authenticated agent may *send* (create a job to any target), but
*pulling/leasing/updating* a job requires the caller's identity to match the
job's addressee.
"""

import hashlib
import hmac
import logging
from datetime import datetime, timezone
from typing import Any, Iterable, List, Optional
from urllib.parse import urlparse

from fastapi import Depends, Header, HTTPException, Request

from mco.config import get_config

logger = logging.getLogger("mco.orchestrator.auth")

# ── Scope vocabulary ─────────────────────────────────────────────────────────
# `admin` is the wildcard: it satisfies every scope check.
KNOWN_SCOPES = {
    "jobs:read",
    "jobs:write",
    "jobs:approve",
    "context:read",
    "context:write",
    "agents:read",
    "agents:manage",
    "integrations:read",
    "integrations:manage",
    "admin",
}

# What a worker token can do when its registry row has no explicit scopes.
# Deliberately excludes jobs:approve, agents:manage, integrations:manage -
# autonomous agents must not decide approval gates or touch live platforms
# directly (they address jobs to connector roles instead, keeping the
# lease/audit lifecycle intact).
WORKER_DEFAULT_SCOPES = frozenset({
    "jobs:read",
    "jobs:write",
    "context:read",
    "context:write",
    "agents:read",
    "integrations:read",
})


def hash_token(token: str) -> str:
    """SHA-256 hex digest of an agent access token (matches `mco register`)."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _approver_roles() -> set:
    from mco.orchestrator.utils import get_approver_roles
    return get_approver_roles()


def normalize_scopes(raw) -> List[str]:
    """Normalize a scopes value (list, set, or comma string) to a clean list."""
    if raw is None:
        return []
    if isinstance(raw, str):
        parts = raw.split(",")
    else:
        parts = list(raw)
    return sorted({str(s).strip().lower() for s in parts if str(s).strip()})


def resolve_scopes(agent: dict) -> List[str]:
    """Effective scopes for an agent.

    Explicit `scopes` on the registry row win. Without them, defaults are
    derived from the role so pre-RBAC installs behave identically:
    approver roles (MCO_APPROVER_ROLES) -> admin; everything else -> the
    worker default set.
    """
    explicit = normalize_scopes(agent.get("scopes"))
    if explicit:
        return explicit
    role = (agent.get("role") or "").lower()
    if role in _approver_roles():
        return ["admin"]
    return sorted(WORKER_DEFAULT_SCOPES)


def has_scope(agent: dict, scope: str) -> bool:
    """True when the agent's effective scopes satisfy `scope` (admin = all)."""
    scopes = set(resolve_scopes(agent))
    return "admin" in scopes or scope in scopes


def verify_token(db_client: Any, token: str) -> Optional[dict]:
    """Return the `agent_registry` row for a valid token, else None.

    The row includes `org_id` (the tenant boundary every downstream query is
    scoped to; defaults to 'default' pre-migration) and `scopes` when the
    registry carries them. The token hash itself is never returned.
    """
    if not token or db_client is None:
        return None
    try:
        res = (
            db_client.table("agent_registry")
            .select("*")
            .eq("auth_token_hash", hash_token(token))
            .execute()
        )
    except Exception as e:
        logger.warning(f"Token lookup failed: {e}")
        return None
    rows = res.data or []
    if not rows:
        return None
    agent = {k: v for k, v in rows[0].items() if k != "auth_token_hash"}
    agent.setdefault("org_id", "default")
    return agent


def extract_bearer(authorization: str) -> str:
    """Pull the raw token out of an `Authorization: Bearer <token>` header."""
    if authorization and authorization.lower().startswith("bearer "):
        return authorization[7:].strip()
    return ""


def verify_user_session(db_client: Any, raw_token: str) -> Optional[dict]:
    """Resolve one active human session and its current Organization Membership."""
    if not raw_token or db_client is None:
        return None
    token_hash = hash_token(raw_token)
    try:
        sessions = (
            db_client.table("user_sessions")
            .select("*")
            .eq("session_token_hash", token_hash)
            .execute()
        ).data or []
        if not sessions:
            return None
        session = sessions[0]
        if session.get("revoked_at"):
            return None
        expires_at = datetime.fromisoformat(
            str(session.get("expires_at") or "").replace("Z", "+00:00")
        )
        if expires_at <= datetime.now(timezone.utc):
            return None
        memberships = (
            db_client.table("org_memberships")
            .select("*")
            .eq("org_id", session.get("org_id") or "default")
            .eq("user_id", session.get("user_id"))
            .execute()
        ).data or []
        if not memberships or not memberships[0].get("active", True):
            return None
        users = (
            db_client.table("users")
            .select("*")
            .eq("id", session.get("user_id"))
            .execute()
        ).data or []
        if not users or not users[0].get("active", True):
            return None
    except Exception as exc:
        logger.warning("User session lookup failed: %s", exc)
        return None
    membership = memberships[0]
    return {
        "instance_id": f"user:{session['user_id']}",
        "user_id": session["user_id"],
        "role": membership.get("role") or "viewer",
        "scopes": normalize_scopes(membership.get("scopes")),
        "status": "online",
        "org_id": session.get("org_id") or "default",
        "auth_method": "session",
        "session_id": session.get("id"),
    }


def enforce_session_csrf(request: Optional[Request]) -> None:
    """Require same-origin browser context for cookie-authenticated mutations."""
    if request is None or request.method.upper() in {"GET", "HEAD", "OPTIONS"}:
        return
    if (request.headers.get("sec-fetch-site") or "").lower() == "cross-site":
        raise HTTPException(status_code=403, detail="Cross-site session request denied")
    origin = request.headers.get("origin", "")
    host = request.headers.get("host", "")
    if not origin or urlparse(origin).netloc.casefold() != host.casefold():
        raise HTTPException(status_code=403, detail="Session request origin did not match this gateway")


# ── Trusted-header identity (SSO delegation) ─────────────────────────────────

def _truthy(value) -> bool:
    return str(value or "").strip().lower() in ("1", "true", "on", "yes")


def trusted_header_agent(request: Optional[Request]) -> Optional[dict]:
    """Identity asserted by an SSO reverse proxy, or None.

    Hard requirements before any header is believed:
    - MCO_TRUSTED_HEADER_AUTH must be explicitly enabled (default off: the
      headers are attacker-controllable on a directly exposed gateway).
    - The active edition must include `trusted_header_auth` (enterprise).
    - If MCO_TRUSTED_HEADER_SECRET is set, the proxy must echo it in
      X-MCO-Proxy-Secret (constant-time compared) - this proves the request
      actually traversed the proxy. Strongly recommended.

    The proxy MUST strip/overwrite the identity headers on inbound traffic;
    every mainstream auth proxy does this by default. See docs/ENTERPRISE.md.
    """
    if request is None:
        return None
    config = get_config()
    if not _truthy(config.get("MCO_TRUSTED_HEADER_AUTH")):
        return None

    from mco.editions import has_feature  # lazy: editions imports config only
    if not has_feature("trusted_header_auth"):
        logger.warning(
            "MCO_TRUSTED_HEADER_AUTH is on but the active edition does not "
            "include it (set MCO_EDITION=enterprise). Ignoring identity headers."
        )
        return None

    secret = config.get("MCO_TRUSTED_HEADER_SECRET") or ""
    if secret:
        supplied = request.headers.get("x-mco-proxy-secret", "")
        if not hmac.compare_digest(supplied, secret):
            return None

    user_header = (config.get("MCO_TRUSTED_HEADER_USER") or "X-Forwarded-User").lower()
    user = (request.headers.get(user_header) or "").strip()
    if not user:
        return None

    role_header = (config.get("MCO_TRUSTED_HEADER_ROLE") or "X-Forwarded-Role").lower()
    role = (request.headers.get(role_header) or "").strip().lower()
    if not role:
        role = (config.get("MCO_TRUSTED_HEADER_DEFAULT_ROLE") or "human").strip().lower()

    return {
        "instance_id": f"sso:{user}",
        "role": role,
        "status": "online",
        "org_id": (config.get("MCO_TRUSTED_HEADER_ORG") or "default").strip() or "default",
        "auth_method": "trusted_header",
    }


# ── FastAPI dependencies ─────────────────────────────────────────────────────

async def require_agent(
    request: Request = None,
    authorization: str = Header(default=""),
) -> dict:
    """FastAPI dependency: authenticate the caller.

    Order: trusted proxy headers (when enabled) -> human session cookie ->
    bearer token against the agent registry -> Local-Only static token
    (MCO_LOCAL_TOKEN) when no database is configured.

    Raises 401 if no path authenticates.
    Returns the agent's {instance_id, role, status, org_id, scopes?, ...}.
    """
    from mco.orchestrator.routes import get_db_client

    sso = trusted_header_agent(request)
    if sso:
        return sso

    db_client = get_db_client()
    if db_client is None:
        # Local-Only mode: validate against the static token in config.
        local_token = (get_config().get("MCO_LOCAL_TOKEN") or "").strip()
        bearer = extract_bearer(authorization)
        if local_token:
            if not hmac.compare_digest(bearer or "", local_token):
                raise HTTPException(
                    status_code=401,
                    detail="Invalid token. Paste the MCO_LOCAL_TOKEN shown in your server window.",
                )
        elif not bearer:
            # No token configured and none provided — block unauthenticated requests.
            raise HTTPException(
                status_code=401,
                detail="No agent token. Add MCO_LOCAL_TOKEN to .env or run 'mco setup'.",
            )
        # Token is either correct or MCO_LOCAL_TOKEN is absent and any token is accepted.
        return {
            "instance_id": "local",
            "role": "admin",
            "status": "online",
            "org_id": "default",
        }

    session = verify_user_session(
        db_client,
        request.cookies.get("mco_session", "") if request is not None else "",
    )
    if session:
        enforce_session_csrf(request)
        return session

    agent = verify_token(db_client, extract_bearer(authorization))
    if not agent:
        raise HTTPException(status_code=401, detail="Invalid or missing agent token")
    return agent


def require_scopes(*scopes: str):
    """Dependency factory: authenticate AND authorize against `scopes`.

    Usage:
        @router.post("", ...)
        async def create_job(payload: dict,
                             agent: dict = Depends(require_scopes("jobs:write"))):

    Returns the agent dict (same shape as require_agent) so handlers are
    drop-in compatible. Raises 403 naming exactly what is missing.
    """
    needed: Iterable[str] = scopes

    async def _dep(agent: dict = Depends(require_agent)) -> dict:
        missing = [s for s in needed if not has_scope(agent, s)]
        if missing:
            raise HTTPException(
                status_code=403,
                detail=(
                    f"Token lacks required scope(s): {', '.join(missing)}. "
                    f"Re-register the agent with --scope, or use an admin token."
                ),
            )
        return agent

    return _dep
