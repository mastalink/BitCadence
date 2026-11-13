"""Exclusive named-resource lease arbiter with a priority queue and hard deadlines.

Built for a single shared GPU: several independent, unrelated processes (a
nightly content factory, an overnight training job, a standing local
inference server) each want the whole card and none of them knows about the
others. This gives them one shared, crash-safe arbiter instead of three
independent schedules that silently collide.

State is one JSON file per arbiter instance, written with atomic replace, so
a crashed holder leaves state readable by the next caller — no daemon has to
stay alive for the hard-deadline guarantee to fire. `enforce_deadlines` is
meant to be invoked on its own independent schedule (a Scheduled Task, a cron
line), separate from any lease holder's process tree, which is what makes the
guarantee hold even if the holder crashed outright.

Two kinds of deadline:
- A per-acquire `deadline` (ISO 8601) is a soft handoff: when it passes, the
  holder is swapped for the next queued waiter, but the resource stays busy
  (no idle transition fires) if the queue is non-empty.
- A resource's `hard_deadline_local` ("HH:MM") is a non-negotiable daily
  cutoff: when local time passes it, the holder AND queue are cleared
  unconditionally and the resource goes idle, regardless of per-acquire
  deadlines or anyone still wanting the queue slot. This is what guarantees
  a paused background occupant (e.g. a local inference server) is always
  back by a fixed time, even if the night's workload ran long or crashed.
"""
from __future__ import annotations

import json
import os
import subprocess
import tempfile
from dataclasses import asdict, dataclass, field
from datetime import datetime, time as dtime, timedelta, timezone
from pathlib import Path
from typing import Any

from filelock import FileLock


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _parse(ts: str) -> datetime:
    dt = datetime.fromisoformat(ts)
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def next_daily_deadline(hhmm: str, now: datetime, tz: timezone = timezone.utc) -> datetime:
    """The next occurrence (today or tomorrow) of local HH:MM, in `tz`."""
    hour, minute = (int(x) for x in hhmm.split(":"))
    local_now = now.astimezone(tz)
    candidate = local_now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if candidate <= local_now:
        candidate += timedelta(days=1)
    return candidate


@dataclass
class Waiter:
    owner: str
    priority: int
    requested_at: str
    deadline: str | None = None


@dataclass
class Holder:
    owner: str
    priority: int
    acquired_at: str
    deadline: str | None = None


@dataclass
class ResourceState:
    holder: Holder | None = None
    queue: list[Waiter] = field(default_factory=list)


