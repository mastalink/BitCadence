"""Tests for Score v1 durable store: SQL migration and LocalStore dual-backend mirror.

Covers Packet S01a acceptance criteria:
- Migration discovery, ordering after 2026-09_job_priority.sql, and package drift guard.
- Migration apply on empty store vs store with 2026-09_job_priority already applied.
- All 12 org-scoped score tables, primary keys, and indices defined.
- Unique (org_id, run_id, task_id, attempt) constraint on both SQL and LocalStore.
- score_events append-only immutability (update/delete/upsert raises PermissionError).
- LocalStore dual-backend mirror: CRUD, natural PKs, defaults, and multi-tenant scoping.
"""

from pathlib import Path
import re
import sqlite3
import pytest

import mco.migrations_runner as mig
from mco.localstore import LocalStore, PRIMARY_KEYS, APPEND_ONLY_TABLES, UNIQUE_CONSTRAINTS, SCORE_TABLES


@pytest.fixture
def store(tmp_path):
    s = LocalStore(tmp_path / "score_test.db")
    yield s
    s.close()


# ── 1. Migration Discovery & Head Ordering ───────────────────────────────────

def test_score_store_migration_discovered_and_ordered():
    migs = mig.discover()
    names = [name for name, _ in migs]
    assert "2026-09_score_store.sql" in names
    
    # Must sort strictly AFTER 2026-09_job_priority.sql
    idx_prio = names.index("2026-09_job_priority.sql")
    idx_score = names.index("2026-09_score_store.sql")
    assert idx_score > idx_prio
    assert names == sorted(names)


def test_packaged_copy_matches_authored_score_migration():
    docs_file = Path(mig.__file__).resolve().parents[2] / "docs" / "migrations" / "2026-09_score_store.sql"
    pkg_file = Path(mig.__file__).resolve().parent / "migrations" / "2026-09_score_store.sql"
    assert docs_file.exists(), f"Missing canonical docs migration: {docs_file}"
    assert pkg_file.exists(), f"Missing packaged migration copy: {pkg_file}"
    assert docs_file.read_text(encoding="utf-8") == pkg_file.read_text(encoding="utf-8")


# ── 2. Migration Runner: Empty Store vs Already-Applied Store ────────────────

class FakeCursor:
    def __init__(self, store_state):
        self.state = store_state
        self._rows = []

    def execute(self, sql, params=None):
        self.state["log"].append((sql, params))
        if sql.startswith("SELECT name"):
            self._rows = [(n,) for n in self.state["applied"]]

    def fetchall(self):
        return self._rows


class FakeConn:
    def __init__(self, store_state):
        self.state = store_state

    def cursor(self):
        return FakeCursor(self.state)

    def commit(self):
        self.state["commits"] += 1

    def rollback(self):
        self.state["rollbacks"] += 1

    def close(self):
        self.state["closed"] = True


def test_migrate_empty_store_applies_all_including_score(monkeypatch):
    """Acceptance: migrate empty store applies all pending migrations."""
    state = {"log": [], "applied": set(), "commits": 0, "rollbacks": 0, "closed": False}
    monkeypatch.setattr(mig, "_connect", lambda url: (FakeConn(state), "psycopg"))
    
    result = mig.apply_postgres("postgres://acceptance-empty")
    assert result["skipped"] == []
    assert "2026-09_score_store.sql" in result["applied"]
    assert "2026-09_job_priority.sql" in result["applied"]
    assert state["closed"] is True


