from unittest.mock import Mock
from io import BytesIO
from datetime import datetime, timedelta, timezone
import json

import pytest

from mco.localstore import LocalStore
from mco.orchestrator import audit, evidence, leases


@pytest.fixture
def cloud_store(tmp_path, monkeypatch):
    monkeypatch.setenv('MCO_EVIDENCE_ACK_REQUIRED', 'true')
    monkeypatch.setenv('MCO_EVIDENCE_BUCKET', 'test-evidence')
    monkeypatch.setattr(audit, '_hmac_key_cache', b'test-signing-key')
    objects = {}
    sink = Mock()
    def put(**kwargs):
        objects[kwargs['Key']] = kwargs
        return {'VersionId': 'locked-version'}
    class Missing(Exception):
        response = {'Error': {'Code': 'NoSuchKey'}}
    def get(**kwargs):
        if kwargs['Key'] not in objects:
            raise Missing()
        item = objects[kwargs['Key']]
        return {**item, 'Body': BytesIO(item['Body']), 'VersionId': 'locked-version'}
    sink.put_object.side_effect = put
    sink.get_object.side_effect = get
    sink.objects = objects
    sink.save = put
    monkeypatch.setattr(evidence, '_sink', lambda: sink)
    with_store = LocalStore(tmp_path/'ack.db')
    with_store.table('agent_jobs').insert({'id':'job', 'title':'test', 'status':'pending'}).execute()
    yield with_store, sink
    with_store.close()


def test_no_lease_ack_when_offbox_is_unavailable(cloud_store):
    db, sink = cloud_store
    sink.put_object.side_effect = OSError('sink offline')
    with pytest.raises(OSError): leases.acquire_lease(db,'job','worker')
    # State evidence survives; the caller received no usable success/proof.
    assert db.table('mco_audit_outbox').select('*').execute().data


def test_result_retry_reaches_offbox_before_acknowledgement(cloud_store):
    db, sink = cloud_store
    claim = leases.acquire_lease(db,'job','worker').as_claim()
    sink.put_object.side_effect = OSError('response lost')
    with pytest.raises(OSError): leases.fenced_update(db,'job',claim,{'status':'completed'})
    sink.put_object.side_effect = sink.save
    result = leases.fenced_update(db,'job',claim,{'status':'completed'})
    assert result['_replayed']
    assert audit.verify_chain(db,'job')['ok']
    for call in sink.put_object.call_args_list:
        assert call.kwargs['ObjectLockMode'] == 'COMPLIANCE'
        assert call.kwargs['Key'].startswith('ledger/job/')
    calls = sink.put_object.call_count
    evidence.acknowledge(db,'job')
    assert sink.put_object.call_count == calls


def test_unversioned_sink_cannot_acknowledge_work(cloud_store):
    db, sink = cloud_store
    sink.put_object.return_value = {}
    sink.put_object.side_effect = None
    with pytest.raises(RuntimeError, match='locked object version'):
        leases.acquire_lease(db,'job','worker')


def test_emergency_stop_fences_even_when_sink_is_down(cloud_store, monkeypatch):
    import asyncio
    from mco.orchestrator import admin_routes
    from tests.test_admin_routes import FakeConfig
    db, sink = cloud_store
    claim = leases.acquire_lease(db,'job','worker')
    monkeypatch.setattr(admin_routes, '_db', lambda: db)
    monkeypatch.setattr(admin_routes, 'get_config', lambda: FakeConfig())
    sink.put_object.side_effect = OSError('offline')
    with pytest.raises(OSError):
        asyncio.run(admin_routes.put_settings({'MCO_KILL_SWITCH':True},
            {'instance_id':'operator','role':'admin','org_id':'default'}))
    assert db.table('agent_jobs').select('*').eq('id','job').execute().data[0]['status'] == 'halted'
    assert not leases.renew_lease(db,claim)


def test_durable_offset_uploads_only_new_events_and_short_retention(cloud_store):
    db, sink = cloud_store
    audit.record_event(db, 'job', 'first')
    first_calls = sink.put_object.call_count
    # No process-local acknowledgement state exists: a newly constructed sink
    # reads the remote receipt, just as a new gateway process would.
    evidence.publish_events(db, 'job')
    assert sink.put_object.call_count == first_calls
    audit.record_event(db, 'job', 'second')
    assert sink.put_object.call_count == first_calls + 2  # new event plus offset
    for item in sink.objects.values():
        remaining = item['ObjectLockRetainUntilDate'] - datetime.now(timezone.utc)
        assert timedelta(hours=23) < remaining <= timedelta(days=1)


def test_remote_offset_detects_restored_missing_tail(cloud_store):
    db, sink = cloud_store
    audit.record_event(db, 'job', 'first')
    audit.record_event(db, 'job', 'second')
    tail = audit.get_events(db, 'job')[-1]
    db._conn.execute('DELETE FROM agent_job_events WHERE pk=?', (tail['id'],))
    db._conn.commit()
    with pytest.raises(RuntimeError, match='restore or truncation'):
        evidence.publish_events(db, 'job')


def test_tampered_offset_fails_closed(cloud_store):
    db, sink = cloud_store
    audit.record_event(db, 'job', 'first')
    obj = sink.objects['ledger/job/acknowledged.json']
    body = json.loads(obj['Body'])
    body['count'] = 900
    obj['Body'] = json.dumps(body).encode()
    with pytest.raises(RuntimeError, match='signature'):
        evidence.publish_events(db, 'job')


def test_expired_retention_republishes_history(cloud_store):
    db, sink = cloud_store
    audit.record_event(db, 'job', 'first')
    before = sink.put_object.call_count
    sink.objects['ledger/job/acknowledged.json']['ObjectLockRetainUntilDate'] = datetime.now(timezone.utc) - timedelta(seconds=1)
    evidence.publish_events(db, 'job')
    assert sink.put_object.call_count == before + 2


def test_broken_prefix_cannot_hide_behind_offset(cloud_store):
    db, sink = cloud_store
    audit.record_event(db, 'job', 'first')
    row = audit.get_events(db, 'job')[0]
    row['detail'] = {'tampered': True}
    db._conn.execute('UPDATE agent_job_events SET data=? WHERE pk=?', (json.dumps(row), row['id']))
    db._conn.commit()
    with pytest.raises(RuntimeError, match='broken audit chain'):
        evidence.publish_events(db, 'job')
