"""Gateway API behind the console's Schedules page.

Same code as `bitcadence schedule` (mco.schedules_plain). Handlers are plain
`def`, so FastAPI runs them in its threadpool: reading and writing the schedules
file never blocks the event loop, and no job board query is made at all.
"""
from fastapi import APIRouter, Depends, HTTPException

from mco import schedules_plain as sp
from mco.orchestrator.auth import require_scopes

schedules_router = APIRouter(prefix="/api/schedules")


def _picked(payload: dict) -> dict:
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="Pick how often and what time.")
    days = payload.get("days") or []
    if not isinstance(days, list) or not all(isinstance(d, int) for d in days):
        raise HTTPException(status_code=400, detail="Pick the days from the list.")
    try:
        return sp.plan(str(payload.get("frequency") or ""), str(payload.get("time") or ""), days)
    except sp.ScheduleWordsError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@schedules_router.get("")
def list_schedules(caller: dict = Depends(require_scopes("jobs:read"))):
    """Every schedule as a sentence, plus what can be scheduled."""
    data = sp.list_schedules()
    data["frequencies"] = [{"id": f, "label": sp.FREQUENCY_LABELS[f]} for f in sp.FREQUENCIES]
    data["days"] = [{"id": i, "label": n} for i, n in enumerate(sp.DAY_NAMES)]
    return data


@schedules_router.post("/preview")
def preview(payload: dict, caller: dict = Depends(require_scopes("jobs:read"))):
    """The sentence the person will see before they save. Changes nothing."""
    picked = _picked(payload)
    # Older clients only request the sentence. Explicit-zone clients also get
    # the occurrence preview; keep the original response contract for others.
    if "timezone" not in payload:
        return {"words": picked["words"]}
    try:
        return {"words": picked["words"], **sp.preview_schedule(picked["cron"], payload.get("timezone"))}
    except sp.ScheduleWordsError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@schedules_router.post("")
def add(payload: dict, caller: dict = Depends(require_scopes("agents:manage"))):
    """Save a new schedule by the same path as `bitcadence schedule add`."""
    picked = _picked(payload)
    try:
        saved = sp.add_schedule(str(payload.get("what") or ""), picked["cron"],
                                timezone_name=payload.get("timezone"))
    except sp.ScheduleWordsError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"success": True, **saved}


@schedules_router.post("/{schedule_id}/enabled")
def set_enabled(schedule_id: str, payload: dict, caller: dict = Depends(require_scopes("agents:manage"))):
    if not isinstance(payload, dict) or not isinstance(payload.get("on"), bool):
        raise HTTPException(status_code=400, detail="Say whether it should be on or off.")
    try:
        sp.set_enabled(schedule_id, payload["on"])
    except sp.ScheduleWordsError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"success": True, "on": payload["on"]}
