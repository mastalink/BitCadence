"""Shared utility functions for the orchestrator module.

Home for helpers needed by both routes and auth, so neither has to import
the other (breaks the routes <-> auth import cycle).
"""

from mco.config import get_config

# Roles allowed to approve/reject jobs paused at the human-in-the-loop gate.
DEFAULT_APPROVER_ROLES = "owner,admin,human,operator,approver"


def get_approver_roles() -> set:
    """Lower-cased roles permitted to decide approval gates (MCO_APPROVER_ROLES)."""
    raw = get_config().get("MCO_APPROVER_ROLES") or DEFAULT_APPROVER_ROLES
    return {r.strip().lower() for r in raw.split(",") if r.strip()}
