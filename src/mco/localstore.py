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
import time
import uuid
import re
from contextlib import contextmanager, nullcontext
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

# Tables whose history must never be rewritten (mirrors the Postgres trigger).
APPEND_ONLY_TABLES = {"agent_job_events", "mco_audit_outbox", "mco_attempt_receipts", "score_events", "score_checkpoint_decisions", "agent_exchanges", "agent_exchange_promotions"}

# Natural primary key per table (upsert conflict target).
# Equality lookups the gateway repeats on every request. The builder inlines the
# JSON path as a literal so SQLite can use these expression indexes; without
# them each audit append, heartbeat and job fetch scanned (and decoded) the
# whole table inside the writer lock.
HOT_INDEXES = {
    "agent_jobs": ("id",),
    "agent_registry": ("instance_id",),
    "agent_job_events": ("job_id", "outbox_id", "event"),
    "mco_audit_outbox": ("job_id",),
    "mco_attempt_receipts": ("job_id",),
}

PRIMARY_KEYS = {
    "agent_registry": "instance_id",
    "job_project_assignments": "job_id",
    "score_documents": "digest",
    "score_runs": "run_id",
    "score_tasks": "id",
    "score_events": "seq",
    "score_outbox": "id",
    "score_grants": "id",
    "score_reviews": "id",
    "score_providers": "instance_id",
    "score_provider_health": "instance_id",
    "score_recovery": "approval_or_attempt_id",
    "score_gate_requests": "id",
    "score_checkpoint_decisions": "id",
    "conductor_leases": "id",
}

# Unique constraints enforced across multi-column natural keys.
UNIQUE_CONSTRAINTS = {
    "score_tasks": ("org_id", "run_id", "task_id", "attempt"),
    "score_outbox": ("org_id", "run_id", "task_id", "phase"),
    "score_grants": ("org_id", "run_id", "digest"),
    "score_gate_requests": ("org_id", "run_id", "digest", "task_id", "kind"),
    "score_checkpoint_decisions": ("gate_id",),
    "agent_exchanges": ("org_id", "author_instance_id", "idempotency_key"),
    "agent_exchange_promotions": ("org_id", "exchange_id", "target_kind"),
}

