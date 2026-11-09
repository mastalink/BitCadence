"""Job capability admission and durable quota cooldown evidence."""
import json
import re
from datetime import datetime, timedelta, timezone

from mco.config import get_config
from mco.orchestrator.delivery import _parse_ts


def can_run(job, role, instance, config=None):
    cfg = config if config is not None else get_config()
    payload = job.get('input_payload') or {}
    routing = payload.get('routing', {}) if isinstance(payload, dict) else {}
    if not isinstance(routing, dict):
        return False
    roles = routing.get('allowed_roles')
    required = routing.get('required_capabilities', [])
    if roles is not None and (not isinstance(roles, list) or any(not isinstance(v, str) for v in roles)
                              or role.lower() not in {v.lower() for v in roles}):
        return False
    if not isinstance(required, list) or any(not isinstance(v, str) for v in required):
        return False
    if not required:
        return True
    try:
        capabilities = json.loads(cfg.get('MCO_WORKER_CAPABILITIES') or '{}')
        available = capabilities.get(instance, [])
        return isinstance(available, list) and set(required) <= set(available)
    except (ValueError, TypeError, AttributeError):
        return False


def quota_error(message):
    return bool(re.search(r'\b402\b|out of credits|insufficient credits|credit balance|quota exceeded|out of quota', str(message or ''), re.I))


def defer_quota(db, job, actor):
    """Recoverable second phase after a fenced failure receipt is committed."""
    from mco.orchestrator.audit import record_event
    from mco.orchestrator.store_context import transaction
    with transaction(db):
        record_event(db, job['id'], 'worker_out_of_quota', actor.get('instance_id'), actor.get('role'), None,
                     outbox_id=f"quota:{job.get('lease_id')}")
        rows = db.table('agent_jobs').update({
            'status': 'pending', 'leased_by_instance_id': None, 'lease_id': None,
            'lease_expires_at': None, 'completed_at': None,
            'error_message': 'Worker out of quota; waiting for an eligible worker.'
        }).eq('id', job['id']).eq('status', 'failed').eq('lease_id', job.get('lease_id')).execute().data or []
    return rows[0] if rows else None


def recover_quota(db):
    rows = db.table('agent_jobs').select('*').eq('status', 'failed').execute().data or []
    recovered = []
    for job in rows:
        if job.get('lease_id') and job.get('leased_by_instance_id') and quota_error(job.get('error_message')):
            updated = defer_quota(db, job, {'instance_id': job['leased_by_instance_id'], 'role': job.get('target_agent_role')})
            if updated:
                recovered.append(updated)
    return recovered


def quota_workers(db, *, now=None, config=None):
    cfg = config if config is not None else get_config()
    try:
        seconds = max(1, int(cfg.get('MCO_QUOTA_COOLDOWN_SECONDS', 3600)))
    except (ValueError, TypeError):
        seconds = 3600
    cutoff = (now or datetime.now(timezone.utc)) - timedelta(seconds=seconds)
    rows = db.table('agent_job_events').select('*').eq('event', 'worker_out_of_quota').gt('created_at', cutoff.isoformat()).execute().data or []
    return {r.get('actor_id') for r in rows if _parse_ts(r.get('created_at')) and _parse_ts(r['created_at']) > cutoff}
