"""Liveness, readiness, and timer-driven maintenance independent of workers."""
import asyncio
import logging
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Request
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)


def maintenance_once():
    from mco.orchestrator.routes import get_db_client, reclaim_stale_leases, kill_switch_active
    from mco.orchestrator.audit import drain_outbox
    from mco.orchestrator.leases import set_paused
    db = get_db_client()
    if db is None:
        raise RuntimeError("Database unavailable")
    if kill_switch_active():
        set_paused(db, True)
    reclaimed = reclaim_stale_leases(db)
    drained = drain_outbox(db)
    return {"reclaimed": reclaimed, "evidence_drained": drained}


@asynccontextmanager
async def lifespan(app):
    app.state.maintenance_last_ok = None
    app.state.maintenance_error = None
    async def maintain():
        while True:
            try:
                await asyncio.to_thread(maintenance_once)
                app.state.maintenance_last_ok = time.monotonic()
                app.state.maintenance_error = None
            except Exception as exc:
                app.state.maintenance_error = type(exc).__name__
                logger.exception("Gateway maintenance failed")
            await asyncio.sleep(5)
    task = asyncio.create_task(maintain())
    try:
        yield
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


async def readyz(request: Request):
    from mco.orchestrator.routes import get_db_client, decorate_presence, get_offline_after_seconds
    from mco.config import get_config
    checks = {}
    try:
        db = get_db_client()
        db.table("agent_jobs").select("id").limit(1).execute()
        checks["store"] = {"ok": True}
    except Exception as exc:
        return JSONResponse({"status": "not_ready", "checks": {
            "store": {"ok": False, "error": type(exc).__name__}}}, status_code=503)
    last = getattr(request.app.state, "maintenance_last_ok", None)
    if hasattr(request.app.state, "maintenance_last_ok"):
        checks["maintenance"] = {"ok": last is not None and time.monotonic() - last < 30,
                                  "error": request.app.state.maintenance_error}
    heartbeat = get_config().get("MCO_SCHEDULER_HEARTBEAT_FILE")
    if heartbeat:
        try:
            age = max(0, time.time() - Path(heartbeat).stat().st_mtime)
            checks["scheduler"] = {"ok": age < 120, "age_seconds": round(age, 1)}
        except OSError:
            checks["scheduler"] = {"ok": False, "error": "heartbeat unavailable"}
    else:
        checks["scheduler"] = {"ok": True, "configured": False}
    try:
        rows = db.table("agent_registry").select("*").execute().data or []
        online = sum(1 for row in rows if row.get("role") not in {"admin", "human", "operator"}
                     and decorate_presence(dict(row), get_offline_after_seconds()).get("status") == "online")
        checks["fleet"] = {"online_workers": online, "degraded": online == 0}
    except Exception:
        checks["fleet"] = {"degraded": True, "error": "presence unavailable"}
    ready = all(c.get("ok", True) for c in checks.values())
    return JSONResponse({"status": ("degraded" if checks["fleet"]["degraded"] else "ready") if ready else "not_ready",
                         "checks": checks}, status_code=200 if ready else 503)
