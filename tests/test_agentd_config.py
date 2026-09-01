from pathlib import Path

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
    assert not (tmp_path / ".fleet.toml.tmp").exists()


def test_invalid_config_does_not_replace_existing_file(tmp_path: Path) -> None:
    path = tmp_path / "fleet.toml"
    path.write_text(VALID, encoding="utf-8")
    manager = FleetConfigManager(path)
    with pytest.raises(FleetConfigError):
        manager.write('[workers.bad]\nrole="x"\nmode="waker"\n')
    assert path.read_text(encoding="utf-8") == VALID


def test_watcher_reconciles_only_on_mtime_change(tmp_path: Path) -> None:
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

    path.write_text(VALID.replace('role = "codex"', 'role = "claude"'), encoding="utf-8")
    now[0] = 10
    assert watcher.poll()
    assert supervisor.reconciled[-1]["codex-beast"].role == "claude"


def test_watcher_initially_reconciles_a_missing_file_to_empty(tmp_path: Path) -> None:
    supervisor = RecordingSupervisor()
    watcher = FleetWatcher(FleetConfigManager(tmp_path / "missing.toml"), supervisor)
    assert watcher.poll()
    assert supervisor.reconciled == [{}]
