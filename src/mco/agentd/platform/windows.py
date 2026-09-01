"""Windows process and service integration for ``bitcadenced``."""

from __future__ import annotations

import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path
import signal
import subprocess
from typing import Any, Callable

import psutil

from mco import service
from mco.service import ServiceSpec


CREATE_SUSPENDED = 0x00000004
CREATE_NEW_PROCESS_GROUP = 0x00000200
CREATE_NO_WINDOW = 0x08000000
JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
JOB_OBJECT_EXTENDED_LIMIT_INFORMATION = 9


class _IO_COUNTERS(ctypes.Structure):
    _fields_ = [
        ("ReadOperationCount", ctypes.c_ulonglong),
        ("WriteOperationCount", ctypes.c_ulonglong),
        ("OtherOperationCount", ctypes.c_ulonglong),
        ("ReadTransferCount", ctypes.c_ulonglong),
        ("WriteTransferCount", ctypes.c_ulonglong),
        ("OtherTransferCount", ctypes.c_ulonglong),
    ]


class _JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("PerProcessUserTimeLimit", ctypes.c_longlong),
        ("PerJobUserTimeLimit", ctypes.c_longlong),
        ("LimitFlags", wintypes.DWORD),
        ("MinimumWorkingSetSize", ctypes.c_size_t),
        ("MaximumWorkingSetSize", ctypes.c_size_t),
        ("ActiveProcessLimit", wintypes.DWORD),
        ("Affinity", ctypes.c_size_t),
        ("PriorityClass", wintypes.DWORD),
        ("SchedulingClass", wintypes.DWORD),
    ]


class _JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("BasicLimitInformation", _JOBOBJECT_BASIC_LIMIT_INFORMATION),
        ("IoInfo", _IO_COUNTERS),
        ("ProcessMemoryLimit", ctypes.c_size_t),
        ("JobMemoryLimit", ctypes.c_size_t),
        ("PeakProcessMemoryUsed", ctypes.c_size_t),
        ("PeakJobMemoryUsed", ctypes.c_size_t),
    ]


class WindowsProcessHandle:
    """Small adapter that gives Windows a polite process-group stop."""

    def __init__(self, process: subprocess.Popen[Any]):
        self._process = process
        self.pid = process.pid

    @property
    def stdout(self) -> Any:
        return self._process.stdout

    @property
    def stderr(self) -> Any:
        return self._process.stderr

    def poll(self) -> int | None:
        return self._process.poll()

    def terminate(self) -> None:
        try:
            self._process.send_signal(signal.CTRL_BREAK_EVENT)
        except (OSError, ValueError):
            self._process.terminate()

    def kill(self) -> None:
        self._process.kill()

    def wait(self, timeout: float) -> int:
        return self._process.wait(timeout=timeout)


