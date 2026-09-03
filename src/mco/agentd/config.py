"""Fleet configuration validation, atomic writes, and generation reconciliation."""

from __future__ import annotations

from dataclasses import asdict
import hashlib
import os
from pathlib import Path
import tempfile
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
        fd, temporary_name = tempfile.mkstemp(
            dir=self.path.parent, prefix=f".{self.path.name}.", suffix=".tmp"
        )
        temporary = Path(temporary_name)
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.path)
        finally:
            temporary.unlink(missing_ok=True)
        return parsed

    def content_generation(self) -> str | None:
        if not self.path.exists():
            return None
        return hashlib.sha256(self.path.read_bytes()).hexdigest()

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
        self.last_generation: str | None = None
        self._has_checked = False
        self.last_error: str | None = None

    def poll(self, force: bool = False) -> bool:
        now = self.clock()
        if not force and now < self.next_check:
            return False
        self.next_check = now + self.interval
        generation = self.manager.content_generation()
        if not force and self._has_checked and generation == self.last_generation:
            return False
        try:
            configs = self.manager.load() if self.manager.path.exists() else {}
            self.supervisor.reconcile(configs)
        except Exception as exc:
            self.last_error = str(exc)
            return False
        self.last_generation = generation
        self._has_checked = True
        self.last_error = None
        return True

    def replace(self, content: str) -> dict[str, WorkerConfig]:
        parsed = self.manager.write(content)
        self.supervisor.reconcile(parsed)
        self.last_generation = self.manager.content_generation()
        self._has_checked = True
        self.last_error = None
        return parsed
