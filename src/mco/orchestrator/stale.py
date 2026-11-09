"""Conservative stale-work detection shared by maintenance and reviewed cleanup."""
from datetime import datetime, timedelta, timezone

from mco.config import get_config
from mco.orchestrator.audit import record_event
from mco.orchestrator.delivery import _parse_ts


def all_jobs(db, org=None):
    rows, offset = [], 0
    while True:
        query = db.table('agent_jobs').select('*')
        if org is not None:
            query = query.eq('org_id', org)
        page = query.order('id').range(offset, offset + 499).execute().data or []
        rows.extend(page)
        if len(page) < 500:
            return rows
        offset += len(page)


def stale_candidates(db, *, org=None, now=None, config=None, waiting_only=False):
    cfg = config if config is not None else get_config()
    try:
        days = int(cfg.get('MCO_STALE_JOB_DAYS', 7))
    except (ValueError, TypeError):
        days = 7
    if days <= 0:
        return []
    cutoff = (now or datetime.now(timezone.utc)) - timedelta(days=days)
    jobs = all_jobs(db, org)
    by_id = {(j.get('org_id') or 'default', j['id']): j for j in jobs}

    def old(j):
        ts = _parse_ts(j.get('created_at'))
        return ts is not None and ts <= cutoff

    def abandoned(j, seen):
        if not j:
            return True
        stamp = (j.get('input_payload') or {}).get('score') if isinstance(j.get('input_payload') or {}, dict) else None
        if isinstance(stamp, dict) and stamp.get('protocol') == 'score-v1':
            return False
        if j['id'] in seen:
            return True  # cycle of never-started old dependencies
        if j.get('status') in {'failed', 'rejected', 'cancelled', 'halted'}:
            return True
        if j.get('status') in {'leased', 'in_progress', 'needs_approval', 'completed'} or j.get('started_at') or not old(j):
            return False
        if j.get('status') == 'pending':
            return True
        parents = j.get('depends_on') or []
        return bool(parents) and any(abandoned(by_id.get((j.get('org_id') or 'default', p)), seen | {j['id']}) for p in parents)

    candidates = []
    for j in jobs:
        payload = j.get('input_payload') or {}
        stamp = payload.get('score') if isinstance(payload, dict) else None
        if isinstance(stamp, dict) and stamp.get('protocol') == 'score-v1':
            continue
        statuses = {'waiting'} if waiting_only else {'waiting', 'pending', 'halted'}
        if j.get('status') not in statuses or j.get('archived') or j.get('started_at') or not old(j):
            continue
        if j.get('status') == 'halted' and not str(j.get('error_message') or '').startswith('Dependency wait expired:'):
            continue
        if j.get('status') == 'pending' or abandoned(j, set()):
            candidates.append(j)
    return candidates


def expire_waiting(db, *, now=None, config=None):
    expired = []
    for job in stale_candidates(db, now=now, config=config, waiting_only=True):
        rows = db.table('agent_jobs').update({
            'status': 'halted', 'error_message': 'Dependency wait expired: prerequisite is missing, stopped, or never started.'
        }).eq('id', job['id']).eq('status', 'waiting').execute().data or []
        if rows:
            record_event(db, job['id'], 'dependency_expired', 'system', 'delivery', {'depends_on': job.get('depends_on') or []})
            expired.extend(rows)
    return expired
