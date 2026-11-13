"""Stale-job policy, tested against real embedded transactions."""
from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from mco.localstore import LocalStore
from mco.orchestrator import stale_reaper as reaper
from mco.orchestrator.audit import get_events

NOW = datetime(2026, 11, 11, 20, tzinfo=timezone.utc)


@pytest.fixture
def policy(tmp_path):
    return reaper.Policy(ladder=("builder", "economy", "local"),
                         owner_ids=frozenset({"owner"}),
                         retired_instances=frozenset({"retired-worker"}),
                         deputy_id="deputy", stop_file=tmp_path / "STOP")


def job(age=6, **kw):
    return {"id": "j", "org_id": "default", "status": "pending",
            "created_at": (NOW - timedelta(hours=age)).isoformat(),
            "source_agent_id": "worker", "source_agent_role": "builder",
            "target_agent_role": "builder", "input_payload": {}, **kw}


def agents():
    return [{"instance_id": "cheap", "role": "economy", "org_id": "default",
             "status": "online", "last_seen_at": NOW.isoformat()}]


def event(hours=24, name="stale_reaper_rerouted", **kw):
    return {"job_id": "j", "event": name,
            "created_at": (NOW - timedelta(hours=hours)).isoformat(), **kw}


def decision(policy, row=None, events=(), fleet=None):
    report = reaper.plan_snapshot([row or job()], list(events), fleet or agents(), policy, now=NOW)
    return report["jobs"][0]


@pytest.mark.parametrize("age,want", [(5.999, "hold"), (6, "reroute")])
def test_six_hour_boundary(policy, age, want):
    assert decision(policy, job(age))["action"] == want


@pytest.mark.parametrize("hours,want", [(23.999, "hold"), (24, "cancel_stale")])
def test_cancel_requires_24_hours_after_recorded_reroute(policy, hours, want):
    assert decision(policy, job(40), [event(hours)])["action"] == want
    assert decision(policy, job(40))["action"] == "reroute"


def test_latest_reroute_and_taken_history_are_respected(policy):
    assert decision(policy, job(60), [event(30), event(1)])["action"] == "hold"
    assert decision(policy, job(60), [event(30), event(29, "leased")])["action"] == "hold"


@pytest.mark.parametrize("patch", [
    {"source_agent_id": "owner"}, {"source_agent_role": "admin"},
    {"source_agent_id": None}, {"target_agent_id": "retired-worker"},
    {"input_payload": {"pinned": True}}, {"input_payload": {"no_reroute": True}},
    {"archived": True}, {"status": "leased"}, {"started_at": NOW.isoformat()},
    {"input_payload": {"unmerged_work": True}}, {"created_at": "invalid"},
    {"depends_on": ["unfinished"]},
])
def test_protected_work_is_never_changed(policy, patch):
    assert decision(policy, job(100, **patch), [event(90)])["action"] == "hold"


def test_ladder_is_downward_and_tenant_and_presence_filtered(policy):
    fleet = agents() + [{**agents()[0], "instance_id": "retired-worker"}]
    assert decision(policy, fleet=fleet)["to_role"] == "economy"
    assert decision(policy, fleet=[{**agents()[0], "org_id": "other"}])["action"] == "hold"
    assert decision(policy, fleet=[{**agents()[0], "last_seen_at": (NOW-timedelta(hours=1)).isoformat()}])["action"] == "hold"
    assert decision(policy, job(target_agent_role="local"))["action"] == "hold"
    assert decision(policy, fleet=[{**agents()[0], "instance_id": "retired-worker"}])["action"] == "hold"


def test_retirement_is_a_proposal_not_a_write(policy):
    fleet = [{**agents()[0], "instance_id": "retired-worker"}]
    report = reaper.plan_snapshot([], [], fleet, policy, now=NOW)
    assert report["retirements"][0]["instance_id"] == "retired-worker"
    assert fleet[0]["status"] == "online"


@pytest.fixture
def db(tmp_path):
    store = LocalStore(tmp_path / "board.db")
    store.table("agent_jobs").insert(job(40)).execute()
    store.table("agent_registry").insert(agents()[0]).execute()
    store.table("agent_job_events").insert(event(25)).execute()
    yield store
    store.close()


def actor(**kw):
    return {"instance_id": "deputy", "role": "chief", "org_id": "default",
            "scopes": ["jobs:read", "jobs:cancel-stale"], **kw}


def test_disabled_default_and_explicit_deputy_grant(db, policy):
    with pytest.raises(PermissionError):
        reaper.cancel_stale(db, "j", policy, actor(), now=NOW)
    for caller in [actor(instance_id="other"), actor(scopes=["admin"]), actor(org_id="other")]:
        with pytest.raises(PermissionError):
            reaper.cancel_stale(db, "j", replace(policy, enabled=True), caller, now=NOW)
    assert db.table("agent_jobs").select("*").execute().data[0]["status"] == "pending"


