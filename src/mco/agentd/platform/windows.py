"""Windows process and per-user singleton integration for ``bitcadenced``."""

from __future__ import annotations

import ctypes
from ctypes import wintypes
import os
from pathlib import Path
import secrets
import signal
import subprocess
from typing import Any, Callable

from mco import service
from mco.service import ServiceSpec

from .. import identity
from ..identity import NONCE_ENV, ReapReport
from ..tokens import current_user_sid
from .base import SupervisorAlreadyRunning


CREATE_SUSPENDED = 0x00000004
CREATE_NEW_PROCESS_GROUP = 0x00000200
CREATE_NO_WINDOW = 0x08000000
JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
JOB_OBJECT_EXTENDED_LIMIT_INFORMATION = 9
ERROR_ALREADY_EXISTS = 183
SDDL_REVISION_1 = 1


class _SECURITY_ATTRIBUTES(ctypes.Structure):
    _fields_ = [
        ("nLength", wintypes.DWORD),
        ("lpSecurityDescriptor", wintypes.LPVOID),
        ("bInheritHandle", wintypes.BOOL),
    ]


def _create_user_mutex(name: str, sid: str) -> tuple[int, bool]:
    """Create a named mutex whose DACL grants access only to the owning SID."""
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
    security_descriptor = wintypes.LPVOID()
    convert = advapi32.ConvertStringSecurityDescriptorToSecurityDescriptorW
    convert.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.LPVOID),
        wintypes.LPVOID,
    ]
    convert.restype = wintypes.BOOL
    sddl = f"D:P(A;;GA;;;{sid})"
    if not convert(sddl, SDDL_REVISION_1, ctypes.byref(security_descriptor), None):
        raise ctypes.WinError(ctypes.get_last_error())
    attributes = _SECURITY_ATTRIBUTES(
        ctypes.sizeof(_SECURITY_ATTRIBUTES), security_descriptor, False
    )
    kernel32.CreateMutexW.argtypes = [
        ctypes.POINTER(_SECURITY_ATTRIBUTES),
        wintypes.BOOL,
        wintypes.LPCWSTR,
    ]
    kernel32.CreateMutexW.restype = wintypes.HANDLE
    try:
        ctypes.set_last_error(0)
        handle = kernel32.CreateMutexW(ctypes.byref(attributes), True, name)
        error = ctypes.get_last_error()
        if not handle:
            raise ctypes.WinError(error)
        return int(handle), error == ERROR_ALREADY_EXISTS
    finally:
        kernel32.LocalFree(security_descriptor)


def _close_handle(handle: int) -> None:
    ctypes.WinDLL("kernel32", use_last_error=True).CloseHandle(wintypes.HANDLE(handle))


class WindowsSupervisorLock:
    """Per-user, cross-session singleton backed by a Windows named mutex."""

    def __init__(
        self,
        metadata_path: Path | None = None,
        *,
        sid_provider: Callable[[], str] = current_user_sid,
        pid: int | None = None,
    ) -> None:
        self.metadata_path = metadata_path or Path.home() / ".mco" / "agentd.lock"
        self.sid = sid_provider()
        self.pid = pid or os.getpid()
        self.name = f"Global\\BitCadence-agentd-{self.sid}"
        self._handle: int | None = None

    def acquire(self) -> None:
        if self._handle is not None:
            return
        handle, existed = _create_user_mutex(self.name, self.sid)
        if existed:
            _close_handle(handle)
            holder = self._holding_pid()
            raise SupervisorAlreadyRunning(
                f"BitCadence agentd is already running for this user; holding pid {holder}"
            )
        self._handle = handle
        self.metadata_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.metadata_path.with_suffix(".lock.tmp")
        temporary.write_text(str(self.pid), encoding="ascii")
        os.replace(temporary, self.metadata_path)

    def _holding_pid(self) -> str:
        try:
            value = self.metadata_path.read_text(encoding="ascii").strip()
            return str(int(value))
        except (FileNotFoundError, OSError, ValueError):
            return "unknown"

    def release(self) -> None:
        if self._handle is None:
            return
        _close_handle(self._handle)
        self._handle = None
        if self._holding_pid() == str(self.pid):
            self.metadata_path.unlink(missing_ok=True)


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
    """A child plus the Job Object that owns its whole process tree."""

    def __init__(
        self,
        process: subprocess.Popen[Any],
        job: int | None = None,
        *,
        terminate_job: Callable[[int], None] | None = None,
        close_job: Callable[[int], None] | None = None,
    ):
        self._process = process
        self.pid = process.pid
        self._job = job
        self._terminate_job = terminate_job
        self._close_job = close_job

    @property
    def stdout(self) -> Any:
        return self._process.stdout

    @property
    def stderr(self) -> Any:
        return self._process.stderr

    def poll(self) -> int | None:
        return self._process.poll()

    def terminate(self) -> None:
        """Polite stop: CTRL_BREAK to the child's process group.

        Best effort by nature - it only reaches a child that shares a console
        with the daemon and handles the event. The supervisor's terminate
        deadline, then ``kill``, is what actually bounds the stop.
        """
        try:
            self._process.send_signal(signal.CTRL_BREAK_EVENT)
        except (OSError, ValueError):
            self._process.terminate()

    def kill(self) -> None:
        """Kill the whole tree: everything in the child's Job Object."""
        if self._job is not None and self._terminate_job is not None:
            try:
                self._terminate_job(self._job)
            except OSError:
                pass
        try:
            self._process.kill()
        except OSError:
            pass

    def wait(self, timeout: float) -> int:
        return self._process.wait(timeout=timeout)

    def close(self) -> None:
        """The child is gone: kill any descendants it left, free the job."""
        job, self._job = self._job, None
        if job is None:
            return
        if self._terminate_job is not None:
            try:
                self._terminate_job(job)
            except OSError:
                pass
        if self._close_job is not None:
            self._close_job(job)


