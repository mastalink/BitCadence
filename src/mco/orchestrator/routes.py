"""FastAPI routes for the Job Board API, serving GET and POST requests."""

import os
import logging
import importlib.metadata as importlib_metadata
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from fastapi import APIRouter, HTTPException, Depends
from mco.orchestrator.contracts import (
    ARCHIVABLE_STATUSES,
    CANCELLABLE_STATUSES,
    JobStatus,
    REASSIGNABLE_STATUSES,
)
from mco.orchestrator.auth import require_agent, require_scopes
from mco.orchestrator.audit import record_event, get_events
from mco.config import get_config
from mco.notifiers.ntfy import (
    notify_job_created,
    notify_job_completed,
    notify_job_failed,
    notify_job_leased,
    notify_job_needs_approval,
)

from mco.orchestrator import utils as utils_mod
# Re-exported for back-compat: these used to live here (now in utils to
# break the routes <-> auth import cycle).
from mco.orchestrator.utils import DEFAULT_APPROVER_ROLES, get_approver_roles  # noqa: F401


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def agent_org(agent: dict) -> str:
    """Tenant boundary for the calling agent ('default' on single-tenant installs)."""
    return agent.get("org_id") or "default"


def job_org(job: dict) -> str:
    return job.get("org_id") or "default"


def get_gated_roles() -> set:
    """Roles whose jobs are force-gated on human approval (MCO_POLICY_GATED_ROLES).

    Guardrail for high-blast-radius targets: set it to your connector roles
    (e.g. 'servicenow,dynatrace') and no agent can write to those platforms
    without a human approving first - regardless of what the sender asked for.
    """
    raw = get_config().get("MCO_POLICY_GATED_ROLES") or ""
    return {r.strip().lower() for r in raw.split(",") if r.strip()}


def kill_switch_active() -> bool:
    """Global pause (MCO_KILL_SWITCH): no new jobs created, no leases granted.

    In-flight work may finish and report; humans can still approve/audit.
    """
    return str(get_config().get("MCO_KILL_SWITCH") or "").lower() in ("1", "true", "on", "yes")


def get_offline_after_seconds() -> int:
    """Health-check threshold: an 'online' agent that hasn't been heard from
    in this many seconds is reported offline (MCO_AGENT_OFFLINE_AFTER)."""
    try:
        return max(30, int(get_config().get("MCO_AGENT_OFFLINE_AFTER") or 300))
    except (TypeError, ValueError):
        return 300


def get_lease_ttl_seconds() -> int:
    """How long a job may sit LEASED before it's considered abandoned and
    reclaimed to PENDING (MCO_LEASE_TTL_SECONDS). 0 (or negative) disables
    reclamation entirely. Default 900s / 15m."""
    try:
        return int(get_config().get("MCO_LEASE_TTL_SECONDS") or 900)
    except (TypeError, ValueError):
        return 900


def reclaim_stale_leases(db_client) -> int:
    """Revert LEASED jobs whose lease has outlived the TTL back to PENDING so
    another worker can pick them up.

    Without this, a worker that crashes / is Ctrl-C'd / loses the network
    mid-job leaves that job LEASED forever - permanent starvation (audit
    finding F-01). Matching the presence design (no standalone background
    sweeper), this runs opportunistically on the pending-poll path: the next
    time ANY worker polls, stale leases are recovered. Best-effort and never
    raises - reclamation must never break the job path. Returns the count
    reclaimed."""
    ttl = get_lease_ttl_seconds()
    if ttl <= 0:
        return 0
    try:
        from datetime import datetime, timezone
        res = db_client.table("agent_jobs").select("*").eq("status", "leased").execute()
        now = datetime.now(timezone.utc)
        reclaimed = 0
        for job in (res.data or []):
            started = job.get("started_at")
            if not started:
                continue
            try:
                ts = datetime.fromisoformat(str(started).replace("Z", "+00:00"))
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=timezone.utc)
            except (ValueError, TypeError):
                continue
            if (now - ts).total_seconds() <= ttl:
                continue
            job_id = job.get("id")
            db_client.table("agent_jobs").update({
                "status": "pending",
                "leased_by_instance_id": None,
                "started_at": None,
            }).eq("id", job_id).execute()
            record_event(db_client, job_id, "lease_expired", "system", "reaper",
                         {"lease_ttl_seconds": ttl, "leased_by": job.get("leased_by_instance_id")})
            reclaimed += 1
        if reclaimed:
            logger.info(f"Reclaimed {reclaimed} stale lease(s) past the {ttl}s TTL back to pending.")
        return reclaimed
    except Exception as e:
        logger.debug(f"Stale-lease reclamation skipped: {type(e).__name__}")
        return 0


