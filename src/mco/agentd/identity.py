"""Process identity records: the rule is "do not kill unless identity is proven".

ADR 0002 R6. A pid alone proves nothing (pids are recycled), and neither does a
command name (a stranger can legitimately run the identical command). Every
child the supervisor starts is therefore recorded with enough to recognise it
again later, and a recorded process is only ever signalled after *all* of the
following are re-read from the live process and match the record:

* pid and process start time
* owning user
* canonical executable path
* full argv
* a random per-supervisor nonce, handed to the child in its environment

If any check fails, or cannot be read, the process is left alone and reported.
A surviving orphan is a bad day. Killing a stranger's process is a catastrophe.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import json
import os
from pathlib import Path
import tempfile
from typing import Any, Callable

import psutil


NONCE_ENV = "BITCADENCE_AGENTD_NONCE"
# psutil reports a stable start time for a given process; the tolerance only
# absorbs float round-tripping through JSON.
START_TIME_TOLERANCE_SECONDS = 0.01
REAP_WAIT_SECONDS = 5.0


@dataclass
class ReapReport:
    """What startup reaping did, and - just as important - what it refused to do."""

    killed: list[int] = field(default_factory=list)
    left_alone: list[dict[str, object]] = field(default_factory=list)

    def as_dict(self) -> dict[str, object]:
        return {"killed": list(self.killed), "left_alone": list(self.left_alone)}


def canonical_path(value: str) -> str:
    return os.path.normcase(os.path.realpath(value))


def _owner(process: Any) -> str:
    """The owning user, in the form that is authoritative on this platform."""
    if os.name == "nt":
        return str(process.username())
    return f"uid:{process.uids().real}"


def capture_identity(
    pid: int,
    argv: list[str],
    nonce: str,
    *,
    worker: str | None = None,
    process_factory: Callable[[int], Any] = psutil.Process,
) -> dict[str, object]:
    """Describe a child we just created. Raises if the facts cannot be read.

    A record we could not complete is not written: an incomplete record can
    never be verified later, so it would only ever be noise in the report.
    """
    process = process_factory(pid)
    # Record the command line the OS reports, not the one we asked for: they
    # differ whenever the platform interposes a launcher (Windows runs a batch
    # file as `cmd.exe /c ...`), and verification compares against the OS.
    observed = [str(item) for item in process.cmdline()]
    return {
        "pid": int(pid),
        "create_time": float(process.create_time()),
        "user": _owner(process),
        "exe": canonical_path(str(process.exe())),
        "argv": observed or [str(item) for item in argv],
        "nonce": nonce,
        "worker": worker,
    }


def verify_identity(
    record: dict[str, object],
    *,
    process_factory: Callable[[int], Any] = psutil.Process,
) -> tuple[Any | None, str]:
    """Return ``(process, "")`` only when every recorded fact still holds.

    Otherwise ``(None, reason)``. ``reason`` starts with ``gone:`` when the
    recorded process no longer exists (nothing to report), and with anything
    else when a live process could not be proven to be ours (report it).
    """
    try:
        pid = int(record["pid"])  # type: ignore[arg-type]
        create_time = float(record["create_time"])  # type: ignore[arg-type]
        user = str(record["user"])
        exe = str(record["exe"])
        argv = record["argv"]
        nonce = str(record["nonce"])
    except (KeyError, TypeError, ValueError):
        return None, "record is incomplete (no start time, user, exe, argv or nonce)"
    if not isinstance(argv, list) or not nonce:
        return None, "record is incomplete (no argv or nonce)"

    try:
        process = process_factory(pid)
    except psutil.NoSuchProcess:
        return None, "gone: process no longer exists"
    except psutil.Error as exc:
        return None, f"process could not be inspected: {type(exc).__name__}"

    try:
        if abs(float(process.create_time()) - create_time) > START_TIME_TOLERANCE_SECONDS:
            return None, "gone: pid was recycled (start time differs)"
        if _owner(process) != user:
            return None, "owning user differs"
        if canonical_path(str(process.exe())) != exe:
            return None, "executable path differs"
        if [str(item) for item in process.cmdline()] != [str(item) for item in argv]:
            return None, "argv differs"
        if process.environ().get(NONCE_ENV) != nonce:
            return None, "supervisor nonce is missing or differs"
    except psutil.NoSuchProcess:
        return None, "gone: process exited during inspection"
    except (psutil.Error, OSError) as exc:
        return None, f"identity could not be read: {type(exc).__name__}"
    return process, ""


def kill_verified_tree(process: Any, *, wait_seconds: float = REAP_WAIT_SECONDS) -> None:
    """Kill a verified process and its descendants.

    psutil re-checks pid reuse before every signal, so a pid recycled between
    verification and the kill raises ``NoSuchProcess`` instead of hitting a
    stranger.
    """
    try:
        targets = [*process.children(recursive=True), process]
    except psutil.Error:
        targets = [process]
    for target in targets:
        try:
            target.kill()
        except psutil.Error:
            continue
    psutil.wait_procs(targets, timeout=wait_seconds)


def reap_recorded(
    pidfile: Path,
    *,
    process_factory: Callable[[int], Any] = psutil.Process,
    kill_tree: Callable[[Any], None] = kill_verified_tree,
) -> ReapReport:
    """Kill only the recorded children whose identity is proven; report the rest."""
    report = ReapReport()
    retained: list[dict[str, object]] = []
    for record in read_records(pidfile):
        process, reason = verify_identity(record, process_factory=process_factory)
        if process is not None:
            try:
                kill_tree(process)
            except (psutil.Error, OSError) as exc:
                reason = f"kill failed: {type(exc).__name__}"
            else:
                report.killed.append(int(record["pid"]))  # type: ignore[arg-type]
                continue
        if reason.startswith("gone:"):
            continue
        # Alive, and not provably ours. Leave it alone, keep the record so the
        # operator can see it, and say so.
        retained.append(record)
        report.left_alone.append(
            {"pid": record.get("pid"), "worker": record.get("worker"), "reason": reason}
        )
    write_records(pidfile, retained)
    return report


def read_records(pidfile: Path) -> list[dict[str, object]]:
    try:
        text = pidfile.read_text(encoding="utf-8")
    except (FileNotFoundError, OSError):
        return []
    records: list[dict[str, object]] = []
    for line in text.splitlines():
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            records.append(value)
    return records


def append_record(pidfile: Path, record: dict[str, object]) -> None:
    pidfile.parent.mkdir(parents=True, exist_ok=True)
    with pidfile.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, separators=(",", ":")) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def write_records(pidfile: Path, records: list[dict[str, object]]) -> None:
    text = "".join(json.dumps(r, separators=(",", ":")) + "\n" for r in records)
    atomic_write_text(pidfile, text)


def forget_record(pidfile: Path, pid: int) -> None:
    if not pidfile.exists():
        return
    write_records(pidfile, [r for r in read_records(pidfile) if r.get("pid") != pid])


def atomic_write_text(path: Path, text: str) -> None:
    """Temp file in the same directory, flush + fsync, then atomic replace."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    temporary = Path(temporary_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
