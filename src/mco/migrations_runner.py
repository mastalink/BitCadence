"""
Schema migration runner behind `mco upgrade`.

Two backends, honestly handled:

- **Embedded LocalStore**: rows are JSON documents, so new columns appear
  automatically - there is nothing to migrate. `mco upgrade` confirms this.
- **Postgres / Supabase**: the data plane talks PostgREST, which cannot run
  DDL. So:
    * If DATABASE_URL is set and a psycopg driver is importable, migrations
      are applied transactionally and recorded in a `schema_migrations`
      table (each runs at most once).
    * Otherwise the pending SQL is written to one combined, idempotent
      script and you apply it in the Supabase SQL editor - the runner still
      tells you exactly what is pending.

All shipped migrations are idempotent (`create ... if not exists`,
`add column if not exists`), so re-running is always safe.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import List, Optional, Tuple

TRACK_TABLE = "schema_migrations"


def migrations_dir() -> Path:
    """Locate the SQL directory: env override, then the authored docs copy
    (source/editable installs), then the packaged copy (wheel installs)."""
    override = os.environ.get("MCO_MIGRATIONS_DIR")
    if override:
        return Path(override)
    # Authored canonical location, present in a git clone / editable install.
    docs = Path(__file__).resolve().parents[2] / "docs" / "migrations"
    if docs.is_dir():
        return docs
    # Packaged copy (ships in the wheel).
    return Path(__file__).resolve().parent / "migrations"


def discover() -> List[Tuple[str, str]]:
    """Return [(name, sql)] for every migration, sorted by filename."""
    d = migrations_dir()
    if not d.is_dir():
        return []
    return [(p.name, p.read_text(encoding="utf-8"))
            for p in sorted(d.glob("*.sql"))]


def _connect(database_url: str):
    """Open a Postgres connection via psycopg (v3) or psycopg2, or None."""
    try:
        import psycopg  # type: ignore
        return psycopg.connect(database_url), "psycopg"
    except ImportError:
        pass
    try:
        import psycopg2  # type: ignore
        return psycopg2.connect(database_url), "psycopg2"
    except ImportError:
        return None, None


def _applied_names(conn) -> set:
    cur = conn.cursor()
    cur.execute(f"CREATE TABLE IF NOT EXISTS {TRACK_TABLE} "
                "(name TEXT PRIMARY KEY, applied_at TIMESTAMPTZ DEFAULT now())")
    conn.commit()
    cur.execute(f"SELECT name FROM {TRACK_TABLE}")
    return {r[0] for r in cur.fetchall()}


def apply_postgres(database_url: str) -> dict:
    """Apply pending migrations to Postgres, recording each in schema_migrations.

    Returns {applied: [...], skipped: [...], driver: str}. Each migration runs
    in its own transaction so one failure doesn't roll back earlier successes.
    """
    conn, driver = _connect(database_url)
    if conn is None:
        raise RuntimeError(
            "DATABASE_URL is set but no Postgres driver is installed. "
            "Run: pip install psycopg[binary]")
    applied, skipped = [], []
    try:
        done = _applied_names(conn)
        for name, sql in discover():
            if name in done:
                skipped.append(name)
                continue
            cur = conn.cursor()
            try:
                cur.execute(sql)
                cur.execute(f"INSERT INTO {TRACK_TABLE} (name) VALUES (%s)", (name,))
                conn.commit()
                applied.append(name)
            except Exception:
                conn.rollback()
                raise
    finally:
        conn.close()
    return {"applied": applied, "skipped": skipped, "driver": driver}


def write_combined_script(applied_names: Optional[set] = None) -> Tuple[Path, List[str]]:
    """Write pending migrations to one idempotent script for manual apply.
    Returns (path, [names])."""
    applied_names = applied_names or set()
    pending = [(n, s) for n, s in discover() if n not in applied_names]
    out = Path.home() / ".mco" / "pending_migrations.sql"
    out.parent.mkdir(parents=True, exist_ok=True)
    if pending:
        body = "\n\n".join(f"-- ==== {n} ====\n{s}" for n, s in pending)
        out.write_text(
            "-- BitCadence pending migrations (idempotent; safe to re-run).\n"
            "-- Paste into the Supabase SQL editor, or set DATABASE_URL and\n"
            "-- re-run 'mco upgrade' to apply automatically.\n\n" + body,
            encoding="utf-8")
    return out, [n for n, _ in pending]


def backend_kind() -> str:
    """'local', 'postgres', or 'none' for the active data plane."""
    from mco.orchestrator.routes import get_db_client
    db = get_db_client()
    if db is None:
        return "none"
    return "local" if getattr(db, "backend", "supabase") == "local" else "postgres"
