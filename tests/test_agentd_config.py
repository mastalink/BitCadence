from pathlib import Path
import os

import pytest

from mco.agentd.config import FleetConfigManager, FleetWatcher
from mco.fleet import FleetConfigError


VALID = """[workers.codex-beast]
role = "codex"
instance = "codex-beast"
mode = "waker"
exec = "worker.cmd"
min_interval = 10
poll_interval = 30
"""


class RecordingSupervisor:
    def __init__(self) -> None:
        self.reconciled = []

    def reconcile(self, configs) -> None:
        self.reconciled.append(configs)


def test_validate_and_atomic_replace_reuses_fleet_parser(tmp_path: Path) -> None:
    path = tmp_path / "fleet.toml"
    manager = FleetConfigManager(path)
    parsed = manager.write(VALID)
    assert parsed["codex-beast"].role == "codex"
    assert path.read_text(encoding="utf-8") == VALID
    assert not list(tmp_path.glob(".fleet.toml.*.tmp"))


def test_invalid_config_does_not_replace_existing_file(tmp_path: Path) -> None:
    path = tmp_path / "fleet.toml"
    path.write_text(VALID, encoding="utf-8")
    manager = FleetConfigManager(path)
    with pytest.raises(FleetConfigError):
        manager.write('[workers.bad]\nrole="x"\nmode="waker"\n')
    assert path.read_text(encoding="utf-8") == VALID


@pytest.mark.parametrize("fictional_field", ["enabled", "args"])
def test_daemon_rejects_fields_outside_the_real_fleet_schema(
    tmp_path: Path, fictional_field: str
) -> None:
    manager = FleetConfigManager(tmp_path / "fleet.toml")
    content = VALID + f"{fictional_field} = true\n"
    with pytest.raises(FleetConfigError, match="unsupported field"):
        manager.validate(content)


def test_watcher_reconciles_on_content_change_even_when_mtime_is_unchanged(
    tmp_path: Path,
) -> None:
    path = tmp_path / "fleet.toml"
    path.write_text(VALID, encoding="utf-8")
    supervisor = RecordingSupervisor()
    now = [0.0]
    watcher = FleetWatcher(
        FleetConfigManager(path), supervisor, clock=lambda: now[0], interval=5
    )
    assert watcher.poll()
    assert len(supervisor.reconciled) == 1
    now[0] = 5
    assert not watcher.poll()
    assert len(supervisor.reconciled) == 1

    original_mtime = path.stat().st_mtime_ns
    path.write_text(VALID.replace('role = "codex"', 'role = "claude"'), encoding="utf-8")
    os.utime(path, ns=(original_mtime, original_mtime))
    now[0] = 10
    assert watcher.poll()
    assert supervisor.reconciled[-1]["codex-beast"].role == "claude"


def test_watcher_initially_reconciles_a_missing_file_to_empty(tmp_path: Path) -> None:
    supervisor = RecordingSupervisor()
    watcher = FleetWatcher(FleetConfigManager(tmp_path / "missing.toml"), supervisor)
    assert watcher.poll()
    assert supervisor.reconciled == [{}]


# --- ADR 0002 R5: transactional reconciliation -----------------------------

TWO_WORKERS = VALID + """
[workers.claude-worker]
role = "claude"
mode = "waker"
exec = "claude.cmd"
"""


def _watcher(path: Path, **kwargs):
    supervisor = RecordingSupervisor()
    now = [0.0]
    watcher = FleetWatcher(
        FleetConfigManager(path),
        supervisor,
        clock=lambda: now[0],
        interval=5,
        sleep=kwargs.pop("sleep", lambda seconds: None),
        **kwargs,
    )

    def tick() -> bool:
        now[0] += 5
        return watcher.poll()

    return watcher, supervisor, tick


def test_invalid_candidate_keeps_the_running_generation_and_recovers(tmp_path: Path) -> None:
    path = tmp_path / "fleet.toml"
    path.write_text(TWO_WORKERS, encoding="utf-8")
    watcher, supervisor, tick = _watcher(path)
    assert tick()
    good = watcher.generation

    path.write_text("[workers.codex-beast\nrole =", encoding="utf-8")  # broken TOML
    assert not tick()
    assert len(supervisor.reconciled) == 1  # nothing was reconciled
    assert watcher.generation == good
    assert "invalid fleet config" in watcher.last_error

    path.write_text(VALID + "enabled = true\n", encoding="utf-8")  # valid TOML, bad schema
    assert not tick()
    assert len(supervisor.reconciled) == 1 and "unsupported field" in watcher.last_error

    path.write_text(VALID, encoding="utf-8")  # the operator fixes it
    assert tick()
    assert watcher.last_error is None and sorted(supervisor.reconciled[-1]) == ["codex-beast"]


@pytest.mark.parametrize("damage", ["truncate", "delete"])
def test_a_truncated_or_missing_file_is_never_read_as_remove_every_worker(
    tmp_path: Path, damage: str
) -> None:
    """What `Path.write_text()` on the live file looks like halfway through."""
    path = tmp_path / "fleet.toml"
    path.write_text(TWO_WORKERS, encoding="utf-8")
    watcher, supervisor, tick = _watcher(path)
    assert tick()
    if damage == "truncate":
        path.write_text("", encoding="utf-8")
    else:
        path.unlink()
    assert not tick()
    assert len(supervisor.reconciled) == 1
    assert "keeping the running configuration" in watcher.last_error