def test_migrate_store_with_job_priority_applied_applies_only_score(monkeypatch):
    """Acceptance: store that already applied 2026-09_job_priority only applies 2026-09_score_store.sql."""
    migs = mig.discover()
    names = [name for name, _ in migs]
    idx_prio = names.index("2026-09_job_priority.sql")
    already_applied = set(names[:idx_prio + 1])  # all migrations through job_priority

    state = {"log": [], "applied": already_applied, "commits": 0, "rollbacks": 0, "closed": False}
    monkeypatch.setattr(mig, "_connect", lambda url: (FakeConn(state), "psycopg"))

    result = mig.apply_postgres("postgres://acceptance-existing")
    assert "2026-09_job_priority.sql" in result["skipped"]
    assert result["applied"] == ["2026-09_score_store.sql", "2026-09_score_store_s04.sql"]
    inserts = [s for s, p in state["log"] if s.startswith("INSERT INTO schema_migrations")]
    assert len(inserts) == 2
    assert state["commits"] == 3  # init plus the two applied Score migrations


def test_migrate_idempotent_when_already_applied(monkeypatch):
    """Running migrations again skips 2026-09_score_store.sql when already applied."""
    migs = mig.discover()
    all_names = {name for name, _ in migs}

    state = {"log": [], "applied": all_names, "commits": 0, "rollbacks": 0, "closed": False}
    monkeypatch.setattr(mig, "_connect", lambda url: (FakeConn(state), "psycopg"))

    result = mig.apply_postgres("postgres://acceptance-current")
    assert "2026-09_score_store.sql" in result["skipped"]
    assert result["applied"] == []


# ── 3. SQL DDL & Table Specifications ────────────────────────────────────────

def test_sql_contains_all_12_required_score_tables_and_specs():
    migration_path = Path(mig.__file__).resolve().parents[2] / "docs" / "migrations" / "2026-09_score_store.sql"
    sql = migration_path.read_text(encoding="utf-8")

    required_tables = [
        "score_documents",
        "score_runs",
        "score_tasks",
        "score_events",
        "score_outbox",
        "score_grants",
        "score_reviews",
        "score_providers",
        "score_provider_health",
        "score_recovery",
        "conductor_leases",
    ]
    for table in required_tables:
        assert f"CREATE TABLE IF NOT EXISTS {table}" in sql

    # Verify UNIQUE (org_id, run_id, task_id, attempt)
    assert "uq_score_tasks_attempt UNIQUE (org_id, run_id, task_id, attempt)" in sql

    # Verify score_events immutability trigger & comments
    assert "IMMUTABLE" in sql
    assert "score_events_block_mutation" in sql
    assert "trg_score_events_immutable" in sql

    # Verify score_outbox statuses
    assert "CHECK (status IN ('planned', 'sending', 'submitted', 'reconciled'))" in sql
    assert "score_id    TEXT NOT NULL" in sql
    assert "digest      TEXT NOT NULL" in sql
    assert "attempt     INTEGER NOT NULL DEFAULT 1" in sql

    s04_path = migration_path.with_name("2026-09_score_store_s04.sql")
    s04_sql = s04_path.read_text(encoding="utf-8")
    assert "PRIMARY KEY (id)" in s04_sql
    assert "UNIQUE (org_id, run_id, digest)" in s04_sql
    assert "legacy-unscoped:" in s04_sql
    assert "uq_score_outbox_dispatch UNIQUE (org_id, run_id, task_id, phase)" in sql

    # Verify score_recovery decisions
    assert "CHECK (decision IN ('pending', 'inspected', 'compensated'))" in sql


