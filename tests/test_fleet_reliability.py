from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from mco.localstore import LocalStore
from mco.orchestrator import routes, delivery, presence
from mco.orchestrator.handlers import handle_job_update
from mco.orchestrator.leases import acquire_lease


@pytest.fixture
def db(tmp_path):
    store = LocalStore(tmp_path / 'reliability.db')
    yield store
    store.close()


@pytest.fixture(autouse=True)
def approvers(monkeypatch):
    monkeypatch.setattr(routes.utils_mod, 'get_approver_roles', lambda: {'owner', 'admin'})


def job(db, id='job', **kw):
    row = {'id': id, 'org_id': 'default', 'status': 'pending',
           'created_at': (datetime.now(timezone.utc) - timedelta(days=20)).isoformat(),
           'target_agent_role': 'grok', 'target_agent_id': 'grok-beast', **kw}
    db.table('agent_jobs').insert(row).execute()
    return row


def row(db, id='job'):
    return db.table('agent_jobs').select('*').eq('id', id).execute().data[0]


def test_expiry_skips_live_parent_and_score_graphs(db):
    from mco.orchestrator.stale import expire_waiting
    job(db, 'failed', status='failed')
    job(db, 'active', status='in_progress', leased_by_instance_id='grok-beast')
    job(db, 'dead', status='waiting', depends_on=['failed'])
    job(db, 'missing', status='waiting', depends_on=['absent'])
    job(db, 'live', status='waiting', depends_on=['active'])
    job(db, 'score', status='waiting', depends_on=['failed'], input_payload={'score': {'protocol': 'score-v1'}})
    assert {j['id'] for j in expire_waiting(db)} == {'dead', 'missing'}
    assert row(db, 'dead')['status'] == 'halted'
    assert row(db, 'live')['status'] == row(db, 'score')['status'] == 'waiting'
    assert expire_waiting(db) == []


@pytest.mark.asyncio
async def test_cancel_stale_requires_recent_preview_and_approver(db, monkeypatch):
    job(db)
    monkeypatch.setattr(routes, 'get_db_client', lambda: db)
    owner = {'instance_id': 'owner', 'role': 'owner', 'org_id': 'default'}
    with pytest.raises(HTTPException) as e:
        await routes.cancel_stale_jobs({}, {'instance_id': 'worker', 'role': 'grok'})
    assert e.value.status_code == 403
    with pytest.raises(HTTPException):
        await routes.cancel_stale_jobs({'dry_run': False}, owner)
    preview = await routes.cancel_stale_jobs({}, owner)
    assert [j['id'] for j in preview['jobs']] == ['job']
    assert row(db)['status'] == 'pending'
    # A newly leased job must survive a previously approved stale preview.
    acquire_lease(db, 'job', 'grok-beast')
    result = await routes.cancel_stale_jobs({'dry_run': False, 'preview_id': preview['preview_id']}, owner)
    assert result['cancelled'] == []
    assert row(db)['status'] == 'leased'


@pytest.mark.asyncio
async def test_cancel_stale_only_previewed_org_and_single_use(db, monkeypatch):
    job(db)
    job(db, 'other', org_id='other')
    monkeypatch.setattr(routes, 'get_db_client', lambda: db)
    owner = {'instance_id': 'owner', 'role': 'owner', 'org_id': 'default'}
    preview = await routes.cancel_stale_jobs({}, owner)
    job(db, 'later')
    result = await routes.cancel_stale_jobs({'dry_run': False, 'preview_id': preview['preview_id']}, owner)
    assert result['cancelled'] == ['job']
    assert row(db, 'other')['status'] == row(db, 'later')['status'] == 'pending'
    with pytest.raises(HTTPException):
        await routes.cancel_stale_jobs({'dry_run': False, 'preview_id': preview['preview_id']}, owner)


def test_capability_constraints_prevent_unsafe_fallback(db):
    job(db, input_payload={'routing': {'required_capabilities': ['gpu-render'], 'allowed_roles': ['grok']}})
    cfg = {'MCO_ROUTE_FALLBACKS': 'grok:claude', 'MCO_DELIVERY_STALL_SECONDS': '10'}
    delivery.sweep(db, config=cfg)
    result = delivery.sweep(db, config=cfg, online_instances=lambda *_: [('claude', 'claude-beast')])
    assert result.rerouted == []
    assert row(db)['target_agent_role'] == 'grok'


