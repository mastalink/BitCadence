"""Timer-driven owner notification maintenance."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from mco.notifiers.ntfy import flush_batched, is_owner_decision, notify_event


EASTERN = ZoneInfo("America/New_York")
_last_digest_date = [None]
_PENDING_STATUSES = ("pending", "needs_approval", "waiting")


def pending_decisions(db) -> list[dict]:
    rows = (
        db.table("agent_jobs")
        .select("id,title,status")
        .in_("status", _PENDING_STATUSES)
        .execute()
        .data
        or []
    )
    return [row for row in rows if is_owner_decision(row.get("title", ""))]


def maintenance_once(db, now: datetime | None = None) -> dict:
    """Flush overflow and send one 08:00 ET digest only when work is pending."""
    flushed = flush_batched()
    local_now = (now or datetime.now(EASTERN)).astimezone(EASTERN)
    sent_digest = False
    pending = 0
    if local_now.hour == 8 and _last_digest_date[0] != local_now.date():
        decisions = pending_decisions(db)
        pending = len(decisions)
        # Mark the date after the query, even when empty: silence is intentional.
        _last_digest_date[0] = local_now.date()
        if decisions:
            sent_digest = notify_event(
                "digest", decisions[0].get("id", "digest"), count=len(decisions),
            )
    return {"flushed": flushed, "digest_sent": sent_digest, "pending": pending}
