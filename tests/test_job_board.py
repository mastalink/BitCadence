"""Handler tests use the real SQLite store so ownership and evidence are exercised."""
from unittest.mock import AsyncMock
import pytest
from mco.localstore import LocalStore
from mco.orchestrator.handlers import handle_job_create, handle_job_lease, handle_job_update
from mco.orchestrator.leases import acquire_lease

@pytest.fixture
def db(tmp_path):
    store=LocalStore(tmp_path/'handlers.db')
    yield store
    store.close()

@pytest.mark.asyncio
@pytest.mark.parametrize('dependencies', [False, True])
async def test_handle_job_create(db, dependencies):
    if dependencies:
        db.table('agent_jobs').insert({'id':'parent','status':'pending'}).execute()
    error,ack,broadcast=AsyncMock(),AsyncMock(),AsyncMock()
    await handle_job_create(db,{'title':'Write unit tests','target_agent_role':'test',
        'depends_on':['parent'] if dependencies else []},'creator','test','corr',error,ack,broadcast)
    error.assert_not_called();ack.assert_called_once();broadcast.assert_called_once()
    job=ack.call_args.args[0]['job']
    assert job['status']==('waiting' if dependencies else 'pending')
    assert db.table('agent_job_events').select('*').eq('job_id',job['id']).execute().data

@pytest.mark.asyncio
async def test_handle_job_lease(db):
    db.table('agent_jobs').insert({'id':'job','status':'pending'}).execute()
    error,ack,broadcast=AsyncMock(),AsyncMock(),AsyncMock()
    await handle_job_lease(db,{'task_id':'job'},'worker','corr',error,ack,broadcast)
    error.assert_not_called();ack.assert_called_once()
    result=ack.call_args.args[0]['job']
    assert result['lease_id'] and result['lease_epoch']==1
    assert result['leased_by_instance_id']=='worker'

@pytest.mark.asyncio
async def test_handle_job_update_completion_unlocks_child(db):
    db.table('agent_jobs').insert({'id':'parent','status':'pending'}).execute()
    db.table('agent_jobs').insert({'id':'child','status':'waiting','depends_on':['parent']}).execute()
    proof=acquire_lease(db,'parent','worker')
    error,ack,broadcast=AsyncMock(),AsyncMock(),AsyncMock()
    await handle_job_update(db,{'task_id':'parent','status':'completed',**proof.as_claim()},
        'corr',error,ack,broadcast,actor={'instance_id':'worker','role':'test'})
    error.assert_not_called();ack.assert_called_once()
    rows={r['id']:r for r in db.table('agent_jobs').select('*').execute().data}
    assert rows['parent']['status']=='completed' and rows['parent']['completed_at']
    assert rows['child']['status']=='pending'