class WindowsAdapter:
    """Spawn invisible children, each inside its own kill-on-close Job Object.

    One job per child, not one for the daemon: the daemon holds the only
    handle to each job, so if the daemon dies for any reason Windows closes
    the handles and kills every tree; and stopping one worker is a single
    ``TerminateJobObject`` that takes its descendants with it (a waker's
    running agent CLI, a batch file's children) without touching the others.
    """

    def __init__(
        self,
        pidfile: Path | None = None,
        popen_factory: Callable[..., subprocess.Popen[Any]] = subprocess.Popen,
        supervisor_lock: WindowsSupervisorLock | None = None,
        nonce: str | None = None,
    ) -> None:
        self.pidfile = pidfile or Path.home() / ".mco" / "agentd.pids"
        self._popen_factory = popen_factory
        self._job_handle: int | None = None
        self._supervisor_lock = supervisor_lock
        # Random per-supervisor generation, handed to every child in its
        # environment. Half of what proves a recorded pid is ours (R6).
        self.nonce = nonce or secrets.token_hex(16)
        self.last_reap_report = ReapReport()

    def acquire_supervisor_lock(self) -> None:
        if self._supervisor_lock is None:
            self._supervisor_lock = WindowsSupervisorLock()
        self._supervisor_lock.acquire()

    def release_supervisor_lock(self) -> None:
        if self._supervisor_lock is not None:
            self._supervisor_lock.release()

    def bind_to_parent_lifetime(self) -> None:
        """Prove kill-on-close Job Objects work here, before any spawn."""
        if self._job_handle is not None:
            return
        if os.name != "nt":
            raise RuntimeError("WindowsAdapter can only be initialized on Windows")
        self._job_handle = self._create_job()

    @staticmethod
    def _create_job() -> int:
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
        return int(handle)

    @staticmethod
    def _terminate_job(job: int) -> None:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
        kernel32.TerminateJobObject.restype = wintypes.BOOL
        if not kernel32.TerminateJobObject(wintypes.HANDLE(job), 1):
            raise ctypes.WinError(ctypes.get_last_error())

    @staticmethod
    def _close_job(job: int) -> None:
        _close_handle(job)

    def spawn(
        self,
        argv: list[str],
        cwd: Path,
        env: dict[str, str],
        stdout: int,
        stderr: int,
    ) -> WindowsProcessHandle:
        self.bind_to_parent_lifetime()
        child_env = dict(env)
        child_env[NONCE_ENV] = self.nonce
        # CREATE_NO_WINDOW: a console with no window, inherited by everything
        # the child starts. No console window can appear at any depth.
        flags = CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP | CREATE_SUSPENDED
        process = self._popen_factory(
            argv,
            cwd=str(cwd),
            env=child_env,
            stdin=subprocess.DEVNULL,
            stdout=stdout,
            stderr=stderr,
            creationflags=flags,
        )
        job: int | None = None
        try:
            # Suspended until it is recorded and inside its job: the child
            # cannot run a single instruction, or fork, before we own it.
            self._record_child(process.pid, argv)
            job = self._create_job()
            self._assign_to_job(process, job)
            self._resume_process(process)
        except Exception:
            process.kill()
            process.wait()
            self.forget_pid(process.pid)
            if job is not None:
                self._close_job(job)
            raise
        return WindowsProcessHandle(
            process, job, terminate_job=self._terminate_job, close_job=self._close_job
        )

    def _assign_to_job(self, process: subprocess.Popen[Any], job: int | None = None) -> None:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        kernel32.AssignProcessToJobObject.restype = wintypes.BOOL
        ok = kernel32.AssignProcessToJobObject(
            wintypes.HANDLE(job if job is not None else self._job_handle),
            wintypes.HANDLE(process._handle),
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

    def _record_child(self, pid: int, argv: list[str]) -> None:
        """Record the full identity of a child (R6), before it runs."""
        identity.append_record(self.pidfile, identity.capture_identity(pid, argv, self.nonce))

    def forget_pid(self, pid: int) -> None:
        identity.forget_record(self.pidfile, pid)

    def reap_orphans(self, pidfile: Path) -> list[int]:
        """Kill recorded children only when their identity is proven (R6)."""
        self.last_reap_report = identity.reap_recorded(pidfile)
        return list(self.last_reap_report.killed)

    @staticmethod
    def _read_pidfile(pidfile: Path) -> list[dict[str, object]]:
        return identity.read_records(pidfile)

    @staticmethod
    def _write_records(pidfile: Path, records: list[dict[str, object]]) -> None:
        identity.write_records(pidfile, records)

    def install_service(self, spec: ServiceSpec) -> tuple[bool, str]:
        return service._win_install_service(spec)

    def uninstall_service(self, name: str) -> tuple[bool, str]:
        return service._win_uninstall(name)
