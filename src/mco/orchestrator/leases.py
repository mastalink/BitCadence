"""Fenced leases: one owner per attempt, proven at every worker write.

F1: A partitioned worker must not overwrite its replacement's result. The
random lease id, owner, epoch, store incarnation and current state all belong
to the proof; HTTP and WebSocket handlers use this same state machine.

F2: Reapers compare the observed expiry as well as the attempt. Only one can
requeue it, and a renewal since observation prevents a stale reaper winning.
F3: Renewal must arrive before expiry; a reaper need not run to fence a lease.

F4: Epochs roll back with database snapshots (the ABA problem). Operators must
run restore-fence to rotate the persisted incarnation after restoring a backup.
Identity is read inside each transaction, never cached across a restore.

F5: Worker transitions are enforced here before handlers unlock dependent jobs
or emit success. HALTED is terminal for worker claims; operator retry is a
separate path that requires a new attempt. Pause fences existing work atomically.

F6: A receipt permits an identical final result to be retried after a lost
response; a changed result is rejected. External effects still require the
downstream service to honor the provided idempotency key.

SQLite serializes these multi-step checks in a LocalStore transaction.
PostgreSQL serializes them inside mco_lease, not across separate HTTP queries.
Required off-box acknowledgement runs after commit; failure cannot undo work
already committed, so callers retain their proof and retry the same result.
"""
from __future__ import annotations
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from functools import wraps
from typing import Any, Iterable
from mco.orchestrator.evidence import attempt_boundary
from mco.orchestrator.store_context import transaction, is_postgres

PENDING, LEASED, IN_PROGRESS = 'pending', 'leased', 'in_progress'
COMPLETED, FAILED, CANCELLED, HALTED = 'completed', 'failed', 'cancelled', 'halted'
NEEDS_APPROVAL = 'needs_approval'
TERMINAL = frozenset({COMPLETED, FAILED, CANCELLED, HALTED, 'rejected'})
RENEWABLE = frozenset({LEASED, IN_PROGRESS})
WORKER_TRANSITIONS = {s: frozenset({IN_PROGRESS, COMPLETED, FAILED}) for s in RENEWABLE}
DEFAULT_TTL_SECONDS, DEFAULT_RENEW_SECONDS = 900, 300
_INCARNATION_TABLE, _INCARNATION_ROW = 'mco_store_identity', 'store'

class LeaseError(RuntimeError):
    def __init__(self, reason: str, *, expected: dict | None = None, actual: dict | None = None):
        super().__init__(reason)
        self.reason, self.expected, self.actual = reason, expected or {}, actual or {}
    def as_detail(self) -> dict:
        return {'fenced': True, 'reason': self.reason, 'expected': self.expected, 'actual': self.actual}

@dataclass(frozen=True)
class Lease:
    job_id: str
    lease_id: str
    epoch: int
    incarnation: str
    owner: str
    def as_claim(self) -> dict:
        return {'lease_id': self.lease_id, 'lease_epoch': self.epoch,
                'lease_incarnation': self.incarnation, 'agent_instance_id': self.owner}

def atomic(func):
    @wraps(func)
    def wrapped(db, *args, **kwargs):
        with transaction(db):
            return func(db, *args, **kwargs)
    return wrapped

def _remote(db, action, job_id='', owner='', claim=None, updates=None, ttl=900):
    data = db.rpc('mco_lease', {'p_action': action, 'p_job_id': str(job_id),
        'p_owner': owner, 'p_claim': claim or {}, 'p_updates': updates or {},
        'p_ttl': max(1, int(ttl))}).execute().data
    if isinstance(data, list):
        data = data[0] if data else {}
    if (data or {}).get('error'):
        raise LeaseError(data['error'])
    return data or {}

@atomic
def store_incarnation(db: Any, *, refresh: bool = False) -> str:
    if is_postgres(db):
        return _remote(db, 'identity')['incarnation']
    rows = db.table(_INCARNATION_TABLE).select('*').eq('id', _INCARNATION_ROW).execute().data
    if rows:
        return rows[0]['incarnation']
    fresh = uuid.uuid4().hex
    db.table(_INCARNATION_TABLE).insert({'id': _INCARNATION_ROW,
        'incarnation': fresh, 'created_at': _now_iso()}).execute()
    return fresh

