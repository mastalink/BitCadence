"""Portfolio page API (read-only). Auth: same bearer/session scopes as the other console pages."""
from pathlib import Path

from fastapi import APIRouter, Depends
from fastapi.responses import HTMLResponse

from mco import portfolio
from mco.orchestrator.auth import require_scopes

portfolio_router = APIRouter(prefix="/api/portfolio")
page_router = APIRouter()
PORTFOLIO_HTML_PATH = Path(__file__).resolve().parents[1] / "static" / "portfolio.html"


def _jobs() -> list:
    from mco.orchestrator.routes import get_db_client

    db = get_db_client()
    if not db:
        return []
    try:
        return db.table("agent_jobs").select("*").order("created_at", desc=True).limit(300).execute().data or []
    except Exception:  # noqa: BLE001 - the page still renders without board data
        return []


def _snapshot(caller: dict) -> dict:
    from mco.orchestrator.routes import get_agents

    try:
        agents = get_agents(caller)
    except Exception:  # noqa: BLE001
        agents = []
    return portfolio.build_portfolio(portfolio.load_config(), _jobs(), agents)


# Plain `def` handlers: file reads, nvidia-smi and gh run in the threadpool.
@portfolio_router.get("")
def get_portfolio(caller: dict = Depends(require_scopes("jobs:read"))):
    return _snapshot(caller)


@portfolio_router.get("/flags")
def get_flags(caller: dict = Depends(require_scopes("jobs:read"))):
    snap = _snapshot(caller)
    return {"generated_at": snap["generated_at"], "flags": snap["flags"],
            "red": sum(1 for f in snap["flags"] if f["severity"] == "red")}


@page_router.get("/portfolio", response_class=HTMLResponse, include_in_schema=False)
async def portfolio_ui() -> str:
    return PORTFOLIO_HTML_PATH.read_text(encoding="utf-8")