def decorate_presence(row: dict, threshold: int) -> dict:
    """Add derived liveness to a registry row.

    - last_seen_seconds: age of the last heartbeat (None if never seen)
    - effective_status: the stored status, demoted to 'offline' when an
      'online' agent has been silent past the threshold.

    The **response** `status` is overwritten with the derived value too, so a
    consumer that reads `status` cannot get a stale 'online'. This is the whole
    fix for "the board says online but the agent died days ago": the stored
    status flips to 'online' on the last heartbeat and never flips back on its
    own, so any surface reading the raw field (the MCP `mco_agents` tool other
    agents call, the console, ad-hoc API clients) reported a corpse as live.
    `effective_status` had the truth, but nothing forced consumers onto it.

    `row` is a per-read dict copy, so this changes only the response, never the
    stored row - the design's "derive liveness at read time, no background
    sweeper" property is preserved. Demotion only ever turns a stale 'online'
    into 'offline'; 'disabled' and never-seen states pass through untouched.
    """
    from datetime import datetime, timezone

    secs = None
    last = row.get("last_seen_at")
    if last:
        try:
            ts = datetime.fromisoformat(str(last).replace("Z", "+00:00"))
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            secs = max(0, int((datetime.now(timezone.utc) - ts).total_seconds()))
        except (ValueError, TypeError):
            pass
    effective = row.get("status") or "offline"
    if effective == "online" and secs is not None and secs > threshold:
        effective = "offline"
    row["last_seen_seconds"] = secs
    row["effective_status"] = effective
    # Collapse the footgun: the response's status IS the derived truth.
    row["status"] = effective
    return row


def touch_agent_presence(db_client, agent: dict) -> None:
    """Heartbeat: polling IS the liveness signal for HTTP/MCP workers.

    Stamps last_seen_at (and flips to online) for the calling agent.
    Best-effort - presence must never break the job path."""
    instance_id = agent.get("instance_id")
    if not instance_id or instance_id == "local" or agent.get("status") == "disabled":
        return
    try:
        from datetime import datetime, timezone
        db_client.table("agent_registry").update({
            "status": "online",
            "last_seen_at": datetime.now(timezone.utc).isoformat(),
        }).eq("instance_id", instance_id).execute()
    except Exception as e:
        logger.debug(f"Presence heartbeat skipped for {instance_id}: {e}")

logger = logging.getLogger("mco.orchestrator.routes")
router = APIRouter(prefix="/api/jobs")
agents_router = APIRouter(prefix="/api/agents")
events_router = APIRouter(prefix="/api/events")
version_router = APIRouter(prefix="/api")

# Dynamic callback hook for gateway websocket notifications
# Callable signature: async def callback(event: str, job: dict)
_broadcast_callback = None

def register_broadcast_callback(callback) -> None:
    """Register a custom callback function to broadcast gateway events."""
    global _broadcast_callback
    _broadcast_callback = callback
    logger.info("Registered custom Job Board broadcast callback.")


# Memoized Supabase client. create_client() is expensive (~3-4s: spins up
# PostgREST/Auth/Realtime sub-clients), so building it per request made every
# endpoint slow enough to trip client timeouts. Build once, reuse.
_db_client = None


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _package_version() -> str:
    try:
        return importlib_metadata.version("batoncadence")
    except importlib_metadata.PackageNotFoundError:
        pass

    pyproject = _repo_root() / "pyproject.toml"
    text = pyproject.read_text(encoding="utf-8")
    match = re.search(r'(?m)^version\s*=\s*"([^"]+)"', text)
    if not match:
        raise RuntimeError("project version not found in pyproject.toml")
    return match.group(1)


def _git_commit() -> str | None:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=str(_repo_root()),
            text=True,
            stderr=subprocess.DEVNULL,
            timeout=1,
        ).strip() or None
    except Exception:
        return None


@version_router.get("/version")
async def get_version():
    return {"version": _package_version(), "git_commit": _git_commit()}


def get_db_client(force_new: bool = False):
    """Return the cached data-plane client (created on first use).

    Supabase when credentials are configured; otherwise BatonCadence's
    embedded LocalStore (SQLite) so the Local-Only profile gets real
    persistence - jobs, audit trail, agent registry, and Drumline shared
    context all work with zero cloud dependencies. Set MCO_DISABLE_LOCAL_DB
    to opt out of the embedded fallback (returns None, as before).
    """
    global _db_client
    if _db_client is not None and not force_new:
        return _db_client
    config = get_config()
    url = config.get("SUPABASE_URL")
    key = config.get("SUPABASE_KEY")
    if url and key and url != "encrypted_in_secret_store":
        from supabase import create_client
        _db_client = create_client(url, key)
        return _db_client

    if str(config.get("MCO_DISABLE_LOCAL_DB") or "").lower() in ("1", "true", "on", "yes"):
        return None

    from mco.localstore import get_local_store, seed_local_operator
    store = get_local_store()
    local_token = (config.get("MCO_LOCAL_TOKEN") or "").strip()
    if local_token:
        try:
            seed_local_operator(store, local_token)
        except Exception as e:
            logger.warning(f"Could not seed local operator agent: {e}")
    _db_client = store
    return _db_client