class WindowsAdapter:
    """Spawn invisible children inside a kill-on-close Windows Job Object."""

    def __init__(
        self,
        pidfile: Path | None = None,
        popen_factory: Callable[..., subprocess.Popen[Any]] = subprocess.Popen,
    ) -> None:
        self.pidfile = pidfile or Path.home() / ".mco" / "agentd.pids"
        self._popen_factory = popen_factory
        self._job_handle: int | None = None

    def bind_to_parent_lifetime(self) -> None:
        if self._job_handle is not None:
            return
        if os.name != "nt":
            raise RuntimeError("WindowsAdapter can only be initialized on Windows")

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateJobObjectW.argtypes = [wintypes.LPVOID, wintypes.LPCWSTR]
        kernel32.CreateJobObjectW.restype = wintypes.HANDLE
        kernel32.SetInformationJobObject.argtypes = [
            wintypes.HANDLE,
            ctypes.c_int,
            wintypes.LPVOID,
            wintypes.DWORD,
        ]
        kernel32.SetInformationJobObject.restype = wintypes.BOOL
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel32.CloseHandle.restype = wintypes.BOOL
        handle = kernel32.CreateJobObjectW(None, None)
        if not handle:
            raise ctypes.WinError(ctypes.get_last_error())

        limits = _JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
        limits.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        ok = kernel32.SetInformationJobObject(
            handle,
            JOB_OBJECT_EXTENDED_LIMIT_INFORMATION,
            ctypes.byref(limits),
            ctypes.sizeof(limits),
        )
        if not ok:
            kernel32.CloseHandle(handle)
            raise ctypes.WinError(ctypes.get_last_error())
        self._job_handle = int(handle)

    def spawn(
        self,
        argv: list[str],
        cwd: Path,
        env: dict[str, str],
        stdout: int,
        stderr: int,
    ) -> WindowsProcessHandle:
        self.bind_to_parent_lifetime()
        flags = CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP | CREATE_SUSPENDED
        process = self._popen_factory(
            argv,
            cwd=str(cwd),
            env=env,
            stdout=stdout,
            stderr=stderr,
            creationflags=flags,
        )
        try:
            self._record_pid(process.pid, argv[0])
            self._assign_to_job(process)
            self._resume_process(process)
        except Exception:
            process.kill()
            process.wait()
            self.forget_pid(process.pid)
            raise
        return WindowsProcessHandle(process)

    def _assign_to_job(self, process: subprocess.Popen[Any]) -> None:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        kernel32.AssignProcessToJobObject.restype = wintypes.BOOL
        ok = kernel32.AssignProcessToJobObject(
            wintypes.HANDLE(self._job_handle), wintypes.HANDLE(process._handle)
        )
        if not ok:
            raise ctypes.WinError(ctypes.get_last_error())

    @staticmethod
    def _resume_process(process: subprocess.Popen[Any]) -> None:
        # subprocess closes the initial thread handle. NtResumeProcess resumes
        # every thread using the process handle that Popen intentionally keeps.
        ntdll = ctypes.WinDLL("ntdll")
        ntdll.NtResumeProcess.argtypes = [wintypes.HANDLE]
        ntdll.NtResumeProcess.restype = ctypes.c_long
        status = ntdll.NtResumeProcess(wintypes.HANDLE(process._handle))
        if status != 0:
            raise OSError(f"NtResumeProcess failed with NTSTATUS 0x{status & 0xffffffff:08x}")

    def _record_pid(self, pid: int, command: str) -> None:
        self.pidfile.parent.mkdir(parents=True, exist_ok=True)
        record = {"pid": pid, "command": Path(command).name}
        with self.pidfile.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, separators=(",", ":")) + "\n")
            handle.flush()
            os.fsync(handle.fileno())

    def forget_pid(self, pid: int) -> None:
        if not self.pidfile.exists():
            return
        retained = [r for r in self._read_pidfile(self.pidfile) if r.get("pid") != pid]
        self._write_records(self.pidfile, retained)

    def reap_orphans(self, pidfile: Path) -> list[int]:
        killed: list[int] = []
        for record in self._read_pidfile(pidfile):
            try:
                pid = int(record["pid"])
                expected = Path(str(record["command"])).name.casefold()
                process = psutil.Process(pid)
                cmdline = process.cmdline()
                actual = Path(cmdline[0]).name.casefold() if cmdline else process.name().casefold()
                if actual != expected:
                    continue
                process.kill()
                process.wait(timeout=5)
                killed.append(pid)
            except (KeyError, TypeError, ValueError, psutil.Error):
                continue
        self._write_records(pidfile, [])
        return killed

    @staticmethod
    def _read_pidfile(pidfile: Path) -> list[dict[str, object]]:
        if not pidfile.exists():
            return []
        records: list[dict[str, object]] = []
        for line in pidfile.read_text(encoding="utf-8").splitlines():
            try:
                value = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(value, dict):
                records.append(value)
        return records

    @staticmethod
    def _write_records(pidfile: Path, records: list[dict[str, object]]) -> None:
        pidfile.parent.mkdir(parents=True, exist_ok=True)
        text = "".join(json.dumps(r, separators=(",", ":")) + "\n" for r in records)
        pidfile.write_text(text, encoding="utf-8")

    def install_service(self, spec: ServiceSpec) -> tuple[bool, str]:
        return service._win_install_service(spec)

    def uninstall_service(self, name: str) -> tuple[bool, str]:
        return service._win_uninstall(name)
