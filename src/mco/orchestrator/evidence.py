"""Optional synchronous off-box acknowledgement for governed cloud work.

The database remains the source of truth. A failed mirror does not undo its
commit: callers retry the same attempt, and no successful response is issued.
"""
import os
import json
import hmac
from datetime import datetime, timedelta, timezone
from functools import wraps
import threading
import weakref

_lock = threading.Lock()
_publish_locks = weakref.WeakValueDictionary()


def _publish_lock(bucket, job_id):
    """Serialize one job without blocking unrelated jobs on its S3 latency."""
    with _lock:
        scope = (bucket, str(job_id))
        lock = _publish_locks.get(scope)
        if lock is None:
            lock = threading.Lock()
            _publish_locks[scope] = lock
        return lock


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
    from mco.orchestrator.audit import get_events, verify_chain, _audit_hmac_key, _canonical, _sign
    bucket = os.environ.get('MCO_EVIDENCE_BUCKET')
    if not bucket:
        raise RuntimeError('Off-box acknowledgement requires MCO_EVIDENCE_BUCKET')
    days = int(os.environ.get('MCO_EVIDENCE_RETENTION_DAYS', '1'))
    if days < 1:
        raise ValueError('MCO_EVIDENCE_RETENTION_DAYS must be at least 1')
    # Serialize local publishers. Across processes a late checkpoint may regress
    # the offset, which only causes harmless repeat uploads, never skipped events.
    with _publish_lock(bucket, job_id):
        events = get_events(db, str(job_id))
        if not verify_chain(db, str(job_id), events=events)['ok']:
            raise RuntimeError('Refusing to acknowledge a broken audit chain')
        if not events:
            return
        sink = _sink()
        now = datetime.now(timezone.utc)
        retain_until = now + timedelta(days=days)
        event_retain_until = retain_until
        checkpoint_key = f'ledger/{job_id}/acknowledged.json'
        offset = 0
        key = _audit_hmac_key()
        try:
            response = sink.get_object(Bucket=bucket, Key=checkpoint_key)
        except Exception as exc:
            code = getattr(exc, 'response', {}).get('Error', {}).get('Code')
            if code not in {'NoSuchKey', '404'}:
                raise
        else:
            receipt = json.loads(response['Body'].read())
            signature = receipt.pop('signature', None)
            # The receipt lives in the same protected S3 sink as the evidence,
            # outside database backups. Signed installations authenticate it too.
            if key and not hmac.compare_digest(signature or '', _sign(_canonical(receipt), key) or ''):
                raise RuntimeError('Evidence checkpoint signature does not verify')
            count = receipt.get('count')
            if (receipt.get('bucket') != bucket or receipt.get('job_id') != str(job_id)
                    or type(count) is not int or count < 1 or count > len(events)
                    or events[count - 1]['hash'] != receipt.get('head_hash')):
                raise RuntimeError('Evidence checkpoint differs from audit history; investigate restore or truncation')
            expiry = datetime.fromisoformat(receipt['retain_until'])
            # Refresh the whole prefix before its retention expires. Never trust
            # an unlocked receipt or extend older versions by advancing an offset.
            locked_until = response.get('ObjectLockRetainUntilDate')
            if (response.get('VersionId') and response.get('ObjectLockMode') == 'COMPLIANCE'
                    and locked_until and min(expiry, locked_until) > now + timedelta(minutes=5)):
                offset = count
                retain_until = min(retain_until, expiry, locked_until)
        if offset == len(events):
            return
        for row in events[offset:]:
            _put_locked(sink, bucket, f"ledger/{job_id}/{row['id']}.json", row, event_retain_until)
        receipt = {'bucket': bucket, 'job_id': str(job_id), 'count': len(events),
                   'head_hash': events[-1]['hash'], 'retain_until': retain_until.isoformat()}
        receipt['signature'] = _sign(_canonical(receipt), key)
        # Publish the offset only after every preceding PUT acknowledged a version.
        # A lost response safely repeats writes; there is no local persistent cache.
        _put_locked(sink, bucket, checkpoint_key, receipt, retain_until)


def _put_locked(sink, bucket, path, body, retain_until):
    response = sink.put_object(Bucket=bucket, Key=path,
        Body=json.dumps(body, default=str, sort_keys=True, separators=(',', ':')).encode(),
        ContentType='application/json', ObjectLockMode='COMPLIANCE',
        ObjectLockRetainUntilDate=retain_until)
    if not response.get('VersionId'):
        raise RuntimeError('Evidence sink did not acknowledge a locked object version')


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
