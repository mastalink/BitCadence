"""CLI surface for schedules: enable/disable/reset and the config-editing rules."""

from typer.testing import CliRunner

import mco.cli as cli
from mco import launcher as launcher_mod
from mco import scheduler
from mco.scheduler import ScheduleState

runner = CliRunner()

CONFIG_WITH_COMMENTS = """# BitCadence schedules - top comment
launchers:
  audit:
    role: reviewer          # inline comment
    title: Nightly audit
    instructions: Check deps

schedules:
  nightly:
    launcher: audit
    cron: "0 3 * * *"

# Loops are schedules that stop.
loops:
  drain:
    launcher: audit
    every: 5m
    max_iterations: 3
"""


def _use_config(tmp_path, monkeypatch, text=CONFIG_WITH_COMMENTS):
    path = tmp_path / "schedules.yaml"
    path.write_text(text, encoding="utf-8")
    monkeypatch.setattr(scheduler, "SCHEDULES_CONFIG_PATH", path)
    monkeypatch.setattr(launcher_mod, "SCHEDULES_CONFIG_PATH", path)
    return path


def _use_state(tmp_path, monkeypatch, states=None):
    path = tmp_path / "state.json"
    monkeypatch.setattr(launcher_mod, "SCHEDULE_STATE_PATH", path)
    if states:
        launcher_mod.save_state(states, path)
    return path


def _comment_count(text):
    return sum(1 for line in text.splitlines() if "#" in line)


# ── enable / disable ──────────────────────────────────────────────────────────

def test_disable_sets_enabled_false(tmp_path, monkeypatch):
    path = _use_config(tmp_path, monkeypatch)
    result = runner.invoke(cli.app, ["schedule", "disable", "nightly"])
    assert result.exit_code == 0
    assert "enabled: false" in path.read_text(encoding="utf-8")


def test_enable_sets_enabled_true(tmp_path, monkeypatch):
    path = _use_config(tmp_path, monkeypatch)
    runner.invoke(cli.app, ["schedule", "disable", "nightly"])
    result = runner.invoke(cli.app, ["schedule", "enable", "nightly"])
    assert result.exit_code == 0
    assert "enabled: true" in path.read_text(encoding="utf-8")


def test_toggling_preserves_every_comment(tmp_path, monkeypatch):
    # PyYAML's safe_dump discards comments, so a one-word toggle must not be
    # implemented as parse-and-redump - it would silently destroy the
    # explanatory config the user wrote.
    path = _use_config(tmp_path, monkeypatch)
    before = _comment_count(path.read_text(encoding="utf-8"))
    runner.invoke(cli.app, ["schedule", "disable", "nightly"])
    after = path.read_text(encoding="utf-8")
    assert _comment_count(after) == before
    assert "# BitCadence schedules - top comment" in after
    assert "# inline comment" in after
    assert "# Loops are schedules that stop." in after


def test_repeated_toggles_replace_rather_than_duplicate(tmp_path, monkeypatch):
    path = _use_config(tmp_path, monkeypatch)
    for _ in range(3):
        runner.invoke(cli.app, ["schedule", "disable", "nightly"])
        runner.invoke(cli.app, ["schedule", "enable", "nightly"])
    text = path.read_text(encoding="utf-8")
    assert text.count("enabled:") == 1


def test_toggle_leaves_other_entries_untouched(tmp_path, monkeypatch):
    path = _use_config(tmp_path, monkeypatch)
    runner.invoke(cli.app, ["schedule", "disable", "nightly"])
    text = path.read_text(encoding="utf-8")
    # The loop below it must not have gained an `enabled` key.
    drain_block = text.split("drain:", 1)[1]
    assert "enabled:" not in drain_block


def test_toggle_works_on_loops_too(tmp_path, monkeypatch):
    path = _use_config(tmp_path, monkeypatch)
    result = runner.invoke(cli.app, ["schedule", "disable", "drain"])
    assert result.exit_code == 0
    assert "loop 'drain'" in result.stdout
    assert "enabled: false" in path.read_text(encoding="utf-8").split("drain:", 1)[1]


def test_toggle_rejects_an_unknown_name(tmp_path, monkeypatch):
    _use_config(tmp_path, monkeypatch)
    result = runner.invoke(cli.app, ["schedule", "disable", "ghost"])
    assert result.exit_code == 1
    assert "No schedule or loop named 'ghost'" in result.stdout


def test_toggled_config_still_parses(tmp_path, monkeypatch):
    path = _use_config(tmp_path, monkeypatch)
    runner.invoke(cli.app, ["schedule", "disable", "nightly"])
    _, schedules = scheduler.load_config(path)
    assert schedules["nightly"].enabled is False
    assert schedules["drain"].enabled is True


# ── reset ─────────────────────────────────────────────────────────────────────

def test_reset_clears_a_finished_loop(tmp_path, monkeypatch):
    _use_config(tmp_path, monkeypatch)
    state_path = _use_state(tmp_path, monkeypatch, {
        "drain": ScheduleState(name="drain", iterations=3, exhausted_reason="completed all 3 iterations")
    })
    result = runner.invoke(cli.app, ["schedule", "reset", "drain", "--yes"])
    assert result.exit_code == 0
    assert "drain" not in launcher_mod.load_state(state_path)


def test_reset_is_a_noop_with_no_history(tmp_path, monkeypatch):
    _use_config(tmp_path, monkeypatch)
    _use_state(tmp_path, monkeypatch)
    result = runner.invoke(cli.app, ["schedule", "reset", "drain", "--yes"])
    assert result.exit_code == 0
    assert "no run history" in result.stdout


def test_reset_rejects_an_unknown_name(tmp_path, monkeypatch):
    _use_config(tmp_path, monkeypatch)
    _use_state(tmp_path, monkeypatch)
    result = runner.invoke(cli.app, ["schedule", "reset", "ghost", "--yes"])
    assert result.exit_code == 1


def test_reset_declined_at_the_prompt_leaves_state_intact(tmp_path, monkeypatch):
    _use_config(tmp_path, monkeypatch)
    state_path = _use_state(tmp_path, monkeypatch, {
        "drain": ScheduleState(name="drain", iterations=3, exhausted_reason="done")
    })
    result = runner.invoke(cli.app, ["schedule", "reset", "drain"], input="n\n")
    assert result.exit_code == 0
    assert "drain" in launcher_mod.load_state(state_path)


# ── list ──────────────────────────────────────────────────────────────────────

def test_list_shows_schedules_and_loops(tmp_path, monkeypatch):
    _use_config(tmp_path, monkeypatch)
    _use_state(tmp_path, monkeypatch)
    result = runner.invoke(cli.app, ["schedule", "list"])
    assert result.exit_code == 0
    assert "nightly" in result.stdout and "drain" in result.stdout


def test_list_reports_a_missing_config_kindly(tmp_path, monkeypatch):
    monkeypatch.setattr(scheduler, "SCHEDULES_CONFIG_PATH", tmp_path / "absent.yaml")
    result = runner.invoke(cli.app, ["schedule", "list"])
    assert result.exit_code == 0
    assert "mco schedule init" in result.stdout


def test_invalid_config_exits_nonzero_with_the_reason(tmp_path, monkeypatch):
    _use_config(tmp_path, monkeypatch, text="""
launchers:
  audit:
    role: reviewer
    title: t
loops:
  forever:
    launcher: audit
    every: 5m
""")
    result = runner.invoke(cli.app, ["schedule", "list"])
    assert result.exit_code == 1
    assert "max_iterations" in result.stdout
