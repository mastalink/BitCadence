"""Regression for the 2026-10-28 gateway writer-lock contention.

Writes ran full-table JSON decodes (update/insert/upsert, audit chain reads,
the outbox drain) while holding the single writer lock, so with a 100+ MB audit
trail every lease and heartbeat queued behind them.
"""
import uuid

import pytest

from mco.localstore import LocalStore
from mco.orchestrator import audit, leases


@pytest.fixture
def store(tmp_path):
    s = LocalStore(tmp_path / "contention.db")
    yield s
    s.close()


def _job(store):
    return store.table("agent_jobs").insert({
        "id": str(uuid.uuid4()), "title": "t", "target_agent_role": "test", "status": "pending",
    }).execute().data[0]


def _forbid_table_scans(store, monkeypatch):
    def scan(table):
        raise AssertionError(f"whole-table decode of {table}")
    monkeypatch.setattr(store, "_load_rows", scan)


def test_hot_paths_never_decode_whole_tables(store, monkeypatch):
    jobs = [_job(store) for _ in range(6)]
    audit.drain_outbox(store)
    _forbid_table_scans(store, monkeypatch)

    store.table("agent_jobs").update({"title": "u"}).eq("id", jobs[0]["id"]).execute()
    store.table("agent_registry").upsert({"instance_id": "w1", "role": "test", "status": "online"}).execute()
    store.table("agent_registry").update({"status": "online"}).eq("instance_id", "w1").execute()
    assert leases.acquire_lease(store, jobs[1]["id"], "w1") is not None
    audit.record_event(store, jobs[2]["id"], "probe", "a", "r", {})
    assert audit.drain_outbox(store) >= 1
    assert audit.drain_outbox(store) == 0
    store.table("agent_job_events").select("*").eq("event", "x").gt("created_at", "2026").execute()


def test_narrow_select_projects_without_changing_values(store):
    row = store.table("agent_jobs").insert({
        "id": "j1", "title": "t", "status": "pending", "input_payload": {"a": [1, 2]}, "flag": False,
    }).execute().data[0]
    got = store.table("agent_jobs").select("id,input_payload,flag,missing").eq("id", "j1").execute().data
    assert got == [{"id": "j1", "input_payload": {"a": [1, 2]}, "flag": False, "missing": None}]
    assert row["id"] == "j1"


def test_non_job_tables_keep_order_limit_and_offset(store):
    for n in range(5):
        store.table("agent_context").insert({"id": f"c{n}", "created_at": f"2026-01-0{n + 1}"}).execute()
    desc = store.table("agent_context").select("id").order("created_at", desc=True).range(1, 2).execute().data
    assert desc == [{"id": "c3"}, {"id": "c2"}]


def test_hot_expression_indexes_exist_and_are_used(store):
    _job(store)
    names = {r[0] for r in store._conn.execute("SELECT name FROM sqlite_master WHERE type='index'")}
    assert "ix_agent_jobs_id" in names
    plan = store._conn.execute(
        "EXPLAIN QUERY PLAN SELECT data FROM agent_jobs WHERE json_extract(data, '$.\"id\"') = ?", ("x",)
    ).fetchall()
    assert any("ix_agent_jobs_id" in str(r) for r in plan)


def test_gateway_client_timeout_env_override(monkeypatch):
    from mco.orchestrator.client import GatewayClient

    monkeypatch.delenv("MCO_HTTP_TIMEOUT", raising=False)
    assert GatewayClient(instance_id="w", token="t").timeout == 30.0
    monkeypatch.setenv("MCO_HTTP_TIMEOUT", "170")
    assert GatewayClient(instance_id="w", token="t").timeout == 170.0
    assert GatewayClient(instance_id="w", token="t", timeout=5).timeout == 5