@router.get("")
async def get_jobs(include_archived: bool = False, agent: dict = Depends(require_scopes("jobs:read"))):
    """Retrieve job list from the Supabase database.

    Archived jobs (soft-hidden terminal jobs - see POST /{job_id}/archive) are
    excluded by default so a long-running board doesn't drown in old Done/
    Problems rows; pass ?include_archived=true to see everything.
    """
    db_client = get_db_client()
    if not db_client:
        return []
    try:
        res = (
            db_client.table("agent_jobs").select("*")
            .eq("org_id", agent_org(agent))
            .order("created_at", desc=True).limit(100).execute()
        )
        jobs = res.data or []
        if not include_archived:
            jobs = [j for j in jobs if not j.get("archived")]
        return jobs
    except Exception as e:
        logger.error(f"Error fetching jobs: {e}")
        return []


@router.post("")
async def create_job(payload: dict, agent: dict = Depends(require_scopes("jobs:write"))):
    """Create a job. Any authenticated agent may send to any target ('drop mail')."""
    db_client = get_db_client()
    if not db_client:
        raise HTTPException(status_code=400, detail="Database not configured")
    if kill_switch_active():
        raise HTTPException(status_code=503, detail="MCO_KILL_SWITCH is active: job intake is paused")
    try:
        title = payload.get("title")
        description = payload.get("description")
        target_agent_role = payload.get("target_agent_role")
        target_agent_id = payload.get("target_agent_id")
        depends_on = payload.get("depends_on") or []
        input_payload = payload.get("input_payload") or {}
        requires_approval = bool(payload.get("requires_approval"))
        max_retries = int(payload.get("max_retries") or 0)
        escalate_to_role = payload.get("escalate_to_role")

        if not title or not target_agent_role:
            raise HTTPException(status_code=400, detail="title and target_agent_role are required")

        # Policy guardrail: jobs targeting gated roles ALWAYS pause for a human,
        # no matter what the sender requested.
        if target_agent_role.lower() in get_gated_roles():
            requires_approval = True

        from mco.orchestrator.handlers import _initial_status
        status = _initial_status(db_client, depends_on, requires_approval)

        data = {
            "title": title,
            "description": description,
            "source_agent_id": agent["instance_id"],
            "source_agent_role": agent["role"],
            "target_agent_role": target_agent_role,
            "target_agent_id": target_agent_id,
            "status": status,
            "depends_on": depends_on,
            "input_payload": input_payload,
        }
        # Tenant stamp (omitted for the default org so pre-migration DBs keep working).
        if agent_org(agent) != "default":
            data["org_id"] = agent_org(agent)
        # Governance columns are only sent when used (pre-migration DB compatibility).
        if requires_approval:
            data["requires_approval"] = True
        if max_retries is not None:
            data["max_retries"] = max_retries
        if escalate_to_role:
            data["escalate_to_role"] = escalate_to_role

        res = db_client.table("agent_jobs").insert(data).execute()
        if res.data:
            new_job = res.data[0]
            record_event(db_client, new_job.get("id"), "created",
                         agent["instance_id"], agent["role"],
                         {"status": status, "target_agent_role": target_agent_role})
            # Trigger registered broadcast callback first
            if _broadcast_callback:
                try:
                    if status == JobStatus.PENDING.value:
                        event_name = "job_pending"
                    elif status == JobStatus.NEEDS_APPROVAL.value:
                        event_name = "job_needs_approval"
                    else:
                        event_name = "job_created"
                    await _broadcast_callback(event_name, new_job)
                except Exception as e:
                    logger.warning(f"Error executing broadcast callback: {e}")
            # ntfy webhook addon (if enabled via NTFY_* env vars)
            try:
                if status == JobStatus.NEEDS_APPROVAL.value:
                    notify_job_needs_approval(
                        job_id=new_job.get("id", "unknown"),
                        title=new_job.get("title", "Untitled job"),
                        to_role=new_job.get("target_agent_role", "unknown"),
                    )
                else:
                    notify_job_created(
                        job_id=new_job.get("id", "unknown"),
                        title=new_job.get("title", "Untitled job"),
                        to_role=new_job.get("target_agent_role", "unknown"),
                    )
            except Exception as ntfy_err:
                logger.debug(f"ntfy addon skipped: {ntfy_err}")

            return {"success": True, "job": new_job}
        return {"success": False, "error": "Insert failed"}
    except Exception as e:
        logger.error(f"Error creating job: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/pending")
async def get_pending_jobs(role: str, instance_id: str = None, agent: dict = Depends(require_scopes("jobs:read"))):
    """Retrieve pending jobs for a role. Dropbox rule: you may only poll your own mail."""
    if role.lower() != agent["role"].lower():
        raise HTTPException(status_code=403, detail="Cannot poll jobs for a role you are not registered as")
    if instance_id and instance_id != agent["instance_id"]:
        raise HTTPException(status_code=403, detail="instance_id does not match the authenticated agent")
    db_client = get_db_client()
    if not db_client:
        return []
    touch_agent_presence(db_client, agent)
    # Recover jobs abandoned by crashed/killed workers (F-01) before we hand
    # this poller its work. Best-effort; never blocks the poll on failure.
    reclaim_stale_leases(db_client)
    try:
        res = db_client.table("agent_jobs")\
            .select("*")\
            .eq("status", "pending")\
            .eq("target_agent_role", role)\
            .execute()

        jobs = res.data or []
        filtered = []
        for job in jobs:
            if job_org(job) != agent_org(agent):
                continue
            target_id = job.get("target_agent_id")
            if target_id and target_id != instance_id:
                continue
            filtered.append(job)
        return filtered
    except Exception as e:
        logger.error(f"Error fetching pending jobs: {e}")
        return []


