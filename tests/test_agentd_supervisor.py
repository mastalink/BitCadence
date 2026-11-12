from __future__ import annotations

import json
from pathlib import Path
import subprocess

import pytest

from mco.agentd.supervisor import CRASHLOOP_COOLDOWN_SECONDS, Supervisor, WorkerState, backoff_delay
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


def _crashloop(supervisor: Supervisor, adapter: FakeAdapter, clock: FakeClock) -> None:
    for failure in range(5):
        adapter.processes[-1].returncode = 1
        supervisor.tick()
        if failure < 4:
            clock.advance(backoff_delay(failure))
            supervisor.tick()
    assert supervisor.workers["codex-beast"].state == WorkerState.CRASHLOOPED


def test_crashloop_retries_after_cooldown_instead_of_latching_forever(tmp_path: Path) -> None:
    clock = FakeClock()
    adapter = FakeAdapter()
    supervisor = make_supervisor(adapter, clock, tmp_path)
    supervisor.reconcile({"codex-beast": worker()})
    _crashloop(supervisor, adapter, clock)
    spawned = len(adapter.processes)

    clock.advance(CRASHLOOP_COOLDOWN_SECONDS - 1)
    supervisor.tick()
    assert len(adapter.processes) == spawned

    clock.advance(1)
    supervisor.tick()
    runtime = supervisor.workers["codex-beast"]
    assert len(adapter.processes) == spawned + 1
    assert runtime.state == WorkerState.RUNNING
    assert not runtime.failure_timestamps
    assert runtime.backoff_exponent == 0
    assert "cooldown" in (runtime.last_error or "")


def test_worker_that_keeps_failing_latches_again_after_cooldown(tmp_path: Path) -> None:
    clock = FakeClock()
    adapter = FakeAdapter()
    supervisor = make_supervisor(adapter, clock, tmp_path)
    supervisor.reconcile({"codex-beast": worker()})
    _crashloop(supervisor, adapter, clock)
    clock.advance(CRASHLOOP_COOLDOWN_SECONDS)
    supervisor.tick()
    _crashloop(supervisor, adapter, clock)
    spawned = len(adapter.processes)
    clock.advance(60)
    supervisor.tick()
    assert len(adapter.processes) == spawned


def test_latch_restored_from_yesterday_retries_on_first_tick(tmp_path: Path) -> None:
    clock = FakeClock()
    adapter = FakeAdapter()
    supervisor = make_supervisor(adapter, clock, tmp_path)
    supervisor.reconcile({"codex-beast": worker()})
    _crashloop(supervisor, adapter, clock)

    clock.advance(86400)
    restarted_adapter = FakeAdapter()
    restarted = make_supervisor(restarted_adapter, clock, tmp_path)
    restarted.reconcile({"codex-beast": worker()})
    assert restarted.workers["codex-beast"].state == WorkerState.CRASHLOOPED
    restarted.tick()
    assert len(restarted_adapter.processes) == 1
    assert restarted.workers["codex-beast"].state == WorkerState.RUNNING


def test_crashlooped_worker_turned_off_is_not_retried(tmp_path: Path) -> None:
    clock = FakeClock()
    adapter = FakeAdapter()
    supervisor = make_supervisor(adapter, clock, tmp_path)
    supervisor.reconcile({"codex-beast": worker()})
    _crashloop(supervisor, adapter, clock)
    supervisor.reconcile({"codex-beast": worker("off")})
    spawned = len(adapter.processes)
    clock.advance(CRASHLOOP_COOLDOWN_SECONDS * 2)
    supervisor.tick()
    assert len(adapter.processes) == spawned


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


# --- ADR 0002 Revision 2 -------------------------------------------------


def _restart_supervisor(tmp_path: Path, clock: FakeClock, **kwargs) -> tuple[Supervisor, FakeAdapter]:
    """A brand new supervisor process reading the same state directory."""
    adapter = FakeAdapter()
    return (
        Supervisor(
            adapter,
            clock=clock,
            wall_clock=clock,
            state_file=tmp_path / "agentd.state.json",
            pidfile=tmp_path / "agentd.pids",
            **kwargs,
        ),
        adapter,
    )


