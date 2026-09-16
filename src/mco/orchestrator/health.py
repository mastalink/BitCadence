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
    from mco.orchestrator import score_sweep

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
    tasks = [asyncio.create_task(maintain()), asyncio.create_task(delivery_loop())]
    # The conductor sweep is the only optional task here: with
    # MCO_SCORE_SWEEP_SECONDS unset no task is created and no score database is
    # opened, so upgrading a gateway cannot start it driving score runs.
    interval = score_sweep.get_sweep_seconds()
    app.state.score_sweep_seconds = interval
    app.state.score_sweep_started = time.monotonic()
    app.state.score_sweep_last_ok = None
    app.state.score_sweep_error = None
    app.state.score_sweep_failing_runs = []
    sweep_task = None
    sweep_stop = asyncio.Event()
    if interval > 0:
        logger.info("Conductor sweep enabled: advancing score runs every %ss", interval)
        sweep_task = asyncio.create_task(score_sweep_loop(app, interval, sweep_stop))
    try:
        yield
    finally:
        # The sweep is stopped by asking, not by cancelling, and it is stopped
        # first. A tick runs in a worker thread via asyncio.to_thread, which
        # cannot be cancelled: cancelling the await returns at once while the
        # thread is still inside board.create, and shutdown would then return
        # having left a dispatch row durably 'sending' with no job on the board
        # for it - a restart's problem, invented by shutdown. Setting the flag
        # lets the tick in flight commit; awaiting the task is what makes
        # shutdown wait for it. Bounded, so a board that never answers cannot
        # hold the gateway open, and none of it blocks the event loop.
        if sweep_task is not None:
            sweep_stop.set()
            await asyncio.wait({sweep_task}, timeout=SCORE_SWEEP_DRAIN_SECONDS)
            if sweep_task.done():
                exc = sweep_task.exception()
                if exc is not None:
                    logger.warning("Conductor sweep stopped with %s", type(exc).__name__)
            else:
                logger.warning("Conductor sweep did not drain within %ss; cancelling it",
                               SCORE_SWEEP_DRAIN_SECONDS)
                tasks.append(sweep_task)
        for task in tasks:
            task.cancel()
        for task in tasks:
            try:
                await task
            except asyncio.CancelledError:
                pass


DELIVERY_SWEEP_SECONDS = 60
# How long shutdown waits for a tick already in flight to commit before it gives
# up and cancels. Long enough for a board call to finish, short enough that an
# unresponsive board cannot stop the gateway from exiting.
SCORE_SWEEP_DRAIN_SECONDS = 30


async def delivery_once():
    """One delivery-watchdog sweep: store work in a thread, sends on the loop."""
    from mco.orchestrator import delivery, routes
    db = routes.get_db_client()
    if db is None:
        return None
    result = await asyncio.to_thread(delivery.sweep, db)
    callback = routes._broadcast_callback
    if callback is not None:
        for event_name, job in result.broadcasts:
            try:
                await callback(event_name, job)
            except Exception as exc:
                logger.warning(f"Delivery broadcast failed for {job.get('id')}: {type(exc).__name__}")
    await asyncio.to_thread(delivery.send_notifications, result)
    if result.rekicked or result.rerouted or result.escalated:
        logger.info("Delivery sweep: rekicked=%s rerouted=%s escalated=%s",
                    result.rekicked, result.rerouted, result.escalated)
    return result


async def delivery_loop():
    while True:
        await asyncio.sleep(DELIVERY_SWEEP_SECONDS)
        try:
            await delivery_once()
        except Exception:
            logger.exception("Delivery sweep failed")


async def score_sweep_once(conductor):
    """One conductor sweep. Every tick is SQLite plus board HTTP, so it runs in a
    thread - never inline on the event loop."""
    from mco.orchestrator import score_sweep
    return await asyncio.to_thread(score_sweep.sweep, conductor)