@router.post("/lease")
async def lease_job(payload: dict, agent: dict = Depends(require_scopes("jobs:write"))):
    """Atomically lease a job. Dropbox rule: you may only lease as yourself."""
    db_client = get_db_client()
    if not db_client:
        raise HTTPException(status_code=400, detail="Database not configured")
    if kill_switch_active():
        raise HTTPException(status_code=503, detail="MCO_KILL_SWITCH is active: leasing is paused")
    try:
        task_id = payload.get("task_id")
        agent_instance_id = payload.get("agent_instance_id")
        if not task_id or not agent_instance_id:
            raise HTTPException(status_code=400, detail="task_id and agent_instance_id are required")
        if agent_instance_id != agent["instance_id"]:
            raise HTTPException(status_code=403, detail="Cannot lease on behalf of another agent")

        # Tenant isolation: you can only lease jobs inside your own org.
        pre = db_client.table("agent_jobs").select("*").eq("id", task_id).execute()
        if pre.data and job_org(pre.data[0]) != agent_org(agent):
            raise HTTPException(status_code=404, detail="Job not found")

        # Dropbox rule: you may only lease mail addressed to you. This mirrors
        # the inbox filter (get_pending_jobs) exactly - a role match is required,
        # and an instance-targeted job is reserved for that instance - so an
        # agent can lease precisely what its inbox shows and nothing else. The
        # org check above stays first, so cross-org probes get 404 (not 403) and
        # never learn a job exists. Without this, any authenticated agent could
        # lease another agent's job (completion is still blocked downstream, so
        # the job would strand in leased-limbo and mis-attribute the audit trail).
        if pre.data:
            j = pre.data[0]
            target_role = (j.get("target_agent_role") or "").lower()
            target_id = j.get("target_agent_id")
            if target_role != (agent.get("role") or "").lower() or \
               (target_id and target_id != agent.get("instance_id")):
                raise HTTPException(
                    status_code=403,
                    detail="Cannot lease a job not addressed to you",
                )

        res = db_client.rpc("lease_task", {
            "p_agent_instance_id": agent_instance_id,
            "p_task_id": task_id
        }).execute()
        
        success = res.data if hasattr(res, "data") else False
        
        if success:
            record_event(db_client, task_id, "leased", agent_instance_id, agent["role"])
            try:
                notify_job_leased(task_id, agent_instance_id, agent["role"])
            except Exception as ntfy_err:
                logger.debug(f"ntfy lease hook skipped: {ntfy_err}")
        
        if success and _broadcast_callback:
            try:
                job_res = db_client.table("agent_jobs").select("*").eq("id", task_id).execute()
                if job_res.data:
                    await _broadcast_callback("job_leased", job_res.data[0])
            except Exception as e:
                logger.warning(f"Error executing broadcast callback after lease: {e}")
                
        return {"success": success}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error leasing job: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.put("/{job_id}")
async def update_job_status(job_id: str, payload: dict, agent: dict = Depends(require_scopes("jobs:write"))):
    """Update job status/results. Dropbox rule: you may only update mail addressed to you."""
    db_client = get_db_client()
    if not db_client:
        raise HTTPException(status_code=400, detail="Database not configured")
    try:
        # Authorization: the job must be addressed to the calling agent's role or instance.
        job_res = db_client.table("agent_jobs").select("*").eq("id", job_id).execute()
        if job_res.data:
            j = job_res.data[0]
            if job_org(j) != agent_org(agent):
                raise HTTPException(status_code=404, detail="Job not found")
            target_role = (j.get("target_agent_role") or "").lower()
            caller_role = (agent.get("role") or "").lower()
            target_id = j.get("target_agent_id")
            caller_id = agent.get("instance_id")
            if target_role != caller_role and target_id != caller_id:
                raise HTTPException(status_code=403, detail="Cannot update a job not addressed to you")

        status = payload.get("status")
        output_payload = payload.get("output_payload")
        error_message = payload.get("error_message")
        
        if not status:
            raise HTTPException(status_code=400, detail="status is required")
            
        from mco.orchestrator.handlers import handle_job_update
        
        updated_job = None
        error_occurred = None
        
        async def send_ack(ack_payload: dict):
            nonlocal updated_job
            updated_job = ack_payload.get("job")
            
        async def send_error(err_msg: str, correlation_id: str):
            nonlocal error_occurred
            error_occurred = err_msg
            
        async def broadcast_event(event_name: str, event_job: dict):
            if _broadcast_callback:
                await _broadcast_callback(event_name, event_job)
                
        await handle_job_update(
            db_client=db_client,
            payload={
                "task_id": job_id,
                "status": status,
                "output_payload": output_payload,
                "error_message": error_message
            },
            correlation_id="http_update",
            send_error=send_error,
            send_ack=send_ack,
            broadcast_event=broadcast_event,
            actor=agent,
        )
        
        if error_occurred:
            raise HTTPException(status_code=400, detail=error_occurred)

        # NTFY addon hooks for completion/failure
        try:
            final_status = (updated_job or {}).get("status", status)
            role = (updated_job or {}).get("target_agent_role") or "unknown"
            jid = (updated_job or {}).get("id", job_id)

            if final_status in ("completed", "success", "done"):
                notify_job_completed(jid, final_status, role)
            elif final_status in ("failed", "error"):
                notify_job_failed(jid, error_message or "Unknown error", role)
        except Exception as ntfy_err:
            logger.debug(f"ntfy completion/failure hook skipped: {ntfy_err}")

        return {"success": True, "job": updated_job}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error updating job: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/{job_id}/events")
