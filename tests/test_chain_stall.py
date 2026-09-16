"""Chain-stall detection: the failure the delivery watchdog cannot see.

A worker that finishes its own job and never hands off leaves nothing pending,
so the board looks idle and healthy while the mission is dead. Real case:
a Score packet completed at 20:04 saying "review is next", created no review
job, and the chain sat untouched for two hours."""

from datetime import datetime, timedelta, timezone

import pytest

from mco.localstore import LocalStore
from mco.orchestrator import delivery
from mco.orchestrator.audit import get_events

GRACE = 600
CONF = {"MCO_DELIVERY_STALL_SECONDS": str(GRACE), "MCO_ROUTE_FALLBACKS": ""}


@pytest.fixture
def db(tmp_path):
    s = LocalStore(tmp_path / "test.db")
    yield s
    s.close()


def _now():
    return datetime.now(timezone.utc)


def _done(db, job_id, *, worker="codex-beast", expects=True, completed_ago=GRACE + 60, title=None):
    db.table("agent_jobs").insert({
        "id": job_id, "title": title or f"Packet {job_id}", "status": "completed",
        "target_agent_role": "codex", "leased_by_instance_id": worker,
        "input_payload": {"prompt": "work", **({"expects_successor": True} if expects else {})},
        "created_at": (_now() - timedelta(seconds=completed_ago + 600)).isoformat(),
        "completed_at": (_now() - timedelta(seconds=completed_ago)).isoformat(),
    }).execute()


def _successor(db, job_id, *, source, created_ago=60, chain_parent=None):
    db.table("agent_jobs").insert({
        "id": job_id, "title": "next packet", "status": "pending",
        "target_agent_role": "claude", "source_agent_id": source,
        "input_payload": {"prompt": "next", **({"chain_parent": chain_parent} if chain_parent else {})},
        "created_at": (_now() - timedelta(seconds=created_ago)).isoformat(),
    }).execute()


def _sweep(db, config=None):
    return delivery.sweep(db, config={**CONF, **(config or {})}, online_roles=lambda *_a: set())


def _events(db, job_id):
    return [e["event"] for e in get_events(db, job_id)]


class TestChainStall:
    def test_handoff_that_never_happened_is_caught(self, db):
        _done(db, "s04", title="S04: policy + human gate")
        result = _sweep(db)
        assert result.chain_stalled == ["s04"]
        event, broadcast_job = result.broadcasts[0]
        assert event == "chain_stalled"
        assert broadcast_job["id"] == "s04"
        assert "never handed off" in result.notifications[0]["message"]
        assert delivery.CHAIN_STALLED in _events(db, "s04")

    def test_stall_is_handed_to_a_role_that_can_resume_it(self, db):
        _done(db, "s04")
        _sweep(db)
        rows = db.table("agent_jobs").select("*").eq("status", "pending").execute().data
        assert len(rows) == 1
        successor = rows[0]
        assert successor["target_agent_role"] == "chief"
        assert successor["input_payload"]["chain_parent"] == "s04"
        assert "created no follow-up job" in successor["description"]
        assert "Do not redo the completed work" in successor["description"]

    def test_a_worker_that_did_hand_off_is_not_flagged(self, db):
        _done(db, "s03")
        _successor(db, "review", source="codex-beast")
        assert _sweep(db).chain_stalled == []

    def test_successor_by_explicit_chain_parent_counts(self, db):
        _done(db, "s03", worker="codex-beast")
        _successor(db, "review", source="someone-else", chain_parent="s03")
        assert _sweep(db).chain_stalled == []

    def test_a_job_created_before_completion_does_not_count(self, db):
        _done(db, "s04", completed_ago=GRACE + 60)
        _successor(db, "earlier", source="codex-beast", created_ago=GRACE + 900)
        assert _sweep(db).chain_stalled == ["s04"]

    def test_grace_window_is_respected(self, db):
        _done(db, "fresh", completed_ago=GRACE - 60)
        assert _sweep(db).chain_stalled == []

    def test_only_flagged_once(self, db):
        _done(db, "s04")
        assert _sweep(db).chain_stalled == ["s04"]
        second = _sweep(db)
        assert second.chain_stalled == []
        assert second.notifications == []

    def test_jobs_without_the_flag_are_ignored(self, db):
        _done(db, "plain", expects=False)
        assert _sweep(db).chain_stalled == []

    def test_ancient_completions_are_not_revisited(self, db):
        _done(db, "old", completed_ago=delivery.CHAIN_LOOKBACK_SECONDS + 3600)
        assert _sweep(db).chain_stalled == []

    def test_handoff_role_can_be_disabled(self, db):
        _done(db, "s04")
        result = _sweep(db, {"MCO_CHAIN_STALL_TO_ROLE": ""})
        assert result.chain_stalled == ["s04"]          # still recorded and pushed
        assert db.table("agent_jobs").select("*").eq("status", "pending").execute().data == []
        assert "Sent to" not in result.notifications[0]["message"]

    def test_kill_switch_stops_chain_detection_too(self, db):
        _done(db, "s04")
        assert _sweep(db, {"MCO_KILL_SWITCH": "on"}).chain_stalled == []


class TestChainStallRole:
    def test_defaults_to_chief_and_can_be_overridden(self):
        assert delivery.get_chain_stall_role({}) == "chief"
        assert delivery.get_chain_stall_role({"MCO_CHAIN_STALL_TO_ROLE": " Reviewer "}) == "reviewer"
        assert delivery.get_chain_stall_role({"MCO_CHAIN_STALL_TO_ROLE": ""}) == ""
