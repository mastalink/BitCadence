"""`mco fleet migrate --dry-run`: lists every legacy service, changes nothing."""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from mco import cli, fleet, service
from mco.agentd import migrate


FLEET = """[workers.codex-one]
role = "codex"
instance = "codex-one"
mode = "waker"
exec = "codex.cmd"

[workers.claude-one]
role = "claude"
instance = "claude-one"
mode = "poll"
exec = "claude.cmd"

[workers.parked]
role = "grok"
instance = "parked"
mode = "off"

[workers.fresh]
role = "local"
instance = "fresh"
mode = "waker"
exec = "local.cmd"
"""

INSTALLED = [
    {"installed": True, "running": True},  # the gateway reports no name
    {"name": "BitCadence-scheduler", "installed": True, "running": True},
    {"name": "BitCadence-wake-codex-codex-one", "installed": True, "running": True},
    {"name": "BitCadence-poll-claude-claude-one", "installed": True, "running": False},
    {"name": "BatonCadence-wake-codex-codex-one", "installed": True, "running": True},
    {"name": "BitCadence-wake-grok-parked", "installed": True, "running": False},
    {"name": "BitCadence-wake-gone-retired", "installed": True, "running": True},
    {"name": "BitCadence-wake-claude-claude-one", "installed": True, "running": False},
]


@pytest.fixture
def fleet_file(tmp_path: Path) -> Path:
    path = tmp_path / "fleet.toml"
    path.write_text(FLEET, encoding="utf-8")
    return path


def test_plan_lists_every_legacy_service_under_every_brand(fleet_file: Path) -> None:
    plan = migrate.plan_migration(fleet_file, list_services=lambda: INSTALLED)
    by_name = {item.name: item for item in plan.legacy}
    assert set(by_name) == {
        "BitCadence-wake-codex-codex-one",
        "BitCadence-poll-claude-claude-one",
        "BatonCadence-wake-codex-codex-one",  # the pre-rename brand is not missed
        "BitCadence-wake-grok-parked",
        "BitCadence-wake-gone-retired",
        "BitCadence-wake-claude-claude-one",
    }
    assert by_name["BatonCadence-wake-codex-codex-one"].worker == "codex-one"
    assert by_name["BitCadence-poll-claude-claude-one"].kind == "poll"
    assert by_name["BitCadence-wake-codex-codex-one"].running is True
    assert "mode = off" in by_name["BitCadence-wake-grok-parked"].note
    # Installed as a waker, but fleet.toml says poll: the plan says so.
    assert "mode = poll" in by_name["BitCadence-wake-claude-claude-one"].note


def test_a_service_reported_once_per_trigger_is_listed_once(fleet_file: Path) -> None:
    doubled = [
        {"name": "BitCadence-wake-codex-codex-one", "installed": True, "running": False},
        {"name": "BitCadence-wake-codex-codex-one", "installed": True, "running": True},
        {"name": "BitCadence-scheduler", "installed": True, "running": True},
        {"name": "BitCadence-scheduler", "installed": True, "running": True},
    ]
    plan = migrate.plan_migration(fleet_file, list_services=lambda: doubled)
    assert [(item.name, item.running) for item in plan.legacy] == [
        ("BitCadence-wake-codex-codex-one", True)
    ]
    assert plan.left_alone == ["BitCadence-scheduler"]


def test_a_service_with_no_fleet_entry_is_an_orphan_and_is_left_alone(fleet_file: Path) -> None:
    plan = migrate.plan_migration(fleet_file, list_services=lambda: INSTALLED)
    assert [item.name for item in plan.orphans] == ["BitCadence-wake-gone-retired"]
    assert plan.orphans[0].action.startswith("ORPHAN")
    assert "BitCadence-wake-gone-retired" in plan.services_after
    assert not any("gone-retired" in step for step in plan.steps())


def test_gateway_and_scheduler_are_not_touched(fleet_file: Path) -> None:
    plan = migrate.plan_migration(fleet_file, list_services=lambda: INSTALLED)
    assert plan.left_alone == [service.SERVICE_NAME, "BitCadence-scheduler"]
    assert plan.services_after == sorted(
        [service.SERVICE_NAME, "BitCadence-scheduler", "BitCadence-agentd", "BitCadence-wake-gone-retired"]
    )
    assert plan.workers_without_service == ["fresh"]
    assert plan.agentd_installed is False


