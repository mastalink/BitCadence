"""Exercise attempt proofs across long-lived transports and failed responses."""
import hashlib
import json

import httpx
import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from mco.localstore import LocalStore
from mco.orchestrator import leases, routes
from mco.orchestrator.client import GatewayClient


@pytest.mark.parametrize('failure', ['disconnect', 'server', 'fenced'])
def test_result_retry_preserves_exact_claim(monkeypatch, failure):
    proof = {'lease_id': 'opaque', 'lease_epoch': 3, 'lease_incarnation': 'store', 'agent_instance_id': 'worker'}
    attempts = []
    def respond(request):
        if request.url.path.endswith('/lease'):
            return httpx.Response(200, json={'success': True, 'lease': proof})
        attempts.append(json.loads(request.content))
        if failure == 'fenced':
            return httpx.Response(409, json={'detail': 'halted'})
        if len(attempts) == 1:
            if failure == 'disconnect':
                raise httpx.ReadError('response lost')
            return httpx.Response(503)
        return httpx.Response(200, json={'success': True})
    monkeypatch.setattr('time.sleep', lambda _: None)
    client = GatewayClient(transport=httpx.MockTransport(respond))
    client.lease('job')
    if failure == 'fenced':
        with pytest.raises(httpx.HTTPStatusError): client.complete('job', 'result')
        assert len(attempts) == 1
    else:
        assert client.complete('job', 'result')['success']
        assert len(attempts) == 2 and attempts[0] == attempts[1]
    assert all(attempts[0][key] == value for key, value in proof.items())


def test_mcp_keeps_claim_between_tool_calls(monkeypatch):
    from mco import mcp_server
    monkeypatch.setattr(mcp_server, '_clients', {})
    monkeypatch.setenv('MCO_AGENT_TOKEN', 'first')
    first = mcp_server._client()
    first._leases['job'] = {'lease_id': 'proof'}
    assert mcp_server._client()._leases['job']['lease_id'] == 'proof'
    monkeypatch.setenv('MCO_AGENT_TOKEN', 'second')
    assert mcp_server._client()._leases == {}


def test_result_survives_worker_restart_and_keeps_original_proof(tmp_path, monkeypatch):
    monkeypatch.setenv('MCO_RESULT_SPOOL_DIR', str(tmp_path))
    monkeypatch.setattr('time.sleep',lambda _:None)
    first = GatewayClient(instance_id='worker',token='private',
        transport=httpx.MockTransport(lambda request:httpx.Response(503)))
    proof = {'lease_id':'original','lease_epoch':7,'lease_incarnation':'store','agent_instance_id':'worker'}
    first._leases['job'] = proof
    with pytest.raises(httpx.HTTPStatusError): first.complete('job','valuable output')
    assert len(list(tmp_path.rglob('*.json'))) == 1
    received = []
    def acknowledge(request):
        received.append(json.loads(request.content))
        return httpx.Response(200,json={'success':True})
    restarted = GatewayClient(instance_id='worker',token='private',transport=httpx.MockTransport(acknowledge))
    assert restarted.flush_reports() == 1
    assert not list(tmp_path.rglob('*.json'))
    assert received[0]['lease_id'] == 'original'
    assert received[0]['output_payload']['result'] == 'valuable output'


def test_replayed_spool_fence_preserves_rejected_output(tmp_path, monkeypatch):
    monkeypatch.setenv('MCO_RESULT_SPOOL_DIR', str(tmp_path))
    client = GatewayClient(transport=httpx.MockTransport(lambda request:httpx.Response(409)))
    client._save_report('job',{'status':'completed','output_payload':{'result':'inspect me'}},{'lease_id':'stale'})
    assert client.flush_reports() == 0
    assert not list(tmp_path.rglob('*.json'))
    assert 'inspect me' in next(tmp_path.rglob('*.rejected')).read_text()


@pytest.mark.parametrize('disabled', [False, True])
def test_websocket_enforces_claim_and_disabled_identity(tmp_path, monkeypatch, disabled):
    from mco.cli import create_app
    from mco.orchestrator import auth
    from mco.notifiers import ntfy
    db = LocalStore(tmp_path/'socket.db')
    monkeypatch.setattr(routes, 'get_db_client', lambda: db)
    monkeypatch.setattr(routes, 'kill_switch_active', lambda: False)
    monkeypatch.setattr(ntfy, 'notify', lambda *a, **k: False)
    db.table('agent_registry').insert({'instance_id': 'worker', 'role': 'test',
        'auth_token_hash': hashlib.sha256(b'test-token').hexdigest(),
        'status': 'disabled' if disabled else 'offline'}).execute()
    db.table('agent_jobs').insert({'id': 'socket-job', 'title': 'socket test',
        'target_agent_role': 'test', 'status': 'pending'}).execute()
    claim = leases.acquire_lease(db, 'socket-job', 'worker').as_claim()
    try:
        with TestClient(create_app()) as client, client.websocket_connect('/ws/broadcast') as ws:
            ws.send_json({'type':'authenticate', 'payload':{'token':'test-token'}})
            if disabled:
                with pytest.raises(WebSocketDisconnect): ws.receive_json()
            else:
                assert ws.receive_json()['type'] == 'authenticated'
                ws.send_json({'type':'job_update', 'payload':{'task_id':'socket-job',
                    **claim, 'lease_epoch':999, 'status':'completed'}})
                response = ws.receive_json()
                assert response['type'] == 'error' and response['payload']['status'] == 409
                assert db.table('agent_jobs').select('*').eq('id','socket-job').execute().data[0]['status'] == 'leased'
    finally:
        db.close()
