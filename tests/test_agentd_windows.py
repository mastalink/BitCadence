from __future__ import annotations

import json
from pathlib import Path

import pytest

from mco.agentd.platform.windows import (
    CREATE_NEW_PROCESS_GROUP,
    CREATE_NO_WINDOW,
    CREATE_SUSPENDED,
    SupervisorAlreadyRunning,
    WindowsAdapter,
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


def test_spawn_is_invisible_grouped_and_records_pid_before_resume(tmp_path: Path) -> None:
    captured = {}
    process = FakePopen()

    def popen(argv, **kwargs):
        captured.update(kwargs)
        return process

    pidfile = tmp_path / "agentd.pids"
    adapter = WindowsAdapter(pidfile=pidfile, popen_factory=popen)
    adapter._job_handle = 1
    adapter._assign_to_job = lambda child: captured.update(assigned=child.pid)

    def resume(child):
        captured["record_at_resume"] = json.loads(pidfile.read_text().strip())

    adapter._resume_process = resume
    handle = adapter.spawn(["pythonw.exe", "worker.py"], tmp_path, {}, -1, -1)

    required = CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP | CREATE_SUSPENDED
    assert captured["creationflags"] & required == required
    assert captured["record_at_resume"] == {"pid": 4242, "command": "pythonw.exe"}
    assert captured["assigned"] == 4242
    assert handle.pid == 4242


class FakePsutilProcess:
    def __init__(self, pid: int, command: str) -> None:
        self.pid = pid
        self.command = command
        self.killed = False

    def cmdline(self):
        return [self.command, "worker.py"]

    def name(self):
        return Path(self.command).name

    def kill(self):
        self.killed = True

    def wait(self, timeout):
        return 0


def test_reap_orphans_requires_command_name_match(tmp_path: Path, monkeypatch) -> None:
    pidfile = tmp_path / "agentd.pids"
    pidfile.write_text(
        '{"pid":11,"command":"pythonw.exe"}\n'
        '{"pid":12,"command":"pythonw.exe"}\n',
        encoding="utf-8",
    )
    processes = {
        11: FakePsutilProcess(11, "C:/Python/pythonw.exe"),
        12: FakePsutilProcess(12, "C:/Windows/notepad.exe"),
    }
    monkeypatch.setattr("mco.agentd.platform.windows.psutil.Process", processes.__getitem__)

    killed = WindowsAdapter(pidfile=pidfile).reap_orphans(pidfile)
    assert killed == [11]
    assert processes[11].killed
    assert not processes[12].killed
    assert pidfile.read_text(encoding="utf-8") == ""


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