def test_cancel_receipt_is_atomic_and_repeat_safe(db, policy):
    policy = replace(policy, enabled=True)
    receipt = reaper.cancel_stale(db, "j", policy, actor(), now=NOW)
    assert receipt["action"] == "cancel_stale"
    assert db.table("agent_jobs").select("*").execute().data[0]["status"] == "cancelled"
    assert get_events(db, "j")[-1]["event"] == "stale_reaper_cancelled"
    with pytest.raises(ValueError):
        reaper.cancel_stale(db, "j", policy, actor(), now=NOW)
    assert len(get_events(db, "j")) == 2


def test_receipt_failure_rolls_back_cancellation(db, policy, monkeypatch):
    import mco.orchestrator.audit as audit
    def broken(*a, **kw):
        raise RuntimeError("receipt storage failed")
    monkeypatch.setattr(audit, "record_event", broken)
    with pytest.raises(RuntimeError):
        reaper.cancel_stale(db, "j", replace(policy, enabled=True), actor(), now=NOW)
    assert db.table("agent_jobs").select("*").execute().data[0]["status"] == "pending"


def test_stop_or_new_pin_prevents_mutation(db, policy):
    policy = replace(policy, enabled=True)
    policy.stop_file.write_text("stop")
    with pytest.raises(PermissionError):
        reaper.cancel_stale(db, "j", policy, actor(), now=NOW)
    policy.stop_file.unlink()
    db.table("agent_jobs").update({"target_agent_id": "pinned"}).eq("id", "j").execute()
    with pytest.raises(ValueError):
        reaper.cancel_stale(db, "j", policy, actor(), now=NOW)


def test_narrow_route_enforces_scope_and_server_policy(db, policy, monkeypatch):
    from mco.orchestrator import routes
    from mco.orchestrator.auth import require_agent
    monkeypatch.setattr(routes, "get_db_client", lambda: db)
    monkeypatch.setattr(reaper, "load_server_policy", lambda: policy)
    app = FastAPI()
    app.include_router(routes.router)
    app.dependency_overrides[require_agent] = lambda: actor()
    client = TestClient(app)
    response = client.post("/api/jobs/j/cancel-stale")
    assert response.status_code == 403
    app.dependency_overrides[require_agent] = lambda: actor(scopes=["jobs:read"])
    assert client.post("/api/jobs/j/cancel-stale").status_code == 403


def test_readonly_snapshot_does_not_mutate_database(db):
    before = db._conn.total_changes
    snapshot = reaper.read_snapshot(db._path)
    assert len(snapshot["jobs"]) == 1
    assert db._conn.total_changes == before


def test_retrospective_lists_thresholds_in_window(policy):
    report = reaper.retrospective([job(40)], [], agents(), policy, now=NOW, days=14)
    assert report["proposals"][0]["at"] == (NOW-timedelta(hours=34)).isoformat()
    assert report["proposals"][1]["at"] == (NOW-timedelta(hours=10)).isoformat()
    assert report["proposals"][1]["action"] == "cancel_stale"
    assert report["historical_presence_verified"] is False


def test_retrospective_does_not_invent_routes_for_unknown_roles(policy):
    report = reaper.retrospective([job(40, target_agent_role="human")], [], agents(), policy, now=NOW)
    assert report["proposals"] == []


def test_sweep_defaults_to_no_writes_and_gated_apply_keeps_receipts(db, policy):
    # A different job has never been rerouted; this sweep must not cancel it.
    db.table("agent_jobs").insert(job(40, id="unrouted")).execute()
    db.table("agent_registry").insert({"instance_id": "retired-worker", "role": "builder",
                                        "status": "offline"}).execute()
    before = db._conn.total_changes
    reaper.sweep_local(db, policy, now=NOW)
    assert db._conn.total_changes == before
    with pytest.raises(PermissionError):
        reaper.sweep_local(db, policy, now=NOW, dry_run=False)
    result = reaper.sweep_local(db, replace(policy, enabled=True), now=NOW, dry_run=False)
    assert sorted(r["action"] for r in result["receipts"]) == ["cancel_stale", "reroute", "retire_from_routing"]
    row = db.table("agent_jobs").select("*").eq("id", "unrouted").execute().data[0]
    assert row["target_agent_role"] == "economy" and row["status"] == "pending"
    assert get_events(db, "unrouted")[-1]["event"] == "stale_reaper_rerouted"
    retired = db.table("agent_registry").select("*").eq("instance_id", "retired-worker").execute().data[0]
    assert retired["status"] == "disabled" and retired["retired"] is True
    assert reaper.sweep_local(db, replace(policy, enabled=True), now=NOW, dry_run=False)["receipts"] == []


def test_retirement_does_not_interrupt_an_active_attempt(db, policy):
    db.table("agent_registry").insert({"instance_id": "retired-worker", "role": "builder", "status": "offline"}).execute()
    db.table("agent_jobs").insert(job(id="active", status="leased", leased_by_instance_id="retired-worker")).execute()
    result = reaper.sweep_local(db, replace(policy, enabled=True), now=NOW, dry_run=False)
    assert not any(r["action"] == "retire_from_routing" for r in result["receipts"])


def test_reset_clock_and_missing_creator_fail_closed(policy):
    assert decision(policy, job(90), [event(1, "retried")])["action"] == "hold"
    assert decision(policy, job(90), [event(30), event(1, "retried")])["action"] == "hold"
    assert decision(policy, job(90, source_agent_id=""))["action"] == "hold"
