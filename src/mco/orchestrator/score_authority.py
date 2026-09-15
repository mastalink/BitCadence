"""Digest- and tenant-bound authority checks for Score execution.

The durable schema's historical primary key on ``score_grants.digest`` is too
coarse to be an authorization decision by itself.  This layer always checks the
caller org, immutable document digest, and a stable grant identity.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone


class AuthorityError(ValueError):
    pass


def grant_identity(grant: dict) -> str:
    """Return a stable identity for the signed authority, excluding timestamps."""
    fields = ("org_id", "digest", "actions", "resources", "env", "expires_at", "human_principal", "signature")
    if not isinstance(grant, dict) or any(field not in grant for field in fields):
        raise AuthorityError("malformed_grant")
    body = {field: grant[field] for field in fields}
    return hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def require_grant(grant: dict, *, org_id: str, digest: str, action: str, now: datetime | None = None) -> str:
    if grant.get("org_id") != org_id or grant.get("digest") != digest:
        raise AuthorityError("grant_not_bound_to_org_and_digest")
    if action not in grant.get("actions", []):
        raise AuthorityError("grant_does_not_authorize_action")
    try:
        expiry = datetime.fromisoformat(str(grant["expires_at"]).replace("Z", "+00:00"))
    except (KeyError, ValueError) as exc:
        raise AuthorityError("malformed_grant_expiry") from exc
    now = now or datetime.now(timezone.utc)
    if expiry <= now:
        raise AuthorityError("grant_expired")
    return grant_identity(grant)


def require_receipt(receipt: dict, *, org_id: str, digest: str, grant: dict, action: str, now: datetime | None = None) -> None:
    """Reject receipts created under another document, tenant, or grant."""
    expected = require_grant(grant, org_id=org_id, digest=digest, action=action, now=now)
    if not isinstance(receipt, dict) or receipt.get("org_id") != org_id or receipt.get("digest") != digest:
        raise AuthorityError("stale_or_cross_tenant_receipt")
    if receipt.get("grant_identity") != expected:
        raise AuthorityError("receipt_not_bound_to_grant")