def test_plan_stops_and_verifies_each_legacy_service_before_installing(fleet_file: Path) -> None:
    """R2: not uninstall-then-install, and not install-first."""
    steps = migrate.plan_migration(fleet_file, list_services=lambda: INSTALLED).steps()
    install = next(i for i, step in enumerate(steps) if step.startswith("Install and start"))
    stops = [i for i, step in enumerate(steps) if step.startswith("Stop ")]
    assert "lock" in steps[0]
    assert len(stops) == 5 and max(stops) < install
    assert all("verify its process has exited" in steps[i] for i in stops)
    assert "Verify the supervisor adopted" in steps[install + 1]
    assert "roll" in steps[-1].lower() or "restore" in steps[-1].lower()


def test_without_a_fleet_file_every_legacy_service_is_an_orphan(tmp_path: Path) -> None:
    plan = migrate.plan_migration(tmp_path / "missing.toml", list_services=lambda: INSTALLED)
    assert plan.fleet_error and len(plan.orphans) == 6
    assert all(not step.startswith("Stop ") for step in plan.steps())


@pytest.fixture
def no_changes_allowed(monkeypatch, fleet_file: Path):
    """Anything that could change the machine fails the test if it is reached."""

    def forbidden(*args, **kwargs):
        raise AssertionError(f"a dry run must not change anything: {args}")

    for name in (
        "uninstall", "install", "install_waker", "install_poll", "install_scheduler",
        "restart", "_win_uninstall", "_win_install_service", "_linux_uninstall",
        "_mac_uninstall", "_run",
    ):
        monkeypatch.setattr(service, name, forbidden)
    monkeypatch.setattr(service, "list_status", lambda: INSTALLED)
    monkeypatch.setattr(migrate, "FLEET_CONFIG_PATH", fleet_file)
    return fleet_file


def test_cli_dry_run_prints_the_plan_and_changes_nothing(no_changes_allowed: Path) -> None:
    before = no_changes_allowed.read_bytes()
    result = CliRunner().invoke(cli.app, ["fleet", "migrate", "--dry-run"], env={"COLUMNS": "250"})
    assert result.exit_code == 0, result.output
    assert "nothing was changed" in result.output
    for name in ("BatonCadence-wake-codex-codex-one", "BitCadence-wake-gone-retired"):
        assert name in result.output
    assert "1 orphan(s)" in result.output
    assert "start when you log in" in result.output  # R1, stated plainly
    assert no_changes_allowed.read_bytes() == before
    assert list(no_changes_allowed.parent.iterdir()) == [no_changes_allowed]


def test_cli_default_is_a_dry_run(no_changes_allowed: Path) -> None:
    result = CliRunner().invoke(cli.app, ["fleet", "migrate"], env={"COLUMNS": "250"})
    assert result.exit_code == 0 and "nothing was changed" in result.output


def test_cli_apply_is_refused_in_this_build(no_changes_allowed: Path, monkeypatch) -> None:
    monkeypatch.setattr(
        migrate, "plan_migration", lambda *a, **k: pytest.fail("--apply must not even plan")
    )
    result = CliRunner().invoke(cli.app, ["fleet", "migrate", "--apply"])
    assert result.exit_code == 2
    assert "not available in this build" in result.output
    assert "Nothing was changed" in result.output


def test_legacy_kind_covers_both_brands_and_nothing_else() -> None:
    assert migrate.legacy_kind("BitCadence-wake-codex-x") == "waker"
    assert migrate.legacy_kind("BatonCadence-poll-codex-x") == "poll"
    for other in ("BitCadence-gateway", "BitCadence-scheduler", "BitCadence-agentd",
                  "BatonCadenceGateway", "SomeoneElse-wake-codex"):
        assert migrate.legacy_kind(other) is None
    assert fleet.VALID_MODES == {"waker", "poll", "off"}