# Dedicated Score tables mirrored in LocalStore.
SCORE_TABLES = {
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
    "score_gate_requests",
    "score_checkpoint_decisions",
    "conductor_leases",
}

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
        self._offset: int = 0

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

    def range(self, start: int, end: int):
        self._offset = max(0, int(start))
        self._limit = max(0, int(end) - self._offset + 1)
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
        self._thread = threading.local()
        self._readers_lock = threading.Lock()
        self._readers = []
        self._closed = False
        self._conn = sqlite3.connect(str(self._path), check_same_thread=False, timeout=30)
        mode = self._conn.execute("PRAGMA journal_mode=WAL").fetchone()[0]
        if mode.lower() != "wal":
            self._conn.close()
            raise RuntimeError(f"LocalStore requires WAL mode, got {mode}")
        for table in HOT_INDEXES:
            if self._table_exists(self._conn, table):
                self._ensure_indexes(table)

    # ── public API (mirrors the Supabase client) ─────────────────────────
    def table(self, name: str) -> _Query:
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name):
            raise ValueError("Invalid table name")
        return _Query(self, name)

    @contextmanager
    def transaction(self, *, immediate: bool = True):
        """Serialize writes while allowing snapshot reads during a writer transaction."""
        from mco.request_timing import add_lock_wait

        started = time.perf_counter()
        self._lock.acquire()
        add_lock_wait((time.perf_counter() - started) * 1000)
        try:
            outer = self._depth == 0
            if outer:
                self._conn.execute("BEGIN IMMEDIATE" if immediate else "BEGIN")
            self._depth += 1
            self._thread.depth = getattr(self._thread, "depth", 0) + 1
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
                self._thread.depth -= 1
        finally:
            self._lock.release()

    def _commit(self):
        if not self._depth:
            self._conn.commit()

    def rpc(self, name: str, params: dict) -> _RpcCall:
        return _RpcCall(self, name, params)

    def close(self) -> None:
        """Close all connections after callers have stopped using the store."""
        with self._lock:
            with self._readers_lock:
                if self._closed:
                    return
                self._closed = True
                for conn in self._readers:
                    conn.close()
                self._readers.clear()
                self._conn.close()

    def _read_connection(self) -> sqlite3.Connection:
        # Transactional reads must see this thread's uncommitted writes.
        # Another thread's active transaction must never redirect a reader.
        if getattr(self._thread, "depth", 0):
            return self._conn
        if self._closed:
            raise sqlite3.ProgrammingError("Cannot operate on a closed database.")
        conn = getattr(self._thread, "reader", None)
        if conn is None:
            with self._readers_lock:
                if self._closed:
                    raise sqlite3.ProgrammingError("Cannot operate on a closed database.")
                uri = self._path.resolve().as_uri() + "?mode=ro"
                conn = sqlite3.connect(uri, uri=True, check_same_thread=False, timeout=30)
                conn.execute("PRAGMA query_only=ON")
                self._readers.append(conn)
                self._thread.reader = conn
        return conn

    def _table_exists(self, conn: sqlite3.Connection, table: str) -> bool:
        return conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
        ).fetchone() is not None

    # ── storage plumbing ─────────────────────────────────────────────────
    def _ensure_indexes(self, table: str) -> None:
        for column in HOT_INDEXES.get(table, ()):
            self._conn.execute(
                f'CREATE INDEX IF NOT EXISTS "ix_{table}_{column}" '
                f"ON \"{table}\"(json_extract(data, '{self._json_path(column)}'))"
            )

    def _ensure_table(self, table: str) -> None:
        # Do not issue CREATE TABLE for every read: DDL upgrades a deferred
        # reader into a writer and collides with a worker's heartbeat commit.
        exists = self._conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
        ).fetchone()
        if not exists:
            self._conn.execute(
                f'CREATE TABLE IF NOT EXISTS "{table}" (pk TEXT PRIMARY KEY, data TEXT NOT NULL)'
            )
            self._ensure_indexes(table)

    def _pk_field(self, table: str) -> str:
        return PRIMARY_KEYS.get(table, "id")

    def _load_rows(self, table: str) -> List[dict]:
        conn = self._read_connection()
        if not self._table_exists(conn, table):
            return []
        cur = conn.execute(f'SELECT data FROM "{table}"')
        return [json.loads(r[0]) for r in cur.fetchall()]

    @staticmethod
    def _json_path(column: str) -> Optional[str]:
        """Return a bindable JSON1 path for a builder column, or None."""
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", column):
            return None
        return f'$."{column}"'

    def _python_select(self, q: _Query) -> List[dict]:
        """Compatibility path: decode every row, then filter/sort/window in Python."""
        rows = [r for r in self._load_rows(q._table) if self._matches(r, q._filters)]
        if q._order:
            col, desc = q._order
            rows.sort(key=lambda r: str(r.get(col) or ""), reverse=desc)
        if q._offset:
            rows = rows[q._offset:]
        if q._limit is not None:
            rows = rows[: q._limit]
        return rows

    def _count_rows(self, table: str) -> int:
        conn = self._read_connection()
        if not self._table_exists(conn, table):
            return 0
        return conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]

    def _find(self, table: str, *filters: tuple) -> List[dict]:
        """Rows matching ("eq"|"in"|"gt", column, value) filters, filtered in SQLite."""
        q = _Query(self, table)
        q._filters = list(filters)
        return self._select_rows(q)

    def _select_rows(self, q: _Query) -> List[dict]:
        """Select rows in SQLite before decoding their JSON documents.

        LocalStore keeps rows as JSON for PostgREST compatibility, but the
        audit, outbox and job tables hold hundreds of MB. Decoding whole
        tables inside the writer lock starved every other request (the
        2026-10-28 lock contention), so push builder predicates and the
        window into SQLite. Unsupported value shapes retain the generic
        compatibility path.
        """
        scalar = (str, int, float, bool, type(None))
        fallback = self._python_select
        clauses: List[str] = []
        params: List[Any] = []
        for kind, column, value in q._filters:
            path = self._json_path(column)
            if path is None:
                return fallback(q)
            if kind == "eq":
                if not isinstance(value, scalar):
                    return fallback(q)
                if value is None:
                    clauses.append(f"json_extract(data, '{path}') IS NULL")
                else:
                    clauses.append(f"json_extract(data, '{path}') = ?")
                    params.append(value)
            elif kind == "in":
                values = list(value)
                if not all(isinstance(item, scalar) for item in values):
                    return fallback(q)
                non_null = [item for item in values if item is not None]
                parts: List[str] = []
                if non_null:
                    placeholders = ", ".join("?" for _ in non_null)
                    parts.append(f"json_extract(data, '{path}') IN ({placeholders})")
                    params.extend(non_null)
                if any(item is None for item in values):
                    parts.append(f"json_extract(data, '{path}') IS NULL")
                clauses.append(f"({' OR '.join(parts)})" if parts else "0")
            elif kind == "gt":
                if not isinstance(value, scalar) or value is None:
                    return fallback(q)
                clauses.append(f"json_extract(data, '{path}') > ?")
                params.append(value)
            else:
                return fallback(q)

        conn = self._read_connection()
        if not self._table_exists(conn, q._table):
            return []
        select_expr = "data"
        wanted = [c.strip() for c in q._columns.split(",") if c.strip()]
        if q._columns.strip() != "*" and wanted and all(self._json_path(c) for c in wanted):
            # Narrow reads (ids, watermarks) must not decode whole documents.
            select_expr = "json_object(" + ", ".join(
                f"'{c}', json_extract(data, '{self._json_path(c)}')" for c in wanted) + ")"
        sql = f'SELECT {select_expr} FROM "{q._table}"'
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        if q._order:
            column, desc = q._order
            path = self._json_path(column)
            if path is None:
                return fallback(q)
            # _run_once historically sorted by str(value or ""). This cast
            # preserves that ordering for the job fields used by the API.
            sql += f" ORDER BY CAST(COALESCE(json_extract(data, '{path}'), '') AS TEXT)"
            sql += " DESC" if desc else " ASC"
            # Python's former stable sort preserved the table scan's rowid
            # order for equal values. Make that tie order explicit so LIMIT
            # and OFFSET page boundaries cannot drift across query plans.
            sql += ", rowid"
        if q._limit is not None:
            sql += " LIMIT ?"
            params.append(q._limit)
        elif q._offset:
            sql += " LIMIT -1"
        if q._offset:
            sql += " OFFSET ?"
            params.append(q._offset)

        cur = conn.execute(sql, params)
        return [json.loads(row[0]) for row in cur.fetchall()]

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
        elif pk == "seq" and not row.get("seq"):
            row["seq"] = self._count_rows(table) + 1
        elif pk == "run_id" and not row.get("run_id"):
            row["run_id"] = str(uuid.uuid4())

        if table == "score_events":
            if not row.get("at"):
                row["at"] = _now_iso()
        elif not row.get("created_at"):
            row["created_at"] = _now_iso()

        # Tenant column defaults to the single-tenant org, matching the
        # cloud migration's backfill, so org-scoped queries always match.
        row.setdefault("org_id", "default")

        # Sensible defaults for Score tables
        if table == "score_tasks":
            row.setdefault("attempt", 1)
            row.setdefault("status", "pending")
            row.setdefault("checkpoint_approved", False)
        elif table == "score_outbox":
            row.setdefault("attempt", 1)
            row.setdefault("status", "planned")
            row.setdefault("phase", "work")
            row.setdefault("payload", {})
        elif table == "score_recovery":
            row.setdefault("decision", "pending")
            row.setdefault("uncertain_effect", {})
        elif table == "score_gate_requests":
            row.setdefault("status", "pending")
        elif table == "score_runs":
            row.setdefault("status", "pending")
            row.setdefault("authorized_budget_cents", 0)
            row.setdefault("grants", [])
            row.setdefault("policy_snapshot", {})
        elif table == "score_documents":
            row.setdefault("revision", 1)
        elif table == "score_providers":
            row.setdefault("independence_class", "default")
            row.setdefault("cost_weight", 1.0)
            row.setdefault("capabilities", [])
            row.setdefault("approved_digests", [])
            row.setdefault("authority_scopes", [])
        return row

    # ── query execution ──────────────────────────────────────────────────
    def _run(self, q: _Query) -> APIResult:
        """Execute a query, briefly retrying inter-process SQLite contention.

        The gateway, listeners, and Score conductor can all write the same
        LocalStore from separate processes.  WAL plus a busy timeout handles
        ordinary overlap, but a long heartbeat/audit transaction can still
        surface ``database is locked`` at the transaction boundary.  A short
        bounded retry keeps that transient contention from failing an
        otherwise valid Score step while preserving fail-closed behavior for
        every other SQLite error.
        """
        delay = 0.1
        for attempt in range(8):
            try:
                return self._run_once(q)
            except sqlite3.OperationalError as exc:
                if "locked" not in str(exc).lower() or attempt == 7:
                    raise
                time.sleep(delay)
                delay = min(delay * 2, 2.0)

    def _run_once(self, q: _Query) -> APIResult:
        # Ordinary readers never acquire the writer lock or perform DDL.
        # Writes (including state + audit outbox) retain one atomic transaction.
        with (nullcontext() if q._op == "select" else self.transaction()):
            if q._table in APPEND_ONLY_TABLES and q._op == "upsert":
                raise PermissionError(f"{q._table} is append-only: UPSERT is not allowed")
            if q._op == "select":
                rows = self._select_rows(q)
                return APIResult([self._project(r, q._columns) for r in rows])

            if q._op == "insert":
                self._ensure_table(q._table)
                row = self._apply_defaults(q._table, q._payload or {})
                pk_name = self._pk_field(q._table)
                if any(r.get(pk_name) == row.get(pk_name)
                       for r in self._find(q._table, ("eq", pk_name, row.get(pk_name)))):
                    raise ValueError("Duplicate primary key")
                if q._table in UNIQUE_CONSTRAINTS:
                    cols = UNIQUE_CONSTRAINTS[q._table]
                    key_tuple = tuple(row.get(c) for c in cols)
                    if any(tuple(r.get(c) for c in cols) == key_tuple
                           for r in self._find(q._table, *(("eq", c, row.get(c)) for c in cols))):
                        raise ValueError(f"Duplicate unique constraint {cols} on {q._table}")
                self._write_row(q._table, row)
                self._commit()
                return APIResult([dict(row)])

            if q._op == "upsert":
                self._ensure_table(q._table)
                pk = self._pk_field(q._table)
                payload = dict(q._payload or {})
                existing = None
                if payload.get(pk) is not None:
                    for r in self._find(q._table, ("eq", pk, payload[pk])):
                        if r.get(pk) == payload[pk]:
                            existing = r
                            break
                row = {**existing, **payload} if existing else self._apply_defaults(q._table, payload)
                if q._table in UNIQUE_CONSTRAINTS:
                    cols = UNIQUE_CONSTRAINTS[q._table]
                    key_tuple = tuple(row.get(c) for c in cols)
                    for r in self._find(q._table, *(("eq", c, row.get(c)) for c in cols)):
                        if str(r.get(pk)) != str(row.get(pk)) and tuple(r.get(c) for c in cols) == key_tuple:
                            raise ValueError(f"Duplicate unique constraint {cols} on {q._table}")
                self._write_row(q._table, row)
                self._commit()
                return APIResult([dict(row)])

            if q._op == "update":
                if q._table in APPEND_ONLY_TABLES:
                    raise PermissionError(f"{q._table} is append-only: UPDATE is not allowed")
                pk = self._pk_field(q._table)
                updated = []
                for r in self._find(q._table, *q._filters):
                    if self._matches(r, q._filters):
                        candidate = {**r, **(q._payload or {})}
                        if q._table in UNIQUE_CONSTRAINTS:
                            cols = UNIQUE_CONSTRAINTS[q._table]
                            cand_tuple = tuple(candidate.get(c) for c in cols)
                            for other in self._find(q._table, *(("eq", c, candidate.get(c)) for c in cols)):
                                if str(other.get(pk)) != str(r.get(pk)) and tuple(other.get(c) for c in cols) == cand_tuple:
                                    raise ValueError(f"Duplicate unique constraint {cols} on {q._table}")
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
                for r in self._find(q._table, *q._filters):
                    if self._matches(r, q._filters):
                        removed.append(r)
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
