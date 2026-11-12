"""Declarative worker service orchestration for ~/.mco/fleet.toml."""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
import tempfile
from typing import Any

try:  # pragma: no cover - exercised by interpreter version
    import tomllib
except ModuleNotFoundError:  # pragma: no cover
    import tomli as tomllib

from mco import service

FLEET_CONFIG_PATH = Path.home() / ".mco" / "fleet.toml"
VALID_MODES = {"waker", "poll", "off"}
ALLOWED_FIELDS = {"role", "instance", "mode", "exec", "min_interval", "poll_interval", "background"}


class FleetConfigMissing(FileNotFoundError):
    pass


class FleetConfigError(ValueError):
    pass


@dataclass(frozen=True)
class WorkerConfig:
    worker: str
    role: str
    instance: str | None
    mode: str
    exec_command: str | None
    min_interval: float
    poll_interval: float
    # True (S4U scheduled task, no stored password) lets a waker run whether or
    # not a user is logged on. Default False preserves every existing worker's
    # InteractiveToken behavior; only a sidecar that must never go dark opts in.
    background: bool = False

    @property
    def waker_service_name(self) -> str:
        return service._waker_service_name(self.role, self.instance)

    @property
    def poll_service_name(self) -> str:
        return service._poll_service_name(self.role, self.instance)

    @property
    def active_service_name(self) -> str | None:
        if self.mode == "waker":
            return self.waker_service_name
        if self.mode == "poll":
            return self.poll_service_name
        return None


def sample_config() -> str:
    return """[workers.opencode-beast]
role = "opencode"
instance = "opencode-beast"
mode = "waker"
exec = "C:/Users/masta/.mco/bin/opencode-beast-run.cmd"
min_interval = 10
poll_interval = 1800
"""


def load_fleet(path: Path = FLEET_CONFIG_PATH) -> dict[str, WorkerConfig]:
    if not path.exists():
        raise FleetConfigMissing(str(path))
    with path.open("rb") as handle:
        try:
            data = tomllib.load(handle)
        except tomllib.TOMLDecodeError as exc:
            raise FleetConfigError(f"Invalid TOML in {path}: {exc}") from exc
    return parse_fleet_data(data)


def parse_fleet_data(data: dict[str, Any]) -> dict[str, WorkerConfig]:
    workers = data.get("workers")
    if workers is None:
        return {}
    if not isinstance(workers, dict):
        raise FleetConfigError("[workers] must be a TOML table")

    parsed: dict[str, WorkerConfig] = {}
    for worker, raw in workers.items():
        if not isinstance(raw, dict):
            raise FleetConfigError(f"workers.{worker} must be a TOML table")
        unknown = set(raw) - ALLOWED_FIELDS
        if unknown:
            fields = ", ".join(sorted(unknown))
            raise FleetConfigError(f"workers.{worker} has unsupported field(s): {fields}")
        role = _required_string(raw, "role", worker)
        instance = _optional_string(raw, "instance", worker) or worker
        mode = _required_string(raw, "mode", worker)
        if mode not in VALID_MODES:
            raise FleetConfigError(f"workers.{worker}.mode must be one of: off, poll, waker")
        exec_command = _optional_string(raw, "exec", worker)
        if mode in {"waker", "poll"} and not exec_command:
            raise FleetConfigError(f"workers.{worker}.exec is required when mode={mode}")
        parsed[worker] = WorkerConfig(
            worker=worker,
            role=role,
            instance=instance,
            mode=mode,
            exec_command=exec_command,
            min_interval=_number(raw.get("min_interval", 10), "min_interval", worker),
            poll_interval=_number(raw.get("poll_interval", 1800), "poll_interval", worker),
            background=_bool(raw.get("background", False), "background", worker),
        )
    return parsed


