import os
from mco import killswitch as ks


def test_set_and_active(tmp_path, monkeypatch):
    monkeypatch.setenv(ks.ENV_VAR, str(tmp_path / "STOP"))
    assert not ks.stop_active() and ks.guard()
    ks.set_stop("test")
    assert ks.stop_active() and not ks.guard()
    assert ks.stop_reason()["source"] == "test"


def test_set_never_overwrites(tmp_path, monkeypatch):
    monkeypatch.setenv(ks.ENV_VAR, str(tmp_path / "STOP"))
    ks.set_stop("first")
    ks.set_stop("second")
    assert ks.stop_reason()["source"] == "first"


def test_phone_command(tmp_path, monkeypatch):
    monkeypatch.setenv(ks.ENV_VAR, str(tmp_path / "STOP"))
    assert ks.handle_phone_command("STOP", sender_is_owner=False).startswith("ignored")
    assert not ks.stop_active()
    assert ks.handle_phone_command(" Stop  All ", sender_is_owner=True) == "stopped"
    assert ks.handle_phone_command("resume", sender_is_owner=True).startswith("refused")
    assert ks.stop_active()


def test_only_the_exact_file_is_a_signal(tmp_path, monkeypatch):
    monkeypatch.setenv(ks.ENV_VAR, str(tmp_path / "STOP"))
    leo = tmp_path / "leo" / "STOP"
    leo.parent.mkdir()
    leo.write_text("x")
    other = tmp_path / "PAUSE"
    other.write_text("x")
    # Even if someone sets the old alias variable, it is ignored.
    monkeypatch.setenv("MCO_LEGACY_PAUSE_FILES", f"{leo}{os.pathsep}{other}")
    assert not ks.stop_active()
    (tmp_path / "STOP").write_text("x")
    assert ks.stop_active()


def test_unreadable_state_fails_closed(tmp_path, monkeypatch):
    monkeypatch.setenv(ks.ENV_VAR, str(tmp_path / "STOP"))

    def boom(path, *a, **k):
        raise PermissionError(13, "denied")

    monkeypatch.setattr(ks.os, "stat", boom)
    assert ks.stop_active() and not ks.guard()


def test_supervisor_spawn_blocked_when_stopped(tmp_path, monkeypatch):
    from unittest import mock
    from mco.agentd import supervisor as sv
    monkeypatch.setenv(ks.ENV_VAR, str(tmp_path / "STOP"))
    ks.set_stop("test")
    obj = mock.MagicMock()
    sv.Supervisor._spawn(obj, mock.MagicMock())
    assert not obj._adapter.spawn.called and not obj.adapter.spawn.called


def test_sweep_skips_when_stopped(tmp_path, monkeypatch):
    from mco.orchestrator import score_sweep
    monkeypatch.setenv(ks.ENV_VAR, str(tmp_path / "STOP"))
    ks.set_stop("test")
    res = score_sweep.sweep(object(), force=True)
    assert res.skipped == {"_all": "kill_switch"}
