"""
The execution half of scheduling: launching, state, and the scheduler tick.

`scheduler.py` is pure - models, parsing, and time math with no I/O, so the
tricky parts (cron expansion, loop bounds, next-fire calculation) are testable
without a gateway or a clock. This module is where those decisions turn into
real jobs on the board.

Two entry points:

- `launch(...)` - fire one launcher right now. This is what `mco launch <name>`
  calls, and it is *the same code path* a scheduled fire uses, so a manual run
  and a 3am run are provably identical apart from the audit trail's note about
  which schedule triggered it.
- `tick(...)` - one scheduler pass: find what's due, respect overlap policy,
  launch it, advance state. `run_forever(...)` is just this on a sleep.

State (iteration counts, last fire times, the jobs each fire created) lives in
`~/.mco/schedule-state.json`, written atomically. It is deliberately separate
from `schedules.yaml`: the config is yours to edit and version-control, the
state is the runtime's to own.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional

from mco.scheduler import (
    Launcher,
    Schedule,
    ScheduleState,
    exhaustion_reason,
    due_schedules,
    load_config,
    SCHEDULES_CONFIG_PATH,
)

logger = logging.getLogger("mco.launcher")

SCHEDULE_STATE_PATH = Path.home() / ".mco" / "schedule-state.json"

# In-flight statuses: a schedule with `overlap: skip` won't fire again while any
# job from its previous fire is still in one of these.
_ACTIVE_STATUSES = {"waiting", "needs_approval", "pending", "leased", "in_progress"}


class LaunchError(RuntimeError):
    """Raised when a launcher could not be turned into jobs."""


# ── state persistence ─────────────────────────────────────────────────────────

def _state_path(path: Optional[Path]) -> Path:
    """Resolve the state path at call time, not at import time.

    Binding these as default arguments would freeze whatever the module-level
    constant was when this module was first imported, which makes the path
    impossible to redirect (for tests, or for an alternate MCO home).
    """
    return path if path is not None else SCHEDULE_STATE_PATH


def _config_path(path: Optional[Path]) -> Path:
    """Resolve the schedules.yaml path at call time. See `_state_path`."""
    if path is not None:
        return path
    from mco import scheduler
    return scheduler.SCHEDULES_CONFIG_PATH


def load_state(path: Optional[Path] = None) -> dict[str, ScheduleState]:
    """Read runtime state. A missing or corrupt file yields empty state.

    Corrupt state must never wedge the scheduler: the worst case of starting
    fresh is one duplicate run, while refusing to start means silent downtime
    nobody notices until the nightly job hasn't run for a week.
    """
    path = _state_path(path)
    if not path.is_file():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning(f"schedule state at {path} is unreadable ({exc}); starting fresh")
        return {}
    if not isinstance(raw, dict):
        return {}
    states: dict[str, ScheduleState] = {}
    for name, entry in (raw.get("schedules") or {}).items():
        try:
            states[str(name)] = ScheduleState.from_dict({**entry, "name": name})
        except Exception as exc:
            logger.warning(f"dropping unreadable state for '{name}': {exc}")
    return states


def save_state(states: dict[str, ScheduleState], path: Optional[Path] = None) -> None:
    """Write state atomically, so a crash mid-write can't corrupt the file."""
    path = _state_path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": 1,
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "schedules": {name: state.to_dict() for name, state in states.items()},
    }
    tmp_fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), prefix=".schedule-state-")
    try:
        with os.fdopen(tmp_fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2)
        os.replace(tmp_name, path)
    except Exception:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


# ── launching ─────────────────────────────────────────────────────────────────

def launch(
    launcher: Launcher,
    client: Any,
    trigger: str = "manual",
    schedule_name: Optional[str] = None,
    iteration: Optional[int] = None,
    requires_approval_override: Optional[bool] = None,
) -> list[str]:
    """Turn a launcher into real jobs. Returns the created job ids.

    `trigger` / `schedule_name` / `iteration` are stamped into the job payload so
    the audit trail can answer "what created this job?" months later - the
    question plain cron can never answer.
    """
    origin = {
        "launcher": launcher.name,
        "trigger": trigger,
        "launched_at": datetime.now(timezone.utc).isoformat(),
    }
    if schedule_name:
        origin["schedule"] = schedule_name
    if iteration is not None:
        origin["iteration"] = iteration

    if launcher.url:
        return _launch_url(launcher)
    if launcher.app:
        return _launch_app(launcher)
    if launcher.is_workflow:
        return _launch_workflow(launcher, client, origin)
    return _launch_job(launcher, client, origin, requires_approval_override)


