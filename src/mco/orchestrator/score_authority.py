"""Digest- and tenant-bound authority checks for Score execution.

The durable schema's historical primary key on ``score_grants.digest`` is too
coarse to be an authorization decision by itself.  This layer always checks the
caller org, immutable document digest, and a stable grant identity.
"""
from __future__ import annotations

import hashlib
import hmac
import json
from datetime import datetime, timezone


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
                  now: datetime | None = None) -> str:
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
    now = now or datetime.now(timezone.utc)
    if "not_before" in grant:
        try:
            not_before = datetime.fromisoformat(str(grant["not_before"]).replace("Z", "+00:00"))
        except ValueError as exc:
            raise AuthorityError("malformed_grant_not_before") from exc
        if now < not_before:
            raise AuthorityError("grant_not_yet_valid")
    if expiry <= now:
        raise AuthorityError("grant_expired")
    if verification_key is not None:
        verify_grant_signature(grant, verification_key)
    return grant_identity(grant)


def require_receipt(receipt: dict, *, org_id: str, digest: str, grant: dict, action: str, now: datetime | None = None) -> None:
    """Reject receipts created under another document, tenant, or grant."""
    expected = require_grant(grant, org_id=org_id, digest=digest, action=action, now=now)
    if not isinstance(receipt, dict) or receipt.get("org_id") != org_id or receipt.get("digest") != digest:
        raise AuthorityError("stale_or_cross_tenant_receipt")
    if receipt.get("grant_identity") != expected:
        raise AuthorityError("receipt_not_bound_to_grant")