def reset_incarnation_cache() -> None:
    pass  # No cache: identity is read in every transaction.

@atomic
def rotate_incarnation(db: Any) -> str:
    if is_postgres(db):
        return _remote(db, 'rotate')['incarnation']
    fresh = uuid.uuid4().hex
    db.table(_INCARNATION_TABLE).upsert({'id': _INCARNATION_ROW,
        'incarnation': fresh, 'created_at': _now_iso()}).execute()
    return fresh

def _now():
    return datetime.now(timezone.utc)

def _now_iso():
    return _now().isoformat()

def _parse(ts):
    try:
        d = datetime.fromisoformat(str(ts).replace('Z', '+00:00'))
        return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return None

def _get(db, job_id):
    rows = db.table('agent_jobs').select('*').eq('id', str(job_id)).execute().data
    return rows[0] if rows else None

def _paused(db):
    rows = db.table('mco_control').select('*').eq('id', 'governance').execute().data
    return bool(rows and rows[0].get('paused'))

def _lease(job):
    return Lease(str(job['id']), job['lease_id'], int(job['lease_epoch']),
                 job['lease_incarnation'], job['leased_by_instance_id'])

@attempt_boundary
@atomic
def acquire_lease(db: Any, job_id: str, owner: str, *, ttl_seconds: int = DEFAULT_TTL_SECONDS) -> Lease | None:
    if not owner:
        return None
    if is_postgres(db):
        result = _remote(db, 'acquire', job_id, owner, ttl=ttl_seconds)
        return _lease(result['job']) if result.get('job') else None
    job = _get(db, job_id)
    if not job or job.get('status') != PENDING or _paused(db):
        return None
    now = _now()
    updates = {'status': LEASED, 'leased_by_instance_id': owner,
        'started_at': now.isoformat(), 'completed_at': None, 'lease_id': uuid.uuid4().hex,
        'lease_epoch': int(job.get('lease_epoch') or 0) + 1,
        'lease_incarnation': store_incarnation(db),
        'lease_expires_at': (now + timedelta(seconds=max(1, ttl_seconds))).isoformat()}
    rows = db.table('agent_jobs').update(updates).eq('id', str(job_id)).eq('status', PENDING).execute().data
    return _lease(rows[0]) if rows else None

def is_expired(job: dict, *, now: datetime | None = None, ttl_seconds: int = DEFAULT_TTL_SECONDS) -> bool:
    expires, started = _parse(job.get('lease_expires_at')), _parse(job.get('started_at'))
    expires = expires or (started + timedelta(seconds=ttl_seconds) if started else None)
    return expires is not None and expires <= (now or _now())

def _claim_query(db, job, claim, updates):
    owner = claim.get('agent_instance_id')
    if job.get('lease_id') and claim.get('lease_id') and claim['lease_id'] != job['lease_id']:
        raise LeaseError('stale lease: this attempt no longer owns the job',
                         actual={'status': job['status'], 'lease_epoch': job.get('lease_epoch')})
    if not owner or owner != job.get('leased_by_instance_id'):
        raise LeaseError('not the lease holder')
    if job.get('lease_id') is None and not job.get('lease_epoch'):
        return db.table('agent_jobs').update(updates).eq('id', job['id']).eq('status', job['status']).eq('leased_by_instance_id', owner)
    if not claim.get('lease_id'):
        raise LeaseError('no lease presented')
    for key in ('lease_id', 'lease_epoch', 'lease_incarnation'):
        if claim.get(key) != job.get(key):
            raise LeaseError('stale lease: this attempt no longer owns the job')
    if claim['lease_incarnation'] != store_incarnation(db):
        raise LeaseError('stale lease: store incarnation changed')
    return (db.table('agent_jobs').update(updates).eq('id', job['id']).eq('status', job['status'])
            .eq('leased_by_instance_id', owner).eq('lease_id', claim['lease_id'])
            .eq('lease_epoch', claim['lease_epoch']).eq('lease_incarnation', claim['lease_incarnation']))

