"""`mco fleet migrate`: plan the move from per-worker services to one supervisor.

This module only *plans*. It reads the installed services and ``fleet.toml``
and says what a migration would do; it never stops, deletes or installs
anything. Applying the plan is a separate, deliberate step that is not in this
build.

The order the plan describes is the corrected one from ADR 0002 R2. Deleting a
per-worker service does not stop its process (``schtasks /Delete`` never runs
``/End``), so "uninstall, then install" starts a second copy of every waker.
The safe order is: stop, **verify the process exited**, then start the
supervisor, then verify it adopted the worker.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from mco import service
from mco.fleet import FLEET_CONFIG_PATH, FleetConfigError, FleetConfigMissing, load_fleet


AGENTD_SERVICE_NAME = "BitCadence-agentd"
_KINDS = {"wake": "waker", "poll": "poll"}


@dataclass(frozen=True)
class LegacyService:
    name: str
    kind: str  # "waker" | "poll"
    running: bool
    worker: str | None  # fleet.toml table name, or None for an orphan
    action: str
    note: str = ""


@dataclass
class MigrationPlan:
    fleet_path: Path
    fleet_error: str | None = None
    legacy: list[LegacyService] = field(default_factory=list)
    left_alone: list[str] = field(default_factory=list)
    workers_without_service: list[str] = field(default_factory=list)
    agentd_installed: bool = False

    @property
    def orphans(self) -> list[LegacyService]:
        return [item for item in self.legacy if item.worker is None]

    @property
    def services_after(self) -> list[str]:
        """What a completed migration leaves registered."""
        return sorted({*self.left_alone, AGENTD_SERVICE_NAME, *(o.name for o in self.orphans)})

    def steps(self) -> list[str]:
        movable = [item for item in self.legacy if item.worker is not None]
        steps = [
            "Take the supervisor lock (or coordinate with a running supervisor).",
        ]
        for item in movable:
            steps.append(
                f"Stop {item.name}, verify its process has exited, then remove the service."
            )
        steps.extend(
            [
                f"Install and start {AGENTD_SERVICE_NAME} (starts at login, for this user only).",
                "Verify the supervisor adopted every worker listed above.",
                "On any failure: restore the stopped services or report a recoverable "
                "stopped state. Never leave the machine both unsupervised and unmigrated.",
            ]
        )
        return steps


def legacy_kind(name: str) -> str | None:
    """`waker` / `poll` for a per-worker service under ANY brand prefix, else None."""
    suffix = service._debrand(service._slug(name))
    if suffix is None:
        return None
    for prefix, kind in _KINDS.items():
        if suffix.startswith(f"{prefix}-"):
            return kind
    return None


def _identity_slug(name: str) -> str:
    """The brand-independent part of a service name: `wake-<role>-<instance>`."""
    slug = service._slug(name)
    return service._debrand(slug) or slug


def plan_migration(
    fleet_path: Path | None = None,
    *,
    list_services: Callable[[], list[dict[str, object]]] | None = None,
) -> MigrationPlan:
    """Read-only. Enumerate every legacy per-worker service and what would happen to it."""
    fleet_path = fleet_path or FLEET_CONFIG_PATH
    list_services = list_services or service.list_status
    plan = MigrationPlan(fleet_path=fleet_path)

    by_slug: dict[str, tuple[str, str]] = {}
    workers: dict[str, object] = {}
    try:
        workers = dict(load_fleet(fleet_path))
    except FleetConfigMissing:
        plan.fleet_error = f"no fleet config at {fleet_path}"
    except (FleetConfigError, OSError, ValueError) as exc:
        plan.fleet_error = f"fleet config could not be read: {exc}"
    for key, worker in workers.items():
        by_slug[_identity_slug(worker.waker_service_name)] = (key, worker.mode)  # type: ignore[attr-defined]
        by_slug[_identity_slug(worker.poll_service_name)] = (key, worker.mode)  # type: ignore[attr-defined]

    # The OS can report one service several times (Task Scheduler lists a
    # task once per trigger). One row per service; running if any row says so.
    installed: dict[str, bool] = {}
    for state in list_services():
        name = str(state.get("name") or service.SERVICE_NAME)
        installed[name] = installed.get(name, False) or bool(state.get("running"))

    matched: set[str] = set()
    for name, running in installed.items():
        kind = legacy_kind(name)
        if kind is None:
            if service._slug(name) == service._slug(AGENTD_SERVICE_NAME):
                plan.agentd_installed = True
            else:
                plan.left_alone.append(name)
            continue
        entry = by_slug.get(_identity_slug(name))
        if entry is None:
            reason = plan.fleet_error or "no matching worker in fleet.toml"
            plan.legacy.append(
                LegacyService(
                    name,
                    kind,
                    running,
                    None,
                    "ORPHAN - left alone",
                    f"{reason}; nothing is deleted without being asked",
                )
            )
            continue
        worker_key, mode = entry
        matched.add(worker_key)
        note = ""
        if mode == "off":
            note = "fleet.toml has mode = off: removed, and the supervisor will not run it"
        elif mode != kind:
            note = f"fleet.toml says mode = {mode}: the supervisor runs it as {mode}"
        plan.legacy.append(
            LegacyService(name, kind, running, worker_key, "stop, verify exit, remove", note)
        )

    plan.legacy.sort(key=lambda item: item.name.casefold())
    plan.left_alone.sort(key=str.casefold)
    plan.workers_without_service = sorted(
        key
        for key, worker in workers.items()
        if key not in matched and worker.mode != "off"  # type: ignore[attr-defined]
    )
    return plan
