"""ADR 0002 R6 and R2: prove identity before killing; fence workers with locks."""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
import time

import psutil
import pytest

from mco.agentd import identity
from mco.agentd.identity import NONCE_ENV
from mco.agentd.locks import FileLock, LockHeld, worker_identity, worker_lock_path
from mco.agentd.ownership import find_foreign_waker, is_waker_command, owned_pids
from mco.fleet import WorkerConfig


class FakeProcess:
    """A live process as psutil would describe it."""

    def __init__(self, pid=11, create_time=100.0, user="alice", exe="/usr/bin/python",
                 argv=("python", "-m", "mco.cli", "wake"), nonce="n-1", unreadable=False):
        self.pid = pid
        self._create_time = create_time
        self._user = user
        self._exe = exe
        self._argv = list(argv)
        self._nonce = nonce
        self._unreadable = unreadable
        self.killed = False

    def create_time(self):
        return self._create_time

    def username(self):
        return self._user

    def uids(self):
        return type("U", (), {"real": self._user})()

    def exe(self):
        return self._exe

    def cmdline(self):
        return list(self._argv)

    def environ(self):
        if self._unreadable:
            raise psutil.AccessDenied(self.pid)
        return {NONCE_ENV: self._nonce} if self._nonce else {}

    def children(self, recursive=False):
        return []

    def kill(self):
        self.killed = True


def _record(process: FakeProcess, nonce: str = "n-1") -> dict[str, object]:
    return identity.capture_identity(
        process.pid, process.cmdline(), nonce, process_factory=lambda pid: process
    )


def _reap(tmp_path: Path, record: dict[str, object], live) -> tuple[identity.ReapReport, list[FakeProcess]]:
    pidfile = tmp_path / "agentd.pids"
    identity.write_records(pidfile, [record])
    killed: list[FakeProcess] = []

    def factory(pid):
        if live is None:
            raise psutil.NoSuchProcess(pid)
        return live

    report = identity.reap_recorded(pidfile, process_factory=factory, kill_tree=killed.append)
    return report, killed


def test_a_proven_orphan_is_killed_and_its_record_cleared(tmp_path: Path) -> None:
    ours = FakeProcess()
    report, killed = _reap(tmp_path, _record(ours), ours)
    assert report.killed == [11] and killed == [ours] and report.left_alone == []
    assert identity.read_records(tmp_path / "agentd.pids") == []


@pytest.mark.parametrize(
    "stranger, reason",
    [
        (dict(user="bob"), "owning user differs"),
        (dict(exe="/usr/bin/other"), "executable path differs"),
        (dict(argv=("python", "-m", "mco.cli", "serve")), "argv differs"),
        (dict(nonce="someone-elses"), "nonce"),
        (dict(nonce=None), "nonce"),
        (dict(unreadable=True), "could not be read"),
    ],
)
def test_a_process_that_is_not_provably_ours_is_left_alone_and_reported(
    tmp_path: Path, stranger: dict, reason: str
) -> None:
    """Same pid, same start time - and still not ours. This includes a
    legitimately IDENTICAL command line with a different nonce."""
    record = _record(FakeProcess())
    report, killed = _reap(tmp_path, record, FakeProcess(**stranger))
    assert killed == [] and report.killed == []
    assert len(report.left_alone) == 1 and reason in str(report.left_alone[0]["reason"])
    # The record is kept so the operator can still see what was left behind.
    assert identity.read_records(tmp_path / "agentd.pids") == [record]


def test_a_recycled_pid_is_not_killed_and_not_reported(tmp_path: Path) -> None:
    record = _record(FakeProcess())
    report, killed = _reap(tmp_path, record, FakeProcess(create_time=999.0))
    assert killed == [] and report.left_alone == []
    assert identity.read_records(tmp_path / "agentd.pids") == []


def test_an_exited_process_and_an_incomplete_record_are_never_signalled(tmp_path: Path) -> None:
    report, killed = _reap(tmp_path, _record(FakeProcess()), None)
    assert killed == [] and report.left_alone == []
    # A record from the old command-name pidfile format cannot prove anything.
    report, killed = _reap(tmp_path, {"pid": 11, "command": "pythonw.exe"}, FakeProcess())
    assert killed == []
    assert "incomplete" in str(report.left_alone[0]["reason"])


