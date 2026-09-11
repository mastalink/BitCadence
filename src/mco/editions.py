"""
Edition model - one codebase, three deployment postures.

BitCadence is open core in a single repository: every edition runs the same
code, and the edition only determines which surfaces are active. Drumline
(shared context) is deliberately first-class in EVERY edition - collective
memory is the product, not an upsell.

    community   Everything local: job board, governance (audit/approvals/
                escalation), workflows, Drumline on the embedded LocalStore,
                dashboard, MCP server.
    team        + shared gateway (cloud database), multi-org tenancy,
                scoped-token RBAC management.
    enterprise  + enterprise connectors (ServiceNow/Dynatrace/webhook),
                trusted-header SSO delegation, audit export.

Resolution order:
    1. MCO_EDITION set explicitly -> that edition (lets an operator pin a
       posture, e.g. force `community` to verify nothing enterprise leaks in).
    2. Inferred from configuration, so existing installs keep working exactly
       as before this module existed (grandfathering, never a surprise 403):
       trusted-header auth or a connector configured -> enterprise;
       a cloud database configured -> team; otherwise community.

Gating is honor-system by design (the code is MIT); `require_feature` exists
so a posture is *deterministic* - explicitly pinning `community` reliably
disables enterprise surfaces, which is also how the test matrix exercises
each edition.
"""

from __future__ import annotations

from fastapi import HTTPException

from mco.config import get_config

COMMUNITY = "community"
TEAM = "team"
ENTERPRISE = "enterprise"

EDITIONS = (COMMUNITY, TEAM, ENTERPRISE)
_RANK = {COMMUNITY: 0, TEAM: 1, ENTERPRISE: 2}

# Lowest edition that includes each feature. Anything not listed is core
# (available everywhere) - the matrix only names gated or story-relevant
# surfaces.
FEATURE_MATRIX = {
    # Core - in every edition, listed for `mco edition` display honesty.
    "job_board": COMMUNITY,
    "governance": COMMUNITY,
    "workflows": COMMUNITY,
    "drumline": COMMUNITY,
    "dashboard": COMMUNITY,
    "mcp_server": COMMUNITY,
    # Team
    "shared_gateway": TEAM,
    "multi_org": TEAM,
    "rbac_management": TEAM,
    # Enterprise (runtime-enforced)
    "connectors": ENTERPRISE,
    "identity_federation": ENTERPRISE,
    "trusted_header_auth": ENTERPRISE,
    "audit_export": ENTERPRISE,
}


def _truthy(value) -> bool:
    return str(value or "").strip().lower() in ("1", "true", "on", "yes")


def infer_edition() -> str:
    """Derive the edition from what is actually configured (grandfathering)."""
    config = get_config()
    if _truthy(config.get("MCO_TRUSTED_HEADER_AUTH")):
        return ENTERPRISE
    # Any connector credential present means the install is using the
    # enterprise integration surface already.
    for key in (
        "SERVICENOW_INSTANCE_URL",
        "DYNATRACE_ENV_URL",
        "MCO_WEBHOOK_SECRET",
    ):
        if config.get(key):
            return ENTERPRISE
    url = config.get("SUPABASE_URL")
    if url and url != "encrypted_in_secret_store":
        return TEAM
    return COMMUNITY


def current_edition() -> str:
    """The active edition: explicit MCO_EDITION wins, else inferred."""
    explicit = (get_config().get("MCO_EDITION") or "").strip().lower()
    if explicit in EDITIONS:
        return explicit
    return infer_edition()


def has_feature(feature: str, edition: str | None = None) -> bool:
    """True when `feature` is available in the active (or given) edition.

    Unknown feature names are treated as core so a typo can never lock a
    surface that was meant to be open.
    """
    needed = FEATURE_MATRIX.get(feature, COMMUNITY)
    active = edition or current_edition()
    return _RANK.get(active, 0) >= _RANK[needed]


def require_feature(feature: str):
    """FastAPI dependency: 403 with an actionable message when the active
    edition does not include `feature`."""

    async def _dep():
        if not has_feature(feature):
            needed = FEATURE_MATRIX.get(feature, COMMUNITY)
            raise HTTPException(
                status_code=403,
                detail=(
                    f"'{feature}' requires the {needed} edition "
                    f"(current: {current_edition()}). "
                    f"Set MCO_EDITION={needed} to enable it."
                ),
            )
        return True

    return _dep


def edition_summary() -> dict:
    """Edition + full feature availability map (powers `mco edition`)."""
    active = current_edition()
    explicit = (get_config().get("MCO_EDITION") or "").strip().lower()
    return {
        "edition": active,
        "source": "explicit" if explicit in EDITIONS else "inferred",
        "features": {
            name: {
                "available": has_feature(name, active),
                "minimum_edition": minimum,
            }
            for name, minimum in sorted(FEATURE_MATRIX.items())
        },
    }
