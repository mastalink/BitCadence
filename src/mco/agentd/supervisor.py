"""Worker state machine, restart policy, and fleet reconciliation."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from enum import Enum
import json
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
    backoff_exponent: int = 0
    failure_timestamps: deque[float] = field(default_factory=deque)
    starts: int = 0
    last_exit: int | None = None
    last_error: str | None = None


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
    ) -> None:
        self.adapter = adapter
        self.clock = clock
        self.wall_clock = wall_clock
        self.cwd = cwd or Path.home()
        self.env_factory = env_factory or (lambda: dict(os.environ))
        self.logs = logs
        self.pidfile = pidfile or Path.home() / ".mco" / "agentd.pids"
        self.state_file = state_file or Path.home() / ".mco" / "agentd.state.json"
        self._persistent_state = self._load_persistent_state()
        self.workers: dict[str, WorkerRuntime] = {}
        self._lock = threading.RLock()
        self._started = False

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
                    self._restore_runtime(runtime)
                    if config.mode != "off" and runtime.state != WorkerState.CRASHLOOPED:
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
            runtime.backoff_exponent = 0
            runtime.failure_timestamps.clear()
            runtime.last_error = None
            if runtime.process is not None and runtime.process.poll() is None:
                runtime.state = WorkerState.RUNNING
                self._persist_runtime(runtime)
                return
            if runtime.process is not None:
                self._forget_pid(runtime.process.pid)
                runtime.process = None
                runtime.started_at = None
            runtime.state = WorkerState.STOPPED
            self._persist_runtime(runtime)
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
                and runtime.backoff_exponent
            ):
                runtime.backoff_exponent = 0
                self._persist_runtime(runtime)
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
            runtime.backoff_exponent = 0
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

    def _restore_runtime(self, runtime: WorkerRuntime) -> None:
        raw = self._persistent_state.get(runtime.config.worker, {})
        if not isinstance(raw, dict):
            return
        try:
            runtime.backoff_exponent = max(0, int(raw.get("backoff_exponent", 0)))
            timestamps = deque(float(item) for item in raw.get("failure_timestamps", []))
        except (TypeError, ValueError):
            return
        runtime.failure_timestamps = timestamps
        self._prune_failures(runtime, self.wall_clock())
        if bool(raw.get("crashlooped")) or len(runtime.failure_timestamps) >= CRASH_LIMIT:
            runtime.state = WorkerState.CRASHLOOPED
            runtime.desired_running = runtime.config.mode != "off"
            runtime.last_error = "crash-loop latch restored from persistent state"
        self._persist_runtime(runtime)

    def _load_persistent_state(self) -> dict[str, object]:
        try:
            data = json.loads(self.state_file.read_text(encoding="utf-8"))
        except (FileNotFoundError, OSError, json.JSONDecodeError):
            return {}
        workers = data.get("workers", {}) if isinstance(data, dict) else {}
        return workers if isinstance(workers, dict) else {}

    def _persist_runtime(self, runtime: WorkerRuntime) -> None:
        self._persistent_state[runtime.config.worker] = {
            "backoff_exponent": runtime.backoff_exponent,
            "failure_timestamps": list(runtime.failure_timestamps),
            "crashlooped": runtime.state == WorkerState.CRASHLOOPED,
        }
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.state_file.with_suffix(".json.tmp")
        with temporary.open("w", encoding="utf-8", newline="") as handle:
            json.dump({"version": 1, "workers": self._persistent_state}, handle, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, self.state_file)

    def _forget_pid(self, pid: int) -> None:
        forget = getattr(self.adapter, "forget_pid", None)
        if forget is not None:
            forget(pid)

    def shutdown(self) -> None:
        with self._lock:
            for name in list(self.workers):
                self.stop(name)
            self.adapter.release_supervisor_lock()
            self._started = False

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