async def get_job_events(job_id: str, agent: dict = Depends(require_scopes("jobs:read"))):
    """Immutable audit trail for one job, oldest event first."""
    db_client = get_db_client()
    if not db_client:
        return []
    # Tenant isolation: only jobs in the caller's org expose their trail.
    job_res = db_client.table("agent_jobs").select("*").eq("id", job_id).execute()
    if job_res.data and job_org(job_res.data[0]) != agent_org(agent):
        return []
    return get_events(db_client, job_id)


@events_router.get("")
async def get_recent_events(
    since: str = "",
    limit: int = 100,
    agent: dict = Depends(require_scopes("jobs:read")),
):
    """Cross-job audit feed, newest event first (powers the Activity screen).

    `since` is an ISO timestamp lower bound (exclusive). Events are enriched
    with their job's title/status so the caller doesn't need a second fetch.
    Tenant rules match the rest of the API: the default org is the host
    operator and sees everything; other orgs see only their own jobs' events.
    """
    db_client = get_db_client()
    if not db_client:
        return []
    limit = max(1, min(int(limit or 100), 500))
    try:
        res = (
            db_client.table("agent_job_events").select("*")
            .order("created_at", desc=True).limit(limit).execute()
        )
        events = res.data or []
    except Exception as e:
        logger.error(f"Error fetching events: {e}")
        return []

    # `since` filtered in Python: ISO-8601 strings compare lexicographically
    # and LocalStore's query builder has no gt().
    if since:
        events = [e for e in events if str(e.get("created_at") or "") > since]

    # Enrich with job title/status; the same map drives tenant scoping.
    jobs_by_id = {}
    try:
        jres = (
            db_client.table("agent_jobs").select("*")
            .order("created_at", desc=True).limit(500).execute()
        )
        jobs_by_id = {j.get("id"): j for j in (jres.data or [])}
    except Exception as e:
        logger.debug(f"Event enrichment skipped: {e}")

    caller_org = agent_org(agent)
    out = []
    for ev in events:
        job = jobs_by_id.get(ev.get("job_id"))
        if caller_org != "default":
            # Org callers only see their own jobs' events; events whose job
            # fell out of the enrichment window are omitted rather than leaked.
            if not job or job_org(job) != caller_org:
                continue
        entry = dict(ev)
        if job:
            entry["job_title"] = job.get("title")
            entry["job_status"] = job.get("status")
            entry["target_agent_role"] = job.get("target_agent_role")
        out.append(entry)
    return out


async def _decide_approval(job_id: str, agent: dict, approve: bool, reason: str = "") -> dict:
    """Shared approve/reject flow for jobs paused at the human-in-the-loop gate."""
    db_client = get_db_client()
    if not db_client:
        raise HTTPException(status_code=400, detail="Database not configured")

    if (agent.get("role") or "").lower() not in utils_mod.get_approver_roles():
        raise HTTPException(status_code=403, detail="Your role is not permitted to decide approval gates")

    job_res = db_client.table("agent_jobs").select("*").eq("id", job_id).execute()
    if not job_res.data:
        raise HTTPException(status_code=404, detail="Job not found")
    job = job_res.data[0]
    if job_org(job) != agent_org(agent):
        raise HTTPException(status_code=404, detail="Job not found")
    if job.get("status") != JobStatus.NEEDS_APPROVAL.value:
        raise HTTPException(status_code=400, detail=f"Job is not awaiting approval (status: {job.get('status')})")

    if approve:
        update_data = {
            "status": JobStatus.PENDING.value,
            "approved_by": agent["instance_id"],
        }
        event, event_name = "approved", "job_pending"
    else:
        update_data = {
            "status": JobStatus.REJECTED.value,
            "approved_by": agent["instance_id"],
            "error_message": f"Rejected by {agent['instance_id']}: {reason or 'no reason given'}",
        }
        event, event_name = "rejected", "job_updated"

    res = db_client.table("agent_jobs").update(update_data).eq("id", job_id).execute()
    if not res.data:
        raise HTTPException(status_code=500, detail="Approval decision failed to persist")
    decided_job = res.data[0]

    record_event(db_client, job_id, event, agent["instance_id"], agent["role"],
                 {"reason": reason} if reason else None)

    if _broadcast_callback:
        try:
            await _broadcast_callback(event_name, decided_job)
        except Exception as e:
            logger.warning(f"Error executing broadcast callback after approval decision: {e}")

    return {"success": True, "job": decided_job}