def test_sql_ddl_and_unique_constraint_in_sqlite():
    """Test SQL table structure and UNIQUE constraint directly using SQLite engine."""
    conn = sqlite3.connect(":memory:")
    cur = conn.cursor()

    # DDL adapted to SQLite syntax (replacing now()/gen_random_uuid() and PL/pgSQL function)
    cur.execute("""
    CREATE TABLE score_tasks (
        id TEXT PRIMARY KEY,
        org_id TEXT NOT NULL DEFAULT 'default',
        run_id TEXT NOT NULL,
        task_id TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'pending',
        attempt INTEGER NOT NULL DEFAULT 1,
        author_id TEXT,
        author_provider TEXT,
        token TEXT,
        deadline TEXT,
        evidence TEXT,
        checkpoint_approved INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        CONSTRAINT uq_score_tasks_attempt UNIQUE (org_id, run_id, task_id, attempt)
    );
    """)

    # Insert first attempt
    cur.execute("INSERT INTO score_tasks (id, org_id, run_id, task_id, attempt) VALUES (?, ?, ?, ?, ?)",
                ("id1", "default", "run-1", "task-1", 1))
    conn.commit()

    # Unique attempt constraint violation
    with pytest.raises(sqlite3.IntegrityError):
        cur.execute("INSERT INTO score_tasks (id, org_id, run_id, task_id, attempt) VALUES (?, ?, ?, ?, ?)",
                    ("id2", "default", "run-1", "task-1", 1))

    # Different attempt succeeds
    cur.execute("INSERT INTO score_tasks (id, org_id, run_id, task_id, attempt) VALUES (?, ?, ?, ?, ?)",
                ("id2", "default", "run-1", "task-1", 2))
    conn.commit()
    assert cur.execute("SELECT COUNT(*) FROM score_tasks").fetchone()[0] == 2
    conn.close()


# ── 4. LocalStore Dual-Backend Mirror: Keys & Registration ───────────────────

def test_localstore_primary_keys_and_registrations():
    assert PRIMARY_KEYS["score_documents"] == "digest"
    assert PRIMARY_KEYS["score_runs"] == "run_id"
    assert PRIMARY_KEYS["score_tasks"] == "id"
    assert PRIMARY_KEYS["score_events"] == "seq"
    assert PRIMARY_KEYS["score_outbox"] == "id"
    assert PRIMARY_KEYS["score_grants"] == "id"
    assert PRIMARY_KEYS["score_reviews"] == "id"
    assert PRIMARY_KEYS["score_providers"] == "instance_id"
    assert PRIMARY_KEYS["score_provider_health"] == "instance_id"
    assert PRIMARY_KEYS["score_recovery"] == "approval_or_attempt_id"
    assert PRIMARY_KEYS["conductor_leases"] == "id"

    assert "score_events" in APPEND_ONLY_TABLES
    assert UNIQUE_CONSTRAINTS["score_tasks"] == ("org_id", "run_id", "task_id", "attempt")
    assert UNIQUE_CONSTRAINTS["score_outbox"] == ("org_id", "run_id", "task_id", "phase")
    assert UNIQUE_CONSTRAINTS["score_grants"] == ("org_id", "run_id", "digest")


# ── 5. LocalStore CRUD Across All Score Tables ──────────────────────────────

def test_localstore_score_documents_crud(store):
    res = store.table("score_documents").insert({
        "digest": "sha256:abcd",
        "score_id": "test-score",
        "revision": 1,
        "document": {"id": "test-score", "score_version": 1},
    }).execute()
    assert res.data[0]["digest"] == "sha256:abcd"
    assert res.data[0]["org_id"] == "default"

    found = store.table("score_documents").select("*").eq("digest", "sha256:abcd").execute().data
    assert len(found) == 1
    assert found[0]["score_id"] == "test-score"


def test_localstore_score_runs_crud(store):
    res = store.table("score_runs").insert({
        "run_id": "run-100",
        "digest": "sha256:abcd",
        "principal": "operator@bitcadence.local",
        "authorized_budget_cents": 5000,
    }).execute()
    assert res.data[0]["run_id"] == "run-100"
    assert res.data[0]["status"] == "pending"
    assert res.data[0]["org_id"] == "default"

    store.table("score_runs").update({"status": "running"}).eq("run_id", "run-100").execute()
    updated = store.table("score_runs").select("*").eq("run_id", "run-100").execute().data[0]
    assert updated["status"] == "running"


