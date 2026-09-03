"""Platform contract for the per-user daemon.

Keep this module deliberately boring: platform implementations and the
supervisor meet here, and neither side should need to know the other's details.
"""

from __future__ import annotations

from pathlib import Path
from typing import Protocol

from mco.service import ServiceSpec


class ProcessHandle(Protocol):
    pid: int

    def poll(self) -> int | None: ...

    def terminate(self) -> None: ...

    def kill(self) -> None: ...

    def wait(self, timeout: float) -> int: ...


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
        """Start a child that shows no console window and dies with the daemon."""

    def bind_to_parent_lifetime(self) -> None:
        """Arrange for children to be killed if this process dies. Idempotent."""

    def reap_orphans(self, pidfile: Path) -> list[int]:
        """Kill recorded children only when their command name still matches."""

    def install_service(self, spec: ServiceSpec) -> tuple[bool, str]: ...

    def uninstall_service(self, name: str) -> tuple[bool, str]: ...