@router.post("/{job_id}/approve")
async def approve_job(job_id: str, agent: dict = Depends(require_scopes("jobs:approve"))):
    """Approve a NEEDS_APPROVAL job, releasing it to PENDING for execution."""
    return await _decide_approval(job_id, agent, approve=True)


@router.post("/{job_id}/retry")
async def retry_job(job_id: str, agent: dict = Depends(require_scopes("jobs:approve"))):
    """Re-queue a FAILED or REJECTED job (approver roles only).

    Clears the lease and puts the job back to PENDING so a worker can pick it
    up again - the human override behind the console's 'Try again' button.
    """
    db_client = get_db_client()
    if not db_client:
        raise HTTPException(status_code=400, detail="Database not configured")
    if (agent.get("role") or "").lower() not in utils_mod.get_approver_roles():
        raise HTTPException(status_code=403, detail="Your role is not permitted to re-queue jobs")

    job_res = db_client.table("agent_jobs").select("*").eq("id", job_id).execute()
    if not job_res.data:
        raise HTTPException(status_code=404, detail="Job not found")
    job = job_res.data[0]
    if job_org(job) != agent_org(agent):
        raise HTTPException(status_code=404, detail="Job not found")
    if job.get("status") not in (JobStatus.FAILED.value, JobStatus.REJECTED.value):
        raise HTTPException(status_code=400, detail=f"Only failed/rejected jobs can be retried (status: {job.get('status')})")

    res = db_client.table("agent_jobs").update({
        "status": JobStatus.PENDING.value,
        "leased_by_instance_id": None,
        "error_message": None,
    }).eq("id", job_id).execute()
    if not res.data:
        raise HTTPException(status_code=500, detail="Retry failed to persist")
    requeued_job = res.data[0]

    record_event(db_client, job_id, "retried", agent["instance_id"], agent["role"],
                 {"manual": True, "previous_status": job.get("status")})

    if _broadcast_callback:
        try:
            await _broadcast_callback("job_pending", requeued_job)
        except Exception as e:
            logger.warning(f"Error executing broadcast callback after retry: {e}")

    return {"success": True, "job": requeued_job}


@router.post("/{job_id}/reject")
async def reject_job(job_id: str, payload: dict = None, agent: dict = Depends(require_scopes("jobs:approve"))):
    """Reject a NEEDS_APPROVAL job. Terminal: the job moves to REJECTED."""
    reason = (payload or {}).get("reason", "")
    return await _decide_approval(job_id, agent, approve=False, reason=reason)


def _load_job_in_org(db_client, job_id: str, agent: dict) -> dict:
    """Fetch a job, 404-ing (not leaking existence) across org boundaries."""
    job_res = db_client.table("agent_jobs").select("*").eq("id", job_id).execute()
    if not job_res.data:
        raise HTTPException(status_code=404, detail="Job not found")
    job = job_res.data[0]
    if job_org(job) != agent_org(agent):
        raise HTTPException(status_code=404, detail="Job not found")
    return job


@router.post("/{job_id}/cancel")
async def cancel_job(job_id: str, payload: dict = None, agent: dict = Depends(require_scopes("jobs:approve"))):
    """Call off a job that hasn't finished yet (waiting/needs_approval/pending/
    leased/in_progress -> cancelled). Distinct from reject: reject is only for
    jobs paused at the approval gate; cancel works on any non-terminal job,
    e.g. one you posted to the wrong role or that's no longer needed.
    Approver roles only, same as retry/reject."""
    db_client = get_db_client()
    if not db_client:
        raise HTTPException(status_code=400, detail="Database not configured")
    if (agent.get("role") or "").lower() not in utils_mod.get_approver_roles():
        raise HTTPException(status_code=403, detail="Your role is not permitted to cancel jobs")

    job = _load_job_in_org(db_client, job_id, agent)
    if job.get("status") not in CANCELLABLE_STATUSES:
        raise HTTPException(status_code=400, detail=f"Job is already terminal (status: {job.get('status')})")

    reason = (payload or {}).get("reason", "")
    res = db_client.table("agent_jobs").update({
        "status": JobStatus.CANCELLED.value,
        "leased_by_instance_id": None,
        "error_message": f"Cancelled by {agent['instance_id']}" + (f": {reason}" if reason else ""),
    }).eq("id", job_id).eq("status", job.get("status")).execute()
    if not res.data:
        raise HTTPException(
            status_code=409,
            detail="Job changed state before cancellation; refresh and retry",
        )
    cancelled_job = res.data[0]

    record_event(db_client, job_id, "cancelled", agent["instance_id"], agent["role"],
                 {"reason": reason, "previous_status": job.get("status")} if reason else {"previous_status": job.get("status")})

    if _broadcast_callback:
        try:
            await _broadcast_callback("job_updated", cancelled_job)
        except Exception as e:
            logger.warning(f"Error executing broadcast callback after cancel: {e}")

    return {"success": True, "job": cancelled_job}