# R2 - single owner


def test_supervisor_lock_is_taken_before_anything_is_reaped_or_spawned(tmp_path: Path) -> None:
    adapter = FakeAdapter()
    supervisor = make_supervisor(adapter, FakeClock(), tmp_path)
    supervisor.reconcile({"codex-beast": worker()})
    assert adapter.events[:3] == ["lock", "bind", "spawn"]


def test_a_second_supervisor_spawns_nothing_when_the_lock_is_taken(tmp_path: Path) -> None:
    class Taken(FakeAdapter):
        def acquire_supervisor_lock(self) -> None:
            raise RuntimeError("already running for this user; holding pid 4321")

    adapter = Taken()
    supervisor = make_supervisor(adapter, FakeClock(), tmp_path)
    with pytest.raises(RuntimeError, match="holding pid 4321"):
        supervisor.startup()
    assert adapter.spawn_calls == [] and adapter.bound == 0


def test_two_supervisors_cannot_run_the_same_worker(tmp_path: Path) -> None:
    clock = FakeClock()
    first_adapter, second_adapter = FakeAdapter(), FakeAdapter()
    first = make_supervisor(first_adapter, clock, tmp_path)
    second = Supervisor(
        second_adapter,
        clock=clock,
        wall_clock=clock,
        state_file=tmp_path / "other" / "agentd.state.json",
        pidfile=tmp_path / "other" / "agentd.pids",
        lock_dir=first.lock_dir,
    )
    first.reconcile({"codex-beast": worker()})
    second.reconcile({"codex-beast": worker()})

    blocked = second.workers["codex-beast"]
    assert blocked.state == WorkerState.BLOCKED
    assert second_adapter.spawn_calls == []
    assert "another supervisor" in (blocked.last_error or "")
    assert not blocked.failure_timestamps  # being fenced out is not a crash

    # Handoff: once the outgoing owner has really stopped, the incoming one starts.
    first.shutdown()
    clock.advance(31)
    second.tick()
    assert blocked.state == WorkerState.RUNNING
    assert len(second_adapter.spawn_calls) == 1


def test_a_legacy_copy_still_running_blocks_a_second_copy(tmp_path: Path) -> None:
    """R2 acceptance: a legacy service's process outlives its registration."""
    legacy = {"pid": 7777}
    clock = FakeClock()
    supervisor, adapter = _restart_supervisor(
        tmp_path, clock, foreign_owner=lambda config: legacy["pid"]
    )
    supervisor.reconcile({"codex-beast": worker()})
    runtime = supervisor.workers["codex-beast"]
    assert runtime.state == WorkerState.BLOCKED
    assert adapter.spawn_calls == []
    assert "pid 7777" in (runtime.last_error or "")

    for _ in range(10):  # however long it stays, never a second copy, never a crash loop
        clock.advance(31)
        supervisor.tick()
    assert adapter.spawn_calls == [] and runtime.state == WorkerState.BLOCKED

    legacy["pid"] = None  # the legacy process finally exits
    clock.advance(31)
    supervisor.tick()
    assert runtime.state == WorkerState.RUNNING and len(adapter.spawn_calls) == 1


# R3 - crash-loop state


def test_state_file_is_versioned_and_records_the_boot(tmp_path: Path) -> None:
    clock = FakeClock()
    supervisor, adapter = _restart_supervisor(tmp_path, clock, boot_id=lambda: "boot-1")
    supervisor.reconcile({"codex-beast": worker()})
    adapter.processes[-1].returncode = 1
    supervisor.tick()
    document = json.loads((tmp_path / "agentd.state.json").read_text(encoding="utf-8"))
    assert document["version"] == 2
    assert document["boot_id"] == "boot-1"
    assert len(document["workers"]["codex-beast"]["failure_timestamps"]) == 1