class ResourceArbiter:
    """One JSON file, one exclusive lease per resource_id, a priority queue
    behind it. Config carries the host-specific idle/busy hooks (shell
    commands) and each resource's hard daily deadline — this module itself
    has no opinion about what resource it's arbitrating."""

    def __init__(self, state_path: Path, config: dict[str, Any] | None = None):
        self.state_path = Path(state_path)
        self.config = config or {}
        tz_name = self.config.get("timezone")
        if tz_name:
            from zoneinfo import ZoneInfo
            self.tz = ZoneInfo(tz_name)
        else:
            self.tz = datetime.now().astimezone().tzinfo
        # An OS-level advisory lock on a sidecar file, held across every
        # _read() ... _write() critical section below. acquire/release/
        # enforce_deadlines are invoked by independent, unrelated processes
        # (the CLI is re-invoked fresh per call) with no other synchronization
        # between them, so without this lock two processes can both read
        # holder=None and both be granted the same "exclusive" lease.
        self._lock = FileLock(str(self.state_path) + ".lock",
                               timeout=self.config.get("lock_timeout_seconds", 30))
        if not self.state_path.exists():
            self.state_path.parent.mkdir(parents=True, exist_ok=True)
            with self._lock:
                self._write({})

    # -- persistence --
    def _read(self) -> dict[str, ResourceState]:
        raw = json.loads(self.state_path.read_text(encoding="utf-8") or "{}")
        out = {}
        for rid, rs in raw.items():
            holder = Holder(**rs["holder"]) if rs.get("holder") else None
            queue = [Waiter(**w) for w in rs.get("queue", [])]
            out[rid] = ResourceState(holder=holder, queue=queue)
        return out

    def _write(self, states: dict[str, ResourceState]) -> None:
        raw = {
            rid: {
                "holder": asdict(rs.holder) if rs.holder else None,
                "queue": [asdict(w) for w in rs.queue],
            }
            for rid, rs in states.items()
        }
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=str(self.state_path.parent), prefix=".arbiter-", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(raw, f, indent=2)
            os.replace(tmp, self.state_path)
        except Exception:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise

    # -- core operations --
    def acquire(self, resource_id: str, owner: str, *, priority: int = 0,
                deadline: str | None = None, now: datetime | None = None) -> dict:
        """Try to take the named resource. Returns {"granted": True, ...} if
        `owner` now holds it, or {"granted": False, "position": N} if queued
        behind the current holder. Re-entrant: an owner that already holds or
        is already queued gets its existing entry refreshed, not duplicated.
        Fires the resource's `on_idle_to_busy` hook exactly once, the moment
        the resource goes from unheld to held."""
        now = now or _now()
        with self._lock:
            states = self._read()
            rs = states.setdefault(resource_id, ResourceState())
            self._apply_hard_deadline(resource_id, rs, now)
            self._apply_soft_deadline(rs, now)

            if rs.holder and rs.holder.owner == owner:
                rs.holder.deadline = deadline
                rs.holder.priority = priority
                self._write(states)
                return {"granted": True, "holder": rs.holder.owner}

            was_idle = rs.holder is None
            if was_idle:
                rs.holder = Holder(owner=owner, priority=priority,
                                    acquired_at=now.isoformat(), deadline=deadline)
                rs.queue = [w for w in rs.queue if w.owner != owner]
                self._write(states)
                self._run_hook(resource_id, "on_idle_to_busy")
                return {"granted": True, "holder": owner}

            existing = next((w for w in rs.queue if w.owner == owner), None)
            if existing:
                existing.priority, existing.deadline = priority, deadline
            else:
                rs.queue.append(Waiter(owner=owner, priority=priority,
                                        requested_at=now.isoformat(), deadline=deadline))
            rs.queue.sort(key=lambda w: (-w.priority, w.requested_at))
            self._write(states)
            position = [w.owner for w in rs.queue].index(owner) + 1
            return {"granted": False, "holder": rs.holder.owner, "position": position}

    def release(self, resource_id: str, owner: str, *, now: datetime | None = None) -> dict:
        """Release `owner`'s hold (no-op if it is not the current holder) and
        promote the next queued waiter, if any. Fires `on_busy_to_idle` only
        when the queue is empty, so a handoff between two queued owners never
        pauses/restores the underlying resource in between."""
        now = now or _now()
        with self._lock:
            states = self._read()
            rs = states.get(resource_id)
            if rs is None or rs.holder is None or rs.holder.owner != owner:
                return {"released": False}
            self._promote(rs, now)
            states[resource_id] = rs
            self._write(states)
            if rs.holder is None:
                self._run_hook(resource_id, "on_busy_to_idle")
            return {"released": True, "new_holder": rs.holder.owner if rs.holder else None}

    def status(self, resource_id: str, *, now: datetime | None = None) -> dict:
        now = now or _now()
        with self._lock:
            states = self._read()
            rs = states.setdefault(resource_id, ResourceState())
            hard = self._apply_hard_deadline(resource_id, rs, now)
            soft = self._apply_soft_deadline(rs, now)
            if hard or soft:
                self._write(states)
            return {
                "holder": asdict(rs.holder) if rs.holder else None,
                "queue": [asdict(w) for w in rs.queue],
            }

    def enforce_deadlines(self, *, now: datetime | None = None) -> list[dict]:
        """Sweep every known resource for expired soft and hard deadlines.
        Meant to run on its own independent schedule (not a lease holder's
        process), so the hard deadline fires even if the holder crashed.
        Returns one event dict per resource whose holder actually changed."""
        now = now or _now()
        with self._lock:
            states = self._read()
            events = []
            for resource_id, rs in states.items():
                before = rs.holder.owner if rs.holder else None
                hard = self._apply_hard_deadline(resource_id, rs, now)
                soft = self._apply_soft_deadline(rs, now) if not hard else False
                after = rs.holder.owner if rs.holder else None
                if (hard or soft) and before != after:
                    events.append({"resource_id": resource_id, "expired_owner": before,
                                    "new_holder": after, "hard": hard})
            if events:
                self._write(states)
            return events

    # -- internals --
    def _apply_soft_deadline(self, rs: ResourceState, now: datetime) -> bool:
        if rs.holder and rs.holder.deadline and _parse(rs.holder.deadline) <= now:
            self._promote(rs, now)
            return True
        return False

    def _apply_hard_deadline(self, resource_id: str, rs: ResourceState, now: datetime) -> bool:
        """Today's hard cutoff, direct comparison against `now`'s own
        calendar day. Once it fires the resource is idle, so it is a no-op
        for the rest of the day until the next acquire re-arms it."""
        hhmm = self.config.get("resources", {}).get(resource_id, {}).get("hard_deadline_local")
        if not hhmm or (rs.holder is None and not rs.queue):
            return False
        hour, minute = (int(x) for x in hhmm.split(":"))
        local_now = now.astimezone(self.tz)
        today_deadline = local_now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if local_now < today_deadline:
            return False
        was_busy = rs.holder is not None
        rs.holder, rs.queue = None, []
        if was_busy:
            self._run_hook(resource_id, "on_busy_to_idle")
        return True

    def _promote(self, rs: ResourceState, now: datetime) -> None:
        rs.holder = None
        if rs.queue:
            nxt = rs.queue.pop(0)
            rs.holder = Holder(owner=nxt.owner, priority=nxt.priority,
                                acquired_at=now.isoformat(), deadline=nxt.deadline)

    def _run_hook(self, resource_id: str, hook_name: str) -> None:
        cmd = (self.config.get("resources", {}).get(resource_id, {}).get(hook_name, {}) or {}).get("run")
        if not cmd:
            return
        subprocess.run(cmd, check=False, timeout=self.config.get("hook_timeout_seconds", 120))


