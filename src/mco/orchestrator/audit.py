"""
Tamper-evident, append-only audit trail for the Job Board.

Every job mutation (create, lease, status change, approval decision, retry,
escalation) is recorded as an append-only row in `agent_job_events`. The table
is protected by a database trigger that rejects UPDATE/DELETE (see
docs/migrations/) and, on the embedded LocalStore, by APPEND_ONLY_TABLES - so
the trail can only ever grow.

On top of append-only storage, every row is hash-chained:

    prev_hash = hash of the immediately preceding event for the same job
                ("" for the first event)
    hash      = sha256( prev_hash + "\n" + canonical(content) )

where `content` is a deterministic JSON serialization of the row's meaningful
fields (job_id, event, actor_id, actor_role, detail, created_at). Because each
hash folds in the previous one, removing, reordering, or editing any event
breaks every link after it - even an operator with direct database access
cannot rewrite history without detection.

Optionally, when an audit HMAC key is configured in the encrypted secret store
(secret name ``MCO_AUDIT_HMAC_KEY``), each row also carries an HMAC-SHA256
``signature`` over its ``hash``. The signature proves the chain was produced by
a holder of the key, defending against an attacker who recomputes a consistent
chain from scratch.

Audit failures propagate. Job mutations also persist an outbox row in the
same database transaction, so a failed rich event cannot erase state evidence.
"""

import hashlib
import hmac
import json
import logging
import threading
from mco.orchestrator.leases import transaction, is_postgres
from typing import Any, Optional

logger = logging.getLogger("mco.orchestrator.audit")

EVENTS_TABLE = "agent_job_events"

# Secret name under which an optional audit-signing key lives in the secret store.
HMAC_SECRET_NAME = "MCO_AUDIT_HMAC_KEY"

# Process-level cache for the resolved HMAC key. The key is immutable for the
# life of the process, and resolving it can trigger a vault auto_unlock attempt
# (with its own warning logs) - so we resolve at most once. Sentinel means
# "not yet resolved"; None means "resolved, no key configured".
_HMAC_KEY_UNRESOLVED = object()
_hmac_key_cache: Any = _HMAC_KEY_UNRESOLVED

# Fields that make up a row's signed/hashed content. Storage-only columns
# (id, prev_hash, hash, signature, org_id) are deliberately excluded so the
# hash is stable across backends that assign ids/tenants differently.
_CONTENT_FIELDS = ("job_id", "event", "actor_id", "actor_role", "detail", "created_at")

GENESIS_HASH = ""  # prev_hash of the very first event in a chain


def _canonical(content: dict) -> str:
    """Deterministic JSON serialization used as hash input.

    sort_keys + compact separators make the encoding stable regardless of dict
    insertion order or backend formatting, so the same logical row always hashes
    to the same value on LocalStore and Supabase alike.
    """
    return json.dumps(content, sort_keys=True, separators=(",", ":"), default=str)


def _content_of(row: dict) -> dict:
    """Project a stored row down to the fields the hash is computed over."""
    content = {f: row.get(f) for f in _CONTENT_FIELDS}
    # PostgREST trims fractional trailing zeroes from timestamptz; psycopg
    # preserves six digits. Reuse the signed representation only when both
    # timestamps denote the same instant. All actual row fields still get hashed.
    if row.get('canonical_content'):
        try:
            from datetime import datetime
            stamp = json.loads(row['canonical_content'])['created_at']
            if datetime.fromisoformat(stamp.replace('Z','+00:00')) == datetime.fromisoformat(str(content['created_at']).replace('Z','+00:00')):
                content['created_at'] = stamp
        except (ValueError, TypeError, KeyError):
            pass
    return content


def compute_hash(prev_hash: str, content: dict) -> str:
    """Hash one event: sha256(prev_hash + '\\n' + canonical(content))."""
    material = f"{prev_hash or ''}\n{_canonical(content)}".encode("utf-8")
    return hashlib.sha256(material).hexdigest()


def _resolve_audit_hmac_key() -> Optional[bytes]:
    """Look up the audit HMAC key in the secret store, or None if unavailable.

    Never raises: any failure to reach/unlock the store means "no signing",
    which keeps hash-chaining working on installs without a configured vault.
    """
    try:
        import os
        if os.environ.get(HMAC_SECRET_NAME):
            return os.environ[HMAC_SECRET_NAME].encode("utf-8")
        from mco.security import get_secret_store

        store = get_secret_store()
        if not store.is_initialized():
            return None
        if not store.is_unlocked and not store.auto_unlock():
            return None
        raw = store.get(HMAC_SECRET_NAME)
        if not raw:
            return None
        return raw.encode("utf-8")
    except Exception as e:  # pragma: no cover - defensive
        logger.debug(f"Audit HMAC key unavailable: {type(e).__name__}")
        return None


