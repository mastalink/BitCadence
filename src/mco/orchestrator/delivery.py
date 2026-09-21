"""Delivery watchdog: a pending job must reach a worker without a human nudging it.

Wakers make delivery event-driven, but the last mile fails in ways the board
never saw: a crash-looped supervisor stops restarting a waker, a CLI rejects its
model, an IDE accepts a prompt and never leases. Each left jobs PENDING until a
person noticed and told an agent to look. This sweep closes that loop on the
gateway, where every job is visible:

  1. stalled for MCO_DELIVERY_STALL_SECONDS  -> re-broadcast job_pending, which
     re-wakes every listening waker for the role (a missed or failed wake).
  2. still stalled for another stall window   -> reroute in place to the next
     fallback role (MCO_ROUTE_FALLBACKS) that has an online agent.
  3. no fallback can take it                  -> escalate once: an audit event,
     a job_undeliverable broadcast, and a single ntfy push to the operator.

State lives in the job's audit trail, not in memory, so a gateway restart
neither repeats a reroute nor forgets an escalation. Any non-delivery event
(lease expiry, approval, retry) restarts the clock, as does a reroute - the new
role gets its own full stall window.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Optional

from mco.config import get_config

logger = logging.getLogger("mco.orchestrator.delivery")

REKICKED = "delivery_rekicked"
REROUTED = "delivery_rerouted"
ESCALATED = "delivery_escalated"
CHAIN_STALLED = "chain_stalled"
# Events that record the watchdog's own progress; they must not restart the
# stall clock, or a re-kick would postpone its own follow-up forever.
_PROGRESS_EVENTS = {REKICKED, ESCALATED}
# Shadow annotations are append-only evidence. They must not restart the stall
# clock or clear rekick/escalate progress, or Jev would change delivery.
_ANNOTATION_EVENTS = {"jev_decision"}

DEFAULT_STALL_SECONDS = 600
DEFAULT_MAX_REROUTES = 2
DEFAULT_CHAIN_STALL_ROLE = "chief"
CHAIN_LOOKBACK_SECONDS = 86400
ACTOR_ID = "system"
ACTOR_ROLE = "delivery"


def get_stall_seconds(config: Optional[dict] = None) -> int:
    """Seconds a job may sit PENDING before delivery is retried
    (MCO_DELIVERY_STALL_SECONDS). 0 or negative disables the watchdog."""
    config = config if config is not None else get_config()
    try:
        return int(config.get("MCO_DELIVERY_STALL_SECONDS") or DEFAULT_STALL_SECONDS)
    except (TypeError, ValueError):
        return DEFAULT_STALL_SECONDS


def get_chain_stall_role(config: Optional[dict] = None) -> str:
    """Role that receives a stalled chain (MCO_CHAIN_STALL_TO_ROLE).

    Blank disables hand-off, leaving the audit event, broadcast and push."""
    config = config if config is not None else get_config()
    value = config.get("MCO_CHAIN_STALL_TO_ROLE")
    return (DEFAULT_CHAIN_STALL_ROLE if value is None else str(value)).strip().lower()


def get_max_reroutes(config: Optional[dict] = None) -> int:
    config = config if config is not None else get_config()
    try:
        return max(0, int(config.get("MCO_DELIVERY_MAX_REROUTES") or DEFAULT_MAX_REROUTES))
    except (TypeError, ValueError):
        return DEFAULT_MAX_REROUTES


def parse_fallbacks(raw: Optional[str]) -> dict[str, list[str]]:
    """Parse MCO_ROUTE_FALLBACKS: ``codex:claude, antigravity:claude|codex``.

    Each entry maps a role to the roles that may take its stalled work, in
    order of preference. Roles are lower-cased; a role never falls back to
    itself. Malformed entries are skipped rather than failing the gateway.
    """
    out: dict[str, list[str]] = {}
    for entry in (raw or "").split(","):
        if ":" not in entry:
            continue
        role, targets = entry.split(":", 1)
        role = role.strip().lower()
        chain = [t.strip().lower() for t in targets.split("|") if t.strip()]
        chain = [t for t in chain if t != role]
        if role and chain:
            out[role] = chain
    return out


@dataclass
class SweepResult:
    """What a sweep changed. Broadcasts and pushes are returned, not sent, so
    the blocking store work can run in a thread and the async gateway sends."""
    broadcasts: list[tuple[str, dict]] = field(default_factory=list)
    notifications: list[dict] = field(default_factory=list)
    rekicked: list[str] = field(default_factory=list)
    rerouted: list[str] = field(default_factory=list)
    escalated: list[str] = field(default_factory=list)
    chain_stalled: list[str] = field(default_factory=list)
    jev_annotations: list = field(default_factory=list)


def _parse_ts(value: Any) -> Optional[datetime]:
    if not value:
        return None
    try:
        ts = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)


def _delivery_state(job: dict, events: list) -> tuple[Optional[datetime], set, int]:
    """(stall clock start, progress events since then, reroutes so far)."""
    clock = _parse_ts(job.get("created_at"))
    since: set = set()
    reroutes = 0
    for event in events:
        name = event.get("event")
        if name in _ANNOTATION_EVENTS:
            continue
        if name == REROUTED:
            reroutes += 1
        if name in _PROGRESS_EVENTS:
            since.add(name)
            continue
        ts = _parse_ts(event.get("created_at"))
        if ts is not None and (clock is None or ts >= clock):
            clock = ts
            since = set()
    return clock, since, reroutes


def _online_roles(db: Any, org: str) -> set:
    """Roles that can take rerouted work: an agent in standby or working.

    Reachable is not enough - rerouting onto an agent that is itself `broken`
    (awake but not taking work) just moves the stall."""
    from mco.orchestrator.presence import available_roles, describe_fleet
    from mco.orchestrator.routes import get_offline_after_seconds

    rows = db.table("agent_registry").select("*").execute().data or []
    return available_roles(describe_fleet(db, rows, threshold=get_offline_after_seconds()), org)


def sweep(
    db: Any,
    *,
    now: Optional[datetime] = None,
    config: Optional[dict] = None,
    online_roles: Optional[Callable[[Any, str], set]] = None,
    jev_provider: Optional[Any] = None,
) -> SweepResult:
    """Advance delivery for every stalled PENDING job by at most one step."""
    from mco.orchestrator.audit import get_events, record_event

    config = config if config is not None else get_config()
    result = SweepResult()
    stall = get_stall_seconds(config)
    if stall <= 0:
        return result
    if str(config.get("MCO_KILL_SWITCH") or "").lower() in ("1", "true", "on", "yes"):
        return result

    now = now or datetime.now(timezone.utc)
    fallbacks = parse_fallbacks(config.get("MCO_ROUTE_FALLBACKS"))
    max_reroutes = get_max_reroutes(config)
    online_roles = online_roles or _online_roles
    online_cache: dict[str, set] = {}

    jobs = db.table("agent_jobs").select("*").eq("status", "pending").execute().data or []
    for job in jobs:
        if job.get("archived"):
            continue
        job_id = job.get("id")
        clock, since, reroutes = _delivery_state(job, get_events(db, job_id))
        if clock is None:
            continue
        age = (now - clock).total_seconds()
        if age < stall:
            continue

        if REKICKED not in since:
            record_event(db, job_id, REKICKED, ACTOR_ID, ACTOR_ROLE,
                         {"pending_seconds": int(age), "target_agent_role": job.get("target_agent_role"),
                          "target_agent_id": job.get("target_agent_id")})
            result.broadcasts.append(("job_pending", job))
            result.rekicked.append(job_id)
            _shadow_watchdog(result, jev_provider, db, job_id, "retry", {
                "worker_state": None,
                "pending_seconds": int(age),
                "reroutes": reroutes,
                "max_reroutes": max_reroutes,
                "stall_seconds": stall,
                "crash_loop": False,
                "chain_stalled": False,
                "broken": False,
                "reason": None,
            })
            continue

        if age < 2 * stall or ESCALATED in since:
            continue

        role = str(job.get("target_agent_role") or "").lower()
        payload = job.get("input_payload") or {}
        org = job.get("org_id") or "default"
        target = None
        reason = None
        if isinstance(payload, dict) and payload.get("no_reroute"):
            reason = "job is marked no_reroute"
        elif reroutes >= max_reroutes:
            reason = f"reroute limit reached ({max_reroutes})"
        elif role not in fallbacks:
            reason = f"no fallback configured for role '{role}'"
        else:
            if org not in online_cache:
                online_cache[org] = online_roles(db, org)
            target = next((r for r in fallbacks[role] if r in online_cache[org]), None)
            if target is None:
                reason = f"no online agent for fallback roles {fallbacks[role]}"

        if target is not None:
            updated = db.table("agent_jobs").update({
                "target_agent_role": target,
                "target_agent_id": None,
            }).eq("id", job_id).eq("status", "pending").execute().data
            if not updated:
                continue  # leased or changed since we read it: delivery happened
            record_event(db, job_id, REROUTED, ACTOR_ID, ACTOR_ROLE,
                         {"from_role": job.get("target_agent_role"),
                          "from_instance": job.get("target_agent_id"),
                          "to_role": target, "pending_seconds": int(age)})
            result.broadcasts.append(("job_pending", updated[0]))
            result.rerouted.append(job_id)
            _shadow_watchdog(result, jev_provider, db, job_id, "reroute", {
                "worker_state": None,
                "pending_seconds": int(age),
                "reroutes": reroutes,
                "max_reroutes": max_reroutes,
                "stall_seconds": stall,
                "crash_loop": False,
                "chain_stalled": False,
                "broken": False,
                "reason": None,
                "to_role": target,
            })
            continue

        record_event(db, job_id, ESCALATED, ACTOR_ID, ACTOR_ROLE,
                     {"reason": reason, "pending_seconds": int(age),
                      "target_agent_role": job.get("target_agent_role"),
                      "target_agent_id": job.get("target_agent_id")})
        result.broadcasts.append(("job_undeliverable", job))
        result.notifications.append({
            "title": "BitCadence job stuck",
            "message": (f"'{job.get('title') or job_id}' for {job.get('target_agent_id') or role} "
                        f"has waited {int(age // 60)} min and nothing can take it: {reason}. "
                        f"Job {job_id}"),
        })
        result.escalated.append(job_id)
        _shadow_watchdog(result, jev_provider, db, job_id, "escalate", {
            "worker_state": None,
            "pending_seconds": int(age),
            "reroutes": reroutes,
            "max_reroutes": max_reroutes,
            "stall_seconds": stall,
            "crash_loop": False,
            "chain_stalled": False,
            "broken": True,
            "reason": reason,
        })

    _sweep_chain_stalls(db, result, now=now, config=config, grace=stall,
                        jev_provider=jev_provider)
    return result


def _successor_exists(jobs: list, parent: dict, completed_at: datetime) -> bool:
    """Did the agent that finished `parent` create any job afterwards?"""
    worker = parent.get("leased_by_instance_id")
    parent_id = parent.get("id")
    for job in jobs:
        payload = job.get("input_payload") or {}
        if isinstance(payload, dict) and payload.get("chain_parent") == parent_id:
            return True
        if not worker or job.get("source_agent_id") != worker:
            continue
        created = _parse_ts(job.get("created_at"))
        if created is not None and created >= completed_at:
            return True
    return False


def _shadow_watchdog(
    result: SweepResult,
    provider: Any,
    db: Any,
    job_id: str,
    action: str,
    state: dict,
) -> None:
    """Annotate a concrete watchdog action. Never changes who is rerouted or stalled."""
    try:
        from mco.orchestrator.jev_ops import annotate_watchdog
        result.jev_annotations.append(annotate_watchdog(
            provider,
            deterministic_action=action,
            state=state,
            job_id=job_id,
            db=db,
        ))
    except Exception:
        logger.debug("watchdog shadow annotation skipped", exc_info=True)


def _sweep_chain_stalls(db: Any, result: SweepResult, *, now: datetime,
                        config: dict, grace: int, jev_provider: Any = None) -> None:
    """Catch the failure the rest of this module cannot see: a job that was
    never created.

    A worker that finishes its own job and never hands off leaves NOTHING
    pending, so the board looks idle and healthy while the mission is dead -
    which is exactly how a Score packet chain sat untouched for two hours. A
    job marked `expects_successor` must be followed by a job from the same
    worker; if none appears within the grace window, the chain is stalled.
    """
    from mco.orchestrator.audit import get_events, record_event

    try:
        jobs = db.table("agent_jobs").select("*").execute().data or []
    except Exception:
        logger.exception("chain-stall sweep could not read jobs")
        return

    to_role = get_chain_stall_role(config)
    cutoff = now - timedelta(seconds=CHAIN_LOOKBACK_SECONDS)
    for job in jobs:
        payload = job.get("input_payload") or {}
        if job.get("status") != "completed" or not isinstance(payload, dict):
            continue
        if not payload.get("expects_successor"):
            continue
        completed_at = _parse_ts(job.get("completed_at"))
        if completed_at is None or completed_at < cutoff:
            continue
        if (now - completed_at).total_seconds() < grace:
            continue
        if _successor_exists(jobs, job, completed_at):
            continue
        job_id = job.get("id")
        if any(event.get("event") == CHAIN_STALLED for event in get_events(db, job_id)):
            continue

        worker = job.get("leased_by_instance_id") or "unknown"
        title = job.get("title") or job_id
        detail = {"worker": worker, "completed_at": job.get("completed_at"),
                  "grace_seconds": grace, "handed_to_role": to_role or None}
        record_event(db, job_id, CHAIN_STALLED, ACTOR_ID, ACTOR_ROLE, detail)
        result.broadcasts.append(("chain_stalled", job))
        result.chain_stalled.append(job_id)
        _shadow_watchdog(result, jev_provider, db, job_id, "operator-review", {
            "worker_state": None,
            "pending_seconds": int((now - completed_at).total_seconds()),
            "reroutes": 0,
            "max_reroutes": get_max_reroutes(config),
            "stall_seconds": grace,
            "crash_loop": False,
            "chain_stalled": True,
            "broken": False,
            "reason": "expects_successor with no follow-up",
        })

        if to_role:
            successor = {
                "title": f"Chain stalled: {title}"[:200],
                "description": (
                    f"Job {job_id} ('{title}') completed on {job.get('completed_at')} by {worker} "
                    f"and was expected to hand work to the next agent, but created no follow-up "
                    f"job within {grace // 60} minutes. The board went idle with the mission "
                    f"unfinished.\n\nRead that job's result and its mission notes, then either "
                    f"dispatch the missing next job yourself or report plainly why the chain "
                    f"should stop here. Do not redo the completed work."
                ),
                "source_agent_id": ACTOR_ID,
                "source_agent_role": ACTOR_ROLE,
                "target_agent_role": to_role,
                "status": "pending",
                "priority": 95,
                "input_payload": {"chain_parent": job_id, "stalled_worker": worker},
            }
            if (job.get("org_id") or "default") != "default":
                successor["org_id"] = job["org_id"]
            try:
                created = db.table("agent_jobs").insert(successor).execute().data
            except Exception:
                logger.exception("chain-stall hand-off could not be created for %s", job_id)
                created = None
            if created:
                result.broadcasts.append(("job_pending", created[0]))

        result.notifications.append({
            "title": "BitCadence chain stalled",
            "message": (f"'{title}' finished but never handed off, and nothing is queued. "
                        + (f"Sent to {to_role} to resume. " if to_role else "")
                        + f"Job {job_id}"),
        })


def send_notifications(result: SweepResult) -> None:
    """Push escalations to the operator. Best-effort: ntfy is optional."""
    if not result.notifications:
        return
    from mco.notifiers.ntfy import notify

    for note in result.notifications:
        try:
            notify(note["message"], title=note["title"], priority=4,
                   tags=["mco", "delivery", "escalated"])   # urgent: never dropped for budget
        except Exception as exc:  # pragma: no cover - notifier already swallows
            logger.debug(f"delivery escalation push skipped: {exc}")
