"""Delivery watchdog: a PENDING job is re-woken, then rerouted, then escalated,
without anyone telling an agent to look. Uses a real LocalStore so the in-place
reroute CAS and the audit trail that carries the watchdog's state are exercised
on the embedded backend."""

import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from mco.localstore import LocalStore
from mco.orchestrator import delivery
from mco.orchestrator.audit import get_events, record_event

STALL = 600
BASE = {"MCO_DELIVERY_STALL_SECONDS": str(STALL), "MCO_ROUTE_FALLBACKS": "codex:claude|grok"}


@pytest.fixture
def db(tmp_path):
    s = LocalStore(tmp_path / "test.db")
    yield s
    s.close()


def _now():
    return datetime.now(timezone.utc)


def _job(db, *, age_seconds=0, job_id="job-1", role="codex", instance="codex-beast",
         status="pending", payload=None):
    db.table("agent_jobs").insert({
        "id": job_id, "title": "Fix the thing", "status": status,
        "target_agent_role": role, "target_agent_id": instance,
        "input_payload": payload or {},
        "created_at": (_now() - timedelta(seconds=age_seconds)).isoformat(),
    }).execute()


def _row(db, job_id="job-1"):
    return db.table("agent_jobs").select("*").eq("id", job_id).execute().data[0]


def _events(db, job_id="job-1"):
    return [e["event"] for e in get_events(db, job_id)]


def _online(*roles):
    return lambda _db, _org: set(roles)


def _later(seconds):
    return _now() + timedelta(seconds=seconds)


class TestParseFallbacks:
    def test_parses_ordered_chains(self):
        assert delivery.parse_fallbacks("codex:claude|grok, antigravity:claude") == {
            "codex": ["claude", "grok"], "antigravity": ["claude"]}

    def test_skips_malformed_and_self_references(self):
        assert delivery.parse_fallbacks("junk, codex:, claude:claude, Grok: CLAUDE ") == {"grok": ["claude"]}

    def test_empty(self):
        assert delivery.parse_fallbacks(None) == {}