def missing_agent_tokens(workers: dict[str, Any]) -> list[str]:
    """Instances that will run but have no token to authenticate with.

    A waker with no resolvable token installs cleanly, starts, is rejected by
    the gateway, and exits 1 - while the board still shows the agent "online"
    from its last heartbeat. That combination is why a dead fleet can look
    healthy for days, so `apply_fleet` warns about it up front instead.
    """
    from mco.waker import read_agent_token_file

    missing = []
    for worker in workers.values():
        if worker.mode not in {"waker", "poll"} or not worker.instance:
            continue
        if not read_agent_token_file(worker.instance):
            missing.append(worker.instance)
    return sorted(missing)


def apply_fleet(path: Path = FLEET_CONFIG_PATH) -> list[str]:
    workers = load_fleet(path)
    installed = _installed_worker_service_names()
    summaries: list[str] = []

    # Surfaced as a warning, not a hard failure: the token may legitimately
    # arrive via MCO_AGENT_TOKEN in this worker's environment.
    for instance in missing_agent_tokens(workers):
        summaries.append(
            f"{instance}: WARNING no token at ~/.mco/tokens/{instance}.token - "
            f"the waker will fail to authenticate unless MCO_AGENT_TOKEN is set "
            f"for it. Fix: mco reset-token {instance}"
        )
    active_names = {
        worker.active_service_name
        for worker in workers.values()
        if worker.active_service_name is not None
    }

    for worker in workers.values():
        if worker.mode == "waker":
            summaries.extend(_uninstall_if_present([worker.poll_service_name], installed))
            ok_flag, detail = service.install_waker(
                worker.role,
                worker.exec_command or "",
                instance=worker.instance,
                min_interval=worker.min_interval,
                run_when_logged_off=worker.background,
            )
            summaries.append(_format_result(worker.worker, "waker", ok_flag, detail))
            if not ok_flag:
                raise FleetConfigError(detail)
            installed.add(worker.waker_service_name)
            installed.discard(worker.poll_service_name)
        elif worker.mode == "poll":
            summaries.extend(_uninstall_if_present([worker.waker_service_name], installed))
            ok_flag, detail = service.install_poll(
                worker.role,
                worker.exec_command or "",
                instance=worker.instance,
                poll_interval=worker.poll_interval,
            )
            summaries.append(_format_result(worker.worker, "poll", ok_flag, detail))
            if not ok_flag:
                raise FleetConfigError(detail)
            installed.add(worker.poll_service_name)
            installed.discard(worker.waker_service_name)
        else:
            removed = _uninstall_if_present([worker.waker_service_name, worker.poll_service_name], installed)
            summaries.extend(removed or [f"{worker.worker}: off (no installed worker service)"])

    removed_names = sorted(name for name in installed if name not in active_names)
    summaries.extend(_uninstall_if_present(removed_names, installed, prefix="removed worker"))
    return summaries


def fleet_status(path: Path = FLEET_CONFIG_PATH) -> list[dict[str, object]]:
    workers = load_fleet(path)
    rows: list[dict[str, object]] = []
    for worker in workers.values():
        names = [worker.active_service_name] if worker.active_service_name else [
            worker.waker_service_name,
            worker.poll_service_name,
        ]
        states = [service.status(name) for name in names if name]
        installed = any(bool(state.get("installed")) for state in states)
        running = any(bool(state.get("running")) for state in states)
        rows.append({
            "worker": worker.worker,
            "role": worker.role,
            "instance": worker.instance,
            "mode": worker.mode,
            "service": worker.active_service_name or "off",
            "installed": installed,
            "running": running,
            "last_exit": next((state.get("last_exit") for state in states if state.get("last_exit")), "unknown"),
        })
    return rows


