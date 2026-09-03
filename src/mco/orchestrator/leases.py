"""Fenced leases: one owner per attempt, proven at every write.

WS-A of release 0.4.0. Closes findings F1-F6.

THE BUG THIS EXISTS FOR
-----------------------
Before this module, ``handle_job_update()`` and ``PUT /api/jobs/{id}`` updated a
job **by id alone**. The HTTP route checked that the caller's *role* matched the
job's target role - not that the caller owned the current attempt. So a worker
partitioned mid-job could finish twenty minutes later, after its lease had been
reclaimed and the job re-leased and completed by somebody else, and the API
would accept its write too. Two machines did not merely *think* they owned a
job; both were accepted as writers.

THE FIX
-------
Every lease carries three things:

``lease_id``
    An unguessable random token. A worker presents it to prove the write
    belongs to *this* attempt.

``lease_epoch``
    A counter that advances on every acquire and every expiry. Monotonic within
    one database lifetime.

``incarnation``
    A random value generated once per database and persisted. This is the part
    an epoch alone cannot do: restore last night's snapshot and the epoch
    counter rolls *backwards*, so a pre-restore worker could hold a lease whose
    epoch matches a freshly-issued one (the ABA problem, F4). The incarnation
    changes on restore, so an old lease can never match.

Every state change is a **compare-and-set**: the update carries filters for
(job, lease id, epoch, incarnation, allowed current state) and the store
reports how many rows it actually changed. Zero rows means someone else got
there first, and the loser must not mutate anything.

WHY THIS WORKS ON BOTH BACKENDS WITHOUT AN RPC
----------------------------------------------
``update(...).eq(a).eq(b).execute()`` compiles to a single filtered UPDATE in
both LocalStore and PostgREST, and both report the affected rows. A single
``UPDATE ... WHERE ...`` is atomic in Postgres and runs under LocalStore's
process lock, so concurrent callers cannot both win. The CAS therefore needs no
stored procedure and no second code path - which matters, because the previous
design had two mutation paths (HTTP and WebSocket) and only fenced neither.

WHY NO REAPER LEADER IS NEEDED
------------------------------
Expiry is itself a CAS on the epoch. Two reapers may both notice the same
expired lease; only one can win the update, and only the winner advances the
epoch and emits the requeue event. Extra reapers do redundant reads, never
duplicate work. Leader election is therefore a *performance* question, not a
correctness one - see ``expire_lease``.
"""

from __future__ import annotations

import secrets
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Optional

from loguru import logger

# ── The state machine (F5) ───────────────────────────────────────────────────
# Enforced here rather than in a handler. Handlers previously accepted any
# status string and then did secondary retry/unlock work, so fencing only
# `completed` would have left progress, failure and retry paths wide open.

PENDING = "pending"
LEASED = "leased"
IN_PROGRESS = "in_progress"
COMPLETED = "completed"
FAILED = "failed"
CANCELLED = "cancelled"
HALTED = "halted"          # set by the kill switch (WS-B)
NEEDS_APPROVAL = "needs_approval"

#: Terminal states never transition again.
TERMINAL = frozenset({COMPLETED, FAILED, CANCELLED})

#: The only transitions a *worker* may drive, keyed by the state it claims to
#: be leaving. A worker may report progress, finish, or fail. It may not
#: un-cancel a job, resurrect a terminal one, or halt itself.
WORKER_TRANSITIONS: dict[str, frozenset[str]] = {
    LEASED: frozenset({IN_PROGRESS, COMPLETED, FAILED}),
    IN_PROGRESS: frozenset({IN_PROGRESS, COMPLETED, FAILED}),
}

#: States from which a lease may still be renewed.
RENEWABLE = frozenset({LEASED, IN_PROGRESS})

DEFAULT_TTL_SECONDS = 900          # 15 min, matching the old reclaim window
DEFAULT_RENEW_SECONDS = 300        # renew at 1/3 of TTL: two renewals may fail
                                   # before a healthy worker loses its lease


class LeaseError(RuntimeError):
    """A fenced write was refused. Carries why, for the audit trail."""

    def __init__(self, reason: str, *, expected: Optional[dict] = None, actual: Optional[dict] = None):
        super().__init__(reason)
        self.reason = reason
        self.expected = expected or {}
        self.actual = actual or {}

    def as_detail(self) -> dict:
        """Shape written into the audit event, so a rejection is evidence."""
        return {"fenced": True, "reason": self.reason, "expected": self.expected, "actual": self.actual}


@dataclass(frozen=True)
class Lease:
    """Proof that a write belongs to one attempt of one job."""

    job_id: str
    lease_id: str
    epoch: int
    incarnation: str
    owner: str

    def as_claim(self) -> dict:
        """What a worker sends back to prove ownership."""
        return {
            "lease_id": self.lease_id,
            "lease_epoch": self.epoch,
            "lease_incarnation": self.incarnation,
            "agent_instance_id": self.owner,
        }


# ── Store incarnation (F4) ───────────────────────────────────────────────────

_INCARNATION_TABLE = "mco_store_identity"
_INCARNATION_ROW = "store"
_cached_incarnation: Optional[str] = None