def _sleeper(env_extra: dict[str, str]) -> subprocess.Popen:
    return subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(60)"],
        env={**os.environ, **env_extra},
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )


def test_real_processes_only_the_one_carrying_our_nonce_dies(tmp_path: Path) -> None:
    ours = _sleeper({NONCE_ENV: "generation-a"})
    lookalike = _sleeper({})  # identical command, started by somebody else
    try:
        pidfile = tmp_path / "agentd.pids"
        for process in (ours, lookalike):
            identity.append_record(
                pidfile, identity.capture_identity(process.pid, process.args, "generation-a")
            )
        report = identity.reap_recorded(pidfile)
        assert report.killed == [ours.pid]
        assert ours.wait(timeout=10) is not None
        assert [item["pid"] for item in report.left_alone] == [lookalike.pid]
        assert lookalike.poll() is None
    finally:
        for process in (ours, lookalike):
            if process.poll() is None:
                identity.kill_verified_tree(psutil.Process(process.pid))
            process.wait(timeout=10)


# --- locks (R2) -----------------------------------------------------------


def test_worker_lock_is_exclusive_names_its_holder_and_is_reusable(tmp_path: Path) -> None:
    path = worker_lock_path(tmp_path, "codex", "codex-1")
    first, second = FileLock(path, pid=4321), FileLock(path)
    first.acquire()
    first.acquire()  # idempotent for the holder
    with pytest.raises(LockHeld) as held:
        second.acquire()
    assert held.value.holder == "4321"
    first.release()
    second.acquire()
    second.release()


def test_worker_identity_is_role_plus_instance(tmp_path: Path) -> None:
    assert worker_identity("Codex", "Codex-1") == worker_identity("codex", "codex-1")
    assert worker_lock_path(tmp_path, "codex", "a") != worker_lock_path(tmp_path, "codex", "b")
    assert worker_lock_path(tmp_path, "codex", "a") != worker_lock_path(tmp_path, "claude", "a")


