"""Inbox ordering is the scheduling policy.

Workers are instructed to take the FIRST job their inbox returns, so whatever
order /api/jobs/pending emits decides what actually runs next. Before priority
existed the endpoint returned rows unordered (insertion order), which starved
newly dispatched urgent work behind an old backlog.
"""
import pytest
from mco.orchestrator.routes import _job_priority, get_pending_jobs
from mco.localstore import LocalStore


@pytest.fixture
def db(tmp_path):
    store = LocalStore(tmp_path / "priority.db")
    yield store
    store.close()


def test_job_priority_reads_int():
    assert _job_priority({"priority": 5}) == 5
    assert _job_priority({"priority": "3"}) == 3
    assert _job_priority({"priority": -2}) == -2


@pytest.mark.parametrize("job", [{}, {"priority": None}, {"priority": ""}, {"priority": "urgent"}])
def test_job_priority_defaults_to_zero(job):
    """A job written before this column existed must sort as normal, not crash."""
    assert _job_priority(job) == 0


@pytest.mark.asyncio
async def test_pending_orders_by_priority_then_oldest(db, monkeypatch):
    import mco.orchestrator.routes as routes
    monkeypatch.setattr(routes, "get_db_client", lambda: db)
    monkeypatch.setattr(routes, "touch_agent_presence", lambda *a, **k: None)
    monkeypatch.setattr(routes, "reclaim_stale_leases", lambda *a, **k: None)

    # Inserted oldest-first with no priority, then one urgent job added last -
    # exactly the shape that used to leave the urgent job at the back.
    for jid, created, prio in [
        ("old-a", "2026-07-01T00:00:00Z", 0),
        ("old-b", "2026-07-02T00:00:00Z", 0),
        ("urgent", "2026-09-09T00:00:00Z", 10),
        ("also-urgent-older", "2026-09-08T00:00:00Z", 10),
        ("deprioritised", "2026-06-01T00:00:00Z", -5),
    ]:
        row = {"id": jid, "status": "pending", "target_agent_role": "claude",
               "created_at": created}
        if prio:
            row["priority"] = prio
        db.table("agent_jobs").insert(row).execute()

    agent = {"instance_id": "claude-beast", "role": "claude", "org_id": "default"}
    got = [j["id"] for j in await get_pending_jobs("claude", "claude-beast", agent)]

    # Highest priority first; oldest first *within* a band so raising priority
    # cannot starve equally-urgent older work; negative priority sinks below
    # the untagged backlog rather than jumping it.
    assert got == ["also-urgent-older", "urgent", "old-a", "old-b", "deprioritised"]


# --------------------------------------------------------------------------
# lease_next: priority enforced by the server, not advised to the worker.
# --------------------------------------------------------------------------

def _seed(db, rows):
    for jid, created, prio, target in rows:
        row = {"id": jid, "status": "pending", "target_agent_role": "claude",
               "created_at": created}
        if prio:
            row["priority"] = prio
        if target:
            row["target_agent_id"] = target
        db.table("agent_jobs").insert(row).execute()


def _patch(monkeypatch, db):
    import mco.orchestrator.routes as routes
    monkeypatch.setattr(routes, "get_db_client", lambda: db)
    monkeypatch.setattr(routes, "touch_agent_presence", lambda *a, **k: None)
    monkeypatch.setattr(routes, "reclaim_stale_leases", lambda *a, **k: None)
    monkeypatch.setattr(routes, "kill_switch_active", lambda: False)
    monkeypatch.setattr(routes, "notify_job_leased", lambda *a, **k: None)
    monkeypatch.setattr(routes, "_broadcast_callback", None)
    return routes


AGENT = {"instance_id": "claude-beast", "role": "claude", "org_id": "default"}


@pytest.mark.asyncio
async def test_lease_next_takes_highest_priority_not_oldest(db, monkeypatch):
    """The exact failure seen live: an old P0 sitting above a new P100."""
    routes = _patch(monkeypatch, db)
    _seed(db, [
        ("old-escalation", "2026-07-31T13:29:00Z", 0, None),
        ("via-urgent", "2026-09-08T08:02:00Z", 100, None),
    ])
    res = await routes.lease_next_job({"agent_instance_id": "claude-beast"}, AGENT)
    assert res["success"] is True
    assert res["job"]["id"] == "via-urgent"
    assert res["lease"]


@pytest.mark.asyncio
async def test_lease_next_returns_empty_rather_than_error(db, monkeypatch):
    routes = _patch(monkeypatch, db)
    res = await routes.lease_next_job({}, AGENT)
    assert res["success"] is False
    assert res["job"] is None


@pytest.mark.asyncio
async def test_lease_next_refuses_to_act_as_another_agent(db, monkeypatch):
    from fastapi import HTTPException
    routes = _patch(monkeypatch, db)
    _seed(db, [("j", "2026-09-01T00:00:00Z", 0, None)])
    with pytest.raises(HTTPException) as e:
        await routes.lease_next_job({"agent_instance_id": "somebody-else"}, AGENT)
    assert e.value.status_code == 403


@pytest.mark.asyncio
async def test_lease_next_skips_instance_targeted_at_other_workers(db, monkeypatch):
    """An instance-targeted job is another worker's mail, even at high priority."""
    routes = _patch(monkeypatch, db)
    _seed(db, [
        ("someone-elses", "2026-09-01T00:00:00Z", 500, "claude-mac"),
        ("mine", "2026-09-02T00:00:00Z", 1, None),
    ])
    res = await routes.lease_next_job({}, AGENT)
    assert res["job"]["id"] == "mine"


@pytest.mark.asyncio
async def test_lease_next_falls_through_when_a_candidate_is_taken(db, monkeypatch):
    """Losing a race must hand back the next job, not fail the call."""
    routes = _patch(monkeypatch, db)
    _seed(db, [
        ("contended", "2026-09-01T00:00:00Z", 100, None),
        ("runner-up", "2026-09-02T00:00:00Z", 50, None),
    ])
    real = routes.acquire_lease

    def flaky(dbc, task_id, instance, **kw):
        if task_id == "contended":
            return None          # another worker won it first
        return real(dbc, task_id, instance, **kw)

    monkeypatch.setattr(routes, "acquire_lease", flaky)
    res = await routes.lease_next_job({}, AGENT)
    assert res["success"] is True
    assert res["job"]["id"] == "runner-up"


@pytest.mark.asyncio
async def test_lease_next_honours_kill_switch(db, monkeypatch):
    from fastapi import HTTPException
    routes = _patch(monkeypatch, db)
    monkeypatch.setattr(routes, "kill_switch_active", lambda: True)
    _seed(db, [("j", "2026-09-01T00:00:00Z", 100, None)])
    with pytest.raises(HTTPException) as e:
        await routes.lease_next_job({}, AGENT)
    assert e.value.status_code == 503


@pytest.mark.asyncio
async def test_lease_next_and_pending_agree_on_what_is_next(db, monkeypatch):
    """The list a worker reads and the job the server hands out must match."""
    routes = _patch(monkeypatch, db)
    _seed(db, [
        ("a", "2026-07-01T00:00:00Z", 0, None),
        ("b", "2026-09-01T00:00:00Z", 10, None),
        ("c", "2026-08-01T00:00:00Z", 10, None),
    ])
    inbox = await routes.get_pending_jobs("claude", "claude-beast", AGENT)
    leased = await routes.lease_next_job({}, AGENT)
    assert leased["job"]["id"] == inbox[0]["id"] == "c"
