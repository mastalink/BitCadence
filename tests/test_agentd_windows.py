from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys

import pytest

from mco.agentd.identity import NONCE_ENV
from mco.agentd.platform.windows import (
    CREATE_NEW_PROCESS_GROUP,
    CREATE_NO_WINDOW,
    CREATE_SUSPENDED,
    SupervisorAlreadyRunning,
    WindowsAdapter,
    WindowsProcessHandle,
    WindowsSupervisorLock,
)


class FakePopen:
    def __init__(self, pid: int = 4242) -> None:
        self.pid = pid
        self._handle = 99
        self.stdout = None
        self.stderr = None
        self.returncode = None

    def poll(self):
        return self.returncode

    def send_signal(self, value):
        self.returncode = 0

    def terminate(self):
        self.returncode = 0

    def kill(self):
        self.returncode = -9

    def wait(self, timeout=None):
        return self.returncode


def test_spawn_is_invisible_grouped_and_records_identity_before_resume(
    tmp_path: Path, monkeypatch
) -> None:
    captured = {}
    process = FakePopen()

    def popen(argv, **kwargs):
        captured.update(kwargs)
        return process

    monkeypatch.setattr(
        "mco.agentd.platform.windows.identity.capture_identity",
        lambda pid, argv, nonce, **_: {"pid": pid, "argv": argv, "nonce": nonce},
    )
    pidfile = tmp_path / "agentd.pids"
    adapter = WindowsAdapter(pidfile=pidfile, popen_factory=popen, nonce="generation-a")
    adapter._job_handle = 1
    adapter._create_job = lambda: 55
    adapter._close_job = lambda job: None
    adapter._assign_to_job = lambda child, job=None: captured.update(assigned=(child.pid, job))

    def resume(child):
        captured["record_at_resume"] = json.loads(pidfile.read_text().strip())

    adapter._resume_process = resume
    handle = adapter.spawn(["pythonw.exe", "worker.py"], tmp_path, {"A": "1"}, -1, -1)

    required = CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP | CREATE_SUSPENDED
    assert captured["creationflags"] & required == required
    # The child carries the supervisor nonce, and never inherits a console stdin.
    assert captured["env"] == {"A": "1", NONCE_ENV: "generation-a"}
    assert captured["stdin"] == subprocess.DEVNULL
    # Recorded - with the nonce - while still suspended, in its own job.
    assert captured["record_at_resume"] == {
        "pid": 4242,
        "argv": ["pythonw.exe", "worker.py"],
        "nonce": "generation-a",
    }
    assert captured["assigned"] == (4242, 55)
    assert handle.pid == 4242


def test_spawn_that_cannot_be_recorded_never_runs(tmp_path: Path, monkeypatch) -> None:
    process = FakePopen()
    resumed = []

    def unreadable(pid, argv, nonce, **_):
        raise OSError("cannot read the new process")

    monkeypatch.setattr("mco.agentd.platform.windows.identity.capture_identity", unreadable)
    adapter = WindowsAdapter(pidfile=tmp_path / "agentd.pids", popen_factory=lambda *a, **k: process)
    adapter._job_handle = 1
    adapter._resume_process = resumed.append
    with pytest.raises(OSError):
        adapter.spawn(["pythonw.exe", "worker.py"], tmp_path, {}, -1, -1)
    assert process.returncode == -9 and resumed == []


def test_kill_and_close_take_the_whole_job(tmp_path: Path) -> None:
    calls = []
    handle = WindowsProcessHandle(
        FakePopen(), 55, terminate_job=lambda job: calls.append(("terminate", job)),
        close_job=lambda job: calls.append(("close", job)),
    )
    handle.kill()
    handle.close()
    handle.close()  # idempotent
    assert calls == [("terminate", 55), ("terminate", 55), ("close", 55)]


def test_reap_orphans_refuses_a_command_name_match(tmp_path: Path) -> None:
    """Revision 1 killed on a matching command name. That is not proof (R6)."""
    import os

    pidfile = tmp_path / "agentd.pids"
    pidfile.write_text(
        json.dumps({"pid": os.getpid(), "command": "python.exe"}) + "\n", encoding="utf-8"
    )
    adapter = WindowsAdapter(pidfile=pidfile)
    assert adapter.reap_orphans(pidfile) == []
    assert adapter.last_reap_report.left_alone[0]["pid"] == os.getpid()


windows_only = pytest.mark.skipif(sys.platform != "win32", reason="real Windows process APIs")