async def score_sweep_loop(app, interval, stop=None):
    """Advance score runs on a timer, the way delivery_loop retries delivery.

    Two kinds of failure, kept apart on purpose. A failure of the sweep *itself*
    (no credential, an unreadable database) clears the cached conductor, sets
    ``score_sweep_error`` and makes /readyz say not ready - the gateway is
    advancing nothing. A failure of one *run* is recorded in
    ``score_sweep_failing_runs`` and logged, but leaves the sweep healthy,
    because the other runs did advance.

    ``stop`` is how shutdown ends this loop without stranding a tick. Cancelling
    would return while the worker thread was still mid ``board.create``; setting
    the flag instead lets the tick in flight finish and be recorded, and the loop
    returns at the next opportunity. ``asyncio.CancelledError`` is a
    BaseException and so still travels through the ``except Exception`` below
    untouched, for the bounded case where a drain has to be given up on.

    The named failing runs come from ``result.failing``, which the sweep reads
    out of the runs table rather than out of what this pass happened to touch: a
    run that a sweep blocks is terminal, so the next pass cannot see it, and
    readiness that forgot it would go silent precisely when the failure became
    permanent.
    """
    from mco.orchestrator import score_sweep
    conductor = None
    while True:
        try:
            if conductor is None:
                conductor = await asyncio.to_thread(score_sweep.open_conductor)
            result = await score_sweep_once(conductor)
            app.state.score_sweep_last_ok = time.monotonic()
            app.state.score_sweep_error = None
            app.state.score_sweep_failing_runs = sorted(result.failing)
            if not result.quiet:
                logger.info("Conductor sweep: %s", result.describe())
        except Exception as exc:
            conductor = None
            app.state.score_sweep_error = type(exc).__name__
            logger.exception("Conductor sweep failed")
        # Checked here, not only inside the sleep: a stop asked for while the
        # tick above was in flight has already been honoured by waiting for it,
        # and returning now is what lets shutdown finish.
        if stop is not None and stop.is_set():
            return
        if await _sleep_until(interval, stop):
            return


async def _sleep_until(interval, stop) -> bool:
    """Wait out the interval; return True as soon as a stop has been asked for."""
    if stop is None:
        await asyncio.sleep(interval)
        return False
    try:
        await asyncio.wait_for(stop.wait(), timeout=interval)
        return True
    except (asyncio.TimeoutError, TimeoutError):
        return False


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
    interval = getattr(request.app.state, "score_sweep_seconds", 0) or 0
    if interval > 0:
        # Measured from startup, not from the first success: a sweep that never
        # completes one pass must go stale rather than stay silently "ok".
        swept = getattr(request.app.state, "score_sweep_last_ok", None)
        since = swept if swept is not None else getattr(request.app.state, "score_sweep_started", None)
        error = getattr(request.app.state, "score_sweep_error", None)
        checks["score_sweep"] = {
            "ok": error is None and since is not None and time.monotonic() - since < 3 * interval + 30,
            "error": error,
            "interval_seconds": interval,
            # Runs that are stuck right now, read from the conductor database
            # on every pass rather than remembered, so a run stays named for as
            # long as it stays stuck. Visible, but not a reason to call the
            # whole gateway unready: the rest of the sweep still ran.
            "failing_runs": list(getattr(request.app.state, "score_sweep_failing_runs", [])),
        }
    else:
        checks["score_sweep"] = {"ok": True, "configured": False}
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
        from mco.orchestrator.presence import BROKEN, describe_fleet
        rows = [row for row in (db.table("agent_registry").select("*").execute().data or [])
                if row.get("role") not in {"admin", "human", "operator"}]
        described = describe_fleet(db, rows, threshold=get_offline_after_seconds())
        online = sum(1 for row in described if row.get("status") == "online")
        broken = sorted(row["instance_id"] for row in described if row.get("state") == BROKEN)
        checks["fleet"] = {"online_workers": online, "broken_workers": broken, "degraded": online == 0}
    except Exception:
        checks["fleet"] = {"degraded": True, "error": "presence unavailable"}
    ready = all(c.get("ok", True) for c in checks.values())
    return JSONResponse({"status": ("degraded" if checks["fleet"]["degraded"] else "ready") if ready else "not_ready",
                         "checks": checks}, status_code=200 if ready else 503)
