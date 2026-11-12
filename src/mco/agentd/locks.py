"""OS-held file locks: the per-worker execution lock and the POSIX singleton.

ADR 0002 R2. The atomic job lease stops two processes leasing the *same* row.
It does not stop two copies of one worker leasing *different* rows. The lease
is not a substitute for a lock, so every worker child runs under an exclusive
lock keyed by its canonical identity (role + instance). An outgoing and an
incoming supervisor therefore cannot both run the same worker during handoff.

The lock is held by the operating system on an open file handle, so it is
released when the holding process dies for any reason - there is no stale-lock
cleanup to get wrong.
"""

from __future__ import annotations

import os
from pathlib import Path
import re
from typing import IO


# The lock is taken on one byte far past any content, so the pid written at the
# start of the file stays readable by a process that failed to get the lock
# (Windows byte-range locks block reads of the locked range).
_LOCK_OFFSET = 1 << 30


class LockHeld(RuntimeError):
    """Another process holds this lock."""

    def __init__(self, path: Path, holder: str) -> None:
        super().__init__(f"{path.name} is held by pid {holder}")
        self.path = path
        self.holder = holder


def worker_identity(role: str, instance: str | None) -> str:
    """Canonical worker identity: role + instance, case-folded."""
    return f"{role.strip().casefold()}/{(instance or '').strip().casefold()}"


def worker_lock_path(lock_dir: Path, role: str, instance: str | None) -> Path:
    safe = re.sub(r"[^a-z0-9_.-]+", "-", worker_identity(role, instance)).strip("-.")
    return lock_dir / f"worker-{safe or 'unknown'}.lock"


class FileLock:
    """Exclusive, non-blocking, process-lifetime lock on a file."""

    def __init__(self, path: Path, *, pid: int | None = None) -> None:
        self.path = path
        self.pid = pid or os.getpid()
        self._handle: IO[bytes] | None = None

    @property
    def held(self) -> bool:
        return self._handle is not None

    def acquire(self) -> None:
        if self._handle is not None:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # Not append mode: the pid is rewritten at offset 0 on every acquire.
        handle = os.fdopen(os.open(self.path, os.O_RDWR | os.O_CREAT, 0o600), "r+b")
        try:
            _lock(handle)
        except OSError:
            handle.close()
            raise LockHeld(self.path, self.holder()) from None
        try:
            handle.seek(0)
            handle.truncate()
            handle.write(str(self.pid).encode("ascii"))
            handle.flush()
        except OSError:
            pass  # the pid is diagnostic; the lock itself is what matters
        self._handle = handle

    def holder(self) -> str:
        try:
            with open(self.path, "rb") as handle:
                return str(int(handle.read(32).decode("ascii", errors="ignore").strip()))
        except (OSError, ValueError):
            return "unknown"

    def release(self) -> None:
        handle, self._handle = self._handle, None
        if handle is None:
            return
        try:
            _unlock(handle)
        except OSError:
            pass
        finally:
            handle.close()


if os.name == "nt":
    import msvcrt

    def _lock(handle: IO[bytes]) -> None:
        handle.seek(_LOCK_OFFSET)
        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)

    def _unlock(handle: IO[bytes]) -> None:
        handle.seek(_LOCK_OFFSET)
        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)

else:
    import fcntl

    def _lock(handle: IO[bytes]) -> None:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)

    def _unlock(handle: IO[bytes]) -> None:
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