def test_lock_is_released_when_the_holding_process_dies(tmp_path: Path) -> None:
    path = tmp_path / "worker.lock"
    code = (
        "import sys, time; sys.path.insert(0, sys.argv[2]);"
        "from pathlib import Path; from mco.agentd.locks import FileLock;"
        "import os; lock = FileLock(Path(sys.argv[1])); lock.acquire(); print(os.getpid(), flush=True); time.sleep(60)"
    )
    src = str(Path(identity.__file__).resolve().parents[2])
    holder = subprocess.Popen(
        [sys.executable, "-c", code, str(path), src],
        stdout=subprocess.PIPE,
        stdin=subprocess.DEVNULL,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    try:
        # The process that holds the lock reports its own pid: under a venv
        # the interpreter is a child of the launcher that Popen started.
        holding_pid = holder.stdout.readline().strip().decode("ascii")
        with pytest.raises(LockHeld) as held:
            FileLock(path).acquire()
        assert held.value.holder == holding_pid
        identity.kill_verified_tree(psutil.Process(holder.pid))  # hard kill, no cleanup runs
        holder.wait(timeout=10)
        lock = FileLock(path)
        deadline = time.monotonic() + 10
        while True:
            try:
                lock.acquire()
                break
            except LockHeld:
                assert time.monotonic() < deadline
                time.sleep(0.05)
        lock.release()
    finally:
        if holder.poll() is None:
            identity.kill_verified_tree(psutil.Process(holder.pid))
        holder.wait(timeout=10)


@pytest.mark.skipif(os.name == "nt", reason="POSIX singleton; Windows uses a named mutex")
def test_posix_supervisor_singleton_names_the_holding_pid(tmp_path: Path) -> None:
    from mco.agentd.platform.base import SupervisorAlreadyRunning
    from mco.agentd.platform.posix import PosixAdapter

    first = PosixAdapter(pidfile=tmp_path / "pids", lock_path=tmp_path / "agentd.lock")
    second = PosixAdapter(pidfile=tmp_path / "pids", lock_path=tmp_path / "agentd.lock")
    first.acquire_supervisor_lock()
    with pytest.raises(SupervisorAlreadyRunning, match=f"holding pid {os.getpid()}"):
        second.acquire_supervisor_lock()
    first.release_supervisor_lock()
    second.acquire_supervisor_lock()
    second.release_supervisor_lock()


@pytest.mark.skipif(os.name == "nt", reason="POSIX process groups")
def test_posix_child_is_recorded_grouped_and_its_tree_is_killed(tmp_path: Path) -> None:
    from mco.agentd.platform.posix import PosixAdapter

    adapter = PosixAdapter(pidfile=tmp_path / "pids", lock_path=tmp_path / "agentd.lock")
    code = (
        "import subprocess, sys, time;"
        "c = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)']);"
        "print(c.pid, flush=True); time.sleep(60)"
    )
    handle = adapter.spawn(
        [sys.executable, "-c", code], tmp_path, dict(os.environ), subprocess.PIPE, subprocess.PIPE
    )
    try:
        grandchild = psutil.Process(int(handle.stdout.readline()))
        record = identity.read_records(tmp_path / "pids")[0]
        assert record["pid"] == handle.pid and record["nonce"] == adapter.nonce
        assert os.getpgid(handle.pid) == handle.pid
        handle.kill()
        handle.wait(timeout=10)
        handle.close()
        psutil.wait_procs([grandchild], timeout=10)
        assert not grandchild.is_running() or grandchild.status() == psutil.STATUS_ZOMBIE
    finally:
        handle.kill()


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="PR_SET_PDEATHSIG is Linux-only")
def test_linux_child_outlives_the_thread_that_asked_for_it(tmp_path: Path) -> None:
    """PDEATHSIG follows the forking thread. A worker started from a
    short-lived request thread must not be signalled when that thread ends."""
    import threading

    from mco.agentd.platform.posix import PosixAdapter

    adapter = PosixAdapter(pidfile=tmp_path / "pids", lock_path=tmp_path / "agentd.lock")
    handles = []
    requester = threading.Thread(
        target=lambda: handles.append(
            adapter.spawn(
                [sys.executable, "-c", "import time; time.sleep(60)"],
                tmp_path,
                dict(os.environ),
                subprocess.DEVNULL,
                subprocess.DEVNULL,
            )
        )
    )
    requester.start()
    requester.join(timeout=30)
    try:
        time.sleep(1.5)
        assert handles[0].poll() is None
    finally:
        handles[0].kill()
        handles[0].wait(timeout=10)


# --- legacy copies (R2) ---------------------------------------------------


def _waker(role: str = "codex", instance: str = "codex-1") -> WorkerConfig:
    return WorkerConfig("w", role, instance, "waker", "run.cmd", 10.0, 30.0)


def test_waker_command_matching_is_exact() -> None:
    argv = ["pythonw.exe", "-m", "mco.cli", "wake", "--role", "codex", "--exec", "x", "--instance", "codex-1"]
    assert is_waker_command(argv, "codex", "codex-1")
    assert not is_waker_command(argv, "codex", "codex-2")
    assert not is_waker_command(argv, "claude", "codex-1")
    assert not is_waker_command(["python", "-m", "mco.cli", "serve", "--role", "codex"], "codex", None)
    assert not is_waker_command(["python", "-m", "mco.cli", "mcp"], "codex", "codex-1")


def test_a_real_legacy_waker_left_running_is_found_and_ours_is_not(tmp_path: Path) -> None:
    """R2 acceptance: the process of a deleted legacy service is still there."""
    instance = f"held-open-{os.getpid()}"
    legacy = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(60)", "wake", "--role", "codex",
         "--instance", instance],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    try:
        config = _waker(instance=instance)
        # A venv launcher and the interpreter it starts share one command line.
        time.sleep(1.0)  # let a launcher finish starting its interpreter
        tree = owned_pids([legacy])
        assert find_foreign_waker(config) in tree
        assert find_foreign_waker(config, own_pids=tree) is None
        assert find_foreign_waker(_waker(instance="someone-else")) is None
        poll = WorkerConfig("w", "codex", instance, "poll", "run.cmd", 10.0, 30.0)
        assert find_foreign_waker(poll) is None
    finally:
        identity.kill_verified_tree(psutil.Process(legacy.pid))
        legacy.wait(timeout=10)
