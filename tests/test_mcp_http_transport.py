"""The HTTP MCP transport never serves a request without the agent's bearer token."""
import pytest
from starlette.testclient import TestClient

from mco.mcp_server import bearer_guard, run_http


async def _ok(scope, receive, send):
    await send({"type": "http.response.start", "status": 200, "headers": []})
    await send({"type": "http.response.body", "body": b"ok"})


def test_requests_without_the_bearer_token_are_refused():
    client = TestClient(bearer_guard(_ok, "secret-token"))
    assert client.get("/mcp").status_code == 401
    assert client.get("/mcp", headers={"Authorization": "Bearer wrong"}).status_code == 401
    assert client.get("/mcp", headers={"Authorization": "Bearer secret-token"}).status_code == 200


def test_http_transport_needs_a_token_and_a_specific_address(monkeypatch):
    monkeypatch.delenv("MCO_AGENT_TOKEN", raising=False)
    with pytest.raises(SystemExit, match="MCO_AGENT_TOKEN"):
        run_http("127.0.0.1", 18790)
    monkeypatch.setenv("MCO_AGENT_TOKEN", "x")
    with pytest.raises(SystemExit, match="every interface"):
        run_http("0.0.0.0", 18790)


def test_empty_host_and_duplicate_authorization_are_refused(monkeypatch):
    monkeypatch.setenv("MCO_AGENT_TOKEN", "x")
    for host in ("", "  ", "[::]"):
        with pytest.raises(SystemExit, match="every interface"):
            run_http(host, 18790)
    client = TestClient(bearer_guard(_ok, "secret-token"))
    doubled = [("Authorization", "Bearer secret-token"), ("Authorization", "Bearer secret-token")]
    assert client.get("/mcp", headers=doubled).status_code == 401


def test_websocket_scopes_never_reach_the_app():
    import asyncio
    sent, reached = [], []

    async def app(scope, receive, send):
        reached.append(scope["type"])

    async def send(message):
        sent.append(message)

    asyncio.run(bearer_guard(app, "t")({"type": "websocket", "headers": []}, None, send))
    assert reached == [] and sent[0]["type"] == "websocket.close"