def _audit_hmac_key() -> Optional[bytes]:
    """Return the (process-cached) audit HMAC key, resolving it at most once."""
    global _hmac_key_cache
    if _hmac_key_cache is _HMAC_KEY_UNRESOLVED:
        _hmac_key_cache = _resolve_audit_hmac_key()
    return _hmac_key_cache


def _sign(row_hash: str, key: Optional[bytes]) -> Optional[str]:
    """HMAC-SHA256 over the row hash, or None when no key is configured."""
    if not key:
        return None
    return hmac.new(key, row_hash.encode("utf-8"), hashlib.sha256).hexdigest()


def _last_event(db_client: Any, job_id: str) -> Optional[dict]:
    """Most recent event for a job (by created_at), or None for a fresh chain."""
    res = (db_client.table(EVENTS_TABLE).select("*").eq("job_id", str(job_id))
           .order("created_at", desc=False).execute())
    rows = res.data or []
    return rows[-1] if rows else None


# Read-prev-then-append must be atomic per job, or two concurrent writers can
# read the same tail and append two rows carrying the same prev_hash. That fork
# is indistinguishable from a deleted event when verify_chain() walks the trail,
# so it would raise a *false* tamper alarm on a perfectly honest install. One
# gateway is a single process, so a per-job in-process lock closes the window.
# (Locks are keyed by job and never evicted; jobs are finite and a lock is tiny.)
_chain_locks_guard = threading.Lock()
_chain_locks: dict[str, threading.Lock] = {}


def _chain_lock(job_id: str) -> threading.Lock:
    """Return the append-lock for one job's chain, creating it on first use."""
    with _chain_locks_guard:
        lock = _chain_locks.get(job_id)
        if lock is None:
            lock = threading.Lock()
            _chain_locks[job_id] = lock
        return lock


def _record_event(
    db_client: Any,
    job_id: str,
    event: str,
    actor_id: Optional[str] = None,
    actor_role: Optional[str] = None,
    detail: Optional[dict] = None,
    *, outbox_id: Optional[str] = None,
) -> bool:
    """Append evidence; storage failure is a failed operation, never success."""
    if db_client is None or not job_id:
        return False
    try:
        with transaction(db_client), _chain_lock(str(job_id)):
            if is_postgres(db_client):
                # PostgreSQL serializes the append and finalizes the previous
                # hash under a database lock. Canonical content travels as text
                # so Python and PostgreSQL hash exactly the same bytes.
                from mco.localstore import _now_iso
                content = {"job_id": str(job_id), "event": event, "actor_id": actor_id,
                           "actor_role": actor_role, "detail": detail or {}, "created_at": _now_iso()}
                key = _audit_hmac_key()
                db_client.rpc("mco_append_event", {"p_content": _canonical(content),
                    "p_key": key.decode("utf-8") if key else None,
                    "p_outbox_id": outbox_id}).execute()
                return True
            if outbox_id and db_client.table(EVENTS_TABLE).select("*").eq("outbox_id", outbox_id).execute().data:
                return True
            prev = _last_event(db_client, str(job_id))
            prev_hash = (prev or {}).get("hash") or GENESIS_HASH

            # The row content is finalized BEFORE hashing so the stored hash covers
            # exactly what is persisted. created_at is stamped here for the same
            # reason - a server-assigned timestamp could not be folded into the hash.
            from mco.localstore import _now_iso  # local import avoids a cycle at import time

            content = {
                "job_id": str(job_id),
                "event": event,
                "actor_id": actor_id,
                "actor_role": actor_role,
                "detail": detail or {},
                "created_at": _now_iso(),
            }
            row_hash = compute_hash(prev_hash, content)
            signature = _sign(row_hash, _audit_hmac_key())

            record = dict(content)
            record["prev_hash"] = prev_hash
            record["hash"] = row_hash
            if signature is not None:
                record["signature"] = signature

            if outbox_id:
                record["outbox_id"] = outbox_id
            # Keep per-job timestamps ordered even if the system clock moves back.
            if prev and content["created_at"] <= prev["created_at"]:
                from datetime import datetime, timedelta
                record["created_at"] = (datetime.fromisoformat(prev["created_at"]) + timedelta(microseconds=1)).isoformat()
                record["hash"] = compute_hash(prev_hash, _content_of(record))
                if signature is not None:
                    record["signature"] = _sign(record["hash"], _audit_hmac_key())
            db_client.table(EVENTS_TABLE).insert(record).execute()
        return True
    except Exception as e:
        logger.error(f"Audit write failed for job {job_id} ({event}): {type(e).__name__}")
        raise