@attempt_boundary
@atomic
def renew_lease(db: Any, lease: Lease, *, ttl_seconds: int = DEFAULT_TTL_SECONDS) -> bool:
    if is_postgres(db):
        try:
            return bool(_remote(db, 'renew', lease.job_id, lease.owner, lease.as_claim(), ttl=ttl_seconds).get('job'))
        except LeaseError:
            return False
    job = _get(db, lease.job_id)
    if not job or job.get('status') not in RENEWABLE or is_expired(job) or _paused(db):
        return False
    try:
        return bool(_claim_query(db, job, lease.as_claim(), {
            'lease_expires_at': (_now() + timedelta(seconds=max(1, ttl_seconds))).isoformat()}).execute().data)
    except LeaseError:
        return False

@atomic
def expire_lease(db: Any, job: dict, *, ttl_seconds: int = DEFAULT_TTL_SECONDS) -> bool:
    if is_postgres(db):
        return bool(_remote(db, 'expire', job['id'], claim=job, ttl=ttl_seconds).get('job'))
    current = _get(db, job.get('id'))
    if not current or current.get('status') not in RENEWABLE or not is_expired(current, ttl_seconds=ttl_seconds):
        return False
    if any(current.get(k) != job.get(k) for k in ('lease_epoch', 'lease_id', 'lease_expires_at', 'started_at')):
        return False
    return bool(db.table('agent_jobs').update({'status': PENDING,
        'leased_by_instance_id': None, 'lease_id': None, 'started_at': None,
        'lease_expires_at': None, 'lease_epoch': int(current.get('lease_epoch') or 0) + 1
    }).eq('id', job['id']).eq('status', current['status']).execute().data)

@attempt_boundary
@atomic
def fenced_update(db: Any, job_id: str, claim: dict, updates: dict, *, allowed_from: Iterable[str] | None = None) -> dict:
    if set(updates) - {'status', 'output_payload', 'error_message'}:
        raise LeaseError('worker update contains protected fields')
    if is_postgres(db):
        return _remote(db, 'update', job_id, claim.get('agent_instance_id', ''), claim, updates)['job']
    if claim.get('lease_id') and updates.get('status') in {COMPLETED, FAILED}:
        receipts = db.table('mco_attempt_receipts').select('*').eq('id', claim['lease_id']).execute().data
        if receipts:
            receipt = receipts[0]
            if receipt['claim'] == claim and receipt['updates'] == updates and receipt['job']['id'] == job_id:
                return {**receipt['job'], '_replayed': True}
            raise LeaseError('attempt already reported a different result')
    job = _get(db, job_id)
    if not job:
        raise LeaseError('job not found')
    current, target = job.get('status'), updates.get('status', job.get('status'))
    if current in TERMINAL:
        raise LeaseError('job already reached a terminal state')
    if target not in WORKER_TRANSITIONS.get(current, ()):
        raise LeaseError(f'illegal transition {current} -> {target}')
    if allowed_from is not None and current not in set(allowed_from):
        raise LeaseError('illegal source state')
    if is_expired(job) or _paused(db):
        raise LeaseError('stale lease: expired or halted')
    rows = _claim_query(db, job, claim, updates).execute().data
    if not rows:
        raise LeaseError('stale lease: this attempt no longer owns the job')
    if claim.get('lease_id') and target in {COMPLETED, FAILED}:
        db.table('mco_attempt_receipts').insert({'id': claim['lease_id'], 'claim': claim,
            'updates': updates, 'job': rows[0]}).execute()
    return rows[0]

@atomic
def set_paused(db: Any, paused: bool, actor: dict | None = None) -> list[dict]:
    """Commit admission barrier and halt together, serialized with acquisition."""
    if is_postgres(db):
        return _remote(db, 'pause' if paused else 'resume', updates=actor or {}).get('halted', [])
    db.table('mco_control').upsert({'id': 'governance', 'paused': bool(paused)}).execute()
    halted = []
    if paused:
        rows = db.table('agent_jobs').select('*').in_('status', list(RENEWABLE)).execute().data
        for job in rows:
            halted.extend(db.table('agent_jobs').update({'status': HALTED,
                'lease_epoch': int(job.get('lease_epoch') or 0) + 1,
                'error_message': 'Halted by operator kill switch'}).eq('id', job['id']).eq('status', job['status']).execute().data)
    return halted

def idempotency_key(job_id: str, attempt: Any) -> str:
    return f'bitcadence:{job_id}:{int(attempt or 0)}'
