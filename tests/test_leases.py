"""Adversarial tests for fenced leases (WS-A, release 0.4.0).

The happy path is not the point. Every test here is a race or a replay that
the pre-0.4.0 code lost:

* two workers claiming one job
* a partitioned worker returning after its lease was reclaimed  (F1 - the bug
  that made the demo's P3 pavilion red)
* two reapers reclaiming the same lease                         (F2)
* a renewal arriving after expiry                               (F3)
* a restored snapshot letting an old lease match a new epoch    (F4 - ABA)
* a worker driving an illegal transition                        (F5)

They run against LocalStore because it is the backend a test can hold in a
temp directory, and because the compare-and-set is written in the shared
query-builder dialect - the same calls compile to a filtered UPDATE on
PostgREST, so what passes here is the same mechanism that runs on Postgres.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from mco.localstore import LocalStore
from mco.orchestrator import leases
from mco.orchestrator.leases import (
    COMPLETED,
    IN_PROGRESS,
    LEASED,
    PENDING,
    Lease,
    LeaseError,
    acquire_lease,
    expire_lease,
    fenced_update,
    idempotency_key,
    is_expired,
    renew_lease,
    store_incarnation,
)


@pytest.fixture
def store(tmp_path):
    leases.reset_incarnation_cache()
    s = LocalStore(tmp_path / "leases.db")
    yield s
    s.close()
    leases.reset_incarnation_cache()


def make_job(store, job_id="J-1", **extra):
    row = {
        "id": job_id,
        "title": "t",
        "status": PENDING,
        "target_agent_role": "codex",
        "created_at": datetime.now(timezone.utc).isoformat(),
        **extra,
    }
    store.table("agent_jobs").insert(row).execute()
    return row


def get(store, job_id="J-1"):
    return (store.table("agent_jobs").select("*").eq("id", job_id).execute().data or [{}])[0]


# ── acquire ──────────────────────────────────────────────────────────────────

def test_two_workers_race_one_wins(store):
    make_job(store)
    a = acquire_lease(store, "J-1", "worker-a")
    b = acquire_lease(store, "J-1", "worker-b")
    assert a is not None, "first claimant must win"
    assert b is None, "second claimant must lose: the CAS filters on status=pending"
    assert get(store)["leased_by_instance_id"] == "worker-a"


def test_acquire_stamps_full_lease_identity(store):
    make_job(store)
    lease = acquire_lease(store, "J-1", "worker-a")
    row = get(store)
    assert row["lease_id"] == lease.lease_id and len(lease.lease_id) >= 24
    assert row["lease_epoch"] == lease.epoch >= 1
    assert row["lease_incarnation"] == store_incarnation(store)
    assert row["lease_expires_at"], "a lease with no expiry can never be reclaimed"


# ── F1: the stale writer ─────────────────────────────────────────────────────

def test_partitioned_worker_cannot_complete_a_rebleased_job(store):
    """The exact scenario the demo's P3 pavilion injects.

    Worker A leases and is partitioned. Its lease expires, the reaper reclaims,
    worker B leases and completes. A comes back and reports success. Before
    0.4.0 the API accepted A's write on top of B's.
    """
    make_job(store)
    a = acquire_lease(store, "J-1", "worker-a")

    # A is partitioned; the lease ages out and the reaper reclaims it.
    store.table("agent_jobs").update(
        {"lease_expires_at": (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()}
    ).eq("id", "J-1").execute()
    assert expire_lease(store, get(store)) is True

    b = acquire_lease(store, "J-1", "worker-b")
    assert b is not None
    fenced_update(store, "J-1", b.as_claim(), {"status": IN_PROGRESS})

    # A's partition heals and it reports success while B is still working. The
    # job is NOT terminal here, deliberately: if B had already finished, the
    # terminal-state guard would refuse A and this test would pass without the
    # fence ever being exercised. Mutation-testing caught exactly that.
    with pytest.raises(LeaseError) as err:
        fenced_update(store, "J-1", a.as_claim(), {"status": COMPLETED, "output_payload": {"by": "a"}})

    detail = err.value.as_detail()
    assert detail["fenced"] is True
    assert "stale lease" in detail["reason"], "must be refused BY THE FENCE, not by the terminal guard"
    assert "lease_id" not in detail["actual"], "errors must not disclose another attempt capability"

    # B, which still owns the attempt, finishes normally and its result stands.
    fenced_update(store, "J-1", b.as_claim(), {"status": COMPLETED, "output_payload": {"by": "b"}})
    assert get(store)["output_payload"] == {"by": "b"}, "B's result must survive"


def test_stale_worker_blocked_even_before_terminal(store):
    """Fencing must not depend on the job having already finished."""
    make_job(store)
    a = acquire_lease(store, "J-1", "worker-a")
    store.table("agent_jobs").update({"lease_expires_at": "2000-01-01T00:00:00+00:00"}).eq("id", "J-1").execute()
    expire_lease(store, get(store))
    acquire_lease(store, "J-1", "worker-b")

    with pytest.raises(LeaseError, match="stale lease"):
        fenced_update(store, "J-1", a.as_claim(), {"status": IN_PROGRESS})


# ── F2: two reapers ──────────────────────────────────────────────────────────

def test_two_reapers_exactly_one_wins(store):
    """Why no leader is needed: expiry is itself a CAS on the epoch."""
    make_job(store)
    acquire_lease(store, "J-1", "worker-a")
    store.table("agent_jobs").update({"lease_expires_at": "2000-01-01T00:00:00+00:00"}).eq("id", "J-1").execute()

    snapshot = get(store)                      # both reapers read the same row
    first = expire_lease(store, snapshot)
    second = expire_lease(store, snapshot)

    assert (first, second) == (True, False), "exactly one reaper may requeue"
    assert get(store)["status"] == PENDING


def test_expiry_advances_the_epoch(store):
    make_job(store)
    a = acquire_lease(store, "J-1", "worker-a")
    store.table("agent_jobs").update({"lease_expires_at": "2000-01-01T00:00:00+00:00"}).eq("id", "J-1").execute()
    expire_lease(store, get(store))
    assert get(store)["lease_epoch"] > a.epoch, "a reclaimed epoch must never be reusable"


# ── F3: renewal ──────────────────────────────────────────────────────────────

def test_renew_extends_then_is_refused_after_reclaim(store):
    make_job(store)
    a = acquire_lease(store, "J-1", "worker-a")
    before = get(store)["lease_expires_at"]
    assert renew_lease(store, a) is True
    assert get(store)["lease_expires_at"] >= before

    store.table("agent_jobs").update({"lease_expires_at": "2000-01-01T00:00:00+00:00"}).eq("id", "J-1").execute()
    expire_lease(store, get(store))
    assert renew_lease(store, a) is False, "a fenced worker must learn it has been fenced"


def test_is_expired_falls_back_to_started_at_for_pre_0_4_rows(store):
    old = (datetime.now(timezone.utc) - timedelta(seconds=1000)).isoformat()
    assert is_expired({"started_at": old}, ttl_seconds=900) is True
    fresh = datetime.now(timezone.utc).isoformat()
    assert is_expired({"started_at": fresh}, ttl_seconds=900) is False


# ── F4: the ABA problem after a snapshot restore ─────────────────────────────

def test_restored_snapshot_cannot_be_written_by_a_pre_restore_lease(store):
    """An integer epoch alone is not enough.

    Restore last night's database and the epoch counter rolls backwards, so a
    worker still holding a pre-restore lease could match a freshly-issued
    epoch. The incarnation - regenerated when the identity row is absent, which
    is what a restore looks like - is what actually closes this.
    """
    make_job(store)
    a = acquire_lease(store, "J-1", "worker-a")

    # Simulate the restore: identity row gone, job row back to its pre-lease
    # state, and the epoch counter reset to what it was.
    store.table(leases._INCARNATION_TABLE).delete().eq("id", leases._INCARNATION_ROW).execute()
    leases.reset_incarnation_cache()
    store.table("agent_jobs").update(
        {"status": PENDING, "lease_id": None, "lease_epoch": a.epoch - 1,
         "lease_incarnation": None, "leased_by_instance_id": None}
    ).eq("id", "J-1").execute()

    fresh = acquire_lease(store, "J-1", "worker-b")
    assert fresh is not None
    assert fresh.epoch == a.epoch, "the epoch genuinely collided - that is the ABA setup"
    assert fresh.incarnation != a.incarnation, "the incarnation must differ after a restore"

    with pytest.raises(LeaseError):
        fenced_update(store, "J-1", a.as_claim(), {"status": COMPLETED})


# ── F5: the state machine ────────────────────────────────────────────────────

def test_worker_cannot_drive_an_illegal_transition(store):
    make_job(store)
    lease = acquire_lease(store, "J-1", "worker-a")
    with pytest.raises(LeaseError, match="illegal transition"):
        fenced_update(store, "J-1", lease.as_claim(), {"status": "cancelled"})


def test_terminal_jobs_are_closed_to_workers(store):
    make_job(store)
    lease = acquire_lease(store, "J-1", "worker-a")
    fenced_update(store, "J-1", lease.as_claim(), {"status": COMPLETED})
    assert fenced_update(store, "J-1", lease.as_claim(), {"status": COMPLETED})["_replayed"]
    with pytest.raises(LeaseError):
        fenced_update(store, "J-1", lease.as_claim(), {"status": COMPLETED, "output_payload": {"changed": True}})


def test_progress_updates_are_allowed_and_keep_the_lease(store):
    make_job(store)
    lease = acquire_lease(store, "J-1", "worker-a")
    fenced_update(store, "J-1", lease.as_claim(), {"status": IN_PROGRESS})
    fenced_update(store, "J-1", lease.as_claim(), {"status": IN_PROGRESS})
    assert fenced_update(store, "J-1", lease.as_claim(), {"status": COMPLETED})["status"] == COMPLETED


# ── upgrade safety ───────────────────────────────────────────────────────────

def test_pre_0_4_leased_row_still_completes(store):
    """An in-flight upgrade must not strand work leased by the old code."""
    make_job(store, status=LEASED, leased_by_instance_id="worker-a",
             started_at=datetime.now(timezone.utc).isoformat())
    row = fenced_update(store, "J-1", {"agent_instance_id": "worker-a"}, {"status": COMPLETED})
    assert row["status"] == COMPLETED


def test_pre_0_4_row_still_rejects_a_different_owner(store):
    make_job(store, status=LEASED, leased_by_instance_id="worker-a",
             started_at=datetime.now(timezone.utc).isoformat())
    with pytest.raises(LeaseError, match="not the lease holder"):
        fenced_update(store, "J-1", {"agent_instance_id": "worker-b"}, {"status": COMPLETED})


def test_new_lease_requires_a_presented_lease_id(store):
    make_job(store)
    acquire_lease(store, "J-1", "worker-a")
    with pytest.raises(LeaseError, match="no lease presented"):
        fenced_update(store, "J-1", {"agent_instance_id": "worker-a"}, {"status": COMPLETED})


# ── F6 ───────────────────────────────────────────────────────────────────────

def test_idempotency_key_is_stable_per_attempt(store):
    assert idempotency_key("J-1", 2) == idempotency_key("J-1", 2)
    assert idempotency_key("J-1", 2) != idempotency_key("J-1", 3)


# ── incarnation ──────────────────────────────────────────────────────────────

def test_incarnation_is_created_once_and_reused(store):
    first = store_incarnation(store)
    leases.reset_incarnation_cache()
    assert store_incarnation(store) == first, "must persist, not regenerate per process"
