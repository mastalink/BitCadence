"""
LocalStore - BitCadence's embedded, zero-dependency persistence engine.

This is what makes the Local-Only profile a *real* edition instead of a demo:
jobs, the agent registry, the immutable audit trail, and the Drumline shared
context all persist to a single SQLite file (``~/.mco/local.db``) with no
cloud account, no external database, and no new dependencies (stdlib only).

It speaks the same fluent query dialect the rest of the codebase already
uses against Supabase/PostgREST::

    store.table("agent_jobs").select("*").eq("status", "pending").execute()
    store.table("agent_context").insert({...}).execute()
    store.rpc("lease_task", {"p_agent_instance_id": ..., "p_task_id": ...})

so every route, handler, and Drumline call works unchanged. ``get_db_client()``
returns a LocalStore when no Supabase credentials are configured.

Design notes:
- Rows are stored as JSON documents keyed by their natural primary key
  (``id``, or ``instance_id`` for the agent registry). Filtering happens in
  Python - local single-operator volumes never need an index.
- ``agent_job_events`` is append-only here too: update/delete raise, matching
  the tamper-evident DB trigger used in cloud deployments.
- ``lease_task`` is an atomic compare-and-set under the store lock, matching
  the Postgres function's "only one caller wins the race" contract.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import uuid
import re
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

# Tables whose history must never be rewritten (mirrors the Postgres trigger).
APPEND_ONLY_TABLES = {"agent_job_events", "mco_audit_outbox", "mco_attempt_receipts"}

# Natural primary key per table (upsert conflict target).
PRIMARY_KEYS = {"agent_registry": "instance_id"}

DEFAULT_DB_PATH = Path.home() / ".mco" / "local.db"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class APIResult:
    """Matches the shape of a PostgREST response: a ``.data`` list."""

    def __init__(self, data):
        self.data = data


class _RpcCall:
    """Deferred RPC matching ``db.rpc(name, params).execute()``."""

    def __init__(self, store: "LocalStore", name: str, params: dict):
        self._store = store
        self._name = name
        self._params = params or {}

    def execute(self) -> APIResult:
        if self._name == "lease_task":
            won = self._store._lease_task(
                self._params.get("p_agent_instance_id"),
                self._params.get("p_task_id"),
            )
            return APIResult(won)
        raise NotImplementedError(f"LocalStore has no RPC named '{self._name}'")


class _Query:
    """One chained query against a single table. Terminal call is execute()."""

    def __init__(self, store: "LocalStore", table: str):
        self._store = store
        self._table = table
        self._op = "select"
        self._payload: Optional[dict] = None
        self._columns = "*"
        self._filters: List[tuple] = []      # ("eq"|"in", column, value)
        self._order: Optional[tuple] = None  # (column, desc)
        self._limit: Optional[int] = None

    # ── verbs ────────────────────────────────────────────────────────────
    def select(self, columns: str = "*"):
        self._op = "select"
        self._columns = columns
        return self

    def insert(self, payload: dict):
        self._op = "insert"
        self._payload = payload
        return self

    def upsert(self, payload: dict):
        self._op = "upsert"
        self._payload = payload
        return self

    def update(self, payload: dict):
        self._op = "update"
        self._payload = payload
        return self

    def delete(self):
        self._op = "delete"
        return self

    # ── modifiers ────────────────────────────────────────────────────────
    def eq(self, column: str, value):
        self._filters.append(("eq", column, value))
        return self

    def in_(self, column: str, values):
        self._filters.append(("in", column, list(values or [])))
        return self

    def gt(self, column: str, value):
        self._filters.append(("gt", column, value))
        return self

    def is_(self, column: str, value):
        return self.eq(column, None if value == "null" else value)

    def order(self, column: str, desc: bool = False):
        self._order = (column, desc)
        return self

    def limit(self, n: int):
        self._limit = int(n)
        return self

    # ── terminal ─────────────────────────────────────────────────────────
    def execute(self) -> APIResult:
        return self._store._run(self)


class LocalStore:
    """Embedded SQLite document store speaking the PostgREST builder dialect."""

    backend = "local"

    def __init__(self, path: Optional[Path] = None):
        self._path = Path(path) if path else DEFAULT_DB_PATH
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._depth = 0
        self._conn = sqlite3.connect(str(self._path), check_same_thread=False, timeout=30)
        self._conn.execute("PRAGMA journal_mode=WAL")

    # ── public API (mirrors the Supabase client) ─────────────────────────
    def table(self, name: str) -> _Query:
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name):
            raise ValueError("Invalid table name")
        return _Query(self, name)

    @contextmanager
    def transaction(self):
        """Serialize read/modify/write across connections and roll back on failure."""
        with self._lock:
            outer = self._depth == 0
            if outer:
                self._conn.execute("BEGIN IMMEDIATE")
            self._depth += 1
            try:
                yield self
                if outer:
                    self._conn.commit()
            except BaseException:
                if outer:
                    self._conn.rollback()
                raise
            finally:
                self._depth -= 1

    def _commit(self):
        if not self._depth:
            self._conn.commit()

    def rpc(self, name: str, params: dict) -> _RpcCall:
        return _RpcCall(self, name, params)

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # ── storage plumbing ─────────────────────────────────────────────────
    def _ensure_table(self, table: str) -> None:
        self._conn.execute(
            f'CREATE TABLE IF NOT EXISTS "{table}" (pk TEXT PRIMARY KEY, data TEXT NOT NULL)'
        )

    def _pk_field(self, table: str) -> str:
        return PRIMARY_KEYS.get(table, "id")

    def _load_rows(self, table: str) -> List[dict]:
        self._ensure_table(table)
        cur = self._conn.execute(f'SELECT data FROM "{table}"')
        return [json.loads(r[0]) for r in cur.fetchall()]

    def _write_row(self, table: str, row: dict) -> None:
        pk = str(row[self._pk_field(table)])
        before = None
        if table == "agent_jobs":
            old = self._conn.execute('SELECT data FROM agent_jobs WHERE pk=?', (pk,)).fetchone()
            before = json.loads(old[0]) if old else None
        self._conn.execute(
            f'INSERT OR REPLACE INTO "{table}" (pk, data) VALUES (?, ?)',
            (pk, json.dumps(row, default=str)),
        )
        if table == "agent_jobs":
            # State and evidence commit together, even if a handler crashes
            # before adding its richer actor/decision event.
            self._ensure_table("mco_audit_outbox")
            evidence = self._apply_defaults("mco_audit_outbox", {
                "job_id": row["id"], "event": "state_committed",
                "detail": {"operation": "UPDATE" if before else "INSERT", "before": before, "after": dict(row)},
                "org_id": row.get("org_id", "default"),
            })
            self._write_row("mco_audit_outbox", evidence)

    @staticmethod
    def _matches(row: dict, filters: List[tuple]) -> bool:
        for kind, col, val in filters:
            have = row.get(col)
            if kind == "eq" and have != val:
                return False
            if kind == "in" and have not in val:
                return False
            if kind == "gt" and (have is None or have <= val):
                return False
        return True

    @staticmethod
    def _project(row: dict, columns: str) -> dict:
        if columns.strip() == "*":
            return dict(row)
        wanted = [c.strip() for c in columns.split(",") if c.strip()]
        return {c: row.get(c) for c in wanted}

    def _apply_defaults(self, table: str, row: dict) -> dict:
        row = dict(row)
        pk = self._pk_field(table)
        if pk == "id" and not row.get("id"):
            row["id"] = str(uuid.uuid4())
        if not row.get("created_at"):
            row["created_at"] = _now_iso()
        # Tenant column defaults to the single-tenant org, matching the
        # cloud migration's backfill, so org-scoped queries always match.
        row.setdefault("org_id", "default")
        return row

    # ── query execution ──────────────────────────────────────────────────
    def _run(self, q: _Query) -> APIResult:
        with self.transaction():
            if q._table in APPEND_ONLY_TABLES and q._op == "upsert":
                raise PermissionError(f"{q._table} is append-only: UPSERT is not allowed")
            if q._op == "select":
                rows = [r for r in self._load_rows(q._table) if self._matches(r, q._filters)]
                if q._order:
                    col, desc = q._order
                    rows.sort(key=lambda r: str(r.get(col) or ""), reverse=desc)
                if q._limit is not None:
                    rows = rows[: q._limit]
                return APIResult([self._project(r, q._columns) for r in rows])

            if q._op == "insert":
                self._ensure_table(q._table)
                row = self._apply_defaults(q._table, q._payload or {})
                if any(r.get(self._pk_field(q._table)) == row.get(self._pk_field(q._table))
                       for r in self._load_rows(q._table)):
                    raise ValueError("Duplicate primary key")
                self._write_row(q._table, row)
                self._commit()
                return APIResult([dict(row)])

            if q._op == "upsert":
                self._ensure_table(q._table)
                pk = self._pk_field(q._table)
                payload = dict(q._payload or {})
                existing = None
                if payload.get(pk) is not None:
                    for r in self._load_rows(q._table):
                        if r.get(pk) == payload[pk]:
                            existing = r
                            break
                row = {**existing, **payload} if existing else self._apply_defaults(q._table, payload)
                self._write_row(q._table, row)
                self._commit()
                return APIResult([dict(row)])

            if q._op == "update":
                if q._table in APPEND_ONLY_TABLES:
                    raise PermissionError(f"{q._table} is append-only: UPDATE is not allowed")
                updated = []
                for r in self._load_rows(q._table):
                    if self._matches(r, q._filters):
                        r.update(q._payload or {})
                        # Mirror the cloud trigger that stamps completed_at server-side.
                        if q._table == "agent_jobs" and r.get("status") == "completed" and not r.get("completed_at"):
                            r["completed_at"] = _now_iso()
                        self._write_row(q._table, r)
                        updated.append(dict(r))
                self._commit()
                return APIResult(updated)

            if q._op == "delete":
                if q._table in APPEND_ONLY_TABLES:
                    raise PermissionError(f"{q._table} is append-only: DELETE is not allowed")
                kept, removed = [], []
                for r in self._load_rows(q._table):
                    (removed if self._matches(r, q._filters) else kept).append(r)
                if removed:
                    for r in removed:
                        if q._table == "agent_jobs":
                            self._ensure_table("mco_audit_outbox")
                            evidence = self._apply_defaults("mco_audit_outbox", {
                                "job_id": r["id"], "event": "state_committed",
                                "detail": {"operation": "DELETE", "before": r, "after": None},
                                "org_id": r.get("org_id", "default"),
                            })
                            self._write_row("mco_audit_outbox", evidence)
                        self._conn.execute(f'DELETE FROM "{q._table}" WHERE pk=?', (str(r[self._pk_field(q._table)]),))
                    self._commit()
                return APIResult(removed)

        raise ValueError(f"Unknown operation: {q._op}")

    # ── atomic lease (mirrors the Postgres lease_task function) ──────────
    def _lease_task(self, agent_instance_id: Optional[str], task_id: Optional[str]) -> bool:
        if not agent_instance_id or not task_id:
            return False
        from mco.orchestrator.leases import acquire_lease
        return acquire_lease(self, task_id, agent_instance_id) is not None


# ── module-level singleton + local operator seeding ──────────────────────────

_local_store: Optional[LocalStore] = None
_seed_done = False


def get_local_store(path: Optional[Path] = None) -> LocalStore:
    """Return the process-wide LocalStore (created on first use)."""
    global _local_store
    if _local_store is None:
        _local_store = LocalStore(path)
    return _local_store


def seed_local_operator(store: LocalStore, local_token: str) -> None:
    """Register the operator agent for MCO_LOCAL_TOKEN (idempotent).

    This is what lets the console connect in Local-Only mode through the
    exact same registry/token path used in cloud deployments: the token from
    .env hashes to a real agent_registry row with an approver-capable role.
    """
    global _seed_done
    if _seed_done or not local_token:
        return
    import hashlib

    store.table("agent_registry").upsert({
        "instance_id": "local-operator",
        "role": "admin",
        "status": "online",
        "auth_token_hash": hashlib.sha256(local_token.encode("utf-8")).hexdigest(),
    }).execute()
    _seed_done = True
