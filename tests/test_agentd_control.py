from pathlib import Path

from fastapi.testclient import TestClient

from mco.agentd.config import FleetConfigManager, FleetWatcher
from mco.agentd.control import CONTROL_HOST, CONTROL_PORT, create_control_app
from mco.agentd.logs import LogAggregator


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
        gateway_reachable=lambda: True,
    )
    return TestClient(app), supervisor


def test_control_api_uses_loopback_contract_and_bearer_auth(tmp_path: Path) -> None:
    assert (CONTROL_HOST, CONTROL_PORT) == ("127.0.0.1", 18790)
    client, _ = build_client(tmp_path)
    assert client.get("/v1/status").status_code == 401
    response = client.get("/v1/status", headers={"Authorization": "Bearer secret"})
    assert response.status_code == 200
    assert response.json()["gateway_reachable"] is True


def test_control_actions_remain_thin_and_delegate_to_supervisor(tmp_path: Path) -> None:
    client, supervisor = build_client(tmp_path)
    response = client.post(
        "/v1/workers/codex-beast/restart",
        headers={"Authorization": "Bearer secret"},
    )
    assert response.status_code == 200
    assert supervisor.actions == [("restart", "codex-beast")]