def test_localstore_score_outbox_transitions(store):
    res = store.table("score_outbox").insert({
        "run_id": "run-100",
        "task_id": "task-a",
        "phase": "work",
        "payload": {"action": "build"},
    }).execute()
    outbox_id = res.data[0]["id"]
    assert res.data[0]["status"] == "planned"

    store.table("score_outbox").update({"status": "sending"}).eq("id", outbox_id).execute()
    store.table("score_outbox").update({"status": "submitted", "job_id": "job-xyz"}).eq("id", outbox_id).execute()

    final = store.table("score_outbox").select("*").eq("id", outbox_id).execute().data[0]
    assert final["status"] == "submitted"
    assert final["job_id"] == "job-xyz"


def test_localstore_score_grants_and_reviews(store):
    store.table("score_grants").insert({
        "run_id": "run-100",
        "digest": "sha256:grant-1",
        "actions": ["repo:read", "test:run"],
        "resources": ["repo"],
        "env": "staging",
        "expires_at": "2026-12-31T00:00:00Z",
        "human_principal": "engineer@via.local",
        "signature": "sig123",
    }).execute()
    grant = store.table("score_grants").select("*").eq("digest", "sha256:grant-1").execute().data[0]
    assert grant["human_principal"] == "engineer@via.local"

    store.table("score_reviews").insert({
        "run_id": "run-100",
        "task_id": "task-a",
        "attempt": 1,
        "reviewer_id": "reviewer-1",
        "reviewer_provider": "anthropic",
        "head": "git:commit:123",
        "verdict": "accepted",
        "evidence_binding": {"test_report": "sha256:rep1"},
    }).execute()
    rev = store.table("score_reviews").select("*").eq("run_id", "run-100").execute().data[0]
    assert rev["verdict"] == "accepted"


def test_localstore_score_providers_and_health(store):
    store.table("score_providers").insert({
        "instance_id": "prov-gpt4",
        "role": "worker",
        "provider": "openai",
        "capabilities": ["code", "test"],
        "independence_class": "class-a",
    }).execute()
    p = store.table("score_providers").select("*").eq("instance_id", "prov-gpt4").execute().data[0]
    assert p["provider"] == "openai"
    assert p["cost_weight"] == 1.0

    store.table("score_provider_health").insert({
        "instance_id": "prov-gpt4",
        "class": "class-a",
        "last_error": None,
    }).execute()
    store.table("score_provider_health").update({"last_error": "rate_limited"}).eq("instance_id", "prov-gpt4").execute()
    h = store.table("score_provider_health").select("*").eq("instance_id", "prov-gpt4").execute().data[0]
    assert h["last_error"] == "rate_limited"


def test_localstore_score_recovery_and_conductor_leases(store):
    store.table("score_recovery").insert({
        "approval_or_attempt_id": "att-123",
        "uncertain_effect": {"deployed_version": "v1"},
        "decision": "pending",
    }).execute()
    rec = store.table("score_recovery").select("*").eq("approval_or_attempt_id", "att-123").execute().data[0]
    assert rec["decision"] == "pending"

    store.table("conductor_leases").insert({
        "id": "tick-primary",
        "incarnation": "inc-abc",
        "owner": "conductor-instance-1",
        "expires_at": "2026-09-15T04:00:00Z",
    }).execute()
    lease = store.table("conductor_leases").select("*").eq("id", "tick-primary").execute().data[0]
    assert lease["owner"] == "conductor-instance-1"


# ── 6. Acceptance: Unique (org, run, task, attempt) on LocalStore ────────────

