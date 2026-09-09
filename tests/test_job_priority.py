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
