"""Presence state: working / standby / broken / offline, derived from heartbeats,
live waker sockets, leases, stalled deliveries and delivery reroutes. Uses a
real LocalStore so the job and audit-event reads run on the embedded backend."""

from datetime import datetime, timedelta, timezone

import pytest

from mco.localstore import LocalStore
from mco.orchestrator import presence
from mco.orchestrator.audit import record_event
from mco.orchestrator.delivery import REROUTED

THRESHOLD = 300
STALL = 600


@pytest.fixture
def db(tmp_path):
    s = LocalStore(tmp_path / "test.db")
    yield s
    s.close()


def _now():
    return datetime.now(timezone.utc)


def _agent(instance, role, *, seen_seconds_ago=None, status="online"):
    return {
        "instance_id": instance, "role": role, "status": status,
        "last_seen_at": None if seen_seconds_ago is None
        else (_now() - timedelta(seconds=seen_seconds_ago)).isoformat(),
    }


def _job(db, job_id, *, status="pending", role="codex", target=None, age=0, leased_by=None):
    db.table("agent_jobs").insert({
        "id": job_id, "title": job_id, "status": status,
        "target_agent_role": role, "target_agent_id": target,
        "leased_by_instance_id": leased_by,
        "created_at": (_now() - timedelta(seconds=age)).isoformat(),
    }).execute()


def _describe(db, rows, connected=frozenset()):
    return {r["instance_id"]: r for r in presence.describe_fleet(
        db, rows, threshold=THRESHOLD, connected=set(connected), stall_seconds=STALL)}


def test_live_waker_socket_is_online_standby_even_without_heartbeat(db):
    rows = _describe(db, [_agent("codex-beast", "codex", seen_seconds_ago=3600)], {"codex-beast"})
    row = rows["codex-beast"]
    assert row["status"] == row["effective_status"] == "online"
    assert row["connected"] is True
    assert row["state"] == "standby"


def test_no_socket_and_stale_heartbeat_is_offline(db):
    row = _describe(db, [_agent("grok-beast", "grok", seen_seconds_ago=3600)])["grok-beast"]
    assert row["status"] == "offline"
    assert row["state"] == "offline"


def test_fresh_heartbeat_is_standby(db):
    assert _describe(db, [_agent("claude-mac", "claude", seen_seconds_ago=10)])["claude-mac"]["state"] == "standby"


def test_lease_holder_is_working_even_if_heartbeat_is_stale(db):
    _job(db, "j1", status="in_progress", leased_by="codex-beast")
    row = _describe(db, [_agent("codex-beast", "codex", seen_seconds_ago=3600)])["codex-beast"]
    assert row["state"] == "working"


def test_reachable_agent_with_stalled_work_is_broken(db):
    _job(db, "stuck", role="codex", target="codex-beast", age=STALL + 120)
    row = _describe(db, [_agent("codex-beast", "codex")], {"codex-beast"})["codex-beast"]
    assert row["state"] == "broken"
    assert "1 job(s) addressed to it untaken" in row["state_reason"]


def test_stalled_job_pinned_elsewhere_does_not_break_a_peer(db):
    _job(db, "stuck", role="codex", target="codex-mac", age=STALL + 120)
    rows = _describe(db, [_agent("codex-beast", "codex"), _agent("codex-mac", "codex", seen_seconds_ago=5)],
                     {"codex-beast"})
    assert rows["codex-beast"]["state"] == "standby"
    assert rows["codex-mac"]["state"] == "broken"


def test_fresh_pending_job_is_not_a_stall(db):
    _job(db, "new", role="codex", age=30)
    assert _describe(db, [_agent("codex-beast", "codex")], {"codex-beast"})["codex-beast"]["state"] == "standby"


def test_recent_reroutes_away_mark_broken(db):
    _job(db, "moved", status="completed", role="claude")
    record_event(db, "moved", REROUTED, "system", "delivery",
                 {"from_role": "codex", "from_instance": "codex-beast", "to_role": "claude"})
    row = _describe(db, [_agent("codex-beast", "codex")], {"codex-beast"})["codex-beast"]
    assert row["state"] == "broken"
    assert "1 job(s) rerouted away" in row["state_reason"]


def test_disabled_passes_through(db):
    row = _describe(db, [_agent("old", "codex", status="disabled")], {"old"})["old"]
    assert row["state"] == "disabled"
    assert row["status"] == "disabled"


def test_operator_roles_are_never_broken(db):
    _job(db, "gate", role="admin", age=STALL * 3)
    assert _describe(db, [_agent("local-operator", "admin", seen_seconds_ago=5)])["local-operator"]["state"] == "standby"


def test_available_roles_excludes_broken_and_offline(db):
    _job(db, "stuck", role="codex", age=STALL + 120)
    described = presence.describe_fleet(db, [
        _agent("codex-beast", "codex", seen_seconds_ago=5),
        _agent("claude-mac", "claude", seen_seconds_ago=5),
        _agent("grok-beast", "grok", seen_seconds_ago=9999),
    ], threshold=THRESHOLD, connected=set(), stall_seconds=STALL)
    assert presence.available_roles(described) == {"claude"}


def test_probe_is_used_when_no_connected_set_given(db):
    presence.register_connected_probe(lambda: {"codex-beast"})
    try:
        rows = presence.describe_fleet(db, [_agent("codex-beast", "codex", seen_seconds_ago=9999)],
                                       threshold=THRESHOLD, stall_seconds=STALL)
        assert rows[0]["state"] == "standby"
    finally:
        presence.register_connected_probe(None)


def test_broken_store_still_returns_heartbeat_presence():
    class Broken:
        def table(self, *_a, **_k):
            raise RuntimeError("db down")
    rows = presence.describe_fleet(Broken(), [_agent("claude-mac", "claude", seen_seconds_ago=5)],
                                   threshold=THRESHOLD, connected=set(), stall_seconds=STALL)
    assert rows[0]["state"] == "standby"