def test_capability_match_is_per_instance_and_admission_uses_it(db, monkeypatch):
    from mco.orchestrator.worker_policy import can_run
    payload = {'routing': {'required_capabilities': ['gpu-render']}}
    j = job(db, input_payload=payload)
    cfg = {'MCO_WORKER_CAPABILITIES': '{"grok-beast":["gpu-render"]}'}
    assert can_run(j, 'grok', 'grok-beast', cfg)
    assert not can_run(j, 'claude', 'claude-beast', cfg)
    monkeypatch.setattr(routes, 'get_config', lambda: {})
    assert routes._pending_for_agent(db, 'grok', 'grok-beast', {'org_id': 'default'}) == []


@pytest.mark.asyncio
async def test_quota_failure_defers_job_without_retry_or_failure_escalation(db):
    job(db, max_retries=0)
    proof = acquire_lease(db, 'job', 'grok-beast')
    err, ack, broadcast = AsyncMock(), AsyncMock(), AsyncMock()
    payload = {'task_id': 'job', 'status': 'failed', 'error_message': 'HTTP 402: out of credits', **proof.as_claim()}
    await handle_job_update(db, payload, 'quota', err, ack, broadcast, actor={'instance_id': 'grok-beast', 'role': 'grok'})
    err.assert_not_called()
    assert row(db)['status'] == 'pending'
    assert not row(db).get('retry_count')
    assert not row(db).get('lease_id')
    worker = {'instance_id': 'grok-beast', 'role': 'grok', 'status': 'online'}
    assert presence.describe_fleet(db, [worker], threshold=300, connected={'grok-beast'}, now=datetime.now(timezone.utc))[0]['state'] == 'out_of_quota'
    assert routes._pending_for_agent(db, 'grok', 'grok-beast', {'org_id': 'default'}) == []
    await handle_job_update(db, payload, 'quota', err, ack, broadcast, actor={'instance_id': 'grok-beast', 'role': 'grok'})
    err.assert_not_called()
    assert row(db)['status'] == 'pending'


def test_untaken_work_does_not_prove_broken(db):
    job(db)
    a = {'instance_id': 'grok-beast', 'role': 'grok', 'status': 'online'}
    r = presence.describe_fleet(db, [a], threshold=300, connected={'grok-beast'}, now=datetime.now(timezone.utc))[0]
    assert r['state'] == 'standby'
    assert 'untaken' in r['state_reason']


def test_board_limit_applies_after_archived_filter(db, monkeypatch):
    job(db, 'active')
    for i in range(3):
        job(db, str(i), archived=True, created_at=datetime.now(timezone.utc).isoformat())
    monkeypatch.setattr(routes, 'get_db_client', lambda: db)
    assert [j['id'] for j in routes.get_jobs(limit=2, agent={'org_id': 'default'})] == ['active']


def test_wake_single_instance_lock_releases_after_owner_exits(tmp_path):
    from mco.waker import worker_lock
    from filelock import Timeout
    with worker_lock('grok', 'grok-beast', directory=tmp_path):
        with pytest.raises(Timeout):
            with worker_lock('grok', 'grok-beast', directory=tmp_path):
                pass
        with worker_lock('grok', 'grok-studio', directory=tmp_path):
            pass
    with worker_lock('grok', 'grok-beast', directory=tmp_path):
        pass


def test_actual_worker_fault_is_broken(db):
    a = {'instance_id': 'grok-beast', 'role': 'grok', 'status': 'broken'}
    assert presence.describe_fleet(db, [a], threshold=300, connected={'grok-beast'}, now=datetime.now(timezone.utc))[0]['state'] == 'broken'


def test_quota_has_plain_helper_word():
    from mco.helpers import health, doing
    h = {'instance_id': 'grok-beast', 'state': 'out_of_quota'}
    assert health(h)['word'] == 'Out of quota'
    assert 'quota' in doing(h)


def test_console_paginates_and_has_preview_before_cleanup():
    from pathlib import Path
    root = Path(__file__).parents[1] / 'src/mco/console_src'
    store = (root / '47e66145-9c4d-41a1-acbb-42b12848f160.js').read_text(encoding='utf-8')
    board = (root / '5adac14f-6645-4e02-866b-22c4e571989b.js').read_text(encoding='utf-8')
    assert 'api("/api/jobs")' in store and '/api/jobs?limit=200' not in store
    assert '/api/jobs/cancel-stale' in store
    assert 'Preview stale jobs' in board and 'Cancel stale jobs' in board
    assert 'preview_id' in store


