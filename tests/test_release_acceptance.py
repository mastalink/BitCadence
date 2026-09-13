"""Executable acceptance probes against real stores, never FakeDB."""
import asyncio
import json
import os
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from mco.localstore import LocalStore
from mco.orchestrator import audit, leases, routes
from mco.orchestrator.auth import require_agent
from mco.orchestrator.admin_routes import settings_router
from mco.orchestrator.handlers import handle_job_update
from tests.test_admin_routes import FakeConfig

@pytest.fixture(params=['sqlite', 'postgres'])
def db(request, tmp_path):
    if request.param == 'postgres':
        url = os.environ.get('BC_TEST_POSTGREST_URL')
        if not url:
            pytest.skip('Set BC_TEST_POSTGREST_URL to run the real PostgreSQL seam')
        from postgrest import SyncPostgrestClient
        with SyncPostgrestClient(url) as client:
            leases.set_paused(client, False)
            yield client
    else:
        store = LocalStore(tmp_path / 'acceptance.db')
        yield store
        store.close()


def job(db, **extra):
    return db.table('agent_jobs').insert({'id': str(uuid.uuid4()), 'title': 'Acceptance task',
        'target_agent_role': 'test', 'status': 'pending', **extra}).execute().data[0]


def read(db, jid):
    return db.table('agent_jobs').select('*').eq('id', jid).execute().data[0]

@pytest.mark.parametrize('field,value', [('lease_epoch', 99999), ('lease_incarnation', 'forged'),
                                        ('agent_instance_id', 'other-worker')])
def test_entire_claim_is_enforced(db, field, value):
    j = job(db)
    lease = leases.acquire_lease(db, j['id'], 'worker')
    claim = {**lease.as_claim(), field: value}
    with pytest.raises(leases.LeaseError):
        leases.fenced_update(db, j['id'], claim, {'status': 'completed'})
    assert read(db, j['id'])['status'] == 'leased'


def test_expired_lease_cannot_renew_or_finish_before_reaper(db):
    j = job(db); lease = leases.acquire_lease(db, j['id'], 'worker')
    db.table('agent_jobs').update({'lease_expires_at': '2000-01-01T00:00:00+00:00'}).eq('id', j['id']).execute()
    assert not leases.renew_lease(db, lease)
    with pytest.raises(leases.LeaseError):
        leases.fenced_update(db, j['id'], lease.as_claim(), {'status': 'completed'})


def test_renewed_lease_survives_stale_reaper_snapshot(db):
    j = job(db); lease = leases.acquire_lease(db, j['id'], 'worker')
    snapshot = {**read(db,j['id']), 'lease_expires_at': '2000-01-01T00:00:00+00:00'}
    assert leases.renew_lease(db, lease)
    assert not leases.expire_lease(db, snapshot)
    assert read(db, j['id'])['status'] == 'leased'


def test_pause_fences_active_work_and_requires_manual_retry(db):
    j = job(db); lease = leases.acquire_lease(db, j['id'], 'worker')
    leases.fenced_update(db,j['id'],lease.as_claim(),{'status':'in_progress'})
    halted = leases.set_paused(db, True)
    assert j['id'] in [r['id'] for r in halted]
    assert not leases.renew_lease(db, lease)
    with pytest.raises(leases.LeaseError):
        leases.fenced_update(db,j['id'],lease.as_claim(),{'status':'completed'})
    waiting = job(db)
    assert leases.acquire_lease(db,waiting['id'],'worker') is None
    leases.set_paused(db, False)
    assert read(db,j['id'])['status'] == 'halted'


def test_replayed_response_is_idempotent_but_changed_result_is_rejected(db):
    j=job(db); lease=leases.acquire_lease(db,j['id'],'worker')
    payload={'status':'completed','output_payload':{'result':'once'}}
    leases.fenced_update(db,j['id'],lease.as_claim(),payload)
    assert leases.fenced_update(db,j['id'],lease.as_claim(),payload)['_replayed']
    with pytest.raises(leases.LeaseError):
        leases.fenced_update(db,j['id'],lease.as_claim(),{'status':'completed','output_payload':{'result':'twice'}})
    assert read(db,j['id'])['output_payload']['result']=='once'


def test_restore_rotation_invalidates_a_snapshot_containing_the_old_lease(db):
    j=job(db); lease=leases.acquire_lease(db,j['id'],'worker')
    leases.rotate_incarnation(db)
    with pytest.raises(leases.LeaseError):
        leases.fenced_update(db,j['id'],lease.as_claim(),{'status':'completed'})
    assert not leases.renew_lease(db,lease)


def test_outbox_survives_failed_event_delivery_and_retries_once(db, monkeypatch):
    j=job(db)
    assert db.table('mco_audit_outbox').select('*').eq('job_id',j['id']).execute().data
    original=audit.record_event
    monkeypatch.setattr(audit,'record_event',lambda *a,**k: (_ for _ in ()).throw(RuntimeError('offline')))
    with pytest.raises(RuntimeError):
        audit.drain_outbox(db)
    assert read(db,j['id'])['status']=='pending'
    monkeypatch.setattr(audit,'record_event',original)
    audit.drain_outbox(db); before=audit.get_events(db,j['id'])
    audit.drain_outbox(db)
    assert before==audit.get_events(db,j['id'])
    assert audit.verify_chain(db,j['id'])['ok']


