"""Decoupled database transactional handlers for Job Board operations."""

import logging
from typing import Any, Callable, Coroutine, Dict, Optional
from mco.orchestrator.contracts import JobStatus
from mco.orchestrator.audit import record_event
from mco.orchestrator.leases import acquire_lease, fenced_update, LeaseError

logger = logging.getLogger("mco.orchestrator.handlers")


def _initial_status(db_client: Any, depends_on: list, requires_approval: bool) -> str:
    """Resolve a new job's starting status from its deps and approval gate."""
    if depends_on:
        dep_res = db_client.table("agent_jobs").select("status").in_("id", depends_on).execute()
        for dep in (dep_res.data or []):
            if dep.get("status") != JobStatus.COMPLETED.value:
                return JobStatus.WAITING.value
    if requires_approval:
        return JobStatus.NEEDS_APPROVAL.value
    return JobStatus.PENDING.value

async def handle_job_create(
    db_client: Any,
    payload: Dict[str, Any],
    source_agent_id: str,
    source_agent_role: str,
    correlation_id: str,
    send_error: Callable[[str, str], Coroutine[Any, Any, None]],
    send_ack: Callable[[Dict[str, Any]], Coroutine[Any, Any, None]],
    broadcast_event: Callable[[str, Dict[str, Any]], Coroutine[Any, Any, None]],
) -> None:
    """Handle creation of a new job on the Job Board."""
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
        await send_error("Missing required fields: 'title' and 'target_agent_role'", correlation_id)
        return

    try:
        status = _initial_status(db_client, depends_on, requires_approval)

        data = {
            "title": title,
            "description": description,
            "source_agent_id": source_agent_id,
            "source_agent_role": source_agent_role,
            "target_agent_role": target_agent_role,
            "target_agent_id": target_agent_id,
            "status": status,
            "depends_on": depends_on,
            "input_payload": input_payload,
        }
        # Governance columns are only sent when used, so databases that have not
        # run the Phase A migration keep working for plain jobs.
        if requires_approval:
            data["requires_approval"] = True
        if max_retries is not None:
            data["max_retries"] = max_retries
        if escalate_to_role:
            data["escalate_to_role"] = escalate_to_role

        res = db_client.table("agent_jobs").insert(data).execute()
        if not res.data:
            await send_error("Failed to insert job into database", correlation_id)
            return

        new_job = res.data[0]

        record_event(db_client, new_job.get("id"), "created", source_agent_id, source_agent_role,
                     {"status": status, "target_agent_role": target_agent_role})

        # Send ACK to creator
        await send_ack({"status": "job_created", "job": new_job})

        # Broadcast new job event
        if status == JobStatus.PENDING.value:
            event_type = "job_pending"
        elif status == JobStatus.NEEDS_APPROVAL.value:
            event_type = "job_needs_approval"
        else:
            event_type = "job_created"
        await broadcast_event(event_type, new_job)

    except Exception as e:
        logger.exception(f"[{correlation_id}] JOB_CREATE handler error: {e}")
        await send_error(f"JOB_CREATE failed: {str(e)}", correlation_id)


async def handle_job_lease(
    db_client: Any,
    payload: Dict[str, Any],
    fallback_agent_instance_id: str,
    correlation_id: str,
    send_error: Callable[[str, str], Coroutine[Any, Any, None]],
    send_ack: Callable[[Dict[str, Any]], Coroutine[Any, Any, None]],
    broadcast_event: Callable[[str, Dict[str, Any]], Coroutine[Any, Any, None]],
) -> None:
    """Handle atomic leasing/claiming of a pending job."""
    task_id = payload.get("task_id")
    agent_instance_id = fallback_agent_instance_id
    if payload.get("agent_instance_id", agent_instance_id) != agent_instance_id:
        await send_error("Cannot lease on behalf of another agent", correlation_id)
        return

    if not task_id:
        await send_error("Missing 'task_id'", correlation_id)
        return

    try:
        # Atomic database-level lease function (uses Supabase RPC lease_task)
        success = acquire_lease(db_client, task_id, agent_instance_id)

        if success:
            # Fetch updated job details
            job_res = db_client.table("agent_jobs").select("*").eq("id", task_id).execute()
            if job_res.data:
                job = job_res.data[0]
                # ACK to the leasing agent
                await send_ack({"status": "job_leased", "job": job})

                # Broadcast lease event
                await broadcast_event("job_leased", job)
            else:
                await send_error("Task leased but could not retrieve details", correlation_id)
        else:
            await send_error("Task is no longer pending or is not assigned to this instance", correlation_id)

    except Exception as e:
        logger.exception(f"[{correlation_id}] JOB_LEASE handler error: {e}")
        await send_error(f"JOB_LEASE failed: {str(e)}", correlation_id)


