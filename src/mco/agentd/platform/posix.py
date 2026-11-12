"""Linux and macOS process and per-user singleton integration for ``bitcadenced``.

What this adapter can and cannot promise:

* **Singleton:** an advisory ``flock`` on ``~/.mco/agentd.lock`` held for the
  process lifetime (ADR 0002 R2). The kernel drops it when the daemon dies.
* **Children die with the daemon:** Linux sets ``PR_SET_PDEATHSIG``. macOS has
  no equivalent - if the daemon is ``SIGKILL``ed its children survive, and the
  only remedy is identity-proven reaping at the next start (R6). That is a
  platform limit, not something this code papers over.
* **Process tree:** every child leads its own session/process group, so stop
  and kill address the whole group.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import ctypes
import ctypes.util
import os
from pathlib import Path
import secrets
import signal
import subprocess
import sys
from typing import Any, Callable

from mco import service
from mco.service import ServiceSpec

from .. import identity
from ..identity import NONCE_ENV, ReapReport
from ..locks import FileLock, LockHeld
from .base import SupervisorAlreadyRunning


PR_SET_PDEATHSIG = 1


def _pdeathsig_setter() -> Callable[[], None] | None:
    """A fork-safe preexec hook for Linux; None where the kernel has no such thing."""
    if not sys.platform.startswith("linux"):
        return None
    try:
        libc = ctypes.CDLL(ctypes.util.find_library("c") or "libc.so.6", use_errno=True)
    except OSError:
        return None
    term = int(signal.SIGTERM)

    def set_pdeathsig() -> None:  # runs in the child between fork and exec
        libc.prctl(PR_SET_PDEATHSIG, term)

    return set_pdeathsig


class PosixProcessHandle:
    """A child that leads its own process group."""

    def __init__(self, process: subprocess.Popen[Any]):
        self._process = process
        self.pid = process.pid

    @property
    def stdout(self) -> Any:
        return self._process.stdout

    @property
    def stderr(self) -> Any:
        return self._process.stderr

    def poll(self) -> int | None:
        return self._process.poll()

    def _signal_group(self, signum: int) -> None:
        try:
            os.killpg(self.pid, signum)
        except (ProcessLookupError, PermissionError):
            pass

    def terminate(self) -> None:
        self._signal_group(signal.SIGTERM)

    def kill(self) -> None:
        """Kill the whole group, not just the leader."""
        self._signal_group(signal.SIGKILL)

    def wait(self, timeout: float) -> int:
        return self._process.wait(timeout=timeout)

    def close(self) -> None:
        """The leader is gone: kill whatever is left in its group."""
        self._signal_group(signal.SIGKILL)


class PosixAdapter:
    def __init__(
        self,
        pidfile: Path | None = None,
        popen_factory: Callable[..., subprocess.Popen[Any]] = subprocess.Popen,
        lock_path: Path | None = None,
        nonce: str | None = None,
    ) -> None:
        self.pidfile = pidfile or Path.home() / ".mco" / "agentd.pids"
        self._popen_factory = popen_factory
        self._lock = FileLock(lock_path or Path.home() / ".mco" / "agentd.lock")
        self.nonce = nonce or secrets.token_hex(16)
        self.last_reap_report = ReapReport()
        self._preexec = _pdeathsig_setter()
        # PR_SET_PDEATHSIG fires when the *thread* that forked the child exits,
        # not when the process does. Control-API requests run on short-lived
        # pool threads, so a worker started from one would be signalled a few
        # seconds later for no reason. Every fork therefore happens on this
        # one thread, which lives as long as the daemon.
        self._spawner = (
            ThreadPoolExecutor(max_workers=1, thread_name_prefix="agentd-spawn")
            if self._preexec is not None
            else None
        )

    def acquire_supervisor_lock(self) -> None:
        try:
            self._lock.acquire()
        except LockHeld as exc:
            raise SupervisorAlreadyRunning(
                f"BitCadence agentd is already running for this user; holding pid {exc.holder}"
            ) from None

    def release_supervisor_lock(self) -> None:
        self._lock.release()

    def bind_to_parent_lifetime(self) -> None:
        # Per child, at spawn (PDEATHSIG on Linux). Nothing to do process-wide.
        return None

    def spawn(
        self,
        argv: list[str],
        cwd: Path,
        env: dict[str, str],
        stdout: int,
        stderr: int,
    ) -> PosixProcessHandle:
        if self._spawner is not None:
            return self._spawner.submit(self._spawn, argv, cwd, env, stdout, stderr).result()
        return self._spawn(argv, cwd, env, stdout, stderr)

    def _spawn(
        self,
        argv: list[str],
        cwd: Path,
        env: dict[str, str],
        stdout: int,
        stderr: int,
    ) -> PosixProcessHandle:
        child_env = dict(env)
        child_env[NONCE_ENV] = self.nonce
        process = self._popen_factory(
            argv,
            cwd=str(cwd),
            env=child_env,
            stdin=subprocess.DEVNULL,
            stdout=stdout,
            stderr=stderr,
            start_new_session=True,
            preexec_fn=self._preexec,
        )
        try:
            identity.append_record(
                self.pidfile, identity.capture_identity(process.pid, argv, self.nonce)
            )
        except Exception:
            # A child we cannot identify later is a child we could never reap.
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                pass
            process.wait()
            raise
        return PosixProcessHandle(process)

    def forget_pid(self, pid: int) -> None:
        identity.forget_record(self.pidfile, pid)

    def reap_orphans(self, pidfile: Path) -> list[int]:
        """Kill recorded children only when their identity is proven (R6)."""
        self.last_reap_report = identity.reap_recorded(pidfile)
        return list(self.last_reap_report.killed)

    def install_service(self, spec: ServiceSpec) -> tuple[bool, str]:
        if sys.platform == "darwin":
            return service._mac_install_service(spec)
        return service._linux_install_service(spec)

    def uninstall_service(self, name: str) -> tuple[bool, str]:
        return service.uninstall(name)
