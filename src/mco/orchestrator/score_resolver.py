"""Run-start role resolver for Score v1.

Resolves abstract task roles to concrete online, eligible fleet identities
before ScoreBridge.initialize().
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Callable, Iterable, Optional

from mco.orchestrator.presence import (
    BROKEN,
    DISABLED,
    OFFLINE,
    STANDBY,
    WORKING,
    describe_fleet,
)
from mco.orchestrator.scores import ScoreError, load_score


def resolve_score_targets(
    score: Any,
    org_id: str = "default",
    *,
    db_client: Any = None,
    threshold: Optional[int] = None,
    connected: Optional[set] = None,
    stall_seconds: Optional[int] = None,
    now: Optional[datetime] = None,
    presence_source: Optional[Any] = None,
) -> dict[str, list[str]]:
    """Resolve distinct score task roles into online eligible instance IDs.

    Called ONCE at run-start before ScoreBridge.initialize().
    Given a loaded score document (dict or JSON string) and an org_id, returns
    a targets dict shaped exactly like what initialize() accepts:
    `{role: [online_instance_id, ...]}` for every distinct role/review_role
    referenced in score tasks.

    Honest presence:
    - Online eligible identities (standby or working) are collected per role.
    - Offline, broken, and disabled identities are excluded.
    - Roles with zero eligible identities return an empty list [] for that role,
      allowing ScoreBridge.initialize() to enforce fail-closed validation.
    - Identities in other orgs are excluded per tenant isolation.
    """
    if isinstance(score, (str, dict)):
        score_doc = load_score(score)
    else:
        raise ScoreError("score must be a dict or JSON string")

    distinct_roles: list[str] = []
    for task in score_doc.get("tasks", []):
        for role_key in ("role", "review_role"):
            r = task.get(role_key)
            if r and r not in distinct_roles:
                distinct_roles.append(r)

    targets: dict[str, list[str]] = {role: [] for role in distinct_roles}

    target_org = (org_id or "default").strip() or "default"

    if presence_source is not None:
        if callable(presence_source):
            fleet_data = presence_source()
        else:
            fleet_data = presence_source
        if fleet_data and all(isinstance(r, dict) and "state" in r for r in fleet_data):
            described = list(fleet_data)
        else:
            described = describe_fleet(
                db_client,
                fleet_data or [],
                threshold=threshold if threshold is not None else 300,
                connected=connected,
                stall_seconds=stall_seconds,
                now=now,
            )
    else:
        if db_client is None:
            try:
                from mco.orchestrator.routes import get_db_client

                db_client = get_db_client()
            except Exception:
                db_client = None

        rows = []
        if db_client is not None:
            try:
                res = (
                    db_client.table("agent_registry")
                    .select("*")
                    .order("instance_id")
                    .execute()
                )
                rows = res.data or []
            except Exception:
                rows = []

        if threshold is None:
            try:
                from mco.orchestrator.routes import get_offline_after_seconds

                threshold = get_offline_after_seconds()
            except Exception:
                threshold = 300

        described = describe_fleet(
            db_client,
            rows,
            threshold=threshold,
            connected=connected,
            stall_seconds=stall_seconds,
            now=now,
        )

    for row in described:
        row_org = (row.get("org_id") or "default").strip() or "default"
        if row_org != target_org:
            continue

        state = row.get("state")
        if state not in (STANDBY, WORKING):
            continue

        instance_id = row.get("instance_id")
        if not instance_id or not isinstance(instance_id, str):
            continue

        agent_role = str(row.get("role") or "").strip().lower()
        for role in distinct_roles:
            if str(role).strip().lower() == agent_role:
                if instance_id not in targets[role]:
                    targets[role].append(instance_id)

    for role in targets:
        targets[role].sort()

    return targets
