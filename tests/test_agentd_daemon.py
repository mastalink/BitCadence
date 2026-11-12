"""The supervisor process end to end, with real children, in a temp directory.

These are the ADR 0002 section 9 acceptance criteria that can be proven
without touching the machine's real services: every test runs against its own
state directory, its own lock, and throwaway child processes.
"""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
import time
import uuid

import psutil
import pytest
from fastapi.testclient import TestClient

from mco.agentd import daemon as daemon_module
from mco.agentd import identity
from mco.agentd.daemon import EXIT_ALREADY_RUNNING, SCOPE_NOTICE, Daemon, run_daemon
from mco.agentd.platform.base import SupervisorAlreadyRunning


SLEEPER = "import time; time.sleep(300)"


def make_adapter(state_dir: Path, lock_name: str | None = None):
    """A real platform adapter whose singleton is isolated from the user's own."""
    if os.name == "nt":
        from mco.agentd.platform.windows import WindowsAdapter, WindowsSupervisorLock

        lock = WindowsSupervisorLock(metadata_path=state_dir / "agentd.lock")
        lock.name = "Global\\BitCadence-test-" + (lock_name or uuid.uuid4().hex)
        return WindowsAdapter(pidfile=state_dir / "agentd.pids", supervisor_lock=lock)
    from mco.agentd.platform.posix import PosixAdapter

    return PosixAdapter(pidfile=state_dir / "agentd.pids", lock_path=state_dir / "agentd.lock")


def poll_worker(name: str, script: Path) -> str:
    return (
        f"[workers.{name}]\n"
        f'role = "test"\ninstance = "{name}"\nmode = "poll"\n'
        f"exec = '{sys.executable} {script}'\npoll_interval = 3600\n\n"
    )


@pytest.fixture
def script(tmp_path: Path) -> Path:
    path = tmp_path / "sleeper.py"
    path.write_text(SLEEPER, encoding="utf-8")
    return path


@pytest.fixture
def running(tmp_path: Path, script: Path):
    state = tmp_path / "state"
    state.mkdir()
    fleet = tmp_path / "fleet.toml"
    fleet.write_text(poll_worker("alpha", script), encoding="utf-8")
    daemon = Daemon(
        adapter=make_adapter(state),
        fleet_path=fleet,
        state_dir=state,
        detect_foreign=False,
        gateway_reachable=lambda: False,
    )
    daemon.watcher.interval = 0.2
    daemon.watcher.settle = 0.05
    daemon.start()
    try:
        yield daemon, fleet
    finally:
        daemon.stop()