def test_a_reboot_does_not_clear_the_crash_loop_latch(tmp_path: Path) -> None:
    clock = FakeClock()
    supervisor, adapter = _restart_supervisor(tmp_path, clock, boot_id=lambda: "boot-1")
    supervisor.reconcile({"codex-beast": worker()})
    _crashloop(supervisor, adapter, clock)
    assert supervisor.workers["codex-beast"].state == WorkerState.CRASHLOOPED

    rebooted, adapter2 = _restart_supervisor(tmp_path, clock, boot_id=lambda: "boot-2")
    rebooted.reconcile({"codex-beast": worker()})
    assert rebooted.workers["codex-beast"].state == WorkerState.CRASHLOOPED
    assert adapter2.spawn_calls == []
    assert rebooted.daemon_status()["state_from_previous_boot"] is True


def test_failures_dated_in_the_future_cannot_pin_or_hide_the_window(tmp_path: Path) -> None:
    (tmp_path / "agentd.state.json").write_text(
        json.dumps(
            {
                "version": 1,  # a state file written by the previous release still loads
                "workers": {
                    "codex-beast": {
                        "backoff_exponent": 2,
                        "failure_timestamps": [9e9, 9e9],
                        "crashlooped": False,
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    clock = FakeClock()
    supervisor, _ = _restart_supervisor(tmp_path, clock)
    supervisor.reconcile({"codex-beast": worker()})
    runtime = supervisor.workers["codex-beast"]
    assert list(runtime.failure_timestamps) == [clock(), clock()]
    assert runtime.backoff_exponent == 2


# R4 - the fleet schema that exists


def test_waker_child_is_mco_wake_with_exec_as_one_shell_string(tmp_path: Path) -> None:
    adapter = FakeAdapter()
    supervisor = make_supervisor(adapter, FakeClock(), tmp_path)
    config = WorkerConfig("w", "codex", "codex-1", "waker", "run.cmd --flag 'two words'", 10.0, 30.0)
    supervisor.reconcile({"w": config})
    argv = adapter.spawn_calls[0]
    assert argv[1:4] == ["-m", "mco.cli", "wake"]
    assert argv[argv.index("--exec") + 1] == "run.cmd --flag 'two words'"
    assert argv[argv.index("--role") + 1] == "codex"
    assert argv[argv.index("--instance") + 1] == "codex-1"


def test_poll_child_is_the_exec_string_split_with_no_console_wrapper(tmp_path: Path) -> None:
    adapter = FakeAdapter()
    supervisor = make_supervisor(adapter, FakeClock(), tmp_path)
    config = WorkerConfig("w", "codex", "codex-1", "poll", "run.cmd --once", 10.0, 30.0)
    supervisor.reconcile({"w": config})
    assert adapter.spawn_calls == [["run.cmd", "--once"]]


def test_mode_off_has_no_child_but_stays_listed(tmp_path: Path) -> None:
    adapter = FakeAdapter()
    supervisor = make_supervisor(adapter, FakeClock(), tmp_path)
    supervisor.reconcile({"codex-beast": worker("off")})
    assert adapter.spawn_calls == []
    snapshot = supervisor.status("codex-beast")
    assert snapshot["state"] == "stopped" and snapshot["desired"] == "off"


def test_a_failing_poll_run_counts_as_a_failure(tmp_path: Path) -> None:
    clock = FakeClock()
    adapter = FakeAdapter()
    supervisor = make_supervisor(adapter, clock, tmp_path)
    supervisor.reconcile({"codex-beast": worker("poll")})
    adapter.processes[-1].returncode = 2
    supervisor.tick()
    runtime = supervisor.workers["codex-beast"]
    assert runtime.state == WorkerState.BACKOFF and len(runtime.failure_timestamps) == 1


# R8 - desired state and overrides


def _changed_exec() -> WorkerConfig:
    return WorkerConfig("codex-beast", "codex", "codex-beast", "waker", "other.cmd", 10.0, 30.0)


def test_manual_stop_survives_an_unrelated_config_edit(tmp_path: Path) -> None:
    adapter = FakeAdapter()
    supervisor = make_supervisor(adapter, FakeClock(), tmp_path)
    supervisor.reconcile({"codex-beast": worker()})
    supervisor.stop("codex-beast")
    spawned = len(adapter.spawn_calls)

    supervisor.reconcile({"codex-beast": _changed_exec()})
    supervisor.tick()
    runtime = supervisor.workers["codex-beast"]
    assert len(adapter.spawn_calls) == spawned
    assert runtime.state == WorkerState.STOPPED
    assert supervisor.status("codex-beast")["desired"] == "stopped"
    assert runtime.config.exec_command == "other.cmd"  # the edit is still taken


def test_manual_stop_survives_a_daemon_restart_until_started(tmp_path: Path) -> None:
    clock = FakeClock()
    supervisor, _ = _restart_supervisor(tmp_path, clock)
    supervisor.reconcile({"codex-beast": worker()})
    supervisor.stop("codex-beast")
    supervisor.shutdown()

    restarted, adapter = _restart_supervisor(tmp_path, clock)
    restarted.reconcile({"codex-beast": worker()})
    restarted.tick()
    assert adapter.spawn_calls == []
    assert restarted.workers["codex-beast"].override == "stopped"

    restarted.start("codex-beast")  # explicitly cleared
    assert len(adapter.spawn_calls) == 1
    again, adapter3 = _restart_supervisor(tmp_path, clock)
    restarted.shutdown()
    again.reconcile({"codex-beast": worker()})
    assert len(adapter3.spawn_calls) == 1


def test_config_change_restarts_a_worker_that_is_wanted(tmp_path: Path) -> None:
    adapter = FakeAdapter()
    supervisor = make_supervisor(adapter, FakeClock(), tmp_path)
    supervisor.reconcile({"codex-beast": worker()})
    supervisor.reconcile({"codex-beast": _changed_exec()})
    assert len(adapter.spawn_calls) == 2
    assert adapter.processes[0].terminated


def test_removed_worker_is_stopped_and_its_persisted_state_forgotten(tmp_path: Path) -> None:
    adapter = FakeAdapter()
    supervisor = make_supervisor(adapter, FakeClock(), tmp_path)
    supervisor.reconcile({"codex-beast": worker()})
    supervisor.stop("codex-beast")
    supervisor.reconcile({})
    assert "codex-beast" not in supervisor.workers
    document = json.loads((tmp_path / "agentd.state.json").read_text(encoding="utf-8"))
    assert document["workers"] == {}


def test_drain_stops_everything_keeps_intent_and_resume_restores_it(tmp_path: Path) -> None:
    clock = FakeClock()
    adapter = FakeAdapter()
    supervisor = make_supervisor(adapter, clock, tmp_path)
    stopped = WorkerConfig("held", "claude", "held", "waker", "c.cmd", 10.0, 30.0)
    supervisor.reconcile({"codex-beast": worker(), "held": stopped})
    supervisor.stop("held")
    before = len(adapter.spawn_calls)

    supervisor.drain()
    assert supervisor.daemon_status()["draining"] is True
    assert all(rt.process is None for rt in supervisor.workers.values())
    clock.advance(120)
    supervisor.tick()
    supervisor.reconcile({"codex-beast": _changed_exec(), "held": stopped})
    assert len(adapter.spawn_calls) == before  # nothing spawns while draining
    assert "lock" in adapter.events and "unlock" not in adapter.events  # ownership kept

    supervisor.resume()
    assert len(adapter.spawn_calls) == before + 1
    assert supervisor.workers["codex-beast"].state == WorkerState.RUNNING
    assert supervisor.workers["held"].state == WorkerState.STOPPED


# R10 - shutdown contract


class StubbornProcess(FakeProcess):
    """Ignores the polite stop; dies only when killed."""

    def terminate(self) -> None:
        self.terminated = True


class ImmortalProcess(StubbornProcess):
    def kill(self) -> None:
        self.killed = True


class ScriptedAdapter(FakeAdapter):
    def __init__(self, factory) -> None:
        super().__init__()
        self.factory = factory

    def spawn(self, argv, cwd, env, stdout, stderr):
        self.events.append("spawn")
        process = self.factory(1000 + len(self.processes))
        self.processes.append(process)
        self.spawn_calls.append(argv)
        return process


def _fast(adapter: FakeAdapter, tmp_path: Path, clock: FakeClock) -> Supervisor:
    return Supervisor(
        adapter,
        clock=clock,
        wall_clock=clock,
        state_file=tmp_path / "agentd.state.json",
        pidfile=tmp_path / "agentd.pids",
        terminate_deadline=0.05,
        kill_deadline=0.05,
    )


def test_child_that_ignores_the_polite_stop_is_killed_after_the_deadline(tmp_path: Path) -> None:
    adapter = ScriptedAdapter(StubbornProcess)
    supervisor = _fast(adapter, tmp_path, FakeClock())
    supervisor.reconcile({"codex-beast": worker()})
    supervisor.shutdown()
    process = adapter.processes[0]
    assert process.terminated and process.killed
    assert adapter.forgotten == [process.pid]
    assert adapter.events[-1] == "unlock"


def test_child_that_survives_the_kill_is_reported_and_never_duplicated(tmp_path: Path) -> None:
    clock = FakeClock()
    adapter = ScriptedAdapter(ImmortalProcess)
    supervisor = _fast(adapter, tmp_path, clock)
    supervisor.reconcile({"codex-beast": worker()})
    supervisor.restart("codex-beast")

    runtime = supervisor.workers["codex-beast"]
    assert runtime.state == WorkerState.BLOCKED
    assert "did not exit" in (runtime.last_error or "") or "has not exited" in (runtime.last_error or "")
    assert len(adapter.spawn_calls) == 1  # no second copy beside the survivor
    assert adapter.forgotten == []  # its pid record is kept for the next reap

    adapter.processes[0].returncode = -9  # it finally dies
    clock.advance(31)
    supervisor.tick()
    assert runtime.state == WorkerState.RUNNING and len(adapter.spawn_calls) == 2


def test_descendants_are_cleaned_up_when_a_child_exits_on_its_own(tmp_path: Path) -> None:
    class Closable(FakeProcess):
        closed = False

        def close(self) -> None:
            self.closed = True

    adapter = ScriptedAdapter(Closable)
    supervisor = make_supervisor(adapter, FakeClock(), tmp_path)
    supervisor.reconcile({"codex-beast": worker()})
    adapter.processes[0].returncode = 1
    supervisor.tick()
    assert adapter.processes[0].closed


@pytest.mark.parametrize("prepare", ["backoff", "crashlooped", "blocked", "running"])
def test_shutdown_in_any_state_leaves_nothing_scheduled(tmp_path: Path, prepare: str) -> None:
    clock = FakeClock()
    blocker = {"pid": 1 if prepare == "blocked" else None}
    supervisor, adapter = _restart_supervisor(
        tmp_path, clock, foreign_owner=lambda config: blocker["pid"]
    )
    supervisor.reconcile({"codex-beast": worker()})
    if prepare == "backoff":
        adapter.processes[-1].returncode = 1
        supervisor.tick()
    elif prepare == "crashlooped":
        _crashloop(supervisor, adapter, clock)
    assert supervisor.workers["codex-beast"].state.value == prepare

    supervisor.shutdown()
    blocker["pid"] = None
    spawned = len(adapter.spawn_calls)
    clock.advance(5000)
    supervisor.tick()
    assert len(adapter.spawn_calls) == spawned
    assert supervisor.workers["codex-beast"].next_start_at is None
    assert adapter.events[-1] == "unlock"
