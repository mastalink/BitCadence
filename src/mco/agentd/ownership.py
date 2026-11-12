"""Find a copy of a worker that something other than this supervisor is running.

The per-worker lock (``locks.py``) fences supervisors against each other. It
cannot fence a waker that was started before the lock existed - a legacy
per-worker service, whose process keeps running even after its registration
is deleted. Before starting a waker the supervisor therefore looks for one
that is already running for the same role + instance, and refuses to start a
second copy (ADR 0002 R2).

Matching is deliberately exact: only this user's processes, only a real
``wake`` command line, only the same role and instance.
"""

from __future__ import annotations

import os
from typing import Any, Callable, Iterable

import psutil

from mco.fleet import WorkerConfig


def _option(args: list[str], key: str) -> str | None:
    for index, item in enumerate(args):
        if item == key and index + 1 < len(args):
            return args[index + 1]
        if item.startswith(f"{key}="):
            return item.split("=", 1)[1]
    return None


def is_waker_command(argv: list[str], role: str, instance: str | None) -> bool:
    """True only for `... wake --role <role> [--instance <instance>] ...`."""
    if "wake" not in argv:
        return False
    args = argv[argv.index("wake") + 1 :]
    if _option(args, "--role") != role:
        return False
    return _option(args, "--instance") == instance


def find_foreign_waker(
    config: WorkerConfig,
    *,
    own_pids: Iterable[int] = (),
    processes: Callable[[], Iterable[Any]] | None = None,
    current_user: str | None = None,
) -> int | None:
    """Pid of a same-user waker for this worker that is not one of ours, or None."""
    if config.mode != "waker":
        return None
    excluded = {os.getpid(), *own_pids}
    try:
        user = current_user or psutil.Process().username()
    except psutil.Error:
        return None
    iterator = processes() if processes is not None else psutil.process_iter()
    for process in iterator:
        try:
            if process.pid in excluded or process.username() != user:
                continue
            if is_waker_command(list(process.cmdline()), config.role, config.instance):
                return int(process.pid)
        except (psutil.Error, OSError):
            continue
    return None


def owned_pids(handles: Iterable[Any]) -> set[int]:
    """Every pid in the trees of the children this supervisor started."""
    owned: set[int] = set()
    for handle in handles:
        owned.add(int(handle.pid))
        try:
            root = psutil.Process(handle.pid)
            owned.update(child.pid for child in root.children(recursive=True))
        except psutil.Error:
            continue
    return owned
