"""Worker state machine, restart policy, and fleet reconciliation."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from enum import Enum
import os
from pathlib import Path
import subprocess
import threading
import time
from typing import Callable

from mco import service
from mco.fleet import WorkerConfig

from .logs import LogAggregator
from .platform.base import PlatformAdapter, ProcessHandle


CRASH_LIMIT = 5
CRASH_WINDOW_SECONDS = 300.0
STABLE_RESET_SECONDS = 60.0
MAX_BACKOFF_SECONDS = 60.0


class WorkerState(str, Enum):
    STOPPED = "stopped"
    STARTING = "starting"
    RUNNING = "running"
    BACKOFF = "backoff"
    CRASHLOOPED = "crashlooped"


def backoff_delay(failures: int) -> float:
    """Return 1, 2, 4, ... 60 seconds for zero-based failure count."""
    return min(MAX_BACKOFF_SECONDS, float(2 ** max(0, failures)))


@dataclass
class WorkerRuntime:
    config: WorkerConfig
    state: WorkerState = WorkerState.STOPPED
    process: ProcessHandle | None = None
    desired_running: bool = False
    started_at: float | None = None
    next_start_at: float | None = None
    consecutive_failures: int = 0
    failure_times: deque[float] = field(default_factory=deque)
    starts: int = 0
    last_exit: int | None = None
    last_error: str | None = None


class Supervisor:
    def __init__(
        self,
        adapter: PlatformAdapter,
        *,
        clock: Callable[[], float] = time.monotonic,
        cwd: Path | None = None,
        env_factory: Callable[[], dict[str, str]] | None = None,
        logs: LogAggregator | None = None,
        pidfile: Path | None = None,
    ) -> None:
        self.adapter = adapter
        self.clock = clock
        self.cwd = cwd or Path.home()
        self.env_factory = env_factory or (lambda: dict(os.environ))
        self.logs = logs
        self.pidfile = pidfile or Path.home() / ".mco" / "agentd.pids"
        self.workers: dict[str, WorkerRuntime] = {}
        self._lock = threading.RLock()
        self._started = False

    def startup(self) -> list[int]:
        with self._lock:
            if self._started:
                return []
            self.adapter.bind_to_parent_lifetime()
            reaped = self.adapter.reap_orphans(self.pidfile)
            self._started = True
            return reaped

    def reconcile(self, configs: dict[str, WorkerConfig]) -> None:
        with self._lock:
            for name in set(self.workers) - set(configs):
                self.stop(name)
                del self.workers[name]

            for name, config in configs.items():
                runtime = self.workers.get(name)
                if runtime is None:
                    runtime = self.workers[name] = WorkerRuntime(config=config)
                    if config.mode != "off":
                        self.start(name)
                    continue

                old = runtime.config
                runtime.config = config
                if config.mode == "off":
                    self.stop(name)
                elif old.mode == "off":
                    self.start(name)
                elif self._execution_key(old) != self._execution_key(config):
                    self.restart(name)
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
        with self._lock:
            runtime = self._require(name)
            if runtime.config.mode == "off" or runtime.state == WorkerState.RUNNING:
                return
            if runtime.state == WorkerState.CRASHLOOPED:
                return
            runtime.desired_running = True
            runtime.state = WorkerState.STARTING
            runtime.next_start_at = self.clock()
            self._spawn(runtime)

    def stop(self, name: str) -> None:
        with self._lock:
            runtime = self._require(name)
            runtime.desired_running = False
            runtime.next_start_at = None
            process = runtime.process
            if process is not None and process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except (subprocess.TimeoutExpired, TimeoutError):
                    process.kill()
                    process.wait(timeout=5)
            if process is not None:
                self._forget_pid(process.pid)
            runtime.process = None
            runtime.started_at = None
            runtime.state = WorkerState.STOPPED

    def restart(self, name: str) -> None:
        with self._lock:
            self.stop(name)
            runtime = self._require(name)
            if runtime.config.mode != "off":
                self.start(name)

    def reset(self, name: str) -> None:
        with self._lock:
            runtime = self._require(name)
            runtime.consecutive_failures = 0
            runtime.failure_times.clear()
            runtime.last_error = None
            if runtime.process is not None and runtime.process.poll() is None:
                runtime.state = WorkerState.RUNNING
                return
            if runtime.process is not None:
                self._forget_pid(runtime.process.pid)
                runtime.process = None
                runtime.started_at = None
            runtime.state = WorkerState.STOPPED
            if runtime.config.mode != "off":
                self.start(name)

    def tick(self) -> None:
        with self._lock:
            now = self.clock()
            for runtime in list(self.workers.values()):
                self._tick_worker(runtime, now)

    def _tick_worker(self, runtime: WorkerRuntime, now: float) -> None:
        if runtime.state == WorkerState.RUNNING and runtime.process is not None:
            if (
                runtime.started_at is not None
                and now - runtime.started_at > STABLE_RESET_SECONDS
                and runtime.consecutive_failures
            ):
                runtime.consecutive_failures = 0
                runtime.failure_times.clear()
            returncode = runtime.process.poll()
            if returncode is not None:
                self._handle_exit(runtime, returncode, now)
            return

        if (
            runtime.desired_running
            and runtime.state in {WorkerState.BACKOFF, WorkerState.STARTING, WorkerState.STOPPED}
            and runtime.next_start_at is not None
            and now >= runtime.next_start_at
        ):
            runtime.state = WorkerState.STARTING
            self._spawn(runtime)

    def _spawn(self, runtime: WorkerRuntime) -> None:
        if not runtime.desired_running or runtime.state == WorkerState.CRASHLOOPED:
            return
        if not self._started:
            self.startup()
        now = self.clock()
        try:
            argv = self._argv(runtime.config)
            env = self.env_factory()
            env["AGENT_ROLE"] = runtime.config.role
            if runtime.config.instance:
                env["AGENT_INSTANCE_ID"] = runtime.config.instance
            process = self.adapter.spawn(
                argv,
                self.cwd,
                env,
                subprocess.PIPE,
                subprocess.PIPE,
            )
        except Exception as exc:
            runtime.last_error = str(exc)
            self._record_failure(runtime, now)
            return

        runtime.process = process
        runtime.started_at = now
        runtime.next_start_at = None
        runtime.state = WorkerState.RUNNING
        runtime.starts += 1
        if self.logs is not None:
            self.logs.attach(runtime.config.worker, process)

    @staticmethod
    def _argv(config: WorkerConfig) -> list[str]:
        if config.mode == "waker":
            return service._waker_spec(
                config.role,
                config.exec_command or "",
                config.instance,
                config.min_interval,
            ).argv
        if config.mode == "poll":
            return service._poll_spec(
                config.role,
                config.exec_command or "",
                config.instance,
                config.poll_interval,
            ).argv
        raise ValueError(f"worker {config.worker!r} is disabled")

    def _handle_exit(self, runtime: WorkerRuntime, returncode: int, now: float) -> None:
        process = runtime.process
        uptime = now - runtime.started_at if runtime.started_at is not None else 0.0
        if uptime > STABLE_RESET_SECONDS:
            runtime.consecutive_failures = 0
            runtime.failure_times.clear()
        if process is not None:
            self._forget_pid(process.pid)
        runtime.process = None
        runtime.started_at = None
        runtime.last_exit = returncode

        if not runtime.desired_running:
            runtime.state = WorkerState.STOPPED
        elif runtime.config.mode == "poll" and returncode == 0:
            runtime.state = WorkerState.STOPPED
            runtime.next_start_at = now + runtime.config.poll_interval
        else:
            runtime.last_error = f"process exited with code {returncode}"
            self._record_failure(runtime, now)

    def _record_failure(self, runtime: WorkerRuntime, now: float) -> None:
        runtime.failure_times.append(now)
        while runtime.failure_times and now - runtime.failure_times[0] > CRASH_WINDOW_SECONDS:
            runtime.failure_times.popleft()
        if len(runtime.failure_times) >= CRASH_LIMIT:
            runtime.state = WorkerState.CRASHLOOPED
            runtime.next_start_at = None
            return
        delay = backoff_delay(runtime.consecutive_failures)
        runtime.consecutive_failures += 1
        runtime.state = WorkerState.BACKOFF
        runtime.next_start_at = now + delay

    def _forget_pid(self, pid: int) -> None:
        forget = getattr(self.adapter, "forget_pid", None)
        if forget is not None:
            forget(pid)

    def shutdown(self) -> None:
        with self._lock:
            for name in list(self.workers):
                self.stop(name)

    def status(self, name: str | None = None) -> list[dict[str, object]] | dict[str, object]:
        with self._lock:
            if name is not None:
                return self._snapshot(self._require(name))
            return [self._snapshot(self.workers[key]) for key in sorted(self.workers)]

    def _snapshot(self, runtime: WorkerRuntime) -> dict[str, object]:
        now = self.clock()
        return {
            "name": runtime.config.worker,
            "role": runtime.config.role,
            "instance": runtime.config.instance,
            "mode": runtime.config.mode,
            "state": runtime.state.value,
            "pid": runtime.process.pid if runtime.process is not None else None,
            "uptime_s": (
                max(0.0, now - runtime.started_at) if runtime.started_at is not None else None
            ),
            "restarts": max(0, runtime.starts - 1),
            "last_exit": runtime.last_exit,
            "last_error": runtime.last_error,
        }

    def _require(self, name: str) -> WorkerRuntime:
        try:
            return self.workers[name]
        except KeyError as exc:
            raise KeyError(f"unknown worker: {name}") from exc
