"""FastAPI routes for the Drumline Agent Exchange (/api/exchanges).

Discussion only: nothing here is an approval, a grant, or prompt context.
There is deliberately no update or delete endpoint.
"""

import inspect
import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Response

from mco.orchestrator import agent_exchange as ax
from mco.orchestrator.auth import require_scopes

logger = logging.getLogger("mco.orchestrator.exchanges")


def _enabled():
    if not ax.is_enabled():
        raise HTTPException(status_code=503, detail="Agent Exchange is not enabled on this gateway")


exchange_router = APIRouter(prefix="/api/exchanges", dependencies=[Depends(_enabled)])


def _db():
    from mco.orchestrator.routes import get_db_client
    db_client = get_db_client()
    if not db_client:
        raise HTTPException(status_code=400, detail="Database not configured")
    return db_client


def _fail(exc: ax.ExchangeError) -> HTTPException:
    return HTTPException(status_code=exc.status, detail=exc.detail)


@exchange_router.post("")
async def create_exchange(
    payload: dict,
    response: Response,
    agent: dict = Depends(require_scopes("context:write")),
):
    """Append one exchange. Author, org and time come from the credential."""
    try:
        row, created = ax.append_exchange(_db(), agent, payload)
    except ax.ExchangeError as exc:
        raise _fail(exc)
    response.status_code = 201 if created else 200
    if created:
        publisher = ax.get_publisher()
        if publisher:
            try:
                result = publisher(ax.event_for(row))
                if inspect.isawaitable(result):
                    await result
            except Exception:
                # Events are hints; the committed row is authoritative.
                logger.warning("exchange.created publish failed", exc_info=True)
    return {"success": True, "created": created, "exchange": row}


@exchange_router.get("")
async def list_exchanges(
    job_id: Optional[str] = None,
    workflow_name: Optional[str] = None,
    workflow_run: Optional[str] = None,
    workflow_step: Optional[str] = None,
    thread_id: Optional[str] = None,
    kind: Optional[str] = None,
    limit: int = ax.DEFAULT_PAGE,
    cursor: Optional[str] = None,
    agent: dict = Depends(require_scopes("context:read")),
):
    try:
        page = ax.list_exchanges(
            _db(), agent, job_id=job_id, workflow_name=workflow_name,
            workflow_run=workflow_run, workflow_step=workflow_step,
            thread_id=thread_id, kind=kind, limit=limit, cursor=cursor,
        )
    except ax.ExchangeError as exc:
        raise _fail(exc)
    return {**page, "authoritative": False}


@exchange_router.get("/{exchange_id}")
async def get_exchange(exchange_id: str, agent: dict = Depends(require_scopes("context:read"))):
    try:
        return ax.get_exchange(_db(), agent, exchange_id)
    except ax.ExchangeError as exc:
        raise _fail(exc)


@exchange_router.post("/{exchange_id}/promotions")
async def promote_exchange(
    exchange_id: str,
    payload: dict,
    response: Response,
    agent: dict = Depends(require_scopes("context:promote")),
):
    """Explicitly promote a decision/handoff into canonical Drumline context."""
    try:
        receipt, created = ax.promote(_db(), agent, exchange_id, payload)
    except ax.ExchangeError as exc:
        raise _fail(exc)
    response.status_code = 201 if created else 200
    return {"success": True, "created": created, "promotion": receipt}
