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
        self.events: list[str] = []

    def acquire_supervisor_lock(self) -> None:
        self.events.append("lock")

    def release_supervisor_lock(self) -> None:
        self.events.append("unlock")

    def bind_to_parent_lifetime(self) -> None:
        self.bound += 1
        self.events.append("bind")

    def reap_orphans(self, pidfile: Path) -> list[int]:
        return []

    def spawn(self, argv, cwd, env, stdout, stderr) -> FakeProcess:
        self.events.append("spawn")
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


def make_supervisor(
    adapter: FakeAdapter, clock: FakeClock, tmp_path: Path
) -> Supervisor:
    return Supervisor(
        adapter,
        clock=clock,
        wall_clock=clock,
        state_file=tmp_path / "agentd.state.json",
        pidfile=tmp_path / "agentd.pids",
    )


def test_backoff_is_exponential_and_capped() -> None:
    assert [backoff_delay(i) for i in range(8)] == [1, 2, 4, 8, 16, 32, 60, 60]


def test_five_failures_in_window_crashloop_and_stop_restarting(tmp_path: Path) -> None:
    clock = FakeClock()
    adapter = FakeAdapter()
    supervisor = make_supervisor(adapter, clock, tmp_path)
    supervisor.reconcile({"codex-beast": worker()})
    assert adapter.bound == 1
    assert adapter.events[:3] == ["lock", "bind", "spawn"]

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


def test_more_than_sixty_seconds_resets_backoff_but_not_failure_history(
    tmp_path: Path,
) -> None:
    clock = FakeClock()
    adapter = FakeAdapter()
    supervisor = make_supervisor(adapter, clock, tmp_path)
    supervisor.reconcile({"codex-beast": worker()})

    adapter.processes[-1].returncode = 1
    supervisor.tick()
    assert supervisor.workers["codex-beast"].backoff_exponent == 1
    clock.advance(1)
    supervisor.tick()

    clock.advance(61)
    supervisor.tick()
    assert supervisor.workers["codex-beast"].backoff_exponent == 0
    assert list(supervisor.workers["codex-beast"].failure_timestamps) == [0.0]

    adapter.processes[-1].returncode = 1
    supervisor.tick()
    runtime = supervisor.workers["codex-beast"]
    assert runtime.backoff_exponent == 1
    assert runtime.next_start_at == clock() + 1


def test_worker_failing_every_61_seconds_eventually_latches(tmp_path: Path) -> None:
    clock = FakeClock()
    adapter = FakeAdapter()
    supervisor = make_supervisor(adapter, clock, tmp_path)
    supervisor.reconcile({"codex-beast": worker()})

    for failure in range(5):
        clock.advance(61)
        supervisor.tick()  # reset only the independent backoff exponent
        adapter.processes[-1].returncode = 1
        supervisor.tick()
        if failure < 4:
            assert supervisor.workers["codex-beast"].state == WorkerState.BACKOFF
            clock.advance(1)
            supervisor.tick()

    runtime = supervisor.workers["codex-beast"]
    assert runtime.state == WorkerState.CRASHLOOPED
    assert len(runtime.failure_timestamps) == 5

    restarted_adapter = FakeAdapter()
    restarted = make_supervisor(restarted_adapter, clock, tmp_path)
    restarted.reconcile({"codex-beast": worker()})
    assert restarted.workers["codex-beast"].state == WorkerState.CRASHLOOPED
    assert restarted_adapter.processes == []


def test_backoff_exponent_and_failure_history_survive_restart(tmp_path: Path) -> None:
    clock = FakeClock()
    first_adapter = FakeAdapter()
    first = make_supervisor(first_adapter, clock, tmp_path)
    first.reconcile({"codex-beast": worker()})
    first_adapter.processes[-1].returncode = 1
    first.tick()

    second_adapter = FakeAdapter()
    second = make_supervisor(second_adapter, clock, tmp_path)
    second.reconcile({"codex-beast": worker()})
    runtime = second.workers["codex-beast"]
    assert runtime.backoff_exponent == 1
    assert list(runtime.failure_timestamps) == [0.0]


def test_clean_waker_exit_is_a_failure(tmp_path: Path) -> None:
    clock = FakeClock()
    adapter = FakeAdapter()
    supervisor = make_supervisor(adapter, clock, tmp_path)
    supervisor.reconcile({"codex-beast": worker("waker")})

    adapter.processes[-1].returncode = 0
    supervisor.tick()

    runtime = supervisor.workers["codex-beast"]
    assert runtime.state == WorkerState.BACKOFF
    assert runtime.backoff_exponent == 1


def test_clean_poll_exit_waits_for_next_interval_without_failure(tmp_path: Path) -> None:
    clock = FakeClock()
    adapter = FakeAdapter()
    supervisor = make_supervisor(adapter, clock, tmp_path)
    supervisor.reconcile({"codex-beast": worker("poll", poll_interval=30)})

    adapter.processes[-1].returncode = 0
    supervisor.tick()
    runtime = supervisor.workers["codex-beast"]
    assert runtime.state == WorkerState.STOPPED
    assert runtime.backoff_exponent == 0
    assert len(adapter.processes) == 1

    clock.advance(29)
    supervisor.tick()
    assert len(adapter.processes) == 1
    clock.advance(1)
    supervisor.tick()
    assert len(adapter.processes) == 2


def test_manual_reset_clears_crashloop_and_retries_immediately(tmp_path: Path) -> None:
    clock = FakeClock()
    adapter = FakeAdapter()
    supervisor = make_supervisor(adapter, clock, tmp_path)
    supervisor.reconcile({"codex-beast": worker()})
    runtime = supervisor.workers["codex-beast"]
    runtime.state = WorkerState.CRASHLOOPED
    runtime.backoff_exponent = 5
    runtime.failure_timestamps.extend([0, 1, 2, 3, 4])
    runtime.process = None

    supervisor.reset("codex-beast")
    assert runtime.state == WorkerState.RUNNING
    assert runtime.backoff_exponent == 0
    assert not runtime.failure_timestamps


def test_reset_does_not_duplicate_a_running_process(tmp_path: Path) -> None:
    clock = FakeClock()
    adapter = FakeAdapter()
    supervisor = make_supervisor(adapter, clock, tmp_path)
    supervisor.reconcile({"codex-beast": worker()})

    supervisor.reset("codex-beast")
    assert len(adapter.processes) == 1
    assert supervisor.workers["codex-beast"].state == WorkerState.RUNNING


def test_poll_interval_change_does_not_restart_running_process(tmp_path: Path) -> None:
    clock = FakeClock()
    adapter = FakeAdapter()
    supervisor = make_supervisor(adapter, clock, tmp_path)
    supervisor.reconcile({"codex-beast": worker("poll", poll_interval=30)})
    first = adapter.processes[-1]

    supervisor.reconcile({"codex-beast": worker("poll", poll_interval=90)})
    assert adapter.processes[-1] is first
    assert not first.terminated
