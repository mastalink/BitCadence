"""Unit tests for the BitCadence Client Authentication and Presence tracking."""

import hashlib
import json
from unittest import mock
import pytest
from fastapi.testclient import TestClient
from fastapi import WebSocketDisconnect

from mco.cli import create_app, ws_manager

class MockDBData:
    def __init__(self, data):
        self.data = data

class MockDBTable:
    def __init__(self, data_list=None):
        self.data_list = data_list or []
        self.updated_fields = None
        self.query_instance_id = None
        self.query_token_hash = None

    def select(self, *args, **kwargs):
        return self

    def eq(self, field, value):
        if field == "instance_id":
            self.query_instance_id = value
            self.data_list = [d for d in self.data_list if d.get("instance_id") == value]
        elif field == "auth_token_hash":
            self.query_token_hash = value
            self.data_list = [d for d in self.data_list if d.get("auth_token_hash") == value]
        return self

    def execute(self):
        return MockDBData(self.data_list)

    def update(self, fields):
        self.updated_fields = fields
        return self

class MockDBClient:
    def __init__(self, registered_agents=None):
        self.registered_agents = registered_agents or []
        self.table_mock = None

    def table(self, table_name):
        if table_name == "agent_registry":
            self.table_mock = MockDBTable(self.registered_agents)
            return self.table_mock
        raise ValueError(f"Unsupported table mock: {table_name}")


def test_websocket_bypass_auth():
    """No database AND no MCO_LOCAL_TOKEN configured: the zero-config loopback
    bypass admits the socket. (With a token configured, auth is required -
    covered in test_security_hardening.py.)"""
    app = create_app()
    client = TestClient(app)

    with mock.patch("mco.orchestrator.routes.get_db_client", return_value=None), \
         mock.patch("mco.cli.get_config", return_value={}):
        with client.websocket_connect("/ws/broadcast") as websocket:
            # Should connect successfully
            websocket.send_text("ping")
            # If the bypass works, we should be added to ws_manager's active connections
            assert len(ws_manager.active_connections) > 0


def test_websocket_successful_auth():
    """Verify that a client presenting a valid token successfully authenticates and is marked online."""
    app = create_app()
    client = TestClient(app)
    
    token = "my-secret-token"
    token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
    agent_data = {
        "instance_id": "test_agent",
        "role": "codex",
        "auth_token_hash": token_hash,
        "status": "offline"
    }
    
    db_client = MockDBClient(registered_agents=[agent_data])
    
    with mock.patch("mco.orchestrator.routes.get_db_client", return_value=db_client):
        with client.websocket_connect("/ws/broadcast") as websocket:
            # Send authenticate frame
            websocket.send_json({
                "type": "authenticate",
                "payload": {
                    "instance_id": "test_agent",
                    "role": "codex",
                    "token": token
                }
            })
            
            # Read response
            res = websocket.receive_json()
            assert res["type"] == "authenticated"
            assert res["payload"]["success"] is True
            
            # Check DB update was called to online status
            assert db_client.table_mock.updated_fields is not None
            assert db_client.table_mock.updated_fields["status"] == "online"


def test_websocket_token_only_auth():
    """A client presenting only its token (no instance_id/role) is resolved by
    token hash — the path the console and `mco watch` use."""
    app = create_app()
    client = TestClient(app)

    token = "my-secret-token"
    token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
    agent_data = {
        "instance_id": "test_agent",
        "role": "codex",
        "auth_token_hash": token_hash,
        "status": "offline"
    }

    db_client = MockDBClient(registered_agents=[agent_data])

    with mock.patch("mco.orchestrator.routes.get_db_client", return_value=db_client):
        with client.websocket_connect("/ws/broadcast") as websocket:
            websocket.send_json({
                "type": "authenticate",
                "payload": {"token": token}
            })

            res = websocket.receive_json()
            assert res["type"] == "authenticated"
            assert res["payload"]["success"] is True

            # The identity was resolved from the hash and marked online.
            assert db_client.table_mock.updated_fields is not None
            assert db_client.table_mock.updated_fields["status"] == "online"


def test_websocket_failed_auth_wrong_token():
    """Verify that a client presenting an invalid token is rejected and disconnected."""
    app = create_app()
    client = TestClient(app)
    
    token = "my-secret-token"
    token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
    agent_data = {
        "instance_id": "test_agent",
        "role": "codex",
        "auth_token_hash": token_hash,
        "status": "offline"
    }
    
    db_client = MockDBClient(registered_agents=[agent_data])
    
    with mock.patch("mco.orchestrator.routes.get_db_client", return_value=db_client):
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect("/ws/broadcast") as websocket:
                websocket.send_json({
                    "type": "authenticate",
                    "payload": {
                        "instance_id": "test_agent",
                        "role": "codex",
                        "token": "wrong-token-value"
                    }
                })
                # Attempt to receive response; it should send failure and disconnect
                res = websocket.receive_json()
                assert res["payload"]["success"] is False
                
                # Verify that it disconnects (receive should raise WebSocketDisconnect)
                websocket.receive_text()