@router.post("/{job_id}/archive")
async def archive_job(job_id: str, agent: dict = Depends(require_scopes("jobs:write"))):
    """Hide a terminal job (completed/failed/rejected/cancelled) from the
    default board view. Non-destructive and reversible (see /unarchive) - the
    job keeps its status and full audit trail, it just stops cluttering
    'All'/'Done'/'Problems'. Any authenticated agent may archive (it's tidying,
    not a decision), but only terminal jobs qualify so in-flight work can
    never be hidden by mistake."""
    db_client = get_db_client()
    if not db_client:
        raise HTTPException(status_code=400, detail="Database not configured")

    job = _load_job_in_org(db_client, job_id, agent)
    if job.get("status") not in ARCHIVABLE_STATUSES:
        raise HTTPException(status_code=400, detail=f"Only terminal jobs can be archived (status: {job.get('status')})")
    if job.get("archived"):
        return {"success": True, "job": job}

    res = db_client.table("agent_jobs").update({
        "archived": True,
        "archived_at": _utc_now_iso(),
        "archived_by": agent["instance_id"],
    }).eq("id", job_id).execute()
    if not res.data:
        raise HTTPException(status_code=500, detail="Archive failed to persist")
    archived_job = res.data[0]

    record_event(db_client, job_id, "archived", agent["instance_id"], agent["role"])

    if _broadcast_callback:
        try:
            await _broadcast_callback("job_updated", archived_job)
        except Exception as e:
            logger.warning(f"Error executing broadcast callback after archive: {e}")

    return {"success": True, "job": archived_job}


@router.post("/{job_id}/unarchive")
async def unarchive_job(job_id: str, agent: dict = Depends(require_scopes("jobs:write"))):
    """Undo /archive - brings a job back into the default board view."""
    db_client = get_db_client()
    if not db_client:
        raise HTTPException(status_code=400, detail="Database not configured")

    job = _load_job_in_org(db_client, job_id, agent)
    if not job.get("archived"):
        return {"success": True, "job": job}

    res = db_client.table("agent_jobs").update({
        "archived": False,
        "archived_at": None,
        "archived_by": None,
    }).eq("id", job_id).execute()
    if not res.data:
        raise HTTPException(status_code=500, detail="Unarchive failed to persist")
    unarchived_job = res.data[0]

    record_event(db_client, job_id, "unarchived", agent["instance_id"], agent["role"])

    if _broadcast_callback:
        try:
            await _broadcast_callback("job_updated", unarchived_job)
        except Exception as e:
            logger.warning(f"Error executing broadcast callback after unarchive: {e}")

    return {"success": True, "job": unarchived_job}


@router.get("/{job_id}/duplicates")
async def get_job_duplicates(job_id: str, agent: dict = Depends(require_scopes("jobs:read"))):
    """Best-effort duplicate check: other jobs with the same title and target
    role, plus anything already linked via reassignment. Use before manually
    reposting a failed job's work, or to answer 'did someone already redo
    this?' Heuristic (exact-title match) - not a substitute for reading the
    jobs, just a pointer to look closer."""
    db_client = get_db_client()
    if not db_client:
        return []
    job = _load_job_in_org(db_client, job_id, agent)

    res = db_client.table("agent_jobs").select("*").order("created_at", desc=True).limit(500).execute()
    all_jobs = [j for j in (res.data or []) if job_org(j) == agent_org(agent)]

    linked_ids = {job.get("reassigned_from_job_id"), job.get("reassigned_to_job_id")} - {None}
    title = (job.get("title") or "").strip().lower()

    out = []
    for j in all_jobs:
        if j.get("id") == job_id:
            continue
        same_title = title and (j.get("title") or "").strip().lower() == title \
            and j.get("target_agent_role") == job.get("target_agent_role")
        linked = j.get("id") in linked_ids
        if same_title or linked:
            out.append({
                "id": j.get("id"),
                "title": j.get("title"),
                "status": j.get("status"),
                "created_at": j.get("created_at"),
                "target_agent_role": j.get("target_agent_role"),
                "relation": "reassignment_link" if linked else "same_title",
            })
    return out