CHILD = (
    "import ctypes, subprocess, sys, time;"
    "k = ctypes.windll.kernel32; u = ctypes.windll.user32;"
    "h = k.GetConsoleWindow();"
    "print('window', bool(h) and bool(u.IsWindowVisible(h)), flush=True);"
    "g = subprocess.Popen([sys.executable, '-c', sys.argv[1]], stdout=subprocess.PIPE);"
    "print(g.stdout.readline().decode().strip(), flush=True);"
    "print('grandchild', g.pid, flush=True);"
    "time.sleep(120)"
)
GRANDCHILD = (
    "import ctypes, time;"
    "k = ctypes.windll.kernel32; u = ctypes.windll.user32;"
    "h = k.GetConsoleWindow();"
    "print('grandwindow', bool(h) and bool(u.IsWindowVisible(h)), flush=True);"
    "time.sleep(120)"
)


def _real_adapter(tmp_path: Path) -> WindowsAdapter:
    import uuid

    lock = WindowsSupervisorLock(metadata_path=tmp_path / "test.lock")
    lock.name = "Global\\BitCadence-test-" + uuid.uuid4().hex
    return WindowsAdapter(pidfile=tmp_path / "agentd.pids", supervisor_lock=lock)


@windows_only
def test_real_child_and_grandchild_show_no_console_window_and_die_as_a_tree(
    tmp_path: Path,
) -> None:
    import os

    import psutil

    adapter = _real_adapter(tmp_path)
    handle = adapter.spawn(
        [sys.executable, "-c", CHILD, GRANDCHILD],
        tmp_path,
        dict(os.environ),
        subprocess.PIPE,
        subprocess.PIPE,
    )
    try:
        assert handle.stdout.readline().decode().strip() == "window False"
        assert handle.stdout.readline().decode().strip() == "grandwindow False"
        grandchild = psutil.Process(int(handle.stdout.readline().split()[1]))
        assert grandchild.is_running()

        record = json.loads((tmp_path / "agentd.pids").read_text(encoding="utf-8").strip())
        assert record["pid"] == handle.pid and record["nonce"] == adapter.nonce

        handle.kill()  # one call: the child AND everything it started
        handle.wait(timeout=10)
        psutil.wait_procs([grandchild], timeout=10)
        assert not grandchild.is_running()
    finally:
        handle.kill()
        handle.close()


@windows_only
def test_real_orphan_of_a_dead_supervisor_is_reaped_by_the_next_one(tmp_path: Path) -> None:
    import os

    import psutil

    previous = _real_adapter(tmp_path)
    handle = previous.spawn(
        [sys.executable, "-c", "import time; time.sleep(120)"],
        tmp_path,
        dict(os.environ),
        subprocess.DEVNULL,
        subprocess.DEVNULL,
    )
    try:
        # A new supervisor generation starts with the old one's pidfile.
        successor = _real_adapter(tmp_path)
        assert successor.reap_orphans(tmp_path / "agentd.pids") == [handle.pid]
        psutil.wait_procs([psutil.Process(handle.pid)] if psutil.pid_exists(handle.pid) else [], timeout=10)
        assert handle.poll() is not None
        assert (tmp_path / "agentd.pids").read_text(encoding="utf-8") == ""
    finally:
        handle.kill()
        handle.close()


def test_singleton_mutex_is_user_namespaced_and_records_holding_pid(
    tmp_path: Path, monkeypatch
) -> None:
    created = []
    closed = []
    monkeypatch.setattr(
        "mco.agentd.platform.windows._create_user_mutex",
        lambda name, sid: (created.append((name, sid)) or 77, False),
    )
    monkeypatch.setattr(
        "mco.agentd.platform.windows._close_handle", closed.append
    )
    lock = WindowsSupervisorLock(
        tmp_path / "agentd.lock", sid_provider=lambda: "S-1-5-21-123", pid=4321
    )

    lock.acquire()
    assert created == [("Global\\BitCadence-agentd-S-1-5-21-123", "S-1-5-21-123")]
    assert (tmp_path / "agentd.lock").read_text(encoding="ascii") == "4321"
    lock.release()
    assert closed == [77]
    assert not (tmp_path / "agentd.lock").exists()


def test_singleton_conflict_names_the_holding_pid(tmp_path: Path, monkeypatch) -> None:
    metadata = tmp_path / "agentd.lock"
    metadata.write_text("9876", encoding="ascii")
    monkeypatch.setattr(
        "mco.agentd.platform.windows._create_user_mutex", lambda name, sid: (88, True)
    )
    monkeypatch.setattr("mco.agentd.platform.windows._close_handle", lambda handle: None)
    lock = WindowsSupervisorLock(
        metadata, sid_provider=lambda: "S-1-5-21-123", pid=4321
    )

    with pytest.raises(SupervisorAlreadyRunning, match="holding pid 9876"):
        lock.acquire()
