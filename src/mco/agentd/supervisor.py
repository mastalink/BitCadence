"""Worker state machine, restart policy, and fleet reconciliation.

Contracts this module owns (ADR 0002, Revision 2):

* **Crash loop (R3).** Two independent pieces of state. ``backoff_exponent``
  resets after 60s of healthy uptime; ``failure_timestamps`` never does - only
  age (300s) prunes it. Both, and the latch, are persisted.
* **Reboots (R3).** Persisted failure times are wall-clock epoch seconds, so
  they mean the same thing after a reboot, and the state file records the boot
  it was written in. A reboot deliberately does **not** clear the latch or a
  manual stop: only age, the cooldown, or an explicit reset/start does.
* **Desired state (R8).** What the operator asked for is persisted separately
  from what is observed. A manual stop is a durable override: it survives
  config reconciliation and a daemon restart until start/restart/reset clears
  it.
* **Single owner (R2).** Each child runs under an OS-held lock keyed by
  role + instance, so two supervisors cannot run the same worker.
* **Shutdown (R10).** Polite stop, then ``TERMINATE_DEADLINE_SECONDS``, then a
  tree kill, then ``KILL_DEADLINE_SECONDS``. When a child exits on its own its
  surviving descendants are killed too: the supervisor owns the whole tree.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from enum import Enum
import json
import os
from pathlib import Path
import shlex
import subprocess
import threading
import time
from typing import Callable

import psutil

from mco import service
from mco.fleet import WorkerConfig

from .identity import atomic_write_text
from .locks import FileLock, LockHeld, worker_lock_path
from .logs import LogAggregator
from .platform.base import PlatformAdapter, ProcessHandle


CRASH_LIMIT = 5
CRASH_WINDOW_SECONDS = 300.0
STABLE_RESET_SECONDS = 60.0
MAX_BACKOFF_SECONDS = 60.0
# A crash-loop latch used to be permanent: one bad five minutes (a token that
# could not be read, a gateway restarting) took a worker off the fleet until a
# person noticed and reset it, and nobody did for days. The latch still stops
# a hot loop, but after this cooldown the worker gets a fresh set of attempts.
CRASHLOOP_COOLDOWN_SECONDS = 900.0
# Shutdown contract (R10): how long a child gets to stop politely, and how
# long the tree kill that follows is given to take effect.
TERMINATE_DEADLINE_SECONDS = 10.0
KILL_DEADLINE_SECONDS = 5.0
# A worker another owner is already running is not failing - it is simply not
# ours to start yet. Look again at this interval; never count it as a crash.
BLOCKED_RETRY_SECONDS = 30.0
STATE_SCHEMA_VERSION = 2
OVERRIDE_STOPPED = "stopped"


class WorkerState(str, Enum):
    STOPPED = "stopped"
    STARTING = "starting"
    RUNNING = "running"
    BACKOFF = "backoff"
    CRASHLOOPED = "crashlooped"
    BLOCKED = "blocked"


def backoff_delay(failures: int) -> float:
    """Return 1, 2, 4, ... 60 seconds for zero-based failure count."""
    return min(MAX_BACKOFF_SECONDS, float(2 ** max(0, failures)))


def current_boot_id() -> str:
    """Identify this boot, so persisted state can say which boot wrote it."""
    try:
        return str(int(psutil.boot_time()))
    except Exception:
        return "unknown"


@dataclass
class WorkerRuntime:
    config: WorkerConfig
    state: WorkerState = WorkerState.STOPPED
    process: ProcessHandle | None = None
    # Persisted operator intent. None means "follow fleet.toml".
    override: str | None = None
    started_at: float | None = None
    next_start_at: float | None = None
    backoff_exponent: int = 0
    failure_timestamps: deque[float] = field(default_factory=deque)
    starts: int = 0
    last_exit: int | None = None
    last_error: str | None = None
    lock: FileLock | None = None
    # A child that survived the kill deadline. It keeps its lock and its pid
    # record until it is really gone, so nothing can start a second copy.
    stuck: ProcessHandle | None = None

    @property
    def desired_running(self) -> bool:
        """Desired state: fleet.toml says run, and the operator has not stopped it."""
        return self.config.mode != "off" and self.override is None


class Supervisor:
    def __init__(
        self,
        adapter: PlatformAdapter,
        *,
        clock: Callable[[], float] = time.monotonic,
        wall_clock: Callable[[], float] = time.time,
        cwd: Path | None = None,
        env_factory: Callable[[], dict[str, str]] | None = None,
        logs: LogAggregator | None = None,
        pidfile: Path | None = None,
        state_file: Path | None = None,
        lock_dir: Path | None = None,
        foreign_owner: Callable[[WorkerConfig], int | None] | None = None,
        boot_id: Callable[[], str] = current_boot_id,
        terminate_deadline: float = TERMINATE_DEADLINE_SECONDS,
        kill_deadline: float = KILL_DEADLINE_SECONDS,
    ) -> None:
        self.adapter = adapter
        self.clock = clock
        self.wall_clock = wall_clock
        self.cwd = cwd or Path.home()
        self.env_factory = env_factory or (lambda: dict(os.environ))
        self.logs = logs
        self.pidfile = pidfile or Path.home() / ".mco" / "agentd.pids"
        self.state_file = state_file or Path.home() / ".mco" / "agentd.state.json"
        self.lock_dir = lock_dir or self.state_file.parent / "locks"
        # Finds a copy of this worker that something else is already running
        # (a legacy per-worker service, a second checkout). None disables it.
        self.foreign_owner = foreign_owner
        self.boot_id = boot_id()
        self.terminate_deadline = terminate_deadline
        self.kill_deadline = kill_deadline
        self.state_boot_id: str | None = None
        self._persistent_state = self._load_persistent_state()
        self.workers: dict[str, WorkerRuntime] = {}
        self.reap_report: dict[str, object] = {"killed": [], "left_alone": []}
        self._lock = threading.RLock()
        self._started = False
        self._draining = False

    def startup(self) -> list[int]:
        with self._lock:
            if self._started:
                return []
            self.adapter.acquire_supervisor_lock()
            try:
                self.adapter.bind_to_parent_lifetime()
                reaped = self.adapter.reap_orphans(self.pidfile)
            except Exception:
                self.adapter.release_supervisor_lock()
                raise
            report = getattr(self.adapter, "last_reap_report", None)
            self.reap_report = (
                report.as_dict() if report is not None else {"killed": list(reaped), "left_alone": []}
            )
            self._started = True
            self._draining = False
            return reaped

    def reconcile(self, configs: dict[str, WorkerConfig]) -> None:
        with self._lock:
            for name in set(self.workers) - set(configs):
                self._halt(self.workers[name])
                del self.workers[name]
                self._forget_persisted(name)

            for name, config in configs.items():
                runtime = self.workers.get(name)
                if runtime is None:
                    runtime = self.workers[name] = WorkerRuntime(config=config)
                    self._restore_runtime(runtime)
                    if runtime.desired_running and runtime.state != WorkerState.CRASHLOOPED:
                        self._begin(runtime)
                    continue

                old = runtime.config
                runtime.config = config
                if config.mode == "off":
                    self._halt(runtime)
                elif not runtime.desired_running:
                    # A manual stop is a durable override (R8): an unrelated
                    # config edit must not restart a worker the operator
                    # deliberately stopped.
                    continue
                elif old.mode == "off":
                    self._begin(runtime)
                elif self._execution_key(old) != self._execution_key(config):
                    self._halt(runtime)
                    self._begin(runtime)
                # poll_interval deliberately does not participate: it applies
                # when the current/next poll completes, without a restart.

    @staticmethod
    def _execution_key(config: WorkerConfig) -> tuple[object, ...]:
        return (
            config.mode,
            config.role,
            config.instance,
            config.exec_command,
            config.min_interval,
        )

    def start(self, name: str) -> None:
        """Operator start: clears a manual stop, then runs the worker."""
        with self._lock:
            runtime = self._require(name)
            if runtime.override is not None:
                runtime.override = None
                self._persist_runtime(runtime)
            if runtime.config.mode == "off" or runtime.state == WorkerState.RUNNING:
                return
            if runtime.state == WorkerState.CRASHLOOPED:
                return
            self._begin(runtime)

    def stop(self, name: str) -> None:
        """Operator stop: a durable override, kept until start/restart/reset."""
        with self._lock:
            runtime = self._require(name)
            runtime.override = OVERRIDE_STOPPED
            self._halt(runtime)
            self._persist_runtime(runtime)

    def restart(self, name: str) -> None:
        with self._lock:
            runtime = self._require(name)
            self._halt(runtime)
            runtime.override = None
            self._persist_runtime(runtime)
            if runtime.config.mode != "off":
                self._begin(runtime)

    def reset(self, name: str) -> None:
        with self._lock:
            runtime = self._require(name)
            runtime.backoff_exponent = 0
            runtime.failure_timestamps.clear()
            runtime.last_error = None
            runtime.override = None
            if runtime.process is not None and runtime.process.poll() is None:
                runtime.state = WorkerState.RUNNING
                self._persist_runtime(runtime)
                return
            if runtime.process is not None:
                self._release(runtime, runtime.process)
                runtime.process = None
                runtime.started_at = None
            runtime.state = WorkerState.STOPPED
            self._persist_runtime(runtime)
            if runtime.config.mode != "off":
                self._begin(runtime)

    def drain(self) -> None:
        """Upgrade step (R8): stop spawning and stop every child, bounded.

        Overrides and crash-loop state are untouched, and the supervisor keeps
        its ownership lock, so ``resume`` (or the next daemon) picks up exactly
        the desired state that was in force.
        """
        with self._lock:
            self._draining = True
            self._halt_all()

    def resume(self) -> None:
        with self._lock:
            self._draining = False
            for runtime in self.workers.values():
                if (
                    runtime.desired_running
                    and runtime.process is None
                    and runtime.state != WorkerState.CRASHLOOPED
                ):
                    self._begin(runtime)

    @property
    def draining(self) -> bool:
        return self._draining

    def tick(self) -> None:
        with self._lock:
            now = self.clock()
            for runtime in list(self.workers.values()):
                self._tick_worker(runtime, now)

    def _tick_worker(self, runtime: WorkerRuntime, now: float) -> None:
        if runtime.stuck is not None and runtime.stuck.poll() is not None:
            self._release(runtime, runtime.stuck)
            runtime.stuck = None

        if runtime.state == WorkerState.RUNNING and runtime.process is not None:
            if (
                runtime.started_at is not None
                and now - runtime.started_at > STABLE_RESET_SECONDS
                and runtime.backoff_exponent
            ):
                runtime.backoff_exponent = 0
                self._persist_runtime(runtime)
            returncode = runtime.process.poll()
            if returncode is not None:
                self._handle_exit(runtime, returncode, now)
            return

        if runtime.state == WorkerState.CRASHLOOPED:
            self._maybe_leave_crashloop(runtime, now)
            return

        if (
            runtime.desired_running
            and not self._draining
            and runtime.state
            in {
                WorkerState.BACKOFF,
                WorkerState.STARTING,
                WorkerState.STOPPED,
                WorkerState.BLOCKED,
            }
            and runtime.next_start_at is not None
            and now >= runtime.next_start_at
        ):
            runtime.state = WorkerState.STARTING
            self._spawn(runtime)

    def _maybe_leave_crashloop(self, runtime: WorkerRuntime, now: float) -> None:
        if not runtime.desired_running or runtime.config.mode == "off" or self._draining:
            return
        last_failure = max(runtime.failure_timestamps, default=None)
        if last_failure is not None and self.wall_clock() - last_failure < CRASHLOOP_COOLDOWN_SECONDS:
            return
        runtime.failure_timestamps.clear()
        runtime.backoff_exponent = 0
        runtime.last_error = (
            f"retrying after a {int(CRASHLOOP_COOLDOWN_SECONDS)}s crash-loop cooldown"
            + (f" (was: {runtime.last_error})" if runtime.last_error else "")
        )
        runtime.state = WorkerState.STARTING
        runtime.next_start_at = now
        self._persist_runtime(runtime)
        self._spawn(runtime)

    def _begin(self, runtime: WorkerRuntime) -> None:
        if not runtime.desired_running or self._draining:
            return
        if runtime.process is not None and runtime.process.poll() is None:
            return
        runtime.state = WorkerState.STARTING
        runtime.next_start_at = self.clock()
        self._spawn(runtime)

    def _spawn(self, runtime: WorkerRuntime) -> None:
        if (
            not runtime.desired_running
            or runtime.state == WorkerState.CRASHLOOPED
            or self._draining
        ):
            return
        if not self._started:
            self.startup()
        now = self.clock()

        if runtime.stuck is not None:
            if runtime.stuck.poll() is None:
                self._block(runtime, now, "the previous process has not exited after a kill")
                return
            self._release(runtime, runtime.stuck)
            runtime.stuck = None

        # Single-owner fence (R2): the per-worker lock, then a look for a copy
        # that predates the lock (a legacy per-worker service still running).
        config = runtime.config
        lock = runtime.lock or FileLock(
            worker_lock_path(self.lock_dir, config.role, config.instance)
        )
        runtime.lock = None
        try:
            lock.acquire()  # a no-op when this runtime already holds it
        except LockHeld as exc:
            self._block(
                runtime,
                now,
                f"another supervisor is running this worker (lock held by pid {exc.holder})",
            )
            return
        except OSError as exc:
            runtime.last_error = f"could not take the worker lock: {exc}"
            self._record_failure(runtime, now)
            return
        if self.foreign_owner is not None:
            try:
                foreign = self.foreign_owner(config)
            except Exception:
                foreign = None
            if foreign is not None:
                lock.release()
                self._block(
                    runtime,
                    now,
                    f"pid {foreign} is already running this worker outside the supervisor "
                    "(a legacy per-worker service?); not starting a second copy",
                )
                return

        try:
            argv = self._argv(config)
            env = self.env_factory()
            env["AGENT_ROLE"] = config.role
            if config.instance:
                env["AGENT_INSTANCE_ID"] = config.instance
            process = self.adapter.spawn(
                argv,
                self.cwd,
                env,
                subprocess.PIPE,
                subprocess.PIPE,
            )
        except Exception as exc:
            lock.release()
            runtime.last_error = str(exc)
            self._record_failure(runtime, now)
            return

        runtime.lock = lock
        runtime.process = process
        runtime.started_at = now
        runtime.next_start_at = None
        runtime.state = WorkerState.RUNNING
        runtime.starts += 1
        if self.logs is not None:
            self.logs.attach(config.worker, process)

    def _block(self, runtime: WorkerRuntime, now: float, reason: str) -> None:
        runtime.state = WorkerState.BLOCKED
        runtime.last_error = reason
        runtime.next_start_at = now + BLOCKED_RETRY_SECONDS

    @staticmethod
    def _argv(config: WorkerConfig) -> list[str]:
        if config.mode == "waker":
            return _console_python(
                service._waker_spec(
                    config.role,
                    config.exec_command or "",
                    config.instance,
                    config.min_interval,
                ).argv
            )
        if config.mode == "poll":
            # `exec` is a shell string (R4), split the same way the per-worker
            # service split it. The service wrapped batch files in
            # `conhost --headless` to hide the window Task Scheduler opens;
            # a supervisor child is created with no window at all, and that
            # wrapper would swallow the output the log aggregator needs.
            argv = shlex.split(config.exec_command or "", posix=os.name != "nt")
            if os.name == "nt":
                # Non-POSIX splitting keeps the quotes around a quoted path;
                # as argv items they would be passed to the program literally.
                argv = [
                    item[1:-1] if len(item) >= 2 and item[0] == item[-1] and item[0] in "\"'" else item
                    for item in argv
                ]
            if not argv:
                raise ValueError(f"worker {config.worker!r} has an empty exec")
            return argv
        raise ValueError(f"worker {config.worker!r} is disabled")

    def _handle_exit(self, runtime: WorkerRuntime, returncode: int, now: float) -> None:
        process = runtime.process
        uptime = now - runtime.started_at if runtime.started_at is not None else 0.0
        if uptime > STABLE_RESET_SECONDS:
            runtime.backoff_exponent = 0
        if process is not None:
            self._release(runtime, process)
        runtime.process = None
        runtime.started_at = None
        runtime.last_exit = returncode

        if not runtime.desired_running:
            runtime.state = WorkerState.STOPPED
        elif runtime.config.mode == "poll" and returncode == 0:
            runtime.state = WorkerState.STOPPED
            runtime.next_start_at = now + runtime.config.poll_interval
            self._persist_runtime(runtime)
        else:
            runtime.last_error = f"process exited with code {returncode}"
            self._record_failure(runtime, now)

    def _record_failure(self, runtime: WorkerRuntime, now: float) -> None:
        failed_at = self.wall_clock()
        runtime.failure_timestamps.append(failed_at)
        self._prune_failures(runtime, failed_at)
        if len(runtime.failure_timestamps) >= CRASH_LIMIT:
            runtime.state = WorkerState.CRASHLOOPED
            runtime.next_start_at = None
            self._persist_runtime(runtime)
            return
        delay = backoff_delay(runtime.backoff_exponent)
        runtime.backoff_exponent += 1
        runtime.state = WorkerState.BACKOFF
        runtime.next_start_at = now + delay
        self._persist_runtime(runtime)

    @staticmethod
    def _prune_failures(runtime: WorkerRuntime, now: float) -> None:
        while (
            runtime.failure_timestamps
            and now - runtime.failure_timestamps[0] > CRASH_WINDOW_SECONDS
        ):
            runtime.failure_timestamps.popleft()

    # -- stopping -----------------------------------------------------------

    def _halt(self, runtime: WorkerRuntime) -> None:
        """Stop this worker's child without changing what the operator wants."""
        runtime.next_start_at = None
        process = runtime.process
        if process is not None:
            if process.poll() is None:
                try:
                    process.terminate()
                except Exception:
                    pass
            self._finish_stop(runtime, process, time.monotonic() + self.terminate_deadline)
        runtime.process = None
        runtime.started_at = None
        runtime.state = WorkerState.STOPPED

    def _halt_all(self) -> None:
        """Stop every child against ONE shared deadline, not one per worker."""
        live: list[tuple[WorkerRuntime, ProcessHandle]] = []
        for runtime in self.workers.values():
            runtime.next_start_at = None
            if runtime.process is not None:
                live.append((runtime, runtime.process))
        for _, process in live:
            if process.poll() is None:
                try:
                    process.terminate()
                except Exception:
                    pass
        deadline = time.monotonic() + self.terminate_deadline
        for runtime, process in live:
            self._finish_stop(runtime, process, deadline)
        for runtime in self.workers.values():
            runtime.process = None
            runtime.started_at = None
            if runtime.state != WorkerState.CRASHLOOPED:
                runtime.state = WorkerState.STOPPED

    def _finish_stop(self, runtime: WorkerRuntime, process: ProcessHandle, deadline: float) -> None:
        """Wait out the terminate deadline, then kill the tree, then give up loudly."""
        if not self._wait(process, max(0.0, deadline - time.monotonic())):
            try:
                process.kill()
            except Exception:
                pass
            if not self._wait(process, self.kill_deadline):
                # Still alive after a kill. Keep its lock and pid record so no
                # second copy can start, and say so instead of pretending.
                runtime.stuck = process
                runtime.last_error = (
                    f"pid {process.pid} did not exit within "
                    f"{self.terminate_deadline + self.kill_deadline:.0f}s of being stopped"
                )
                return
        self._release(runtime, process)

    @staticmethod
    def _wait(process: ProcessHandle, timeout: float) -> bool:
        if process.poll() is not None:
            return True
        try:
            process.wait(timeout=timeout)
        except (subprocess.TimeoutExpired, TimeoutError):
            return process.poll() is not None
        except Exception:
            return process.poll() is not None
        return True

    def _release(self, runtime: WorkerRuntime, process: ProcessHandle) -> None:
        """The child is gone: kill what it left behind, then give up ownership."""
        close = getattr(process, "close", None)
        if close is not None:
            try:
                close()  # terminates surviving descendants (R10)
            except Exception:
                pass
        self._forget_pid(process.pid)
        if runtime.lock is not None:
            runtime.lock.release()
            runtime.lock = None

    # -- persistence --------------------------------------------------------

    def _restore_runtime(self, runtime: WorkerRuntime) -> None:
        raw = self._persistent_state.get(runtime.config.worker, {})
        if not isinstance(raw, dict):
            return
        try:
            runtime.backoff_exponent = max(0, int(raw.get("backoff_exponent", 0)))
            timestamps = deque(float(item) for item in raw.get("failure_timestamps", []))
        except (TypeError, ValueError):
            return
        now = self.wall_clock()
        # A failure "in the future" means the wall clock moved backwards. It
        # must not become a way to hide failures, nor to pin them forever.
        runtime.failure_timestamps = deque(min(item, now) for item in timestamps)
        self._prune_failures(runtime, now)
        if raw.get("override") == OVERRIDE_STOPPED:
            runtime.override = OVERRIDE_STOPPED
        if bool(raw.get("crashlooped")) or len(runtime.failure_timestamps) >= CRASH_LIMIT:
            runtime.state = WorkerState.CRASHLOOPED
            runtime.last_error = "crash-loop latch restored from persistent state"
        self._persist_runtime(runtime)

    def _load_persistent_state(self) -> dict[str, object]:
        try:
            data = json.loads(self.state_file.read_text(encoding="utf-8"))
        except (FileNotFoundError, OSError, json.JSONDecodeError):
            return {}
        if not isinstance(data, dict):
            return {}
        boot = data.get("boot_id")
        self.state_boot_id = str(boot) if boot is not None else None
        workers = data.get("workers", {})
        return workers if isinstance(workers, dict) else {}

    def _persist_runtime(self, runtime: WorkerRuntime) -> None:
        self._persistent_state[runtime.config.worker] = {
            "backoff_exponent": runtime.backoff_exponent,
            "failure_timestamps": list(runtime.failure_timestamps),
            "crashlooped": runtime.state == WorkerState.CRASHLOOPED,
            "override": runtime.override,
        }
        self._write_persistent_state()

    def _forget_persisted(self, name: str) -> None:
        if self._persistent_state.pop(name, None) is not None:
            self._write_persistent_state()

    def _write_persistent_state(self) -> None:
        document = {
            "version": STATE_SCHEMA_VERSION,
            # Which boot wrote this. Failure times are wall-clock, so they stay
            # meaningful across a reboot; this records that one happened.
            "boot_id": self.boot_id,
            "workers": self._persistent_state,
        }
        atomic_write_text(self.state_file, json.dumps(document, sort_keys=True) + "\n")

    def _forget_pid(self, pid: int) -> None:
        forget = getattr(self.adapter, "forget_pid", None)
        if forget is not None:
            forget(pid)

    def shutdown(self) -> None:
        with self._lock:
            self._draining = True
            self._halt_all()
            self.adapter.release_supervisor_lock()
            self._started = False

    # -- reporting ----------------------------------------------------------

    def daemon_status(self) -> dict[str, object]:
        with self._lock:
            return {
                "draining": self._draining,
                "state_schema": STATE_SCHEMA_VERSION,
                "state_from_previous_boot": (
                    self.state_boot_id is not None and self.state_boot_id != self.boot_id
                ),
                "orphans": dict(self.reap_report),
            }

    def status(self, name: str | None = None) -> list[dict[str, object]] | dict[str, object]:
        with self._lock:
            if name is not None:
                return self._snapshot(self._require(name))
            return [self._snapshot(self.workers[key]) for key in sorted(self.workers)]

    def _snapshot(self, runtime: WorkerRuntime) -> dict[str, object]:
        now = self.clock()
        if runtime.config.mode == "off":
            desired = "off"
        else:
            desired = "running" if runtime.desired_running else "stopped"
        return {
            "name": runtime.config.worker,
            "role": runtime.config.role,
            "instance": runtime.config.instance,
            "mode": runtime.config.mode,
            "state": runtime.state.value,
            "desired": desired,
            "override": runtime.override,
            "pid": runtime.process.pid if runtime.process is not None else None,
            "uptime_s": (
                max(0.0, now - runtime.started_at) if runtime.started_at is not None else None
            ),
            "next_start_in_s": (
                max(0.0, runtime.next_start_at - now)
                if runtime.next_start_at is not None
                else None
            ),
            "restarts": max(0, runtime.starts - 1),
            "failures_in_window": len(runtime.failure_timestamps),
            "last_exit": runtime.last_exit,
            "last_error": runtime.last_error,
        }

    def _require(self, name: str) -> WorkerRuntime:
        try:
            return self.workers[name]
        except KeyError as exc:
            raise KeyError(f"unknown worker: {name}") from exc


def _console_python(argv: list[str]) -> list[str]:
    """Run supervised Python children under ``python.exe``, never ``pythonw.exe``.

    A per-worker *service* needed ``pythonw.exe`` so Task Scheduler would not
    open a console. A supervisor child is different: it is created with
    ``CREATE_NO_WINDOW``, which gives a console program a console with no
    window. That hidden console is inherited by everything the worker starts,
    so nothing below it can pop a window either. ``pythonw.exe`` has no console
    at all, so the first console program it launches (the worker's own
    ``exec``) gets a brand new, visible one - the exact popup this design
    exists to remove - and its output never reaches the log pipes.
    """
    if os.name != "nt" or not argv:
        return argv
    directory, name = os.path.split(argv[0])
    if name.casefold() != "pythonw.exe":
        return argv
    candidate = os.path.join(directory, "python.exe")
    return [candidate, *argv[1:]] if os.path.exists(candidate) else argv
