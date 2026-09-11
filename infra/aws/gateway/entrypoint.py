"""Gateway entrypoint for the EPCOT demo.

Runs, in order:

1. (postgres) Mint the PostgREST JWT the Supabase client will present, wait
   for PostgREST, apply the SQL migrations, and tell PostgREST to reload its
   schema cache.
2. Start `mco serve` as a child process.
3. Run the LEDGER SHIPPER beside it: every committed audit event is copied to
   the S3 evidence vault (Object Lock, COMPLIANCE) as its own object.

The shipper is an asynchronous repair path. The gateway itself enforces
MCO_EVIDENCE_ACK_REQUIRED: attempt acknowledgements wait for durable state
outbox evidence and locked S3 versions. Emergency stops fence locally before
waiting for the sink. The shipper also recovers events after interrupted work;
its interval is not an acknowledgement guarantee during a storage outage.
"""

from __future__ import annotations

import hashlib
import json
import os
import signal
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

import boto3
import httpx
from loguru import logger

logger.remove()
logger.add(sys.stdout, serialize=True, level="INFO")

BACKEND = os.environ.get("MCO_STORE_BACKEND", "postgres")
BUCKET = os.environ.get("MCO_EVIDENCE_BUCKET", "")
SHIP_INTERVAL = float(os.environ.get("MCO_SHIP_INTERVAL", "10"))
EVENTS_TABLE = "agent_job_events"


# ── 1. Postgres preparation ──────────────────────────────────────────────────

def mint_postgrest_jwt() -> str:
    from authlib.jose import jwt  # a declared dependency of the package

    secret = os.environ["JWT_SECRET"]
    claims = {
        "role": "bitcadence",
        "iss": "bitcadence-epcot",
        "iat": int(time.time()),
        "exp": int(time.time()) + 10 * 365 * 86400,
    }
    token = jwt.encode({"alg": "HS256"}, claims, secret)
    return token.decode() if isinstance(token, bytes) else token


def wait_for(url: str, ok=(200, 401, 404), attempts: int = 60) -> None:
    for i in range(attempts):
        try:
            if httpx.get(url, timeout=3).status_code in ok:
                return
        except Exception:
            pass
        time.sleep(2)
    raise SystemExit(f"gave up waiting for {url}")


def apply_postgres_bootstrap(database_url: str) -> list[str]:
    """Create the small base schema that the additive migrations build on.

    The project migrations intentionally evolve an existing BitCadence schema;
    a bare RDS database has no ``agent_registry`` or ``agent_jobs`` tables yet.
    Keep that first-install concern in the AWS image instead of teaching the
    shared migration runner about one deployment profile.
    """
    import psycopg

    overlay_dir = Path(
        os.environ.get(
            "MCO_POSTGRES_BOOTSTRAP_DIR",
            Path(__file__).with_name("migrations-overlay"),
        )
    )
    files = sorted(overlay_dir.glob("*.sql"))
    if not files:
        raise RuntimeError(f"no PostgreSQL bootstrap SQL found in {overlay_dir}")

    applied = []
    with psycopg.connect(database_url) as conn:
        for path in files:
            with conn.transaction():
                conn.execute(path.read_text(encoding="utf-8"))
            applied.append(path.name)
    return applied


def seed_postgres_operator(database_url: str) -> str:
    """Make the Terraform-managed operator token valid for Postgres auth.

    ``MCO_LOCAL_TOKEN`` is also the conductor's admin credential.  The normal
    LocalStore path seeds that token automatically, but the Supabase/PostgREST
    path authenticates exclusively against ``agent_registry``.  A pristine
    RDS database therefore needs one dedicated operator row, and subsequent
    boots must rotate its hash when Terraform rotates the secret.
    """
    import psycopg

    token = (os.environ.get("MCO_LOCAL_TOKEN") or "").strip()
    if not token:
        raise RuntimeError("MCO_LOCAL_TOKEN is required for the Postgres backend")

    instance_id = "epcot-operator"
    token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
    with psycopg.connect(database_url) as conn:
        conn.execute(
            """
            INSERT INTO agent_registry
                (instance_id, role, status, last_seen_at, auth_token_hash,
                 org_id, scopes)
            VALUES (%s, 'admin', 'online', now(), %s, 'default', '["admin"]'::jsonb)
            ON CONFLICT (instance_id) DO UPDATE SET
                role = EXCLUDED.role,
                status = EXCLUDED.status,
                last_seen_at = EXCLUDED.last_seen_at,
                auth_token_hash = EXCLUDED.auth_token_hash,
                org_id = EXCLUDED.org_id,
                scopes = EXCLUDED.scopes
            """,
            (instance_id, token_hash),
        )
    return instance_id