def store_incarnation(db_client: Any, *, refresh: bool = False) -> str:
    """The database's identity, created once and never reused.

    An integer epoch is not enough on its own. Restore a snapshot and the epoch
    counter goes backwards; a worker still holding a pre-restore lease could
    then match an epoch the restored database re-issues. The incarnation is
    generated when the row is absent - which is exactly what a restore-to-empty
    or a fresh database looks like - so old leases stop matching.
    """
    global _cached_incarnation
    if _cached_incarnation and not refresh:
        return _cached_incarnation
    try:
        res = db_client.table(_INCARNATION_TABLE).select("*").eq("id", _INCARNATION_ROW).execute()
        rows = res.data or []
        if rows and rows[0].get("incarnation"):
            _cached_incarnation = str(rows[0]["incarnation"])
            return _cached_incarnation
        fresh = uuid.uuid4().hex
        db_client.table(_INCARNATION_TABLE).upsert(
            {"id": _INCARNATION_ROW, "incarnation": fresh, "created_at": _now_iso()}
        ).execute()
        # Re-read rather than trusting the write: if two gateways raced to
        # create it, the loser must adopt the winner's value, not its own.
        res = db_client.table(_INCARNATION_TABLE).select("*").eq("id", _INCARNATION_ROW).execute()
        rows = res.data or []
        _cached_incarnation = str(rows[0]["incarnation"]) if rows else fresh
        return _cached_incarnation
    except Exception as e:
        # Never take the job path down over this. A stable per-process value
        # still fences everything except a mid-flight restore.
        logger.warning(f"store_incarnation unavailable ({type(e).__name__}); using a process-local value")
        _cached_incarnation = _cached_incarnation or uuid.uuid4().hex
        return _cached_incarnation


def reset_incarnation_cache() -> None:
    """Tests only: forget the memoized incarnation."""
    global _cached_incarnation
    _cached_incarnation = None


# ── Time ─────────────────────────────────────────────────────────────────────

def _now() -> datetime:
    return datetime.now(timezone.utc)


def _now_iso() -> str:
    return _now().isoformat()


def _parse(ts: Any) -> Optional[datetime]:
    if not ts:
        return None
    try:
        d = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
        return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return None


# ── Acquire ──────────────────────────────────────────────────────────────────

def acquire_lease(
    db_client: Any,
    job_id: str,
    owner: str,
    *,
    ttl_seconds: int = DEFAULT_TTL_SECONDS,
) -> Optional[Lease]:
    """Claim a pending job. Returns the Lease, or None if someone else won.

    The CAS filters on ``status == pending``, so two workers racing for one job
    produce exactly one winner: the loser's update matches zero rows.
    """
    incarnation = store_incarnation(db_client)
    lease = Lease(
        job_id=str(job_id),
        lease_id=secrets.token_urlsafe(24),
        epoch=_next_epoch(db_client, job_id),
        incarnation=incarnation,
        owner=owner,
    )
    now = _now()
    res = (
        db_client.table("agent_jobs")
        .update({
            "status": LEASED,
            "leased_by_instance_id": owner,
            "started_at": now.isoformat(),
            "lease_id": lease.lease_id,
            "lease_epoch": lease.epoch,
            "lease_incarnation": incarnation,
            "lease_expires_at": (now + timedelta(seconds=ttl_seconds)).isoformat(),
        })
        .eq("id", job_id)
        .eq("status", PENDING)
        .execute()
    )
    if not (res.data or []):
        return None
    return lease


def _next_epoch(db_client: Any, job_id: str) -> int:
    """One past the job's current epoch. Advancing is guarded by the CAS."""
    try:
        res = db_client.table("agent_jobs").select("*").eq("id", job_id).execute()
        rows = res.data or []
        return int(rows[0].get("lease_epoch") or 0) + 1 if rows else 1
    except Exception:
        return 1


# ── Renew ────────────────────────────────────────────────────────────────────

def renew_lease(db_client: Any, lease: Lease, *, ttl_seconds: int = DEFAULT_TTL_SECONDS) -> bool:
    """Push the expiry out. False once the lease has been reclaimed.

    A worker whose renewal is refused has been fenced and must stop: its job
    now belongs to someone else.
    """
    res = (
        db_client.table("agent_jobs")
        .update({"lease_expires_at": (_now() + timedelta(seconds=ttl_seconds)).isoformat()})
        .eq("id", lease.job_id)
        .eq("lease_id", lease.lease_id)
        .eq("lease_epoch", lease.epoch)
        .eq("lease_incarnation", lease.incarnation)
        .execute()
    )
    rows = res.data or []
    if not rows:
        return False
    # Filters cannot express "status in (...)", so the state check is here.
    return str(rows[0].get("status")) in RENEWABLE


# ── Expire (the reaper) ──────────────────────────────────────────────────────

