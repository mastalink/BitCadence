"""Creation route against disposable PostgreSQL/PostgREST, with separate clients."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
import os
from threading import Barrier, local
from unittest.mock import AsyncMock
import uuid

import pytest
from fastapi import HTTPException

from mco.orchestrator import routes


def test_postgres_atomic_creation_replay_and_owner_collision(monkeypatch):
    url = os.environ.get('BC_TEST_POSTGREST_URL')
    if not url:
        pytest.skip('Requires disposable PostgreSQL/PostgREST acceptance service')
    from postgrest import SyncPostgrestClient

    state = local()
    barrier = Barrier(2)
    operation = str(uuid.uuid4())
    agent = {'instance_id': 'via-dispatch-test', 'role': 'operator', 'org_id': 'default'}
    payload = {'id': operation, 'title': 'Via concurrency acceptance',
               'target_agent_role': 'via-retrieval', 'target_agent_id': 'via-worker-test',
               'input_payload': {'kind': 'via.retrieve.v1', 'sourceId': operation}, 'max_retries': 0}
    broadcast = AsyncMock()
    monkeypatch.setattr(routes, 'get_db_client', lambda: state.client)
    monkeypatch.setattr(routes, '_broadcast_callback', broadcast)
    monkeypatch.setattr(routes, 'notify_job_created', lambda **kwargs: None)
    monkeypatch.setattr(routes, 'get_gated_roles', lambda: [])
    monkeypatch.setattr(routes, 'kill_switch_active', lambda: False)

    def create():
        with SyncPostgrestClient(url) as client:
            state.client = client
            barrier.wait(timeout=10)
            return asyncio.run(routes.create_job(dict(payload), dict(agent)))

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(create) for _ in range(2)]
        responses = [future.result(timeout=30) for future in futures]
    assert [response['job']['id'] for response in responses] == [operation, operation]
    assert broadcast.await_count == 1

    with SyncPostgrestClient(url) as client:
        state.client = client
        rows = client.table('agent_jobs').select('*').eq('id', operation).execute().data
        assert len(rows) == 1
        assert len(rows[0]['create_intent_hash']) == 64
        events = client.table('agent_job_events').select('*').eq('job_id', operation).eq('event', 'created').execute().data
        assert len(events) == 1
        for changed_payload, changed_agent in [
            ({**payload, 'title': 'Changed intent'}, agent),
            (payload, {**agent, 'instance_id': 'other-sender'}),
            (payload, {**agent, 'org_id': 'other-tenant'}),
        ]:
            with pytest.raises(HTTPException) as error:
                asyncio.run(routes.create_job(changed_payload, changed_agent))
            assert error.value.status_code == 409
        recovered = asyncio.run(routes.create_job(payload, agent))
        assert recovered['job']['id'] == operation
        assert broadcast.await_count == 1
    # Retain append-only evidence in the disposable CI database; never delete it.