def get_events(db_client: Any, job_id: str) -> list:
    """Return the full audit trail for a job, oldest first."""
    if db_client is None:
        return []
    try:
        res = (
            db_client.table(EVENTS_TABLE)
            .select("*")
            .eq("job_id", str(job_id))
            .order("created_at", desc=False)
            .execute()
        )
        return res.data or []
    except Exception as e:
        logger.error(f"Error fetching audit events for job {job_id}: {type(e).__name__}")
        raise


def verify_chain(db_client: Any, job_id: str, checkpoint: Optional[dict] = None) -> dict:
    """Walk a job's hash chain and report integrity.

    Returns a dict::

        {
          "job_id": <id>,
          "ok": bool,
          "count": <events checked>,
          "broken_at": <1-based index of first bad link> | None,
          "reason": <human-readable description> | None,
          "signed": bool,     # whether a signing key was available for checks
        }

    A chain with no events is trivially OK. The first row whose stored hash,
    prev_hash linkage, or HMAC signature does not match is reported as the
    broken link; verification stops there.
    """
    events = get_events(db_client, str(job_id))
    key = _audit_hmac_key()
    result = {
        "job_id": str(job_id),
        "ok": True,
        "count": len(events),
        "broken_at": None,
        "reason": None,
        "signed": key is not None,
    }

    expected_prev = GENESIS_HASH
    for idx, row in enumerate(events, start=1):
        stored_prev = row.get("prev_hash") or GENESIS_HASH
        if stored_prev != expected_prev:
            result.update(ok=False, broken_at=idx,
                          reason=f"prev_hash mismatch at event {idx} "
                                 f"(expected {expected_prev or '<genesis>'}, "
                                 f"got {stored_prev or '<genesis>'})")
            return result

        recomputed = compute_hash(stored_prev, _content_of(row))
        stored_hash = row.get("hash") or ""
        if not hmac.compare_digest(recomputed, stored_hash):
            result.update(ok=False, broken_at=idx,
                          reason=f"content hash mismatch at event {idx} "
                                 f"(row '{row.get('event')}' was altered or "
                                 f"hash is missing)")
            return result

        # Signature check only when both a key is configured AND the row carries
        # one. A row signed under a different/absent key fails closed.
        sig = row.get("signature")
        if key is not None:
            expected_sig = _sign(stored_hash, key)
            if not sig or not hmac.compare_digest(sig, expected_sig or ""):
                result.update(ok=False, broken_at=idx,
                              reason=f"signature mismatch at event {idx} "
                                     f"(HMAC does not verify under the configured key)")
                return result

        expected_prev = stored_hash

    if checkpoint is not None:
        signed = {k: checkpoint.get(k) for k in ("job_id", "count", "head_hash")}
        signature = _sign(_canonical(signed), key)
        if not key or not hmac.compare_digest(checkpoint.get("signature") or "", signature or ""):
            result.update(ok=False, reason="checkpoint signature does not verify")
        elif str(checkpoint.get("job_id")) != str(job_id) or len(events) < checkpoint.get("count", 0):
            result.update(ok=False, reason="checkpoint detects a deleted audit tail")
        elif checkpoint.get("count", 0) and events[checkpoint["count"] - 1].get("hash") != checkpoint.get("head_hash"):
            result.update(ok=False, reason="checkpoint head differs from audit history")
    return result



def make_checkpoint(db_client, job_id):
    """Export a signed head; retain it outside the database being verified."""
    key = _audit_hmac_key()
    if not key:
        raise RuntimeError("Configure MCO_AUDIT_HMAC_KEY before exporting signed checkpoints")
    events = get_events(db_client, str(job_id))
    result = {"job_id": str(job_id), "count": len(events),
              "head_hash": events[-1]["hash"] if events else ""}
    result["signature"] = _sign(_canonical(result), key)
    return result


def record_event(db_client, job_id, *args, **kwargs):
    result = _record_event(db_client, job_id, *args, **kwargs)
    if result:
        from mco.orchestrator.evidence import publish_events
        publish_events(db_client, job_id)
    return result


def drain_outbox(db_client, job_id=None):
    """Idempotently materialize committed state evidence into the audit chain."""
    query = db_client.table("mco_audit_outbox").select("*").order("created_at")
    if job_id is not None:
        query = query.eq("job_id", str(job_id))
    rows = query.execute().data or []
    count = 0
    for row in rows:
        oid = str(row["id"])
        if db_client.table(EVENTS_TABLE).select("*").eq("outbox_id", oid).execute().data:
            continue
        record_event(db_client, row["job_id"], row["event"], "system", "store",
                     row.get("detail") or {}, outbox_id=oid)
        count += 1
    return count
