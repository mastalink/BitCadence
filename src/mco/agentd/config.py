"""Fleet configuration validation, atomic writes, and mtime reconciliation."""

from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
import time
from typing import Callable

try:  # pragma: no cover - selected by interpreter version
    import tomllib
except ModuleNotFoundError:  # pragma: no cover
    import tomli as tomllib

from mco.fleet import FLEET_CONFIG_PATH, WorkerConfig, load_fleet, parse_fleet_data

from .supervisor import Supervisor


class FleetConfigManager:
    def __init__(self, path: Path = FLEET_CONFIG_PATH) -> None:
        self.path = path

    def load(self) -> dict[str, WorkerConfig]:
        return load_fleet(self.path)

    def validate(self, content: str) -> dict[str, WorkerConfig]:
        data = tomllib.loads(content)
        return parse_fleet_data(data)

    def write(self, content: str) -> dict[str, WorkerConfig]:
        parsed = self.validate(content)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_name(f".{self.path.name}.tmp")
        temporary.write_text(content, encoding="utf-8")
        temporary.replace(self.path)
        return parsed

    def snapshot(self) -> dict[str, object]:
        content = self.path.read_text(encoding="utf-8") if self.path.exists() else ""
        parsed = self.load() if self.path.exists() else {}
        return {"content": content, "workers": {k: asdict(v) for k, v in parsed.items()}}


class FleetWatcher:
    def __init__(
        self,
        manager: FleetConfigManager,
        supervisor: Supervisor,
        *,
        clock: Callable[[], float] = time.monotonic,
        interval: float = 5.0,
    ) -> None:
        self.manager = manager
        self.supervisor = supervisor
        self.clock = clock
        self.interval = interval
        self.next_check = 0.0
        self.last_mtime_ns: int | None = None
        self._has_checked = False
        self.last_error: str | None = None

    def poll(self, force: bool = False) -> bool:
        now = self.clock()
        if not force and now < self.next_check:
            return False
        self.next_check = now + self.interval
        mtime = self.manager.path.stat().st_mtime_ns if self.manager.path.exists() else None
        if not force and self._has_checked and mtime == self.last_mtime_ns:
            return False
        try:
            configs = self.manager.load() if self.manager.path.exists() else {}
            self.supervisor.reconcile(configs)
        except Exception as exc:
            self.last_error = str(exc)
            return False
        self.last_mtime_ns = mtime
        self._has_checked = True
        self.last_error = None
        return True

    def replace(self, content: str) -> dict[str, WorkerConfig]:
        parsed = self.manager.write(content)
        self.supervisor.reconcile(parsed)
        self.last_mtime_ns = self.manager.path.stat().st_mtime_ns
        self._has_checked = True
        self.last_error = None
        return parsed