def test_a_file_still_being_written_is_not_reconciled_until_it_settles(tmp_path: Path) -> None:
    """A slow in-place write: the first half is VALID TOML with one worker fewer."""
    path = tmp_path / "fleet.toml"
    path.write_text(TWO_WORKERS, encoding="utf-8")
    edited = TWO_WORKERS.replace("claude.cmd", "claude2.cmd")
    half = edited[: edited.index("[workers.claude-worker]")]

    def writer_finishes(seconds: float) -> None:
        path.write_text(edited, encoding="utf-8")  # lands during the settle wait

    watcher, supervisor, tick = _watcher(path, sleep=writer_finishes)
    assert tick()
    path.write_text(half, encoding="utf-8")
    assert not tick()  # saw the half, re-read, it had changed: wait
    assert len(supervisor.reconciled) == 1
    assert "still changing" in watcher.last_error
    watcher.sleep = lambda seconds: None
    assert tick()
    assert sorted(supervisor.reconciled[-1]) == ["claude-worker", "codex-beast"]
    assert supervisor.reconciled[-1]["claude-worker"].exec_command == "claude2.cmd"


def test_two_writes_inside_one_timestamp_tick_are_both_seen(tmp_path: Path) -> None:
    path = tmp_path / "fleet.toml"
    path.write_text(VALID, encoding="utf-8")
    stamp = path.stat().st_mtime_ns
    watcher, supervisor, tick = _watcher(path)
    assert tick()
    for role in ("claude", "grok"):
        path.write_text(VALID.replace('role = "codex"', f'role = "{role}"'), encoding="utf-8")
        os.utime(path, ns=(stamp, stamp))
        assert tick()
        assert supervisor.reconciled[-1]["codex-beast"].role == role


def test_replacement_by_rename_is_picked_up(tmp_path: Path) -> None:
    path = tmp_path / "fleet.toml"
    path.write_text(VALID, encoding="utf-8")
    watcher, supervisor, tick = _watcher(path)
    assert tick()
    replacement = tmp_path / "fleet.toml.new"
    replacement.write_text(TWO_WORKERS, encoding="utf-8")
    os.replace(replacement, path)
    assert tick() and len(supervisor.reconciled[-1]) == 2


def test_stale_writer_gets_a_conflict_instead_of_overwriting(tmp_path: Path) -> None:
    from mco.agentd.config import ABSENT_GENERATION, GenerationConflict

    path = tmp_path / "fleet.toml"
    manager = FleetConfigManager(path)
    assert manager.snapshot()["generation"] == ABSENT_GENERATION
    manager.write(VALID, expected_generation=ABSENT_GENERATION)
    read_by_console = manager.snapshot()["generation"]

    path.write_text(TWO_WORKERS, encoding="utf-8")  # someone edits by hand meanwhile
    with pytest.raises(GenerationConflict) as conflict:
        manager.write(VALID, expected_generation=read_by_console)
    assert conflict.value.current == manager.snapshot()["generation"]
    assert path.read_text(encoding="utf-8") == TWO_WORKERS  # the newer edit survives


def test_cli_writer_replaces_the_file_atomically(tmp_path: Path, monkeypatch) -> None:
    """`mco fleet set` used Path.write_text(), which truncates the live file first."""
    from mco import fleet

    path = tmp_path / "fleet.toml"
    path.write_text(VALID, encoding="utf-8")
    seen = {}
    real_replace = os.replace

    def replace(source, target):
        # At the moment of the swap the live file is still whole, and the
        # replacement is complete and valid.
        seen["live"] = Path(target).read_text(encoding="utf-8")
        seen["candidate"] = FleetConfigManager(path).validate(Path(source).read_text(encoding="utf-8"))
        real_replace(source, target)

    monkeypatch.setattr(fleet.os, "replace", replace)
    fleet.set_worker_value("codex-beast", "mode=off", path)
    assert seen["live"] == VALID
    assert seen["candidate"]["codex-beast"].mode == "off"
    assert not [p for p in tmp_path.iterdir() if p.name != "fleet.toml"]


def test_cli_and_daemon_round_trip_the_same_file(tmp_path: Path) -> None:
    """R4: every writer and reader agree on one schema, with no drift."""
    from dataclasses import asdict

    from mco import fleet

    path = tmp_path / "fleet.toml"
    manager = FleetConfigManager(path)
    written_by_daemon = manager.write(TWO_WORKERS)
    assert fleet.load_fleet(path) == written_by_daemon  # the CLI reads the daemon's write

    fleet.set_worker_value("claude-worker", "mode=off", path)  # the CLI writes
    read_by_daemon = manager.validate(path.read_text(encoding="utf-8"))
    assert read_by_daemon == fleet.load_fleet(path)
    assert read_by_daemon["claude-worker"].mode == "off"
    assert read_by_daemon["codex-beast"] == written_by_daemon["codex-beast"]
    assert manager.snapshot()["workers"]["claude-worker"] == asdict(read_by_daemon["claude-worker"])