@router.post("/{job_id}/reassign")
async def reassign_job(job_id: str, payload: dict, agent: dict = Depends(require_scopes("jobs:approve"))):
    """Redo a failed/rejected/cancelled job with a different target (or the
    same target, for a plain retry-elsewhere). Unlike /retry, which re-queues
    the SAME job row in place, this clones a NEW job so the failed attempt's
    history stays intact, links the two rows both directions
    (reassigned_from_job_id / reassigned_to_job_id), and auto-archives the old
    one - so 'did a new one get done in its place?' has a direct answer:
    follow reassigned_to_job_id, or call GET /{job_id}/duplicates.
    Approver roles only, same as retry."""
    db_client = get_db_client()
    if not db_client:
        raise HTTPException(status_code=400, detail="Database not configured")
    if (agent.get("role") or "").lower() not in utils_mod.get_approver_roles():
        raise HTTPException(status_code=403, detail="Your role is not permitted to reassign jobs")

    job = _load_job_in_org(db_client, job_id, agent)
    if job.get("status") not in REASSIGNABLE_STATUSES:
        raise HTTPException(status_code=400, detail=f"Only failed/rejected/cancelled jobs can be reassigned (status: {job.get('status')})")

    target_agent_role = payload.get("target_agent_role") or job.get("target_agent_role")
    target_agent_id = payload.get("target_agent_id")
    instructions = payload.get("instructions")
    if not target_agent_role:
        raise HTTPException(status_code=400, detail="target_agent_role is required")
    requires_approval = bool(job.get("requires_approval")) or target_agent_role.lower() in get_gated_roles()
    status = (
        JobStatus.NEEDS_APPROVAL.value
        if requires_approval
        else JobStatus.PENDING.value
    )

    new_data = {
        "title": payload.get("title") or job.get("title"),
        "description": instructions if instructions is not None else job.get("description"),
        "source_agent_id": agent["instance_id"],
        "source_agent_role": agent["role"],
        "target_agent_role": target_agent_role,
        "target_agent_id": target_agent_id,
        "status": status,
        "depends_on": [],
        "input_payload": (
            {**job.get("input_payload", {}), "prompt": instructions}
            if instructions is not None else job.get("input_payload") or {}
        ),
        "reassigned_from_job_id": job_id,
    }
    if agent_org(agent) != "default":
        new_data["org_id"] = agent_org(agent)
    if job.get("max_retries") is not None:
        new_data["max_retries"] = job.get("max_retries")
    if job.get("escalate_to_role"):
        new_data["escalate_to_role"] = job.get("escalate_to_role")
    if requires_approval:
        new_data["requires_approval"] = True

    ins = db_client.table("agent_jobs").insert(new_data).execute()
    if not ins.data:
        raise HTTPException(status_code=500, detail="Reassign failed to create the new job")
    new_job = ins.data[0]

    record_event(db_client, new_job.get("id"), "created", agent["instance_id"], agent["role"],
                 {"status": status, "target_agent_role": target_agent_role,
                  "reassigned_from_job_id": job_id})

    upd = db_client.table("agent_jobs").update({
        "reassigned_to_job_id": new_job.get("id"),
        "archived": True,
        "archived_at": _utc_now_iso(),
        "archived_by": agent["instance_id"],
    }).eq("id", job_id).execute()
    old_job = upd.data[0] if upd.data else job

    record_event(db_client, job_id, "reassigned", agent["instance_id"], agent["role"],
                 {"reassigned_to_job_id": new_job.get("id")})

    if _broadcast_callback:
        try:
            await _broadcast_callback(
                "job_needs_approval" if requires_approval else "job_pending",
                new_job,
            )
            await _broadcast_callback("job_updated", old_job)
        except Exception as e:
            logger.warning(f"Error executing broadcast callback after reassign: {e}")
    try:
        if requires_approval:
            notify_job_needs_approval(
                job_id=new_job.get("id", "unknown"),
                title=new_job.get("title", "Untitled job"),
                to_role=target_agent_role,
            )
        else:
            notify_job_created(
                job_id=new_job.get("id", "unknown"),
                title=new_job.get("title", "Untitled job"),
                to_role=target_agent_role,
            )
    except Exception as ntfy_err:
        logger.debug(f"ntfy addon skipped: {ntfy_err}")

    return {"success": True, "job": new_job, "superseded_job": old_job}


@agents_router.get("")
async def get_agents(agent: dict = Depends(require_scopes("agents:read"))):
    """Registered agents with derived presence (effective_status,
    last_seen_seconds). Excludes the auth_token_hash column.

    Visibility: callers in the default org are the host operator and see
    every org's agents (matching `mco agents`); org-scoped callers see only
    their own fleet.
    """
    db_client = get_db_client()
    if not db_client:
        return []
    try:
        res = db_client.table("agent_registry").select("*").order("instance_id").execute()
        org = agent_org(agent)
        threshold = get_offline_after_seconds()
        out = []
        for r in (res.data or []):
            # Tenant isolation (app-side so pre-migration schemas keep working).
            if org != "default" and (r.get("org_id") or "default") != org:
                continue
            # Never expose auth_token_hash over the API.
            row = {k: v for k, v in r.items() if k != "auth_token_hash"}
            out.append(decorate_presence(row, threshold))
        return out
    except Exception as e:
        logger.error(f"Error fetching registered agents: {e}")
        return []