async def handle_job_update(
    db_client: Any,
    payload: Dict[str, Any],
    correlation_id: str,
    send_error: Callable[[str, str], Coroutine[Any, Any, None]],
    send_ack: Callable[[Dict[str, Any]], Coroutine[Any, Any, None]],
    broadcast_event: Callable[[str, Dict[str, Any]], Coroutine[Any, Any, None]],
    actor: Optional[Dict[str, Any]] = None,
) -> None:
    """Handle status, progress, or output updates for a leased job."""
    task_id = payload.get("task_id")
    status = payload.get("status")
    output_payload = payload.get("output_payload")
    error_message = payload.get("error_message")

    if not task_id or not status:
        await send_error("Missing 'task_id' or 'status'", correlation_id)
        return

    try:
        update_data = {"status": status}
        # completed_at is stamped by the DB trigger (trg_mco_stamp_completed_at) via now(),
        # keeping it on the same clock as created_at / started_at (no worker-clock skew).
        if output_payload is not None:
            update_data["output_payload"] = output_payload
        if error_message is not None:
            update_data["error_message"] = error_message

        actor = actor or {}
        claim = {k: payload.get(k) for k in ("lease_id", "lease_epoch", "lease_incarnation")}
        claim["agent_instance_id"] = actor.get("instance_id")
        try:
            updated_job = fenced_update(db_client, task_id, claim, update_data)
        except LeaseError as exc:
            record_event(db_client, task_id, "write_fenced", actor.get("instance_id"),
                         actor.get("role"), {"reason": exc.reason})
            await send_error("FENCED: " + exc.reason, correlation_id)
            return

        replayed = updated_job.pop('_replayed', False)
        record_event(db_client, task_id, f"status:{status}",
                     actor.get("instance_id"), actor.get("role"),
                     {"error_message": error_message} if error_message else None,
                     outbox_id=f"attempt-status:{claim['lease_id']}:{status}" if claim.get('lease_id') else None)
        if replayed:
            # A result may have committed before its downstream work or its
            # response survived. Dependency release is idempotent and repairable.
            if status == JobStatus.COMPLETED.value:
                await _unlock_dependents(db_client, task_id, broadcast_event)
            await send_ack({"status": "job_updated", "job": updated_job, "replayed": True})
            return
        actor = actor or {}

        # ACK to agent
        await send_ack({"status": "job_updated", "job": updated_job})

        # Broadcast update event
        await broadcast_event("job_updated", updated_job)

        # Unlock downstream dependencies if completed
        if status == JobStatus.COMPLETED.value:
            await _unlock_dependents(db_client, task_id, broadcast_event)

            # Drumline: distill the finished job into shared context (opt-out via
            # MCO_DRUMLINE_DISTILL=false). Best-effort - memory must never break
            # the orchestration path.
            try:
                from mco.config import get_config
                if str(get_config().get("MCO_DRUMLINE_DISTILL") or "true").lower() != "false":
                    from mco.orchestrator.drumline import distill_job
                    entry = distill_job(db_client, updated_job)
                    if entry:
                        record_event(db_client, task_id, "context_distilled", "system", "drumline",
                                     {"context_id": entry.get("id"), "kind": entry.get("kind")})
            except Exception as e:
                logger.debug(f"Drumline distillation skipped for {task_id}: {e}")

        # Retry / escalation paths if failed
        if status == JobStatus.FAILED.value:
            await _handle_failure(db_client, updated_job, error_message, broadcast_event)

    except Exception as e:
        logger.exception(f"[{correlation_id}] JOB_UPDATE handler error: {e}")
        await send_error(f"JOB_UPDATE failed: {str(e)}", correlation_id)


