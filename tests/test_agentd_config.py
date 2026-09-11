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