def test_board_pages_storage_without_losing_old_active_work(db, monkeypatch):
    for i in range(505):
        job(db, f'j{i:04}', archived=i < 501)
    monkeypatch.setattr(routes, 'get_db_client', lambda: db)
    all_rows = routes.get_jobs(agent={'org_id': 'default'})
    assert len(all_rows) == 4
    assert routes.get_jobs(limit=2, offset=2, agent={'org_id': 'default'}) == all_rows[2:4]


def test_expired_dependencies_stay_available_for_reviewed_cleanup(db):
    from mco.orchestrator.stale import expire_waiting, stale_candidates
    job(db, status='waiting', depends_on=['missing'])
    expire_waiting(db)
    assert [j['id'] for j in stale_candidates(db, org='default')] == ['job']


def test_capable_fallback_is_pinned_to_the_matching_instance(db):
    job(db, input_payload={'routing': {'required_capabilities': ['gpu-render']}})
    cfg = {'MCO_ROUTE_FALLBACKS': 'grok:claude', 'MCO_DELIVERY_STALL_SECONDS': '10',
           'MCO_WORKER_CAPABILITIES': '{"claude-beast":["gpu-render"]}'}
    delivery.sweep(db, config=cfg)
    result = delivery.sweep(db, config=cfg, online_instances=lambda *_: [('claude', 'claude-beast')])
    assert result.rerouted == ['job']
    assert row(db)['target_agent_id'] == 'claude-beast'


def test_malformed_requirements_fail_closed_for_unpinned_fallback(db):
    job(db, target_agent_id=None, input_payload={'routing': []})
    cfg = {'MCO_ROUTE_FALLBACKS': 'grok:claude', 'MCO_DELIVERY_STALL_SECONDS': '10'}
    delivery.sweep(db, config=cfg)
    result = delivery.sweep(db, config=cfg, online_roles=lambda *_: {'claude'},
                            online_instances=lambda *_: [('claude', 'claude-beast')])
    assert result.rerouted == []


def test_quota_cooldown_expires_automatically(db):
    from mco.orchestrator.audit import record_event
    from mco.orchestrator.worker_policy import quota_workers
    record_event(db, 'job', 'worker_out_of_quota', 'grok-beast', 'grok')
    assert 'grok-beast' in quota_workers(db)
    assert 'grok-beast' not in quota_workers(db, now=datetime.now(timezone.utc) + timedelta(hours=2))


def test_maintenance_recovers_interrupted_quota_deferral(db):
    job(db, status='failed', lease_id='old-attempt', leased_by_instance_id='grok-beast', error_message='HTTP 402 out of credits')
    delivery.sweep(db, config={})
    assert row(db)['status'] == 'pending'
    assert not row(db).get('lease_id')


@pytest.mark.asyncio
async def test_unfenced_quota_report_cannot_block_worker(db):
    from mco.orchestrator.worker_policy import quota_workers
    job(db)
    acquire_lease(db, 'job', 'grok-beast')
    err, ack, broadcast = AsyncMock(), AsyncMock(), AsyncMock()
    await handle_job_update(db, {'task_id': 'job', 'status': 'failed', 'error_message': '402'},
                            'quota', err, ack, broadcast, actor={'instance_id': 'grok-beast', 'role': 'grok'})
    err.assert_called_once()
    assert row(db)['status'] == 'leased'
    assert quota_workers(db) == set()


@pytest.mark.asyncio
async def test_direct_lease_cannot_bypass_capabilities(db, monkeypatch):
    job(db, input_payload={'routing': {'required_capabilities': ['gpu-render']}})
    monkeypatch.setattr(routes, 'get_db_client', lambda: db)
    monkeypatch.setattr(routes, 'get_config', lambda: {})
    monkeypatch.setattr(routes, 'touch_agent_presence', lambda *_: None)
    monkeypatch.setattr(routes, 'reclaim_stale_leases', lambda *_: None)
    with pytest.raises(HTTPException) as e:
        await routes.lease_job({'task_id': 'job', 'agent_instance_id': 'grok-beast'}, {'instance_id': 'grok-beast', 'role': 'grok', 'org_id': 'default'})
    assert e.value.status_code == 403