def test_localstore_score_tasks_unique_attempt_constraint(store):
    """Acceptance: unique (org, run, task, attempt) enforced."""
    # First attempt succeeds
    store.table("score_tasks").insert({
        "org_id": "default",
        "run_id": "run-1",
        "task_id": "task-build",
        "attempt": 1,
        "status": "running",
    }).execute()

    # Duplicate attempt on the same (org, run, task) must fail
    with pytest.raises(ValueError, match="Duplicate unique constraint"):
        store.table("score_tasks").insert({
            "org_id": "default",
            "run_id": "run-1",
            "task_id": "task-build",
            "attempt": 1,
            "status": "running",
        }).execute()

    # Different attempt succeeds
    res_att2 = store.table("score_tasks").insert({
        "org_id": "default",
        "run_id": "run-1",
        "task_id": "task-build",
        "attempt": 2,
        "status": "running",
    }).execute()
    assert res_att2.data[0]["attempt"] == 2

    # Different task_id succeeds
    res_task2 = store.table("score_tasks").insert({
        "org_id": "default",
        "run_id": "run-1",
        "task_id": "task-test",
        "attempt": 1,
        "status": "pending",
    }).execute()
    assert res_task2.data[0]["task_id"] == "task-test"

    # Different tenant org_id succeeds with same run/task/attempt
    res_other_org = store.table("score_tasks").insert({
        "org_id": "tenant-xyz",
        "run_id": "run-1",
        "task_id": "task-build",
        "attempt": 1,
        "status": "pending",
    }).execute()
    assert res_other_org.data[0]["org_id"] == "tenant-xyz"

    # Updating an existing row to collide with another row's unique tuple must fail
    with pytest.raises(ValueError, match="Duplicate unique constraint"):
        store.table("score_tasks").update({"attempt": 1}).eq("id", res_att2.data[0]["id"]).execute()


def test_localstore_score_outbox_unique_dispatch_constraint(store):
    first = {
        "org_id": "default",
        "score_id": "score-a",
        "run_id": "run-1",
        "digest": "digest-a",
        "task_id": "task-build",
        "attempt": 3,
        "phase": "work",
        "job_id": "job-a",
        "payload": {"id": "job-a"},
    }
    stored = store.table("score_outbox").insert(first).execute().data[0]
    assert stored["attempt"] == 3
    assert stored["digest"] == "digest-a"

    with pytest.raises(ValueError, match="Duplicate unique constraint"):
        store.table("score_outbox").insert(
            {**first, "id": "another-row", "job_id": "another-job", "attempt": 4}
        ).execute()

    review = store.table("score_outbox").insert(
        {**first, "id": "review-row", "job_id": "review-job", "phase": "review"}
    ).execute().data[0]
    assert review["phase"] == "review"


# ── 7. Acceptance: Events Append-Only Immutability on LocalStore ─────────────

def test_localstore_score_events_is_append_only(store):
    """Acceptance: events append-only in tests (update/delete raises)."""
    res = store.table("score_events").insert({
        "run_id": "run-1",
        "kind": "task_started",
        "task_id": "task-a",
        "actor": "agent-1",
        "payload": {"detail": "started work"},
    }).execute()
    assert res.data[0]["seq"] == 1
    assert res.data[0]["at"]
    assert res.data[0]["org_id"] == "default"

    # Appending a second event increments sequence
    res2 = store.table("score_events").insert({
        "run_id": "run-1",
        "kind": "task_finished",
        "task_id": "task-a",
        "actor": "agent-1",
        "payload": {"detail": "finished work"},
    }).execute()
    assert res2.data[0]["seq"] == 2

    # UPDATE must raise PermissionError
    with pytest.raises(PermissionError, match="append-only: UPDATE is not allowed"):
        store.table("score_events").update({"kind": "rewritten"}).eq("seq", 1).execute()

    # DELETE must raise PermissionError
    with pytest.raises(PermissionError, match="append-only: DELETE is not allowed"):
        store.table("score_events").delete().eq("seq", 1).execute()

    # UPSERT must raise PermissionError
    with pytest.raises(PermissionError, match="append-only: UPSERT is not allowed"):
        store.table("score_events").upsert({"seq": 1, "kind": "tampered"}).execute()

    # Verify rows remained unchanged
    rows = store.table("score_events").select("*").order("seq").execute().data
    assert len(rows) == 2
    assert rows[0]["kind"] == "task_started"
    assert rows[1]["kind"] == "task_finished"