async def _unlock_dependents(
    db_client: Any,
    task_id: str,
    broadcast_event: Callable[[str, Dict[str, Any]], Coroutine[Any, Any, None]],
) -> None:
    """Move WAITING jobs whose parents all completed to their next state."""
    waiting_res = db_client.table("agent_jobs").select("*").eq("status", JobStatus.WAITING.value).execute()
    for waiting_job in (waiting_res.data or []):
        depends_on = waiting_job.get("depends_on") or []
        if task_id not in depends_on:
            continue
        # Check all parent statuses
        parents_res = db_client.table("agent_jobs").select("status").in_("id", depends_on).execute()
        all_completed = len(parents_res.data or []) == len(set(depends_on)) and all(
            parent.get("status") == JobStatus.COMPLETED.value
            for parent in (parents_res.data or [])
        )
        if not all_completed:
            continue

        # Jobs with an approval gate pause at NEEDS_APPROVAL instead of going live.
        if waiting_job.get("requires_approval"):
            next_status = JobStatus.NEEDS_APPROVAL.value
            event_name = "job_needs_approval"
        else:
            next_status = JobStatus.PENDING.value
            event_name = "job_pending"

        unlock_res = db_client.table("agent_jobs").update({"status": next_status}).eq("id", waiting_job["id"]).eq("status", "waiting").execute()
        if unlock_res.data:
            unlocked_job = unlock_res.data[0]
            record_event(db_client, waiting_job["id"], f"status:{next_status}",
                         "system", "orchestrator", {"unlocked_by": task_id})
            if next_status == JobStatus.NEEDS_APPROVAL.value:
                try:
                    from mco.notifiers.ntfy import notify_job_needs_approval
                    notify_job_needs_approval(waiting_job["id"], unlocked_job.get("title", ""),
                                              unlocked_job.get("target_agent_role", "unknown"))
                except Exception:
                    pass
            await broadcast_event(event_name, unlocked_job)


async def _handle_failure(
    db_client: Any,
    job: Dict[str, Any],
    error_message: Optional[str],
    broadcast_event: Callable[[str, Dict[str, Any]], Coroutine[Any, Any, None]],
) -> None:
    """Escalation path for a FAILED job: retry while budget remains, then
    hand off to `escalate_to_role` instead of dying silently."""
    job_id = job.get("id")
    max_retries = int(job.get("max_retries") or 0)
    retry_count = int(job.get("retry_count") or 0)
    escalate_to_role = job.get("escalate_to_role")

    if retry_count < max_retries:
        requeue = db_client.table("agent_jobs").update({
            "status": JobStatus.PENDING.value,
            "retry_count": retry_count + 1,
            "leased_by_instance_id": None,
            "lease_id": None,
            "lease_expires_at": None,
            "started_at": None,
        }).eq("id", job_id).eq("status", "failed").execute()
        if requeue.data:
            requeued_job = requeue.data[0]
            record_event(db_client, job_id, "retried", "system", "orchestrator",
                         {"attempt": retry_count + 1, "max_retries": max_retries})
            await broadcast_event("job_pending", requeued_job)
        return

    # Enterprise escalation bridge: mirror the failure into an external ITSM
    # platform (e.g. open a ServiceNow incident) when configured. Best-effort -
    # a platform outage must never break the orchestration path.
    try:
        from mco.config import get_config
        bridge_name = get_config().get("MCO_ESCALATION_CONNECTOR")
        if bridge_name:
            from mco.connectors import get_connector
            bridge = get_connector(bridge_name)
            if bridge:
                ref = bridge.escalate(job, error_message or job.get("error_message") or "unknown")
                record_event(db_client, job_id, "escalated_external", "system", "orchestrator",
                             {"connector": bridge.name, "platform_ref": ref})
    except NotImplementedError:
        pass
    except Exception as e:
        logger.warning(f"Escalation bridge failed for job {job_id}: {e}")

    if not escalate_to_role:
        return

    escalation = {
        "title": f"ESCALATION: {job.get('title', 'Untitled job')}",
        "description": (
            f"Job {job_id} failed after {retry_count + 1} attempt(s) and was escalated.\n"
            f"Last error: {error_message or job.get('error_message') or 'unknown'}\n\n"
            f"Original instructions:\n{job.get('description') or ''}"
        ),
        "source_agent_id": "system",
        "source_agent_role": "orchestrator",
        "target_agent_role": escalate_to_role,
        "status": JobStatus.PENDING.value,
        "depends_on": [],
        "input_payload": {"escalated_from": job_id},
    }
    # Escalations stay inside the failed job's org.
    if (job.get("org_id") or "default") != "default":
        escalation["org_id"] = job["org_id"]
    esc_res = db_client.table("agent_jobs").insert(escalation).execute()
    if esc_res.data:
        esc_job = esc_res.data[0]
        record_event(db_client, job_id, "escalated", "system", "orchestrator",
                     {"escalation_job_id": esc_job.get("id"), "escalate_to_role": escalate_to_role})
        record_event(db_client, esc_job.get("id"), "created", "system", "orchestrator",
                     {"escalated_from": job_id, "status": JobStatus.PENDING.value})
        try:
            from mco.notifiers.ntfy import notify_job_escalated
            notify_job_escalated(job_id, job.get("title", ""), escalate_to_role,
                                 error_message or "unknown")
        except Exception:
            pass
        await broadcast_event("job_pending", esc_job)