class TestSweep:
    def test_fresh_job_is_left_alone(self, db):
        _job(db, age_seconds=60)
        result = delivery.sweep(db, config=BASE, online_roles=_online("claude"))
        assert result.broadcasts == []
        assert _events(db) == []

    def test_stalled_job_is_rekicked_once(self, db):
        _job(db, age_seconds=STALL + 5)
        first = delivery.sweep(db, config=BASE, online_roles=_online("claude"))
        assert first.rekicked == ["job-1"]
        assert first.broadcasts[0][0] == "job_pending"
        second = delivery.sweep(db, config=BASE, online_roles=_online("claude"))
        assert second.broadcasts == []
        assert _events(db) == [delivery.REKICKED]
        assert _row(db)["target_agent_role"] == "codex"

    def test_still_stalled_after_rekick_reroutes_to_online_fallback(self, db):
        _job(db, age_seconds=STALL + 5)
        delivery.sweep(db, config=BASE, online_roles=_online("grok"))
        result = delivery.sweep(db, now=_later(STALL), config=BASE, online_roles=_online("grok"))
        assert result.rerouted == ["job-1"]
        row = _row(db)
        assert row["target_agent_role"] == "grok"  # claude preferred but offline
        assert row["target_agent_id"] is None
        assert row["status"] == "pending"
        assert result.broadcasts == [("job_pending", row)]
        rerouted = [e for e in get_events(db, "job-1") if e["event"] == delivery.REROUTED][0]
        assert rerouted["detail"]["from_instance"] == "codex-beast"
        assert rerouted["detail"]["to_role"] == "grok"

    def test_reroute_gives_new_role_a_full_stall_window(self, db):
        _job(db, age_seconds=STALL + 5)
        delivery.sweep(db, config=BASE, online_roles=_online("claude"))
        delivery.sweep(db, now=_later(STALL), config=BASE, online_roles=_online("claude"))
        # The reroute event was stamped at real "now", so just inside one stall
        # window later the new role is still within its grace period...
        inside = delivery.sweep(db, now=_later(STALL - 30), config=BASE, online_roles=_online("claude"))
        assert inside.broadcasts == []
        # ...and once the window passes, it is re-kicked, not rerouted again.
        after = delivery.sweep(db, now=_later(STALL + 30), config=BASE, online_roles=_online("claude"))
        assert after.rekicked == ["job-1"]
        assert after.rerouted == []

    def test_no_online_fallback_escalates_once_and_pushes(self, db):
        _job(db, age_seconds=STALL + 5)
        delivery.sweep(db, config=BASE, online_roles=_online())
        result = delivery.sweep(db, now=_later(STALL), config=BASE, online_roles=_online())
        assert result.escalated == ["job-1"]
        assert result.broadcasts[0][0] == "job_undeliverable"
        assert "no online agent" in result.notifications[0]["message"]
        assert _row(db)["target_agent_role"] == "codex"
        repeat = delivery.sweep(db, now=_later(STALL * 3), config=BASE, online_roles=_online())
        assert repeat.notifications == []
        assert _events(db) == [delivery.REKICKED, delivery.ESCALATED]

    def test_no_reroute_flag_escalates_instead(self, db):
        _job(db, age_seconds=STALL + 5, payload={"no_reroute": True})
        delivery.sweep(db, config=BASE, online_roles=_online("claude"))
        result = delivery.sweep(db, now=_later(STALL), config=BASE, online_roles=_online("claude"))
        assert result.escalated == ["job-1"]
        assert "no_reroute" in result.notifications[0]["message"]

    def test_role_without_fallback_escalates(self, db):
        _job(db, age_seconds=STALL + 5, role="chief", instance="chief-beast")
        delivery.sweep(db, config=BASE, online_roles=_online("claude"))
        result = delivery.sweep(db, now=_later(STALL), config=BASE, online_roles=_online("claude"))
        assert result.escalated == ["job-1"]

    def test_reroute_limit_is_enforced(self, db):
        config = {**BASE, "MCO_DELIVERY_MAX_REROUTES": "1", "MCO_ROUTE_FALLBACKS": "codex:claude,claude:codex"}
        _job(db, age_seconds=STALL + 5)
        both = _online("claude", "codex")
        delivery.sweep(db, config=config, online_roles=both)
        delivery.sweep(db, now=_later(STALL), config=config, online_roles=both)
        assert _row(db)["target_agent_role"] == "claude"
        delivery.sweep(db, now=_later(STALL * 2), config=config, online_roles=both)
        result = delivery.sweep(db, now=_later(STALL * 3), config=config, online_roles=both)
        assert result.escalated == ["job-1"]
        assert _row(db)["target_agent_role"] == "claude"

    def test_other_activity_restarts_the_clock(self, db):
        _job(db, age_seconds=STALL * 5)
        record_event(db, "job-1", "lease_expired", "system", "reaper", {})
        result = delivery.sweep(db, config=BASE, online_roles=_online("claude"))
        assert result.broadcasts == []

    def test_only_pending_unarchived_jobs(self, db):
        _job(db, age_seconds=STALL * 5, job_id="leased", status="leased")
        _job(db, age_seconds=STALL * 5, job_id="gated", status="needs_approval")
        _job(db, age_seconds=STALL * 5, job_id="archived")
        db.table("agent_jobs").update({"archived": True}).eq("id", "archived").execute()
        assert delivery.sweep(db, config=BASE, online_roles=_online("claude")).broadcasts == []

    def test_disabled_by_zero_stall(self, db):
        _job(db, age_seconds=STALL * 5)
        config = {**BASE, "MCO_DELIVERY_STALL_SECONDS": "0"}
        assert delivery.sweep(db, config=config).broadcasts == []

    def test_kill_switch_pauses_delivery(self, db):
        _job(db, age_seconds=STALL * 5)
        config = {**BASE, "MCO_KILL_SWITCH": "on"}
        assert delivery.sweep(db, config=config).broadcasts == []

    def test_uses_registry_presence_by_default(self, db):
        db.table("agent_registry").insert({
            "instance_id": "claude-mac", "role": "claude", "status": "online",
            "last_seen_at": _now().isoformat(),
        }).execute()
        db.table("agent_registry").insert({
            "instance_id": "grok-beast", "role": "grok", "status": "online",
            "last_seen_at": (_now() - timedelta(days=2)).isoformat(),
        }).execute()
        assert delivery._online_roles(db, "default") == {"claude"}


class TestGatewayWiring:
    def test_delivery_once_broadcasts_and_pushes(self, db, monkeypatch):
        from mco.orchestrator import health, routes

        _job(db, age_seconds=STALL + 5)
        sent, pushed = [], []

        async def callback(event, job):
            sent.append((event, job["id"]))

        monkeypatch.setattr(routes, "get_db_client", lambda: db)
        monkeypatch.setattr(routes, "_broadcast_callback", callback)
        monkeypatch.setattr(delivery, "get_config", lambda: BASE)
        monkeypatch.setattr(delivery, "send_notifications", lambda result: pushed.append(result))

        result = asyncio.run(health.delivery_once())
        assert sent == [("job_pending", "job-1")]
        assert pushed == [result]