def test_http_worker_proof_and_cancel_race(db, monkeypatch):
    monkeypatch.setattr(routes,'get_db_client',lambda: db)
    monkeypatch.setattr(routes,'kill_switch_active',lambda:False)
    import mco.notifiers.ntfy as ntfy
    monkeypatch.setattr(ntfy,'notify',lambda *a,**k:False)
    app=FastAPI();app.include_router(routes.router)
    actor={'instance_id':'worker','role':'test','org_id':'default'}
    app.dependency_overrides[require_agent]=lambda:actor
    client=TestClient(app)
    j=job(db)
    result=client.post('/api/jobs/lease',json={'task_id':j['id'],'agent_instance_id':'worker'})
    assert result.status_code==200, result.text
    proof=result.json()['lease']
    assert client.put('/api/jobs/'+j['id'],json={'status':'completed'}).status_code==409
    assert client.post('/api/jobs/'+j['id']+'/renew',json=proof).status_code==200
    actor={'instance_id':'operator','role':'admin','org_id':'default'}
    assert client.post('/api/jobs/'+j['id']+'/cancel').status_code==200
    actor={'instance_id':'worker','role':'test','org_id':'default'}
    assert client.put('/api/jobs/'+j['id'],json={**proof,'status':'completed'}).status_code==409
    assert read(db,j['id'])['status']=='cancelled'


def test_handler_fences_stale_claim_without_unlocking_children(db):
    j=job(db); child=job(db,status='waiting',depends_on=[j['id']])
    lease=leases.acquire_lease(db,j['id'],'worker')
    error,ack,broadcast=AsyncMock(),AsyncMock(),AsyncMock()
    asyncio.run(handle_job_update(db, {'task_id':j['id'],'status':'completed',
        **lease.as_claim(),'lease_epoch':999}, 'test',error,ack,broadcast,
        actor={'instance_id':'worker','role':'test'}))
    error.assert_called_once();ack.assert_not_called();broadcast.assert_not_called()
    assert read(db,child['id'])['status']=='waiting'


def test_sqlite_multiple_connections_race_exactly_one_wins(tmp_path):
    path=tmp_path/'race.db'
    stores=[LocalStore(path) for _ in range(6)]
    try:
        j=job(stores[0])
        with ThreadPoolExecutor(6) as pool:
            won=list(pool.map(lambda pair: leases.acquire_lease(pair[1],j['id'],f'worker-{pair[0]}'),enumerate(stores)))
        assert sum(x is not None for x in won)==1
        stores[0].table('agent_jobs').update({'lease_expires_at':'2000-01-01T00:00:00+00:00'}).eq('id',j['id']).execute()
        snapshot=read(stores[0],j['id'])
        with ThreadPoolExecutor(6) as pool:
            expired=list(pool.map(lambda db:leases.expire_lease(db,snapshot),stores))
        assert sum(expired)==1
        with ThreadPoolExecutor(6) as pool:
            list(pool.map(lambda pair:audit.record_event(pair[1],j['id'],f'event-{pair[0]}'),enumerate(stores)))
        assert audit.verify_chain(stores[0],j['id'])['ok']
    finally:
        for store in stores:store.close()


def test_sqlite_state_rolls_back_if_outbox_insert_fails(tmp_path,monkeypatch):
    db=LocalStore(tmp_path/'rollback.db'); original=db._write_row
    def broken(table,row):
        if table=='mco_audit_outbox':raise RuntimeError('disk failed')
        return original(table,row)
    monkeypatch.setattr(db,'_write_row',broken)
    with pytest.raises(RuntimeError):job(db)
    assert db.table('agent_jobs').select('*').execute().data==[]
    db.close()


def test_signed_external_checkpoint_detects_deleted_tail(tmp_path,monkeypatch):
    db=LocalStore(tmp_path/'checkpoint.db')
    monkeypatch.setattr(audit,'_hmac_key_cache',b'test-only-signing-key')
    audit.record_event(db,'job','first');audit.record_event(db,'job','second')
    checkpoint=audit.make_checkpoint(db,'job')
    assert audit.verify_chain(db,'job',checkpoint)['ok']
    tail=audit.get_events(db,'job')[-1]
    db._conn.execute('DELETE FROM agent_job_events WHERE pk=?',(tail['id'],));db._conn.commit()
    assert not audit.verify_chain(db,'job',checkpoint)['ok']
    db.close()


def test_readiness_keeps_dead_fleet_accessible_and_rejects_dead_store(tmp_path,monkeypatch):
    from mco.cli import create_app
    db=LocalStore(tmp_path/'health.db');monkeypatch.setattr(routes,'get_db_client',lambda:db)
    client=TestClient(create_app())
    body=client.get('/readyz')
    assert body.status_code==200 and body.json()['checks']['fleet']['degraded']
    db.close()
    assert client.get('/readyz').status_code==503
    assert client.get('/healthz').status_code==200


def test_ntfy_cannot_override_configured_destination(monkeypatch):
    import mco.notifiers.ntfy as ntfy
    calls=[]
    monkeypatch.setattr(ntfy,'get_config',lambda:FakeConfig(NTFY_TOPIC='private-unpredictable',NTFY_SERVER='https://push.example'))
    class Response:
        def raise_for_status(self):pass
    monkeypatch.setattr(ntfy.requests,'post',lambda url,**kw:(calls.append(url) or Response()))
    ntfy.notify_job_created('j','title','codex');ntfy.notify_job_leased('j','a','codex')
    ntfy.notify_job_completed('j','completed','codex');ntfy.notify_job_failed('j','x','codex')
    ntfy.notify_job_needs_approval('j','title','codex');ntfy.notify_job_escalated('j','title','codex','x')
    ntfy.notify_force_pull('codex');ntfy.notify('x',topic='public',server='https://other.example')
    assert len(calls)==8 and set(calls)=={'https://push.example/private-unpredictable'}
    monkeypatch.setattr(ntfy,'get_config',lambda:FakeConfig())
    assert ntfy.notify('x',topic='public') is False