def _load_config(config_path: str | None) -> dict:
    if not config_path:
        return {}
    return json.loads(Path(config_path).read_text(encoding="utf-8"))


def main(argv: list[str] | None = None) -> int:
    """CLI so non-Python callers (a PowerShell nightly script, a Codex
    worker's shell step) can drive the arbiter without a Python dependency
    of their own beyond invoking this module.

    usage:
      python -m mco.orchestrator.resource_arbiter acquire <resource> <owner> \
          [--priority N] [--deadline ISO8601] [--state PATH] [--config PATH]
      python -m mco.orchestrator.resource_arbiter release <resource> <owner> [--state PATH] [--config PATH]
      python -m mco.orchestrator.resource_arbiter status <resource> [--state PATH] [--config PATH]
      python -m mco.orchestrator.resource_arbiter watchdog [--state PATH] [--config PATH]

    Exit code is 0 with the result JSON on stdout for acquire/release/status
    (including a denied acquire — check the "granted" field, don't rely on
    the exit code, since "queued" is an expected outcome, not an error).
    watchdog exits 0 with a JSON list of the resources it force-expired.
    """
    import argparse

    parser = argparse.ArgumentParser(prog="resource_arbiter")
    parser.add_argument("--state", default=os.environ.get("ARBITER_STATE_PATH", "resource_arbiter_state.json"))
    parser.add_argument("--config", default=os.environ.get("ARBITER_CONFIG_PATH"))
    sub = parser.add_subparsers(dest="command", required=True)

    p_acq = sub.add_parser("acquire")
    p_acq.add_argument("resource")
    p_acq.add_argument("owner")
    p_acq.add_argument("--priority", type=int, default=0)
    p_acq.add_argument("--deadline", default=None)

    p_rel = sub.add_parser("release")
    p_rel.add_argument("resource")
    p_rel.add_argument("owner")

    p_stat = sub.add_parser("status")
    p_stat.add_argument("resource")

    sub.add_parser("watchdog")

    args = parser.parse_args(argv)
    arbiter = ResourceArbiter(Path(args.state), _load_config(args.config))

    if args.command == "acquire":
        result = arbiter.acquire(args.resource, args.owner, priority=args.priority, deadline=args.deadline)
    elif args.command == "release":
        result = arbiter.release(args.resource, args.owner)
    elif args.command == "status":
        result = arbiter.status(args.resource)
    else:
        result = arbiter.enforce_deadlines()

    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
