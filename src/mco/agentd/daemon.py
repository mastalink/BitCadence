"""The supervisor process itself: `mco agentd run`.

Startup order is the single-owner fence from ADR 0002 R2 - **lock first**,
then reap, then (only then) bind the control port and start children. A second
copy for the same user exits here, naming the pid that holds the lock.

Scope (R1): one supervisor **per logged-in user**. It starts when that user
logs in and ends when they log out. Workers do not run before login unless the
operator turns on the platform facility for it.
"""

from __future__ import annotations

import os
from pathlib import Path
import threading
from typing import Callable

import uvicorn

from mco.fleet import FLEET_CONFIG_PATH, WorkerConfig

from .config import FleetConfigManager, FleetWatcher
from .control import CONTROL_HOST, control_port, create_control_app
from .logs import LogAggregator
from .ownership import find_foreign_waker, owned_pids
from .platform.base import PlatformAdapter, SupervisorAlreadyRunning
from .supervisor import Supervisor
from .tokens import AgentdTokenStore


SCOPE_NOTICE = (
    "BitCadence agentd runs per user: workers start when you log in and stop when "
    "you log out. Running before login is an opt-in platform setting, not the default."
)
EXIT_ALREADY_RUNNING = 3
TICK_SECONDS = 1.0


def build_adapter() -> PlatformAdapter:
    if os.name == "nt":
        from .platform.windows import WindowsAdapter

        return WindowsAdapter()
    from .platform.posix import PosixAdapter

    return PosixAdapter()


def gateway_probe() -> bool:
    """Whether the local gateway answers. Read-only; the daemon never manages it."""
    import httpx

    url = os.environ.get("MCO_GATEWAY_URL", "http://127.0.0.1:18789").rstrip("/")
    try:
        return httpx.get(f"{url}/healthz", timeout=1.0, trust_env=False).status_code < 500
    except Exception:
        return False


class Daemon:
    """Wires the collaborators together. Everything is injectable for tests."""

    def __init__(
        self,
        *,
        adapter: PlatformAdapter | None = None,
        fleet_path: Path = FLEET_CONFIG_PATH,
        state_dir: Path | None = None,
        token_store: AgentdTokenStore | None = None,
        detect_foreign: bool = True,
        gateway_reachable: Callable[[], bool] = gateway_probe,
    ) -> None:
        state_dir = state_dir or Path.home() / ".mco"
        self.logs = LogAggregator(state_dir / "logs")
        self.adapter = adapter or build_adapter()
        pidfile = state_dir / "agentd.pids"
        if hasattr(self.adapter, "pidfile"):
            self.adapter.pidfile = pidfile  # type: ignore[attr-defined]
        self.supervisor = Supervisor(
            self.adapter,
            logs=self.logs,
            pidfile=pidfile,
            state_file=state_dir / "agentd.state.json",
            lock_dir=state_dir / "locks",
            foreign_owner=self._foreign_owner if detect_foreign else None,
        )
        self.manager = FleetConfigManager(fleet_path)
        self.watcher = FleetWatcher(self.manager, self.supervisor)
        self.token_store = token_store or AgentdTokenStore(
            state_dir / "agentd.token", state_dir / "agentd.logs.token"
        )
        self.app = create_control_app(
            self.supervisor,
            self.manager,
            self.watcher,
            self.logs,
            token_store=self.token_store,
            gateway_reachable=gateway_reachable,
        )
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def _foreign_owner(self, config: WorkerConfig) -> int | None:
        handles = [
            runtime.process
            for runtime in self.supervisor.workers.values()
            if runtime.process is not None
        ]
        return find_foreign_waker(config, own_pids=owned_pids(handles))

    def note(self, message: str) -> None:
        self.logs.write("agentd", "daemon", message)

    def start(self) -> None:
        """Take the lock, reap, then begin reconciling. Raises if already running."""
        self.supervisor.startup()
        self.token_store.control_token()
        self.token_store.logs_token()
        self.note(SCOPE_NOTICE)
        left = self.supervisor.reap_report.get("left_alone") or []
        killed = self.supervisor.reap_report.get("killed") or []
        if killed:
            self.note(f"reaped {len(killed)} orphaned child(ren) from a previous run: {killed}")
        for item in left:  # type: ignore[union-attr]
            self.note(f"left a recorded process alone, identity not proven: {item}")
        self._thread = threading.Thread(target=self._loop, name="agentd-tick", daemon=True)
        self._thread.start()

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self.watcher.poll()
                self.supervisor.tick()
            except Exception as exc:  # the loop must outlive any single bad tick
                self.note(f"tick failed: {type(exc).__name__}: {exc}")
            self._stop.wait(TICK_SECONDS)

    def stop(self) -> None:
        """Bounded shutdown: stop spawning, stop every child, release the lock."""
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=TICK_SECONDS * 5)
        self.supervisor.shutdown()
        self.note("supervisor stopped; all children stopped and the lock released")
        self.logs.close()


def run_daemon(
    fleet_path: Path = FLEET_CONFIG_PATH,
    *,
    port: int | None = None,
    state_dir: Path | None = None,
    say: Callable[[str], None] = print,
) -> int:
    daemon = Daemon(fleet_path=fleet_path, state_dir=state_dir)
    try:
        daemon.start()
    except SupervisorAlreadyRunning as exc:
        say(str(exc))
        return EXIT_ALREADY_RUNNING
    bound_port = port if port is not None else control_port()
    say(SCOPE_NOTICE)
    say(f"Control API: http://{CONTROL_HOST}:{bound_port} (token: ~/.mco/agentd.token)")
    try:
        # log_config=None: under pythonw there is no stderr for uvicorn's default
        # handlers to write to. Explicit loopback bind - never 0.0.0.0 (R7).
        server = uvicorn.Server(
            uvicorn.Config(
                daemon.app,
                host=CONTROL_HOST,
                port=bound_port,
                log_config=None,
                access_log=False,
            )
        )
        server.run()
    finally:
        daemon.stop()
    return 0
