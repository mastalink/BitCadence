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


def test_legacy_alias_and_leo_not_signal(tmp_path, monkeypatch):
    monkeypatch.setenv(ks.ENV_VAR, str(tmp_path / "STOP"))
    leo = tmp_path / "leo" / "STOP"
    leo.parent.mkdir()
    leo.write_text("x")
    assert not ks.stop_active()
    legacy = tmp_path / "PAUSE"
    monkeypatch.setenv("MCO_LEGACY_PAUSE_FILES", str(legacy))
    assert not ks.stop_active()
    legacy.write_text("x")
    assert ks.stop_active()


def test_sweep_skips_when_stopped(tmp_path, monkeypatch):
    from mco.orchestrator import score_sweep
    monkeypatch.setenv(ks.ENV_VAR, str(tmp_path / "STOP"))
    ks.set_stop("test")
    res = score_sweep.sweep(object(), force=True)
    assert res.skipped == {"_all": "kill_switch"}
