"""
Prometheus metrics at /metrics - an orchestrator you can't observe can't be
operated.

Exposition is built by hand (no prometheus_client dependency) so the gateway
stays stdlib-light, matching the rest of the project. One scrape reflects the
live job board, agent fleet, and governance state.

Auth: open like /healthz when MCO_METRICS_TOKEN is unset (the gateway binds to
localhost by default). Set MCO_METRICS_TOKEN to require `Authorization: Bearer
<token>` - use it whenever the gateway is network-exposed.
"""

import hmac
import logging

from fastapi import APIRouter, Header, HTTPException, Response

from mco.config import get_config

logger = logging.getLogger("mco.orchestrator.metrics")
metrics_router = APIRouter()

# Every job status we always emit, so a gauge never silently disappears from a
# dashboard just because the count hit zero.
_JOB_STATUSES = (
    "pending", "waiting", "needs_approval", "in_progress",
    "leased", "completed", "failed", "rejected",
)


def _line(name: str, value, labels: dict = None) -> str:
    if labels:
        lbl = ",".join(f'{k}="{v}"' for k, v in labels.items())
        return f"{name}{{{lbl}}} {value}"
    return f"{name} {value}"


def render_metrics() -> str:
    """Build the Prometheus text exposition for the current gateway state."""
    from mco.editions import current_edition
    from mco.orchestrator.routes import (
        decorate_presence,
        get_db_client,
        get_offline_after_seconds,
        kill_switch_active,
    )

    try:
        from mco.cli import get_version
        version = get_version()
    except Exception:
        version = "unknown"

    out = []

    def metric(name, help_text, mtype, samples):
        out.append(f"# HELP {name} {help_text}")
        out.append(f"# TYPE {name} {mtype}")
        out.extend(samples)

    metric("mco_up", "Gateway liveness (always 1 when scraped).", "gauge",
           [_line("mco_up", 1)])
    metric("mco_build_info", "Build info; value is always 1.", "gauge",
           [_line("mco_build_info", 1, {"version": version, "edition": current_edition()})])
    metric("mco_kill_switch", "1 when the global kill switch is active.", "gauge",
           [_line("mco_kill_switch", 1 if kill_switch_active() else 0)])

    db = get_db_client()
    if db is None:
        metric("mco_database_up", "1 when a data-plane backend is configured.", "gauge",
               [_line("mco_database_up", 0)])
        return "\n".join(out) + "\n"
    metric("mco_database_up", "1 when a data-plane backend is configured.", "gauge",
           [_line("mco_database_up", 1)])

    # Jobs by status (counts every org; metrics are an operator-wide view).
    status_counts = {s: 0 for s in _JOB_STATUSES}
    try:
        jobs = db.table("agent_jobs").select("*").execute().data or []
        for j in jobs:
            st = j.get("status") or "unknown"
            status_counts[st] = status_counts.get(st, 0) + 1
    except Exception as e:
        logger.debug(f"metrics: job scan failed: {e}")
        jobs = []
    metric("mco_jobs", "Jobs on the board by status.", "gauge",
           [_line("mco_jobs", c, {"status": s}) for s, c in sorted(status_counts.items())])
    metric("mco_approval_queue_depth", "Jobs paused awaiting human approval.", "gauge",
           [_line("mco_approval_queue_depth", status_counts.get("needs_approval", 0))])

    # Agent fleet presence (derived, same rule as the dashboard).
    try:
        agents = db.table("agent_registry").select("*").execute().data or []
        threshold = get_offline_after_seconds()
        online = sum(1 for a in agents
                     if decorate_presence(dict(a), threshold)["effective_status"] == "online")
    except Exception as e:
        logger.debug(f"metrics: agent scan failed: {e}")
        agents, online = [], 0
    metric("mco_agents_registered", "Total registered agents.", "gauge",
           [_line("mco_agents_registered", len(agents))])
    metric("mco_agents_online", "Agents heard from within the presence threshold.", "gauge",
           [_line("mco_agents_online", online)])

    # TypeSafe Jev decision metrics (additive, non-blocking)
    try:
        from mco.orchestrator.jev import get_jev_metrics
        jm = get_jev_metrics()
        metric("mco_jev_calls_total", "Total Jev semantic decision calls.", "counter",
               [_line("mco_jev_calls_total", jm["calls"])])
        metric("mco_jev_latency_ms_total", "Total latency of Jev calls in milliseconds.", "counter",
               [_line("mco_jev_latency_ms_total", jm["latency_ms_total"])])
        metric("mco_jev_low_confidence_total", "Total Jev low-confidence or abstention decisions.", "counter",
               [_line("mco_jev_low_confidence_total", jm["low_confidence"])])
        metric("mco_jev_disagreements_total", "Total disagreements between Jev recommendation and deterministic route.", "counter",
               [_line("mco_jev_disagreements_total", jm["disagreements"])])
        metric("mco_jev_fallbacks_total", "Total Jev fallback decisions.", "counter",
               [_line("mco_jev_fallbacks_total", jm["fallbacks"])])
        metric("mco_jev_errors_total", "Total Jev error responses.", "counter",
               [_line("mco_jev_errors_total", jm["errors"])])
    except Exception as e:
        logger.debug(f"metrics: jev metrics export failed: {e}")

    return "\n".join(out) + "\n"



@metrics_router.get("/metrics", include_in_schema=False)
async def metrics(authorization: str = Header(default="")):
    """Prometheus scrape endpoint (text exposition format)."""
    token = (get_config().get("MCO_METRICS_TOKEN") or "").strip()
    if token:
        from mco.orchestrator.auth import extract_bearer
        if not hmac.compare_digest(extract_bearer(authorization) or "", token):
            raise HTTPException(status_code=401, detail="Invalid or missing metrics token")
    body = render_metrics()
    return Response(content=body, media_type="text/plain; version=0.0.4; charset=utf-8")