def _launch_url(launcher: Launcher) -> list[str]:
    """Open a URL in the default browser. Returns no job ids - nothing is queued."""
    import webbrowser

    if not webbrowser.open(launcher.url or ""):
        raise LaunchError(
            f"launcher '{launcher.name}': no browser available to open {launcher.url}"
        )
    logger.info(f"launcher '{launcher.name}' opened {launcher.url}")
    return []


def _launch_app(launcher: Launcher) -> list[str]:
    """Start a local program, fully detached from the scheduler.

    Detaching matters twice over: a GUI app outlives the tick that started it,
    and a long-running app must not hold the scheduler open or die with it.
    """
    import subprocess

    argv = [launcher.app or "", *launcher.args]
    kwargs: dict[str, Any] = {
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
    }
    if os.name == "nt":
        # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
        kwargs["creationflags"] = 0x00000008 | 0x00000200
    else:
        kwargs["start_new_session"] = True

    try:
        process = subprocess.Popen(argv, **kwargs)
    except FileNotFoundError as exc:
        raise LaunchError(
            f"launcher '{launcher.name}': program not found: {launcher.app}"
        ) from exc
    except OSError as exc:
        raise LaunchError(f"launcher '{launcher.name}': could not start {launcher.app}: {exc}") from exc
    logger.info(f"launcher '{launcher.name}' started {launcher.app} (pid {process.pid})")
    return []


def _launch_job(
    launcher: Launcher,
    client: Any,
    origin: dict,
    requires_approval_override: Optional[bool],
) -> list[str]:
    requires_approval = (
        launcher.requires_approval
        if requires_approval_override is None
        else requires_approval_override
    )
    result = client.send(
        to_role=launcher.role,
        title=launcher.title or launcher.name,
        instructions=launcher.instructions or launcher.title or launcher.name,
        to_instance=launcher.instance,
        requires_approval=requires_approval,
        max_retries=launcher.max_retries,
        escalate_to_role=launcher.escalate_to_role,
        extra_payload={"origin": origin},
    )
    job = (result or {}).get("job") or {}
    job_id = job.get("id")
    if not (result or {}).get("success") or not job_id:
        raise LaunchError(f"launcher '{launcher.name}' failed to create a job: {result}")
    return [job_id]


def _launch_workflow(launcher: Launcher, client: Any, origin: dict) -> list[str]:
    from mco.orchestrator.workflows import WorkflowError, load_workflow, submit_workflow

    path = Path(launcher.workflow or "").expanduser()
    if not path.is_file():
        raise LaunchError(
            f"launcher '{launcher.name}': workflow file not found: {path}"
        )
    try:
        # allow_path is intentionally False here - we read the file ourselves and
        # hand over text, so the loader is never given a path to resolve.
        workflow = load_workflow(path.read_text(encoding="utf-8"))
        job_ids = submit_workflow(client, workflow)
    except WorkflowError as exc:
        raise LaunchError(f"launcher '{launcher.name}': {exc}") from exc
    logger.info(f"launcher '{launcher.name}' submitted workflow with {len(job_ids)} steps")
    return list(job_ids.values())


# ── the tick ──────────────────────────────────────────────────────────────────

def has_active_jobs(client: Any, job_ids: list[str]) -> bool:
    """True if any of these jobs is still in flight (drives `overlap: skip`).

    One `jobs()` call rather than one lookup per id - a workflow launcher can
    create a dozen jobs per fire, and this runs on every tick.

    Fails *open*: if the gateway can't be reached we assume nothing is running
    rather than silently pausing every schedule on a transient network blip. A
    duplicate run is recoverable and visible on the board; a scheduler that
    quietly stopped firing is neither.
    """
    if not job_ids:
        return False
    wanted = set(job_ids)
    try:
        board = client.jobs(include_archived=False)
    except Exception as exc:
        logger.debug(f"overlap check could not reach the gateway: {exc}")
        return False
    for job in board or []:
        if not isinstance(job, dict):
            continue
        if job.get("id") in wanted and job.get("status") in _ACTIVE_STATUSES:
            return True
    return False


def queue_is_empty(client: Any, role: str) -> Optional[bool]:
    """Is this role's queue clear of unfinished work?

    Returns None when the gateway can't be reached - the caller must treat
    "unknown" as "keep going" rather than silently declaring victory and
    ending a loop early.
    """
    try:
        board = client.jobs(include_archived=False)
    except Exception as exc:
        logger.debug(f"until_empty check could not reach the gateway: {exc}")
        return None
    for job in board or []:
        if not isinstance(job, dict):
            continue
        if job.get("target_agent_role") == role and job.get("status") in _ACTIVE_STATUSES:
            return False
    return True


