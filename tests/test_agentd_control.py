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
        headers={
            "Authorization": f"Bearer {token_store.control_token()}",
            "If-Match": '"absent"',  # R5: a writer says which generation it read
        },
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


# --- ADR 0002 Revision 2 -------------------------------------------------

AUTH = {"Authorization": "Bearer secret"}
FLEET = '[workers.codex]\nrole="codex"\nmode="off"\n'


def test_daemon_token_cannot_administer_the_gateway(tmp_path: Path, monkeypatch) -> None:
    """R7, the other direction: the two credentials are not interchangeable."""
    import pytest
    from fastapi import HTTPException

    import mco.orchestrator.auth as gateway_auth
    import mco.orchestrator.routes as gateway_routes

    store = AgentdTokenStore(tmp_path / "agentd.token", tmp_path / "agentd.logs.token")

    class GatewayConfig:
        def get(self, key):
            return "the-gateway-token" if key == "MCO_LOCAL_TOKEN" else None

    monkeypatch.setattr(gateway_routes, "get_db_client", lambda: None)
    monkeypatch.setattr(gateway_auth, "get_config", lambda: GatewayConfig())
    for daemon_token in (store.control_token(), store.logs_token()):
        with pytest.raises(HTTPException) as rejected:
            gateway_auth.require_agent(authorization=f"Bearer {daemon_token}")
        assert rejected.value.status_code == 401
    assert gateway_auth.require_agent(authorization="Bearer the-gateway-token")["role"] == "admin"


def test_log_token_cannot_control_and_control_token_cannot_read_logs(tmp_path: Path) -> None:
    client, supervisor = build_client(tmp_path)
    log_only = {"Authorization": "Bearer log-secret"}
    assert client.post("/v1/workers/codex-beast/stop", headers=log_only).status_code == 401
    assert client.put("/v1/config", content=FLEET, headers=log_only).status_code == 401
    assert client.post("/v1/drain", headers=log_only).status_code == 401
    assert client.get("/v1/logs", headers=AUTH).status_code == 401
    assert supervisor.actions == []


def test_control_api_refuses_to_bind_anywhere_but_loopback(tmp_path: Path) -> None:
    import pytest

    from mco.agentd.control import serve_control_app

    client, _ = build_client(tmp_path)
    for host in ("0.0.0.0", "::", "192.0.2.10"):
        with pytest.raises(ValueError, match="127.0.0.1"):
            serve_control_app(client.app, host=host, port=1)


def test_control_port_is_namespaced_per_user_and_stable() -> None:
    """R1: two users on one machine do not race for one port."""
    first = user_scoped_control_port("uid:1000")
    assert first == user_scoped_control_port("uid:1000")
    assert first != user_scoped_control_port("uid:1001")


def test_config_put_requires_the_generation_that_was_read(tmp_path: Path) -> None:
    client, supervisor = build_client(tmp_path)
    assert client.put("/v1/config", content=FLEET, headers=AUTH).status_code == 428
    assert supervisor.actions == []

    read = client.get("/v1/config", headers=AUTH)
    etag = read.headers["etag"]
    assert etag.strip('"') == read.json()["generation"]

    written = client.put("/v1/config", content=FLEET, headers={**AUTH, "If-Match": etag})
    assert written.status_code == 200
    assert written.headers["etag"] != etag
    assert supervisor.actions == [("reconcile", ["codex"])]


def test_stale_config_put_is_a_409_and_changes_nothing(tmp_path: Path) -> None:
    client, supervisor = build_client(tmp_path)
    stale = client.get("/v1/config", headers=AUTH).headers["etag"]
    (tmp_path / "fleet.toml").write_text(FLEET, encoding="utf-8")  # a manual edit lands first

    conflict = client.put(
        "/v1/config",
        content='[workers.other]\nrole="x"\nmode="off"\n',
        headers={**AUTH, "If-Match": stale},
    )
    assert conflict.status_code == 409
    current = conflict.json()["detail"]["current_generation"]
    assert current == client.get("/v1/config", headers=AUTH).json()["generation"]
    assert (tmp_path / "fleet.toml").read_text(encoding="utf-8") == FLEET
    assert supervisor.actions == []

    # JSON body form, carrying the generation it now knows about.
    retried = client.put(
        "/v1/config", json={"content": FLEET, "generation": current}, headers=AUTH
    )
    assert retried.status_code == 200


