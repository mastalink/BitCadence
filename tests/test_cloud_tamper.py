"""Real SQL tampering, rolled back; mocked S3 isolates version-lock semantics."""
import importlib.util
import io
import json
import os
from pathlib import Path
from unittest.mock import Mock
import uuid

import pytest


def test_p5_detects_real_tamper_and_restores_ledger(monkeypatch):
    dsn, url = os.environ.get('BC_TEST_DATABASE_URL'), os.environ.get('BC_TEST_POSTGREST_URL')
    if not dsn or not url:
        pytest.skip('Requires disposable PostgreSQL and PostgREST')
    boto3 = pytest.importorskip('boto3')
    from botocore.exceptions import ClientError
    from postgrest import SyncPostgrestClient
    from mco.orchestrator import audit
    for key in ('GATEWAY_URL','MCO_LOCAL_TOKEN','EVIDENCE_BUCKET','STATUS_BUCKET','ECS_CLUSTER'):
        monkeypatch.setenv(key, 'http://test' if key == 'GATEWAY_URL' else 'test')
    monkeypatch.setenv('DATABASE_URL', dsn)
    monkeypatch.setenv('STORE_BACKEND','postgres')
    sink = Mock()
    monkeypatch.setattr(boto3,'client',lambda *a, **k:sink)
    path = Path(__file__).resolve().parents[1]/'infra/aws/conductor/conductor.py'
    spec = importlib.util.spec_from_file_location('cloud_tamper_probe',path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    with SyncPostgrestClient(url) as db:
        jid = str(uuid.uuid4())
        db.table('agent_jobs').insert({'id':jid,'title':'P5 acceptance','target_agent_role':'test','status':'completed'}).execute()
        audit.record_event(db,jid,'created',detail={'original':True})
        original = audit.get_events(db,jid)[0]
        sink.get_object.return_value = {'VersionId':'immutable-version',
            'Body':io.BytesIO(json.dumps(original).encode())}
        denied = ClientError({'Error':{'Code':'AccessDenied'}},'ObjectLock')
        sink.delete_object.side_effect = denied
        sink.put_object_retention.side_effect = denied
        module.p5_tamper(jid)
        assert all(v['status']=='PASS' for v in module.run.results['P5'].values()), module.run.results
        restored = audit.verify_chain(db,jid)
        assert restored['ok'], restored
        assert audit.get_events(db,jid)[0]['detail'] == original['detail']
        assert sink.delete_object.call_args.kwargs['VersionId'] == 'immutable-version'
        assert sink.put_object_retention.call_args.kwargs['VersionId'] == 'immutable-version'
        # The rollback also restored the mutation-blocking trigger.
        with pytest.raises(Exception, match='append-only'):
            db.table('agent_job_events').update({'detail':{}}).eq('id',original['id']).execute()
