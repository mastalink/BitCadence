from __future__ import annotations

from pathlib import Path
import subprocess

from mco.agentd.supervisor import Supervisor, WorkerState, backoff_delay
from mco.fleet import WorkerConfig


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class FakeProcess:
    def __init__(self, pid: int) -> None:
        self.pid = pid
        self.returncode = None
        self.terminated = False
        self.killed = False

    def poll(self):
        return self.returncode

    def terminate(self) -> None:
        self.terminated = True
        self.returncode = 0

    def kill(self) -> None:
        self.killed = True
        self.returncode = -9

    def wait(self, timeout: float) -> int:
        if self.returncode is None:
            raise subprocess.TimeoutExpired("fake", timeout)
        return self.returncode


class FakeAdapter:
    def __init__(self) -> None:
        self.processes: list[FakeProcess] = []
        self.spawn_calls: list[list[str]] = []
        self.forgotten: list[int] = []
        self.bound = 0

    def bind_to_parent_lifetime(self) -> None:
        self.bound += 1

    def reap_orphans(self, pidfile: Path) -> list[int]:
        return []

    def spawn(self, argv, cwd, env, stdout, stderr) -> FakeProcess:
        process = FakeProcess(1000 + len(self.processes))
        self.processes.append(process)
        self.spawn_calls.append(argv)
        return process

    def forget_pid(self, pid: int) -> None:
        self.forgotten.append(pid)

    def install_service(self, spec):
        return True, "ok"

    def uninstall_service(self, name):
        return True, "ok"


def worker(mode: str = "waker", poll_interval: float = 30.0) -> WorkerConfig:
    return WorkerConfig(
        worker="codex-beast",
        role="codex",
        instance="codex-beast",
        mode=mode,
        exec_command="worker.cmd",
        min_interval=10.0,
        poll_interval=poll_interval,
    )


def test_backoff_is_exponential_and_capped() -> None:
    assert [backoff_delay(i) for i in range(8)] == [1, 2, 4, 8, 16, 32, 60, 60]


def test_five_failures_in_window_crashloop_and_stop_restarting() -> None:
    clock = FakeClock()
    adapter = FakeAdapter()
    supervisor = Supervisor(adapter, clock=clock)
    supervisor.reconcile({"codex-beast": worker()})
    assert adapter.bound == 1

    for failure in range(5):
        adapter.processes[-1].returncode = 1
        supervisor.tick()
        if failure < 4:
            assert supervisor.workers["codex-beast"].state == WorkerState.BACKOFF
            clock.advance(backoff_delay(failure))
            supervisor.tick()

    runtime = supervisor.workers["codex-beast"]
    assert runtime.state == WorkerState.CRASHLOOPED
    spawn_count = len(adapter.processes)
    clock.advance(600)
    supervisor.tick()
    assert len(adapter.processes) == spawn_count


def test_more_than_sixty_seconds_up_resets_failure_counter() -> None:
    clock = FakeClock()
    adapter = FakeAdapter()
    supervisor = Supervisor(adapter, clock=clock)
    supervisor.reconcile({"codex-beast": worker()})

    adapter.processes[-1].returncode = 1
    supervisor.tick()
    assert supervisor.workers["codex-beast"].consecutive_failures == 1
    clock.advance(1)
    supervisor.tick()

    clock.advance(61)
    supervisor.tick()
    assert supervisor.workers["codex-beast"].consecutive_failures == 0
    assert not supervisor.workers["codex-beast"].failure_times

    adapter.processes[-1].returncode = 1
    supervisor.tick()
    runtime = supervisor.workers["codex-beast"]
    assert runtime.consecutive_failures == 1
    assert runtime.next_start_at == clock() + 1


def test_clean_poll_exit_waits_for_next_interval_without_failure() -> None:
    clock = FakeClock()
    adapter = FakeAdapter()
    supervisor = Supervisor(adapter, clock=clock)
    supervisor.reconcile({"codex-beast": worker("poll", poll_interval=30)})

    adapter.processes[-1].returncode = 0
    supervisor.tick()
    runtime = supervisor.workers["codex-beast"]
    assert runtime.state == WorkerState.STOPPED
    assert runtime.consecutive_failures == 0
    assert len(adapter.processes) == 1

    clock.advance(29)
    supervisor.tick()
    assert len(adapter.processes) == 1
    clock.advance(1)
    supervisor.tick()
    assert len(adapter.processes) == 2


def test_manual_reset_clears_crashloop_and_retries_immediately() -> None:
    clock = FakeClock()
    adapter = FakeAdapter()
    supervisor = Supervisor(adapter, clock=clock)
    supervisor.reconcile({"codex-beast": worker()})
    runtime = supervisor.workers["codex-beast"]
    runtime.state = WorkerState.CRASHLOOPED
    runtime.consecutive_failures = 5
    runtime.failure_times.extend([0, 1, 2, 3, 4])
    runtime.process = None

    supervisor.reset("codex-beast")
    assert runtime.state == WorkerState.RUNNING
    assert runtime.consecutive_failures == 0
    assert not runtime.failure_times


def test_reset_does_not_duplicate_a_running_process() -> None:
    clock = FakeClock()
    adapter = FakeAdapter()
    supervisor = Supervisor(adapter, clock=clock)
    supervisor.reconcile({"codex-beast": worker()})

    supervisor.reset("codex-beast")
    assert len(adapter.processes) == 1
    assert supervisor.workers["codex-beast"].state == WorkerState.RUNNING


def test_poll_interval_change_does_not_restart_running_process() -> None:
    clock = FakeClock()
    adapter = FakeAdapter()
    supervisor = Supervisor(adapter, clock=clock)
    supervisor.reconcile({"codex-beast": worker("poll", poll_interval=30)})
    first = adapter.processes[-1]

    supervisor.reconcile({"codex-beast": worker("poll", poll_interval=90)})
    assert adapter.processes[-1] is first
    assert not first.terminated