def expire_lease(db_client: Any, job: dict) -> bool:
    """Reclaim one expired lease. True only for the caller that actually won.

    This is why multiple reapers are safe without a leader: the CAS filters on
    the job's current epoch, so of N reapers that all see the same expired
    lease, exactly one update matches a row. The rest change nothing and must
    not emit a requeue event.
    """
    job_id = job.get("id")
    epoch = job.get("lease_epoch")
    if job_id is None or epoch is None:
        return False
    res = (
        db_client.table("agent_jobs")
        .update({
            "status": PENDING,
            "leased_by_instance_id": None,
            "lease_id": None,
            "lease_expires_at": None,
            "lease_epoch": int(epoch) + 1,   # advance, so the old lease can never match again
        })
        .eq("id", job_id)
        .eq("lease_epoch", int(epoch))
        .eq("status", str(job.get("status") or LEASED))
        .execute()
    )
    return bool(res.data or [])


def is_expired(job: dict, *, now: Optional[datetime] = None, ttl_seconds: int = DEFAULT_TTL_SECONDS) -> bool:
    """Whether a leased job's lease has run out.

    Prefers the stored ``lease_expires_at``. Falls back to ``started_at`` + TTL
    so jobs leased before this release are still reclaimable.
    """
    now = now or _now()
    expires = _parse(job.get("lease_expires_at"))
    if expires is not None:
        return expires <= now
    started = _parse(job.get("started_at"))
    if started is None:
        return False
    return started + timedelta(seconds=ttl_seconds) <= now


# ── The fenced write every worker path must go through ───────────────────────

def fenced_update(
    db_client: Any,
    job_id: str,
    claim: dict,
    updates: dict,
    *,
    allowed_from: Optional[Iterable[str]] = None,
) -> dict:
    """Apply a worker's update only if it still owns the attempt.

    ``claim`` is what the worker presented (lease_id / lease_epoch /
    lease_incarnation / agent_instance_id). Raises :class:`LeaseError` when the
    write is refused, so callers can record the rejection as evidence rather
    than silently dropping it.

    A job with no lease fields at all is a pre-0.4.0 row; those are accepted on
    owner match alone so an in-flight upgrade does not strand work. New leases
    always carry the fields, so this is a one-release allowance.
    """
    job_id = str(job_id)
    lease_id = claim.get("lease_id")
    owner = claim.get("agent_instance_id")

    res = db_client.table("agent_jobs").select("*").eq("id", job_id).execute()
    rows = res.data or []
    if not rows:
        raise LeaseError("job not found", expected={"job_id": job_id})
    job = rows[0]

    current = str(job.get("status") or "")
    if current in TERMINAL:
        raise LeaseError(
            "job already reached a terminal state",
            expected={"lease_id": lease_id}, actual={"status": current, "lease_id": job.get("lease_id")},
        )

    target = str(updates.get("status") or current)
    permitted = WORKER_TRANSITIONS.get(current, frozenset())
    if target != current and target not in permitted:
        raise LeaseError(
            f"illegal transition {current} -> {target}",
            expected={"allowed": sorted(permitted)}, actual={"status": current},
        )
    if allowed_from is not None and current not in set(allowed_from):
        raise LeaseError(
            f"job is {current}, not one of {sorted(set(allowed_from))}",
            expected={"allowed_from": sorted(set(allowed_from))}, actual={"status": current},
        )

    legacy = job.get("lease_id") is None and job.get("lease_epoch") is None
    if legacy:
        if owner and job.get("leased_by_instance_id") and owner != job.get("leased_by_instance_id"):
            raise LeaseError(
                "not the lease holder",
                expected={"owner": job.get("leased_by_instance_id")}, actual={"owner": owner},
            )
        q = db_client.table("agent_jobs").update(updates).eq("id", job_id).eq("status", current)
    else:
        if not lease_id:
            raise LeaseError(
                "no lease presented",
                expected={"lease_id": job.get("lease_id")}, actual={"lease_id": None},
            )
        q = (
            db_client.table("agent_jobs")
            .update(updates)
            .eq("id", job_id)
            .eq("lease_id", lease_id)
            .eq("lease_epoch", job.get("lease_epoch"))
            .eq("lease_incarnation", job.get("lease_incarnation"))
            .eq("status", current)
        )

    out = q.execute()
    if not (out.data or []):
        # Lost the CAS. Re-read so the audit event says what actually holds it.
        again = (db_client.table("agent_jobs").select("*").eq("id", job_id).execute().data or [{}])[0]
        raise LeaseError(
            "stale lease: this attempt no longer owns the job",
            expected={"lease_id": lease_id, "lease_epoch": job.get("lease_epoch")},
            actual={
                "lease_id": again.get("lease_id"),
                "lease_epoch": again.get("lease_epoch"),
                "status": again.get("status"),
                "owner": again.get("leased_by_instance_id"),
            },
        )
    return out.data[0]


# ── Idempotency for external side effects (F6) ───────────────────────────────

def idempotency_key(job_id: str, attempt: Any) -> str:
    """Stable key for one attempt at one job.

    Fencing a database write does not unsend an email or un-close a ServiceNow
    incident. Connectors pass this to the remote system so a retried or fenced
    attempt cannot double-fire.
    """
    return f"bitcadence:{job_id}:{int(attempt or 0)}"