def wait_for(condition, timeout: float = 20.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = condition()
        if value:
            return value
        time.sleep(0.05)
    raise AssertionError("condition not met in time")


def worker(daemon: Daemon, name: str) -> dict | None:
    for item in daemon.supervisor.status():
        if item["name"] == name:
            return item
    return None


def running_pid(daemon: Daemon, name: str) -> int | None:
    item = worker(daemon, name)
    return item["pid"] if item and item["state"] == "running" else None


def test_killed_worker_is_restarted_with_visible_backoff(running) -> None:
    daemon, _ = running
    first = wait_for(lambda: running_pid(daemon, "alpha"))
    identity.kill_verified_tree(psutil.Process(first))

    seen = wait_for(lambda: (worker(daemon, "alpha") or {}).get("state") == "backoff" and worker(daemon, "alpha"))
    assert seen["failures_in_window"] == 1 and seen["next_start_in_s"] is not None
    second = wait_for(lambda: (pid := running_pid(daemon, "alpha")) and pid != first and pid)
    assert worker(daemon, "alpha")["restarts"] == 1 and psutil.pid_exists(second)


def test_editing_fleet_toml_adds_and_removes_workers_within_five_seconds(running, script) -> None:
    daemon, fleet = running
    alpha = wait_for(lambda: running_pid(daemon, "alpha"))

    fleet.write_text(poll_worker("alpha", script) + poll_worker("beta", script), encoding="utf-8")
    beta = wait_for(lambda: running_pid(daemon, "beta"), timeout=5)
    assert running_pid(daemon, "alpha") == alpha  # the untouched worker was not restarted

    fleet.write_text(poll_worker("beta", script), encoding="utf-8")
    wait_for(lambda: worker(daemon, "alpha") is None, timeout=5)
    wait_for(lambda: not psutil.pid_exists(alpha), timeout=10)
    assert running_pid(daemon, "beta") == beta


def test_status_api_reports_scope_versions_and_child_output(running, tmp_path: Path) -> None:
    daemon, _ = running
    wait_for(lambda: running_pid(daemon, "alpha"))
    client = TestClient(daemon.app)
    token = (tmp_path / "state" / "agentd.token").read_text(encoding="utf-8").strip()
    status = client.get("/v1/status", headers={"Authorization": f"Bearer {token}"}).json()
    assert status["daemon"]["state"] == "running"
    assert status["daemon"]["orphans"] == {"killed": [], "left_alone": []}
    assert status["daemon"]["logs"]["dropped_lines"] == 0
    assert status["gateway_reachable"] is False
    # R1: the per-user scope is stated plainly where the operator will read it.
    assert any(SCOPE_NOTICE in line for line in daemon.logs.tail())
    assert "log in" in SCOPE_NOTICE


def test_shutdown_stops_every_child_and_releases_ownership(tmp_path: Path, script: Path) -> None:
    state = tmp_path / "state"
    state.mkdir()
    fleet = tmp_path / "fleet.toml"
    fleet.write_text(poll_worker("alpha", script) + poll_worker("beta", script), encoding="utf-8")
    lock_name = uuid.uuid4().hex
    daemon = Daemon(adapter=make_adapter(state, lock_name), fleet_path=fleet, state_dir=state,
                    detect_foreign=False)
    daemon.start()
    pids = wait_for(
        lambda: all(running_pid(daemon, n) for n in ("alpha", "beta"))
        and [running_pid(daemon, n) for n in ("alpha", "beta")]
    )

    # Single owner (R2): a second supervisor for the same user cannot start.
    rival = Daemon(adapter=make_adapter(state, lock_name), fleet_path=fleet, state_dir=state,
                   detect_foreign=False)
    with pytest.raises(SupervisorAlreadyRunning, match="holding pid"):
        rival.start()

    daemon.stop()
    wait_for(lambda: not any(psutil.pid_exists(pid) for pid in pids), timeout=30)
    assert (state / "agentd.pids").read_text(encoding="utf-8") == ""
    rival.start()  # ownership was handed over
    rival.stop()


DAEMON_SCRIPT = """
import os, sys, time
sys.path[:0] = [{src!r}, {tests!r}]
from pathlib import Path
from mco.agentd.daemon import Daemon
from test_agentd_daemon import make_adapter
state = Path({state!r})
daemon = Daemon(adapter=make_adapter(state), fleet_path=Path({fleet!r}), state_dir=state,
                detect_foreign=False)
daemon.start()
while True:
    workers = daemon.supervisor.status()
    if workers and all(w["pid"] for w in workers):
        break
    time.sleep(0.05)
print(os.getpid(), *[w["pid"] for w in workers], flush=True)
time.sleep(300)
"""


def test_hard_killing_the_supervisor_leaves_no_orphaned_children(tmp_path: Path, script: Path) -> None:
    """Section 9: SIGKILL the daemon, restart it, zero orphans - on every platform.

    Windows kills the children when the daemon's job handles close; Linux
    signals them (PDEATHSIG); macOS can do neither, so there the successor's
    identity-proven reaping is what cleans up. The successor runs everywhere.
    """
    state = tmp_path / "state"
    state.mkdir()
    fleet = tmp_path / "fleet.toml"
    fleet.write_text(poll_worker("alpha", script) + poll_worker("beta", script), encoding="utf-8")
    runner = tmp_path / "run_daemon.py"
    runner.write_text(
        DAEMON_SCRIPT.format(
            src=str(Path(daemon_module.__file__).resolve().parents[2]),
            tests=str(Path(__file__).resolve().parent),
            state=str(state),
            fleet=str(fleet),
        ),
        encoding="utf-8",
    )
    process = subprocess.Popen(
        [sys.executable, str(runner)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        stdin=subprocess.DEVNULL,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    try:
        line = process.stdout.readline().split()
        assert line, process.stderr.read().decode(errors="replace")
        daemon_pid, *children = (int(item) for item in line)
        assert all(psutil.pid_exists(pid) for pid in children)

        psutil.Process(daemon_pid).kill()  # no shutdown code runs
        wait_for(lambda: not psutil.pid_exists(daemon_pid))

        successor = Daemon(adapter=make_adapter(state), fleet_path=tmp_path / "none.toml",
                           state_dir=state, detect_foreign=False)
        successor.start()
        try:
            wait_for(lambda: not any(_alive(pid) for pid in children), timeout=30)
            assert successor.supervisor.reap_report["left_alone"] == []
        finally:
            successor.stop()
    finally:
        if process.poll() is None:
            identity.kill_verified_tree(psutil.Process(process.pid))
        process.wait(timeout=10)


def _alive(pid: int) -> bool:
    try:
        return psutil.Process(pid).status() != psutil.STATUS_ZOMBIE
    except psutil.Error:
        return False


def test_a_second_copy_exits_before_binding_anything(tmp_path: Path, monkeypatch) -> None:
    class Taken:
        pidfile = None

        def acquire_supervisor_lock(self):
            raise SupervisorAlreadyRunning("already running for this user; holding pid 4321")

    monkeypatch.setattr(daemon_module, "build_adapter", lambda: Taken())
    monkeypatch.setattr(
        daemon_module.uvicorn, "Server", lambda *a, **k: pytest.fail("must not bind the port")
    )
    said: list[str] = []
    code = run_daemon(tmp_path / "fleet.toml", state_dir=tmp_path, say=said.append)
    assert code == EXIT_ALREADY_RUNNING
    assert said == ["already running for this user; holding pid 4321"]
    assert not (tmp_path / "agentd.token").exists()  # no credential minted either


def test_control_server_binds_loopback_only(tmp_path: Path, monkeypatch) -> None:
    captured = {}

    class Idle:
        pidfile = None

        def acquire_supervisor_lock(self):
            captured["locked_before_bind"] = "config" not in captured

        def release_supervisor_lock(self):
            pass

        def bind_to_parent_lifetime(self):
            pass

        def reap_orphans(self, pidfile):
            return []

    class Server:
        def __init__(self, config):
            captured["config"] = config

        def run(self):
            pass

    monkeypatch.setattr(daemon_module, "build_adapter", lambda: Idle())
    monkeypatch.setattr(daemon_module.uvicorn, "Server", Server)
    monkeypatch.setattr(
        "mco.agentd.tokens._harden_owner_only", lambda path: None
    )
    assert run_daemon(tmp_path / "fleet.toml", state_dir=tmp_path, port=1, say=lambda m: None) == 0
    assert captured["config"].host == "127.0.0.1"
    assert captured["locked_before_bind"] is True