def set_worker_value(worker: str, assignment: str, path: Path = FLEET_CONFIG_PATH) -> str:
    if "=" not in assignment:
        raise FleetConfigError("Use KEY=VALUE, for example: mode=waker")
    key, value = assignment.split("=", 1)
    key = key.strip()
    if key not in ALLOWED_FIELDS:
        raise FleetConfigError(f"Unsupported field: {key}")
    if not path.exists():
        raise FleetConfigMissing(str(path))
    with path.open("rb") as handle:
        data = tomllib.load(handle)
    workers = data.setdefault("workers", {})
    if worker not in workers or not isinstance(workers[worker], dict):
        raise FleetConfigError(f"Worker not found: {worker}")
    workers[worker][key] = _coerce_assignment_value(key, value.strip())
    parse_fleet_data(data)
    _atomic_write_text(path, _render_fleet_toml(workers))
    return f"Updated {worker}.{key}; run 'mco fleet apply' for the change to take effect."


def _atomic_write_text(path: Path, text: str) -> None:
    """Replace `path` in one step so a reader never sees a half-written file.

    `Path.write_text()` truncates first and writes second. Anything watching
    the file (the supervisor polls it) can read it in between and see a
    shorter fleet than the real one. Writing a sibling temp file, flushing it
    to disk and renaming it over the target has no such window.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    temporary = Path(temporary_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _required_string(raw: dict[str, Any], key: str, worker: str) -> str:
    value = raw.get(key)
    if not isinstance(value, str) or not value.strip():
        raise FleetConfigError(f"workers.{worker}.{key} is required")
    return value.strip()


def _optional_string(raw: dict[str, Any], key: str, worker: str) -> str | None:
    value = raw.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise FleetConfigError(f"workers.{worker}.{key} must be a string")
    return value.strip() or None


def _bool(value: Any, key: str, worker: str) -> bool:
    if not isinstance(value, bool):
        raise FleetConfigError(f"workers.{worker}.{key} must be a boolean")
    return value


def _number(value: Any, key: str, worker: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise FleetConfigError(f"workers.{worker}.{key} must be a number")
    if value <= 0:
        raise FleetConfigError(f"workers.{worker}.{key} must be greater than zero")
    return float(value)


def _installed_worker_service_names() -> set[str]:
    states = service.list_status()
    names = {str(state.get("name", "")) for state in states}
    return {
        name
        for name in names
        if service._slug(name).startswith("bitcadence-wake-")
        or service._slug(name).startswith("bitcadence-poll-")
    }


def _uninstall_if_present(names: list[str], installed: set[str], prefix: str = "uninstalled") -> list[str]:
    summaries: list[str] = []
    for name in names:
        if name not in installed:
            continue
        ok_flag, detail = service.uninstall(name)
        summaries.append(f"{prefix} {name}: {'OK' if ok_flag else 'FAILED'} - {detail}")
        if not ok_flag:
            raise FleetConfigError(detail)
        installed.discard(name)
    return summaries


def _format_result(worker: str, mode: str, ok_flag: bool, detail: str) -> str:
    return f"{worker}: {mode} {'OK' if ok_flag else 'FAILED'} - {detail}"


def _coerce_assignment_value(key: str, value: str) -> str | int | float | bool:
    if key in {"min_interval", "poll_interval"}:
        number = float(value)
        return int(number) if number.is_integer() else number
    if key == "background":
        lowered = value.strip().lower()
        if lowered not in {"true", "false"}:
            raise FleetConfigError("background must be true or false")
        return lowered == "true"
    if key == "mode" and value not in VALID_MODES:
        raise FleetConfigError("mode must be one of: off, poll, waker")
    return value


def _render_fleet_toml(workers: dict[str, Any]) -> str:
    lines: list[str] = []
    field_order = ["role", "instance", "mode", "exec", "min_interval", "poll_interval", "background"]
    for worker, raw in workers.items():
        lines.append(f"[workers.{_toml_key(worker)}]")
        for field in field_order:
            if field in raw:
                lines.append(f"{field} = {_toml_value(raw[field])}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _toml_key(value: str) -> str:
    if re.fullmatch(r"[A-Za-z0-9_-]+", value):
        return value
    return json.dumps(value)


def _toml_value(value: Any) -> str:
    if isinstance(value, str):
        return json.dumps(value)
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    raise FleetConfigError(f"Unsupported TOML value: {value!r}")