def tick(
    client: Any,
    config_path: Optional[Path] = None,
    state_path: Optional[Path] = None,
    now: Optional[datetime] = None,
    dry_run: bool = False,
) -> list[dict]:
    """Run one scheduler pass. Returns a report row per schedule considered.

    Each row: {schedule, action, detail, job_ids}. `action` is one of
    fired / skipped-overlap / exhausted / error / would-fire (dry run).
    """
    now = now or datetime.now(timezone.utc)
    config_path, state_path = _config_path(config_path), _state_path(state_path)
    launchers, schedules = load_config(config_path)
    states = load_state(state_path)
    report: list[dict] = []
    dirty = False

    for schedule in due_schedules(schedules.values(), states, now):
        state = states.setdefault(schedule.name, ScheduleState(name=schedule.name))
        launcher = launchers[schedule.launcher]

        # Condition-based stop: the "work the backlog until it's clear" loop.
        # Checked before overlap so a drained queue ends the loop cleanly rather
        # than looking like a skipped tick forever.
        if schedule.until_empty:
            empty = queue_is_empty(client, schedule.until_empty)
            if empty is True:
                state.exhausted_reason = f"{schedule.until_empty} queue is empty"
                dirty = True
                report.append({
                    "schedule": schedule.name,
                    "action": "complete",
                    "detail": f"{schedule.until_empty} queue is empty - loop finished",
                    "job_ids": [],
                })
                continue
            # empty is None (gateway unreachable): fall through and keep going.
            # Declaring victory on a failed lookup would silently end the loop.

        if schedule.overlap == "skip" and state.last_job_ids and has_active_jobs(client, state.last_job_ids):
            report.append({
                "schedule": schedule.name,
                "action": "skipped-overlap",
                "detail": f"previous run still active ({len(state.last_job_ids)} job(s))",
                "job_ids": [],
            })
            continue

        if dry_run:
            report.append({
                "schedule": schedule.name,
                "action": "would-fire",
                "detail": launcher.describe(),
                "job_ids": [],
            })
            continue

        try:
            job_ids = launch(
                launcher,
                client,
                trigger=schedule.kind,
                schedule_name=schedule.name,
                iteration=state.iterations + 1,
                requires_approval_override=schedule.requires_approval,
            )
        except Exception as exc:
            logger.error(f"schedule '{schedule.name}' failed to launch: {exc}")
            report.append({
                "schedule": schedule.name,
                "action": "error",
                "detail": str(exc),
                "job_ids": [],
            })
            continue

        state.iterations += 1
        state.last_run_at = now
        state.last_job_ids = job_ids
        dirty = True

        finished = exhaustion_reason(schedule, state, now)
        if finished:
            state.exhausted_reason = finished
        report.append({
            "schedule": schedule.name,
            "action": "fired",
            "detail": (
                f"iteration {state.iterations}"
                + (f" - {finished}, loop complete" if finished else "")
            ),
            "job_ids": job_ids,
        })
        logger.info(
            f"schedule '{schedule.name}' fired (iteration {state.iterations}): {job_ids}"
        )

    if dirty:
        save_state(states, state_path)
    return report


def run_forever(
    client: Any,
    config_path: Optional[Path] = None,
    state_path: Optional[Path] = None,
    interval: float = 30.0,
    on_report: Optional[Callable[[list[dict]], None]] = None,
    max_ticks: Optional[int] = None,
) -> None:
    """Tick on a fixed interval until interrupted.

    A bad config or an unreachable gateway must not kill the daemon - it logs
    and keeps ticking, because the failure mode operators actually suffer is a
    scheduler that quietly died three weeks ago.
    """
    ticks = 0
    while max_ticks is None or ticks < max_ticks:
        try:
            report = tick(client, config_path, state_path)
            from mco.config import get_config
            heartbeat = get_config().get("MCO_SCHEDULER_HEARTBEAT_FILE")
            if heartbeat:
                Path(heartbeat).parent.mkdir(parents=True, exist_ok=True)
                Path(heartbeat).touch()
            if report and on_report:
                on_report(report)
        except Exception as exc:
            logger.error(f"scheduler tick failed: {exc}")
        ticks += 1
        if max_ticks is not None and ticks >= max_ticks:
            break
        time.sleep(interval)
