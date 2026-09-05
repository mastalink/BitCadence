from unittest.mock import Mock

import pytest

from mco.localstore import LocalStore
from mco.orchestrator import audit, evidence, leases


@pytest.fixture
def cloud_store(tmp_path, monkeypatch):
    monkeypatch.setenv('MCO_EVIDENCE_ACK_REQUIRED', 'true')
    monkeypatch.setenv('MCO_EVIDENCE_BUCKET', 'test-evidence')
    monkeypatch.setattr(evidence, '_acknowledged', evidence.OrderedDict())
    sink = Mock()
    sink.put_object.return_value = {'VersionId':'locked-version'}
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
    sink.put_object.side_effect = None
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