def test_invalid_config_put_is_rejected_before_the_file_is_touched(tmp_path: Path) -> None:
    client, supervisor = build_client(tmp_path)
    etag = client.get("/v1/config", headers=AUTH).headers["etag"]
    bad = client.put(
        "/v1/config", content=FLEET + "enabled = true\n", headers={**AUTH, "If-Match": etag}
    )
    assert bad.status_code == 422
    assert not (tmp_path / "fleet.toml").exists() and supervisor.actions == []


def test_versions_are_published_and_negotiated(tmp_path: Path) -> None:
    """R8: version skew between daemon, console and tray is negotiated."""
    from mco import __version__
    from mco.fleet import ALLOWED_FIELDS

    client, _ = build_client(tmp_path)
    version = client.get("/v1/version", headers=AUTH).json()
    assert version["package"] == __version__
    assert version["api"] in version["api_supported"]
    assert version["fleet_fields"] == sorted(ALLOWED_FIELDS)
    assert client.get("/v1/status", headers=AUTH).json()["daemon"]["version"] == version

    ok = client.get("/v1/status", headers={**AUTH, "X-BitCadence-Agentd-Api": "1"})
    assert ok.status_code == 200
    too_new = client.get("/v1/status", headers={**AUTH, "X-BitCadence-Agentd-Api": "99"})
    assert too_new.status_code == 400
    assert too_new.json()["detail"]["supported"] == version["api_supported"]


def test_drain_and_resume_are_exposed_for_upgrades(tmp_path: Path) -> None:
    from mco.agentd.supervisor import Supervisor
    from mco.fleet import WorkerConfig

    class Child:
        pid = 1
        returncode = None

        def poll(self):
            return self.returncode

        def terminate(self):
            self.returncode = 0

        kill = terminate

        def wait(self, timeout):
            return self.returncode

    class Adapter:
        spawn_calls: list = []

        def acquire_supervisor_lock(self):
            pass

        release_supervisor_lock = bind_to_parent_lifetime = acquire_supervisor_lock

        def reap_orphans(self, pidfile):
            return []

        def spawn(self, argv, cwd, env, stdout, stderr):
            self.spawn_calls.append(argv)
            return Child()

    adapter = Adapter()
    supervisor = Supervisor(
        adapter, state_file=tmp_path / "state.json", pidfile=tmp_path / "pids"
    )
    manager = FleetConfigManager(tmp_path / "fleet.toml")
    app = create_control_app(
        supervisor,
        manager,
        FleetWatcher(manager, supervisor),
        LogAggregator(tmp_path / "logs"),
        token_provider=lambda: "secret",
        log_token_provider=lambda: "log-secret",
    )
    client = TestClient(app)
    supervisor.reconcile({"w": WorkerConfig("w", "codex", "w", "waker", "run.cmd", 10.0, 30.0)})

    drained = client.post("/v1/drain", headers=AUTH).json()
    assert drained["state"] == "draining" and drained["draining"] is True
    assert client.get("/v1/workers", headers=AUTH).json()[0]["pid"] is None
    assert isinstance(supervisor, Supervisor) and len(adapter.spawn_calls) == 1

    assert client.post("/v1/resume", headers=AUTH).json()["state"] == "running"
    worker = client.get("/v1/workers", headers=AUTH).json()[0]
    assert worker["state"] == "running" and worker["desired"] == "running"
    assert len(adapter.spawn_calls) == 2
