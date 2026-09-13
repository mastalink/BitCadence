"""LocalStore: the embedded SQLite data plane for the Local-Only profile.

Covers the PostgREST-dialect builder, defaults, append-only audit
enforcement, the atomic lease, persistence across reopen, Drumline on the
local store, and a full job lifecycle through the real FastAPI app with
token auth served entirely by the embedded store.
"""

import hashlib

import pytest
from fastapi.testclient import TestClient

import mco.localstore as localstore_mod
from mco.localstore import LocalStore, seed_local_operator
import mco.orchestrator.routes as routes_mod


@pytest.fixture
def store(tmp_path):
    s = LocalStore(tmp_path / "test.db")
    yield s
    s.close()


# ── builder basics ────────────────────────────────────────────────────────────

def test_insert_returns_row_with_defaults(store):
    res = store.table("agent_jobs").insert({"title": "t", "status": "pending"}).execute()
    row = res.data[0]
    assert row["id"]
    assert row["created_at"]
    assert row["org_id"] == "default"
    assert row["title"] == "t"


def test_select_eq_in_order_limit_projection(store):
    for i, status in enumerate(["pending", "pending", "completed"]):
        store.table("agent_jobs").insert({"id": f"j{i}", "title": f"job {i}", "status": status}).execute()

    pending = store.table("agent_jobs").select("*").eq("status", "pending").execute()
    assert {r["id"] for r in pending.data} == {"j0", "j1"}

    subset = store.table("agent_jobs").select("status").in_("id", ["j0", "j2"]).execute()
    assert all(set(r.keys()) == {"status"} for r in subset.data)

    newest = store.table("agent_jobs").select("*").order("id", desc=True).limit(1).execute()
    assert newest.data[0]["id"] == "j2"


def test_update_filters_and_returns_updated_rows(store):
    store.table("agent_jobs").insert({"id": "u1", "status": "pending", "title": "x"}).execute()
    res = store.table("agent_jobs").update({"status": "in_progress"}).eq("id", "u1").execute()
    assert res.data[0]["status"] == "in_progress"
    missed = store.table("agent_jobs").update({"status": "x"}).eq("id", "nope").execute()
    assert missed.data == []


def test_completed_update_stamps_completed_at(store):
    store.table("agent_jobs").insert({"id": "c1", "status": "leased", "title": "x"}).execute()
    res = store.table("agent_jobs").update({"status": "completed"}).eq("id", "c1").execute()
    assert res.data[0]["completed_at"]


def test_upsert_merges_by_instance_id(store):
    store.table("agent_registry").upsert({"instance_id": "a1", "role": "codex", "status": "offline"}).execute()
    res = store.table("agent_registry").upsert({"instance_id": "a1", "status": "online"}).execute()
    assert res.data[0]["role"] == "codex"
    assert res.data[0]["status"] == "online"
    all_rows = store.table("agent_registry").select("*").execute()
    assert len(all_rows.data) == 1


# ── audit immutability ────────────────────────────────────────────────────────

def test_agent_job_events_is_append_only(store):
    store.table("agent_job_events").insert({"job_id": "j1", "event": "created"}).execute()
    with pytest.raises(PermissionError):
        store.table("agent_job_events").update({"event": "rewritten"}).eq("job_id", "j1").execute()
    with pytest.raises(PermissionError):
        store.table("agent_job_events").delete().eq("job_id", "j1").execute()


# ── atomic lease ──────────────────────────────────────────────────────────────

def test_lease_task_single_winner(store):
    store.table("agent_jobs").insert({"id": "L1", "status": "pending", "title": "x"}).execute()
    first = store.rpc("lease_task", {"p_agent_instance_id": "w1", "p_task_id": "L1"}).execute()
    second = store.rpc("lease_task", {"p_agent_instance_id": "w2", "p_task_id": "L1"}).execute()
    assert first.data is True
    assert second.data is False
    row = store.table("agent_jobs").select("*").eq("id", "L1").execute().data[0]
    assert row["status"] == "leased"
    assert row["leased_by_instance_id"] == "w1"
    assert row["started_at"]


