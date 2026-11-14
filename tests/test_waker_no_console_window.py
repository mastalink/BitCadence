"""A background waker does not open a console window for each wake.

Found 2026-11-13: every agent listener runs windowless from a scheduled task,
and each wake's shell command made Windows open a new visible console.
"""
import asyncio

from mco import waker


def test_waker_with_a_console_spawns_as_before(monkeypatch):
    monkeypatch.setattr(waker, "_IS_WINDOWS", True)
    monkeypatch.setattr(waker, "_has_console", lambda: True)
    assert waker._spawn_kwargs() == {}


def test_windowless_waker_hides_the_child_window(monkeypatch):
    monkeypatch.setattr(waker, "_IS_WINDOWS", True)
    monkeypatch.setattr(waker, "_has_console", lambda: False)
    assert waker._spawn_kwargs() == {"creationflags": 0x08000000}


def test_non_windows_is_untouched(monkeypatch):
    monkeypatch.setattr(waker, "_IS_WINDOWS", False)
    monkeypatch.setattr(waker, "_has_console", lambda: False)
    assert waker._spawn_kwargs() == {}


def test_run_exec_passes_the_flags_to_the_shell(monkeypatch):
    seen = {}

    class _Proc:
        async def wait(self):
            return 0

    async def _fake_shell(cmd, **kw):
        seen["cmd"], seen["kw"] = cmd, kw
        return _Proc()

    monkeypatch.setattr(asyncio, "create_subprocess_shell", _fake_shell)
    monkeypatch.setattr(waker, "_spawn_kwargs", lambda: {"creationflags": 0x08000000})
    w = waker.Waker.__new__(waker.Waker)
    w.exec_command = "run.cmd"
    assert asyncio.run(w._run_exec()) == 0
    assert seen == {"cmd": "run.cmd", "kw": {"creationflags": 0x08000000}}
