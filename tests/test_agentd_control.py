from pathlib import Path

from fastapi.testclient import TestClient

from mco.agentd.config import FleetConfigManager, FleetWatcher
from mco.agentd.control import (
    CONTROL_HOST,
    CONTROL_PORT,
    CONTROL_PORT_BASE,
    create_control_app,
    user_scoped_control_port,
)
from mco.agentd.logs import LogAggregator
from mco.agentd.tokens import AgentdTokenStore


class StubSupervisor:
    def __init__(self) -> None:
        self.actions = []

    def status(self, name=None):
        worker = {
            "name": name or "codex-beast",
            "role": "codex",
            "instance": "codex-beast",
            "state": "stopped",
            "pid": None,
            "uptime_s": None,
            "restarts": 0,
            "last_exit": None,
            "last_error": None,
        }
        return worker if name else [worker]

    def reconcile(self, configs) -> None:
        self.actions.append(("reconcile", sorted(configs)))

    def start(self, name):
        self.actions.append(("start", name))

    def stop(self, name):
        self.actions.append(("stop", name))

    def restart(self, name):
        self.actions.append(("restart", name))

    def reset(self, name):
        self.actions.append(("reset", name))


def build_client(tmp_path: Path):
    manager = FleetConfigManager(tmp_path / "fleet.toml")
    supervisor = StubSupervisor()
    watcher = FleetWatcher(manager, supervisor)  # type: ignore[arg-type]
    app = create_control_app(
        supervisor,  # type: ignore[arg-type]
        manager,
        watcher,
        LogAggregator(tmp_path / "logs"),
        token_provider=lambda: "secret",
        log_token_provider=lambda: "log-secret",
        gateway_reachable=lambda: True,
    )
    return TestClient(app), supervisor


def test_control_api_uses_loopback_contract_and_bearer_auth(tmp_path: Path) -> None:
    assert CONTROL_HOST == "127.0.0.1"
    assert CONTROL_PORT_BASE <= CONTROL_PORT < CONTROL_PORT_BASE + 1000
    assert user_scoped_control_port("S-1-5-21-a") != user_scoped_control_port(
        "S-1-5-21-b"
    )
    client, _ = build_client(tmp_path)
    assert client.get("/v1/status").status_code == 401
    response = client.get("/v1/status", headers={"Authorization": "Bearer secret"})
    assert response.status_code == 200
    assert response.json()["gateway_reachable"] is True


def test_gateway_token_cannot_mutate_daemon_config(tmp_path: Path) -> None:
    manager = FleetConfigManager(tmp_path / "fleet.toml")
    supervisor = StubSupervisor()
    watcher = FleetWatcher(manager, supervisor)  # type: ignore[arg-type]
    token_store = AgentdTokenStore(
        tmp_path / "agentd.token", tmp_path / "agentd.logs.token"
    )
    app = create_control_app(
        supervisor,  # type: ignore[arg-type]
        manager,
        watcher,
        LogAggregator(tmp_path / "logs"),
        token_store=token_store,
    )
    client = TestClient(app)
    response = client.put(
        "/v1/config",
        content='[workers.codex]\nrole="codex"\nmode="off"\n',
        headers={"Authorization": "Bearer gateway-token"},
    )
    assert response.status_code == 401
    assert supervisor.actions == []
    accepted = client.put(
        "/v1/config",
        content='[workers.codex]\nrole="codex"\nmode="off"\n',
        headers={"Authorization": f"Bearer {token_store.control_token()}"},
    )
    assert accepted.status_code == 200


def test_logs_require_a_separate_read_capability(tmp_path: Path) -> None:
    client, _ = build_client(tmp_path)
    assert (
        client.get("/v1/logs", headers={"Authorization": "Bearer secret"}).status_code
        == 401
    )
    assert (
        client.get(
            "/v1/logs", headers={"Authorization": "Bearer log-secret"}
        ).status_code
        == 200
    )


def test_control_actions_remain_thin_and_delegate_to_supervisor(tmp_path: Path) -> None:
    client, supervisor = build_client(tmp_path)
    response = client.post(
        "/v1/workers/codex-beast/restart",
        headers={"Authorization": "Bearer secret"},
    )
    assert response.status_code == 200
    assert supervisor.actions == [("restart", "codex-beast")]