def test_lease_task_ignores_non_pending(store):
    store.table("agent_jobs").insert({"id": "L2", "status": "completed", "title": "x"}).execute()
    res = store.rpc("lease_task", {"p_agent_instance_id": "w1", "p_task_id": "L2"}).execute()
    assert res.data is False


# ── persistence ───────────────────────────────────────────────────────────────

def test_rows_survive_reopen(tmp_path):
    path = tmp_path / "persist.db"
    s1 = LocalStore(path)
    s1.table("agent_context").insert({"title": "fact one", "content": "remember me", "kind": "fact"}).execute()
    s1.close()
    s2 = LocalStore(path)
    rows = s2.table("agent_context").select("*").execute()
    s2.close()
    assert rows.data[0]["title"] == "fact one"


# ── Drumline on the local store ─────────────────────────────────────────────────

def test_drumline_remember_and_recall_locally(store):
    from mco.orchestrator.drumline import remember, recall

    entry = remember(store, title="Prod DB read-only on Sundays",
                     content="Maintenance window 02:00-06:00 UTC.",
                     kind="fact", tags=["ops", "postgres"])
    assert entry and entry["id"]

    hits = recall(store, query="prod db maintenance", limit=3)
    assert hits and hits[0]["title"] == "Prod DB read-only on Sundays"


# ── operator seeding ──────────────────────────────────────────────────────────

def test_seed_local_operator_registers_admin(store, monkeypatch):
    monkeypatch.setattr(localstore_mod, "_seed_done", False)
    seed_local_operator(store, "mco_tok_localtest")
    rows = store.table("agent_registry").select("*").execute().data
    assert rows[0]["instance_id"] == "local-operator"
    assert rows[0]["role"] == "admin"
    assert rows[0]["auth_token_hash"] == hashlib.sha256(b"mco_tok_localtest").hexdigest()


# ── end-to-end: full job lifecycle through the app on the embedded store ─────

def test_full_lifecycle_on_local_store(tmp_path, monkeypatch):
    store = LocalStore(tmp_path / "e2e.db")
    monkeypatch.setattr(localstore_mod, "_seed_done", False)
    token = "mco_tok_e2e_secret"
    seed_local_operator(store, token)
    monkeypatch.setattr(routes_mod, "get_db_client", lambda force_new=False: store)

    from mco.cli import create_app
    http = TestClient(create_app())
    auth = {"Authorization": f"Bearer {token}"}

    # Create
    resp = http.post("/api/jobs", headers=auth, json={
        "title": "Summarize logs", "target_agent_role": "admin",
        "input_payload": {"prompt": "Summarize the error logs"},
    })
    assert resp.status_code == 200
    job_id = resp.json()["job"]["id"]

    # Lease (as the seeded operator)
    resp = http.post("/api/jobs/lease", headers=auth,
                     json={"task_id": job_id, "agent_instance_id": "local-operator"})
    assert resp.status_code == 200 and resp.json()["success"] is True

    claim = resp.json()["lease"]
    # Complete with output -> triggers Drumline distillation
    resp = http.put(f"/api/jobs/{job_id}", headers=auth, json={
        **claim, "status": "completed",
        "output_payload": {"result": "Root cause: disk full on web-01."},
    })
    assert resp.status_code == 200

    # Audit trail recorded the whole chain, append-only
    resp = http.get(f"/api/jobs/{job_id}/events", headers=auth)
    assert resp.status_code == 200
    body = resp.json()
    rows = body["events"] if isinstance(body, dict) else body
    events = [e["event"] for e in rows]
    assert "created" in events
    assert "leased" in events
    assert "status:completed" in events

    # Drumline distilled the outcome into shared context on the SAME local store
    ctx = store.table("agent_context").select("*").execute().data
    assert any("Summarize logs" in (e.get("title") or "") for e in ctx)

    store.close()
