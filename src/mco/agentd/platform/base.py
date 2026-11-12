"""Platform contract for the per-user daemon.

Keep this module deliberately boring: platform implementations and the
supervisor meet here, and neither side should need to know the other's details.
"""

from __future__ import annotations

from pathlib import Path
from typing import Protocol

from mco.service import ServiceSpec


class SupervisorAlreadyRunning(RuntimeError):
    """Another supervisor already holds this user's singleton lock (R2)."""


class ProcessHandle(Protocol):
    pid: int

    def poll(self) -> int | None: ...

    def terminate(self) -> None: ...

    def kill(self) -> None:
        """Last resort. Kills the child AND its descendants (the whole tree)."""

    def wait(self, timeout: float) -> int: ...

    # Optional: ``close()``. Called once the child is gone; it must kill any
    # descendants the child left behind and release OS resources. The
    # supervisor calls it when present.


class PlatformAdapter(Protocol):
    def acquire_supervisor_lock(self) -> None:
        """Fence this user's supervisor before binding or spawning."""

    def release_supervisor_lock(self) -> None:
        """Release the process-lifetime supervisor fence."""

    def spawn(
        self,
        argv: list[str],
        cwd: Path,
        env: dict[str, str],
        stdout: int,
        stderr: int,
    ) -> ProcessHandle:
        """Start a child that shows no console window and dies with the daemon.

        The adapter adds its supervisor nonce to ``env`` and records the
        child's identity before the child runs any code.
        """

    def bind_to_parent_lifetime(self) -> None:
        """Arrange for children to be killed if this process dies. Idempotent."""

    def reap_orphans(self, pidfile: Path) -> list[int]:
        """Kill recorded children whose identity is PROVEN; leave the rest alone.

        Identity is pid + start time + user + executable + argv + the
        per-supervisor nonce (see ``mco.agentd.identity``). A command-name
        match is not proof. Returns the pids killed; anything alive that could
        not be proven ours is reported on ``last_reap_report``, never killed.
        """

    def install_service(self, spec: ServiceSpec) -> tuple[bool, str]: ...

    def uninstall_service(self, name: str) -> tuple[bool, str]: ...
