"""Authenticated read-only routes for the local portfolio projection."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import os
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException

from mco.orchestrator.auth import require_scopes
from mco.orchestrator.portfolio import (
    PortfolioConfigError,
    PortfolioConfigResult,
    build_portfolio_snapshot,
    load_portfolio_config,
)
from mco.orchestrator.routes import agent_org, get_db_client


portfolio_router = APIRouter(prefix="/api/portfolio", tags=["portfolio"])


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _selected_path() -> Path | None:
    selected = os.environ.get("MCO_PORTFOLIO_CONFIG")
    return Path(selected).expanduser() if selected else None


def _config_result() -> PortfolioConfigResult:
    try:
        return load_portfolio_config(_selected_path())
    except PortfolioConfigError:
        return PortfolioConfigResult(
            configured=False,
            config={},
            source="explicit" if os.environ.get("MCO_PORTFOLIO_CONFIG") else "default",
            warning="Portfolio configuration is invalid.",
        )


def _rows(db, table: str, org_id: str) -> list[dict]:
    if db is None:
        raise HTTPException(status_code=503, detail="Portfolio board data is unavailable.")
    try:
        return db.table(table).select("*").eq("org_id", org_id).execute().data or []
    except Exception as exc:
        raise HTTPException(
            status_code=503, detail="Portfolio board data is unavailable."
        ) from exc


def _snapshot(caller: dict) -> dict:
    db = get_db_client()
    org_id = agent_org(caller)
    jobs = _rows(db, "agent_jobs", org_id)
    jobs = [job for job in jobs if not job.get("archived")]
    agents = _rows(db, "agent_registry", org_id)

    if db is not None and jobs:
        try:
            assignments = db.table("job_project_assignments").select("*").eq(
                "org_id", org_id
            ).execute().data or []
            by_job = {row.get("job_id"): row for row in assignments}
            jobs = [
                {
                    **job,
                    "project": {
                        "id": by_job[job["id"]].get("project_id"),
                        "name": by_job[job["id"]].get("project_name"),
                    },
                }
                if job.get("id") in by_job
                else job
                for job in jobs
            ]
        except Exception:
            pass

    events = []
    job_ids = [job.get("id") for job in jobs if job.get("id")]
    if db is not None and job_ids:
        try:
            cutoff = (_now() - timedelta(hours=24)).isoformat()
            events = (
                db.table("agent_job_events")
                .select("*")
                .eq("org_id", org_id)
                .eq("event", "chain_stalled")
                .gt("created_at", cutoff)
                .in_("job_id", job_ids)
                .order("created_at", desc=True)
                .limit(100)
                .execute()
                .data
                or []
            )
        except Exception:
            events = []

    return build_portfolio_snapshot(
        _config_result(), jobs=jobs, agents=agents, events=events, now=_now()
    )


@portfolio_router.get("")
def get_portfolio(caller: dict = Depends(require_scopes("jobs:read"))):
    return _snapshot(caller)


@portfolio_router.get("/flags")
def get_portfolio_flags(caller: dict = Depends(require_scopes("jobs:read"))):
    snapshot = _snapshot(caller)
    return {"flags": snapshot["flags"], "generated_at": snapshot["generated_at"]}
