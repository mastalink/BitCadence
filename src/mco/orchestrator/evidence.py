"""Optional synchronous off-box acknowledgement for governed cloud work.

The database remains the source of truth. A failed mirror does not undo its
commit: callers retry the same attempt, and no successful response is issued.
"""
import os
from datetime import datetime, timedelta, timezone
from functools import wraps
from collections import OrderedDict
import threading
import time

_acknowledged = OrderedDict()
_lock = threading.Lock()


def required():
    return os.environ.get('MCO_EVIDENCE_ACK_REQUIRED', '').lower() in {'1', 'true', 'yes'}


def _sink():
    import boto3
    from botocore.config import Config
    return boto3.client('s3', config=Config(connect_timeout=3, read_timeout=5,
        retries={'max_attempts': 2, 'mode': 'standard'}))


def publish_events(db, job_id):
    if not required():
        return
    from mco.orchestrator.audit import get_events, verify_chain
    import json
    bucket = os.environ.get('MCO_EVIDENCE_BUCKET')
    if not bucket:
        raise RuntimeError('Off-box acknowledgement requires MCO_EVIDENCE_BUCKET')
    if not verify_chain(db, str(job_id))['ok']:
        raise RuntimeError('Refusing to acknowledge a broken audit chain')
    sink = None
    retain_until = datetime.now(timezone.utc) + timedelta(
        days=max(1, int(os.environ.get('MCO_EVIDENCE_RETENTION_DAYS', '365'))))
    for row in get_events(db, str(job_id)):
        receipt = (bucket, str(job_id), row['id'], row.get('hash'), row.get('signature'))
        with _lock:
            if _acknowledged.get(receipt, 0) > time.monotonic():
                _acknowledged.move_to_end(receipt)
                continue
        if sink is None:
            sink = _sink()
        # Versioned, locked writes are safe to repeat after a lost response.
        # No DB receipt is trusted: a restored snapshot may contain stale ones.
        response = sink.put_object(Bucket=bucket,
            Key=f"ledger/{job_id}/{row['id']}.json",
            Body=json.dumps(row, default=str, sort_keys=True, separators=(',', ':')).encode(),
            ContentType='application/json', ObjectLockMode='COMPLIANCE',
            ObjectLockRetainUntilDate=retain_until)
        if not response.get('VersionId'):
            raise RuntimeError('Evidence sink did not acknowledge a locked object version')
        with _lock:
            # Process-local receipts cannot survive a snapshot restore. Bound
            # memory and expire well before the minimum one-day retention.
            _acknowledged[receipt] = time.monotonic() + 3600
            while len(_acknowledged) > 10000:
                _acknowledged.popitem(last=False)


def acknowledge(db, job_id):
    if required():
        from mco.orchestrator.audit import drain_outbox
        drain_outbox(db, job_id=str(job_id))
        publish_events(db, str(job_id))


def attempt_boundary(func):
    @wraps(func)
    def wrapped(db, job_or_lease, *args, **kwargs):
        result = func(db, job_or_lease, *args, **kwargs)
        if result:
            job_id = getattr(job_or_lease, 'job_id', job_or_lease)
            acknowledge(db, job_id)
        return result
    return wrapped
