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
         status="pending", payload=None, title="Fix the thing"):
    db.table("agent_jobs").insert({
        "id": job_id, "title": title, "status": status,
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


def _mac_only(_db, _org):
    return [("claude", "claude-mac")]


def _sweep_beast(db, online_instances=None, online_roles=None, **kw):
    """delivery.sweep with every online role present on host `beast`, unless given."""
    if online_instances is None:
        roles = online_roles(db, "default") if online_roles else set()
        def online_instances(_db, _org):
            return [(r, f"{r}-beast") for r in roles]
    return delivery.sweep(db, online_roles=online_roles, online_instances=online_instances, **kw)


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
        result = _sweep_beast(db, config=BASE, online_roles=_online("claude"))
        assert result.broadcasts == []
        assert _events(db) == []

    def test_stalled_job_is_rekicked_once(self, db):
        _job(db, age_seconds=STALL + 5)
        first = _sweep_beast(db, config=BASE, online_roles=_online("claude"))
        assert first.rekicked == ["job-1"]
        assert first.broadcasts[0][0] == "job_pending"
        second = _sweep_beast(db, config=BASE, online_roles=_online("claude"))
        assert second.broadcasts == []
        assert _events(db) == [delivery.REKICKED]
        assert _row(db)["target_agent_role"] == "codex"

    def test_still_stalled_after_rekick_reroutes_to_online_fallback(self, db):
        _job(db, age_seconds=STALL + 5)
        _sweep_beast(db, config=BASE, online_roles=_online("grok"))
        result = _sweep_beast(db, now=_later(STALL), config=BASE, online_roles=_online("grok"))
        assert result.rerouted == ["job-1"]
        row = _row(db)
        assert row["target_agent_role"] == "grok"  # claude preferred but offline
        assert row["target_agent_id"] == "grok-beast"  # pinned to the same host
        assert row["status"] == "pending"
        assert result.broadcasts == [("job_pending", row)]
        rerouted = [e for e in get_events(db, "job-1") if e["event"] == delivery.REROUTED][0]
        assert rerouted["detail"]["from_instance"] == "codex-beast"
        assert rerouted["detail"]["to_role"] == "grok"

    def test_reroute_gives_new_role_a_full_stall_window(self, db):
        _job(db, age_seconds=STALL + 5)
        _sweep_beast(db, config=BASE, online_roles=_online("claude"))
        _sweep_beast(db, now=_later(STALL), config=BASE, online_roles=_online("claude"))
        # The reroute event was stamped at real "now", so just inside one stall
        # window later the new role is still within its grace period...
        inside = _sweep_beast(db, now=_later(STALL - 30), config=BASE, online_roles=_online("claude"))
        assert inside.broadcasts == []
        # ...and once the window passes, it is re-kicked, not rerouted again.
        after = _sweep_beast(db, now=_later(STALL + 30), config=BASE, online_roles=_online("claude"))
        assert after.rekicked == ["job-1"]
        assert after.rerouted == []

    def test_no_online_fallback_escalates_once_and_pushes(self, db):
        _job(db, age_seconds=STALL + 5)
        _sweep_beast(db, config=BASE, online_roles=_online())
        result = _sweep_beast(db, now=_later(STALL), config=BASE, online_roles=_online())
        assert result.escalated == ["job-1"]
        assert result.broadcasts[0][0] == "job_undeliverable"
        assert result.notifications == [{"job_id": "job-1"}]
        assert _row(db)["target_agent_role"] == "codex"
        repeat = _sweep_beast(db, now=_later(STALL * 3), config=BASE, online_roles=_online())
        assert repeat.notifications == []
        assert _events(db) == [delivery.REKICKED, delivery.ESCALATED]

    def test_job_pinned_to_busy_instance_stays_queued(self, db):
        # The pinned worker is alive and working another job: the stalled job is
        # waiting its turn, so it is re-kicked but never rerouted or escalated.
        _job(db, job_id="busy", status="leased", age_seconds=60)
        db.table("agent_jobs").update({"leased_by_instance_id": "codex-beast"}).eq("id", "busy").execute()
        _job(db, age_seconds=STALL + 5)
        _sweep_beast(db, config=BASE, online_roles=_online("claude"))
        result = _sweep_beast(db, now=_later(STALL * 3), config=BASE, online_roles=_online("claude"))
        assert result.rerouted == [] and result.escalated == []
        row = _row(db)
        assert row["target_agent_role"] == "codex"
        assert row["target_agent_id"] == "codex-beast"

    def test_pinned_job_never_reroutes_to_another_host(self, db):
        # claude-mac is online but cannot see Beast's files: stay queued, alert once.
        _job(db, age_seconds=STALL + 5)
        mac = _mac_only
        kw = dict(config=BASE, online_roles=_online("claude"), online_instances=mac)
        _sweep_beast(db, **kw)
        result = _sweep_beast(db, now=_later(STALL), **kw)
        assert result.rerouted == [] and result.escalated == ["job-1"]
        assert result.notifications == [{"job_id": "job-1"}]
        row = _row(db)
        assert row["target_agent_role"] == "codex" and row["target_agent_id"] == "codex-beast"
        esc = [e for e in get_events(db, "job-1") if e["event"] == delivery.ESCALATED][0]
        assert "codex-beast not picking up" in esc["detail"]["reason"]
        repeat = _sweep_beast(db, now=_later(STALL * 3), **kw)
        assert repeat.escalated == [] and repeat.notifications == []

    def test_cross_host_override_allows_reroute(self, db):
        _job(db, age_seconds=STALL + 5)
        mac = _mac_only
        kw = dict(config={**BASE, "MCO_REROUTE_CROSS_HOST": "1"},
                  online_roles=_online("claude"), online_instances=mac)
        _sweep_beast(db, **kw)
        result = _sweep_beast(db, now=_later(STALL), **kw)
        assert result.rerouted == ["job-1"]
        assert _row(db)["target_agent_id"] is None

    def test_instance_host_from_id_suffix(self):
        assert delivery.instance_host("claude-beast") == "beast"
        assert delivery.instance_host("claude-mac-studio") == "mac-studio"

    @pytest.mark.parametrize("role,instance,fallback,target", [
        ("via-dispatch", "via-dispatch-beast", "claude", "claude-beast"),
        ("codex", "codex-beast", "via-retrieval", "via-retrieval-beast"),
        ("score-canary-worker", "score-canary-worker-mac-studio",
         "via-retrieval", "via-retrieval-mac-studio"),
    ])
    def test_compound_roles_reroute_on_same_host(self, db, role, instance, fallback, target):
        _job(db, age_seconds=STALL + 5, role=role, instance=instance)
        config = {**BASE, "MCO_ROUTE_FALLBACKS": f"{role}:{fallback}"}
        kw = dict(config=config, online_instances=lambda _db, _org: [(fallback, target)])
        delivery.sweep(db, **kw)
        result = delivery.sweep(db, now=_later(STALL), **kw)
        assert result.rerouted == ["job-1"]
        assert _row(db)["target_agent_id"] == target
        event = next(e for e in get_events(db, "job-1") if e["event"] == delivery.REROUTED)
        assert event["detail"]["to_instance"] == target

    @pytest.mark.parametrize("instance,target", [("codex", "claude"), ("codex-", "claude-")])
    def test_unknown_host_stays_pinned_and_escalates_once(self, db, instance, target):
        _job(db, age_seconds=STALL + 5, instance=instance)
        kw = dict(config=BASE, online_instances=lambda _db, _org: [("claude", target)])
        delivery.sweep(db, **kw)
        result = delivery.sweep(db, now=_later(STALL), **kw)
        assert result.rerouted == []
        assert result.escalated == ["job-1"]
        assert _row(db)["target_agent_id"] == instance
        event = next(e for e in get_events(db, "job-1") if e["event"] == delivery.ESCALATED)
        assert "no host suffix" in event["detail"]["reason"]
        repeat = delivery.sweep(db, now=_later(STALL * 3), **kw)
        assert repeat.escalated == [] and repeat.notifications == []

    def test_busy_instance_identity_is_case_insensitive(self, db):
        _job(db, job_id="busy", status="leased", age_seconds=60)
        db.table("agent_jobs").update({"leased_by_instance_id": "CODEX-BEAST"}).eq("id", "busy").execute()
        _job(db, age_seconds=STALL + 5)
        _sweep_beast(db, config=BASE, online_roles=_online("claude"))
        result = _sweep_beast(db, now=_later(STALL * 3), config=BASE, online_roles=_online("claude"))
        assert result.rerouted == [] and result.escalated == []
        assert _row(db)["target_agent_id"] == "codex-beast"

    def test_no_reroute_flag_escalates_instead(self, db):
        _job(db, age_seconds=STALL + 5, payload={"no_reroute": True})
        _sweep_beast(db, config=BASE, online_roles=_online("claude"))
        result = _sweep_beast(db, now=_later(STALL), config=BASE, online_roles=_online("claude"))
        assert result.escalated == ["job-1"]
        assert result.notifications == [{"job_id": "job-1"}]

    def test_shepherd_gate_is_never_rerouted(self, db):
        _job(db, age_seconds=STALL + 5, title="[shepherd] review ep 3 (gate)")
        _sweep_beast(db, config=BASE, online_roles=_online("claude"))
        result = _sweep_beast(db, now=_later(STALL), config=BASE, online_roles=_online("claude"))
        assert result.rerouted == []
        assert result.escalated == ["job-1"]
        assert _row(db)["target_agent_id"] == "codex-beast"

    def test_non_gate_shepherd_title_still_reroutes(self, db):
        _job(db, age_seconds=STALL + 5, title="[shepherd] build ep 3")
        _sweep_beast(db, config=BASE, online_roles=_online("claude"))
        result = _sweep_beast(db, now=_later(STALL), config=BASE, online_roles=_online("claude"))
        assert result.rerouted == ["job-1"]

    def test_role_without_fallback_escalates(self, db):
        _job(db, age_seconds=STALL + 5, role="chief", instance="chief-beast")
        _sweep_beast(db, config=BASE, online_roles=_online("claude"))
        result = _sweep_beast(db, now=_later(STALL), config=BASE, online_roles=_online("claude"))
        assert result.escalated == ["job-1"]

    def test_reroute_limit_is_enforced(self, db):
        config = {**BASE, "MCO_DELIVERY_MAX_REROUTES": "1", "MCO_ROUTE_FALLBACKS": "codex:claude,claude:codex"}
        _job(db, age_seconds=STALL + 5)
        both = _online("claude", "codex")
        _sweep_beast(db, config=config, online_roles=both)
        _sweep_beast(db, now=_later(STALL), config=config, online_roles=both)
        assert _row(db)["target_agent_role"] == "claude"
        _sweep_beast(db, now=_later(STALL * 2), config=config, online_roles=both)
        result = _sweep_beast(db, now=_later(STALL * 3), config=config, online_roles=both)
        assert result.escalated == ["job-1"]
        assert _row(db)["target_agent_role"] == "claude"

    def test_other_activity_restarts_the_clock(self, db):
        _job(db, age_seconds=STALL * 5)
        record_event(db, "job-1", "lease_expired", "system", "reaper", {})
        result = _sweep_beast(db, config=BASE, online_roles=_online("claude"))
        assert result.broadcasts == []

    def test_only_pending_unarchived_jobs(self, db):
        _job(db, age_seconds=STALL * 5, job_id="leased", status="leased")
        _job(db, age_seconds=STALL * 5, job_id="gated", status="needs_approval")
        _job(db, age_seconds=STALL * 5, job_id="archived")
        db.table("agent_jobs").update({"archived": True}).eq("id", "archived").execute()
        assert _sweep_beast(db, config=BASE, online_roles=_online("claude")).broadcasts == []

    def test_disabled_by_zero_stall(self, db):
        _job(db, age_seconds=STALL * 5)
        config = {**BASE, "MCO_DELIVERY_STALL_SECONDS": "0"}
        assert _sweep_beast(db, config=config).broadcasts == []

    def test_kill_switch_pauses_delivery(self, db):
        _job(db, age_seconds=STALL * 5)
        config = {**BASE, "MCO_KILL_SWITCH": "on"}
        assert _sweep_beast(db, config=config).broadcasts == []

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
