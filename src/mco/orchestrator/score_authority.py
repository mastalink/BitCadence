"""Digest- and tenant-bound authority checks for Score execution.

The durable schema's historical primary key on ``score_grants.digest`` is too
coarse to be an authorization decision by itself.  This layer always checks the
caller org, immutable document digest, and a stable grant identity.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
from datetime import datetime, timezone

from mco.config import get_config


class AuthorityError(ValueError):
    pass


_SIGNED_FIELDS = (
    "org_id", "run_id", "digest", "actions", "resources", "env",
    "not_before", "expires_at", "budget_cents", "human_principal",
)


def _canonical_grant(grant: dict) -> bytes:
    if not isinstance(grant, dict) or any(field not in grant for field in _SIGNED_FIELDS):
        raise AuthorityError("malformed_grant")
    body = {field: grant[field] for field in _SIGNED_FIELDS}
    return json.dumps(body, sort_keys=True, separators=(",", ":")).encode()


def sign_grant(grant: dict, key: bytes) -> dict:
    """Sign an owner-approved grant at the trusted server boundary."""
    if not isinstance(key, bytes) or len(key) < 32:
        raise AuthorityError("grant_signing_key_too_short")
    signed = dict(grant)
    signed["signature"] = hmac.new(key, _canonical_grant(signed), hashlib.sha256).hexdigest()
    return signed


def verify_grant_signature(grant: dict, key: bytes) -> None:
    if not isinstance(key, bytes) or len(key) < 32:
        raise AuthorityError("grant_verification_key_too_short")
    expected = hmac.new(key, _canonical_grant(grant), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(str(grant.get("signature") or ""), expected):
        raise AuthorityError("grant_signature_invalid")


def configured_grant_key(value: str | None = None) -> bytes:
    """Load the Score grant key from encrypted configuration, failing closed."""
    raw = value if value is not None else get_config().get("MCO_SCORE_GRANT_KEY")
    if not isinstance(raw, str) or not raw.strip():
        raise AuthorityError("grant_verification_key_not_configured")
    raw = raw.strip()
    try:
        if len(raw) == 64:
            key = bytes.fromhex(raw)
        else:
            key = base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4))
    except (ValueError, TypeError):
        key = raw.encode()
    if len(key) < 32:
        # A raw passphrase is supported for local installs, but it must still
        # carry at least 256 bits of material.
        key = raw.encode()
    if len(key) < 32:
        raise AuthorityError("grant_verification_key_too_short")
    return key


def grant_identity(grant: dict) -> str:
    """Return a stable identity for the signed authority, excluding timestamps."""
    # Old S03 receipts remain readable, while every S04-issued grant includes
    # all strict fields above. Including every present scope prevents widening
    # a grant without changing its stable identity.
    fields = ("org_id", "run_id", "digest", "actions", "resources", "env", "not_before", "expires_at", "budget_cents", "human_principal", "signature")
    if not isinstance(grant, dict) or any(field not in grant for field in ("org_id", "digest", "actions", "resources", "env", "expires_at", "human_principal", "signature")):
        raise AuthorityError("malformed_grant")
    body = {field: grant[field] for field in fields if field in grant}
    return hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def require_grant(grant: dict, *, org_id: str, digest: str, action: str,
                  run_id: str | None = None, resource: str | None = None,
                  environment: str | None = None, cost_cents: int | None = None,
                  owner_principal: str | None = None,
                  verification_key: bytes | None = None,
                  allow_legacy_unverified: bool = False,
                  now: datetime | None = None) -> str:
    if not isinstance(grant, dict):
        raise AuthorityError("malformed_grant")
    if not allow_legacy_unverified:
        if verification_key is None:
            raise AuthorityError("grant_verification_key_required")
        if run_id is None or resource is None or environment is None or cost_cents is None or owner_principal is None:
            raise AuthorityError("complete_grant_scope_required")
    if grant.get("org_id") != org_id or grant.get("digest") != digest:
        raise AuthorityError("grant_not_bound_to_org_and_digest")
    if action not in grant.get("actions", []):
        raise AuthorityError("grant_does_not_authorize_action")
    if run_id is not None and grant.get("run_id") != run_id:
        raise AuthorityError("grant_not_bound_to_run")
    if resource is not None and resource not in grant.get("resources", []):
        raise AuthorityError("grant_does_not_authorize_resource")
    if environment is not None and grant.get("env") != environment:
        raise AuthorityError("grant_does_not_authorize_environment")
    if owner_principal is not None and grant.get("human_principal") != owner_principal:
        raise AuthorityError("grant_not_approved_by_owner")
    if cost_cents is not None:
        if type(cost_cents) is not int or cost_cents < 0 or type(grant.get("budget_cents")) is not int:
            raise AuthorityError("malformed_grant_budget")
        if cost_cents > grant["budget_cents"]:
            raise AuthorityError("grant_budget_exceeded")
    try:
        expiry = datetime.fromisoformat(str(grant["expires_at"]).replace("Z", "+00:00"))
    except (KeyError, ValueError) as exc:
        raise AuthorityError("malformed_grant_expiry") from exc
    if expiry.tzinfo is None:
        raise AuthorityError("malformed_grant_expiry")
    now = now or datetime.now(timezone.utc)
    if "not_before" in grant:
        try:
            not_before = datetime.fromisoformat(str(grant["not_before"]).replace("Z", "+00:00"))
        except ValueError as exc:
            raise AuthorityError("malformed_grant_not_before") from exc
        if not_before.tzinfo is None:
            raise AuthorityError("malformed_grant_not_before")
        if now < not_before:
            raise AuthorityError("grant_not_yet_valid")
    if expiry <= now:
        raise AuthorityError("grant_expired")
    if verification_key is not None:
        verify_grant_signature(grant, verification_key)
    return grant_identity(grant)


def require_receipt(receipt: dict, *, org_id: str, digest: str, grant: dict, action: str,
                    run_id: str | None = None, resource: str | None = None,
                    environment: str | None = None, cost_cents: int | None = None,
                    owner_principal: str | None = None,
                    verification_key: bytes | None = None,
                    allow_legacy_unverified: bool = False,
                    now: datetime | None = None) -> None:
    """Reject receipts created under another document, tenant, or grant."""
    expected = require_grant(
        grant, org_id=org_id, digest=digest, action=action, run_id=run_id,
        resource=resource, environment=environment, cost_cents=cost_cents,
        owner_principal=owner_principal, verification_key=verification_key,
        allow_legacy_unverified=allow_legacy_unverified, now=now,
    )
    if not isinstance(receipt, dict) or receipt.get("org_id") != org_id or receipt.get("digest") != digest:
        raise AuthorityError("stale_or_cross_tenant_receipt")
    if receipt.get("grant_identity") != expected:
        raise AuthorityError("receipt_not_bound_to_grant")


class GrantService:
    """Trusted issuance and durable lookup for signed Score grants."""

    def __init__(self, db, *, verification_key: bytes | None = None):
        self.db = db
        self.key = verification_key if verification_key is not None else configured_grant_key()
        if not isinstance(self.key, bytes) or len(self.key) < 32:
            raise AuthorityError("grant_verification_key_too_short")

    def issue(self, grant: dict) -> dict:
        if not isinstance(grant, dict):
            raise AuthorityError("malformed_grant")
        for field in ("org_id", "run_id", "digest", "env", "human_principal"):
            if not isinstance(grant.get(field), str) or not grant[field].strip():
                raise AuthorityError(f"malformed_grant_{field}")
        for field in ("actions", "resources"):
            values = grant.get(field)
            if not isinstance(values, list) or not values or any(not isinstance(v, str) or not v.strip() for v in values):
                raise AuthorityError(f"malformed_grant_{field}")
        if type(grant.get("budget_cents")) is not int or grant["budget_cents"] < 0:
            raise AuthorityError("malformed_grant_budget")
        try:
            not_before = datetime.fromisoformat(str(grant["not_before"]).replace("Z", "+00:00"))
            expires_at = datetime.fromisoformat(str(grant["expires_at"]).replace("Z", "+00:00"))
        except (KeyError, ValueError) as exc:
            raise AuthorityError("malformed_grant_time_window") from exc
        if not_before.tzinfo is None or expires_at.tzinfo is None or expires_at <= not_before:
            raise AuthorityError("malformed_grant_time_window")
        signed = sign_grant(grant, self.key)
        # Verify before persistence so malformed dates/scopes never become
        # durable authority. The caller-specific action check happens at use.
        verify_grant_signature(signed, self.key)
        existing = (
            self.db.table("score_grants").select("*")
            .eq("digest", signed["digest"]).execute().data or []
        )
        if existing:
            if any(existing[0].get(field) != signed.get(field) for field in (*_SIGNED_FIELDS, "signature")):
                raise AuthorityError("grant_digest_already_has_different_authority")
            return existing[0]
        return self.db.table("score_grants").insert(signed).execute().data[0]

    def load(self, *, org_id: str, run_id: str, digest: str) -> dict:
        rows = (
            self.db.table("score_grants").select("*")
            .eq("org_id", org_id).eq("run_id", run_id).eq("digest", digest)
            .execute().data or []
        )
        if len(rows) != 1:
            raise AuthorityError("issued_grant_not_found")
        verify_grant_signature(rows[0], self.key)
        return rows[0]

    def require(self, *, org_id: str, run_id: str, digest: str, action: str,
                resource: str, environment: str, cost_cents: int,
                owner_principal: str, now: datetime | None = None) -> tuple[dict, str]:
        grant = self.load(org_id=org_id, run_id=run_id, digest=digest)
        identity = require_grant(
            grant, org_id=org_id, run_id=run_id, digest=digest, action=action,
            resource=resource, environment=environment, cost_cents=cost_cents,
            owner_principal=owner_principal, verification_key=self.key, now=now,
        )
        return grant, identity