def prepare_postgres() -> None:
    os.environ["SUPABASE_KEY"] = mint_postgrest_jwt()
    logger.info({"event": "postgrest.jwt_minted"})

    wait_for(f"{os.environ['SUPABASE_URL']}/rest/v1/", ok=(200, 401, 404, 503))

    bootstrap = apply_postgres_bootstrap(os.environ["DATABASE_URL"])
    logger.info({"event": "postgres.bootstrap_applied", "files": bootstrap})

    from mco.migrations_runner import apply_postgres

    result = apply_postgres(os.environ["DATABASE_URL"])
    logger.info({"event": "migrations.applied", **{k: v for k, v in result.items() if k != "driver"}})

    operator = seed_postgres_operator(os.environ["DATABASE_URL"])
    logger.info({"event": "postgres.operator_seeded", "instance_id": operator})

    # PostgREST caches the schema at start; the tables were just created.
    import psycopg

    with psycopg.connect(os.environ["DATABASE_URL"], autocommit=True) as conn:
        conn.execute("NOTIFY pgrst, 'reload schema'")
    logger.info({"event": "postgrest.schema_reloaded"})


# ── 3. Ledger shipper ────────────────────────────────────────────────────────

def _rows_postgres(after: tuple[str, str]):
    import psycopg
    from psycopg.rows import dict_row

    with psycopg.connect(os.environ["DATABASE_URL"], row_factory=dict_row) as conn:
        # id is a bigint identity; created_at is timestamptz. Watermark on both.
        cur = conn.execute(
            f"SELECT * FROM {EVENTS_TABLE} "
            "WHERE (created_at, id) > (%s::timestamptz, %s::bigint) "
            "ORDER BY created_at, id LIMIT 500",
            after,
        )
        return cur.fetchall()


def _rows_local(after: tuple[str, str]):
    from mco.localstore import get_local_store
    from pathlib import Path

    store = get_local_store(Path(os.environ.get("MCO_LOCAL_STORE_PATH", "/mco/local.db")))
    rows = store.table(EVENTS_TABLE).select("*").execute().data or []
    rows.sort(key=lambda r: (str(r.get("created_at", "")), str(r.get("id", ""))))
    return [r for r in rows if (str(r.get("created_at", "")), str(r.get("id", ""))) > after][:500]


def ship_forever() -> None:
    if not BUCKET:
        logger.warning({"event": "shipper.disabled", "reason": "MCO_EVIDENCE_BUCKET unset"})
        return

    s3 = boto3.client("s3", region_name=os.environ.get("AWS_REGION"))
    fetch = _rows_postgres if BACKEND == "postgres" else _rows_local
    after: tuple[str, str] = ("1970-01-01T00:00:00+00:00", "0")
    shipped = 0

    logger.info({"event": "shipper.start", "bucket": BUCKET, "backend": BACKEND, "interval_s": SHIP_INTERVAL})
    while True:
        try:
            rows = fetch(after)
            for row in rows:
                key = f"ledger/{row.get('job_id', '_')}/{row.get('id')}.json"
                body = json.dumps(row, default=str, separators=(",", ":"), sort_keys=True).encode()
                # The bucket's default retention applies; no per-object override,
                # so nothing here can shorten what Terraform declared.
                s3.put_object(Bucket=BUCKET, Key=key, Body=body, ContentType="application/json")
                after = (str(row.get("created_at", "")), str(row.get("id", "")))
                shipped += 1
            if rows:
                head = {
                    "shipped_total": shipped,
                    "last": {"created_at": after[0], "id": after[1]},
                    "last_hash": rows[-1].get("hash"),  # the chain's own column name
                    "at": datetime.now(timezone.utc).isoformat(),
                }
                s3.put_object(
                    Bucket=BUCKET,
                    Key=f"ledger/_head/{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.json",
                    Body=json.dumps(head, separators=(",", ":")).encode(),
                    ContentType="application/json",
                )
                logger.info({"event": "shipper.batch", "rows": len(rows), "total": shipped})
        except Exception as e:  # never let the mirror take the gateway down
            logger.warning({"event": "shipper.error", "error": f"{type(e).__name__}: {e}"})
        time.sleep(SHIP_INTERVAL)


# ── main ─────────────────────────────────────────────────────────────────────

def main() -> int:
    if BACKEND == "postgres":
        prepare_postgres()
    else:
        os.makedirs(os.path.dirname(os.environ.get("MCO_LOCAL_STORE_PATH", "/mco/local.db")), exist_ok=True)

    serve = subprocess.Popen(["mco", "serve", "--host", "0.0.0.0", "--port", "18789"])
    logger.info({"event": "serve.started", "pid": serve.pid})

    threading.Thread(target=ship_forever, name="ledger-shipper", daemon=True).start()

    def _forward(sig, _frame):
        serve.send_signal(sig)

    signal.signal(signal.SIGTERM, _forward)
    signal.signal(signal.SIGINT, _forward)

    return serve.wait()


if __name__ == "__main__":
    raise SystemExit(main())
