"""Fleet configuration validation, atomic writes, and generation reconciliation.

ADR 0002 R5. Reconciliation is transactional:

* a **generation** is the SHA-256 of the file's bytes - never ``mtime``, whose
  granularity can coalesce two writes into one tick;
* a candidate is read **once**, and that exact text is what gets hashed,
  parsed and validated - never a second read that may see different bytes;
* a changed candidate must be **stable** (identical on a second read a moment
  later) before it is considered, so a slow in-place write is never mistaken
  for a smaller fleet;
* on any read, parse or validation error the running generation is kept, the
  error is surfaced, and the next poll tries again;
* writers replace the file atomically, and carry the generation they read, so
  a stale writer gets a conflict instead of silently losing someone's edit.
"""

from __future__ import annotations

from dataclasses import asdict
import hashlib
from pathlib import Path
import threading
import time
from typing import Callable

try:  # pragma: no cover - selected by interpreter version
    import tomllib
except ModuleNotFoundError:  # pragma: no cover
    import tomli as tomllib

from mco.fleet import FLEET_CONFIG_PATH, WorkerConfig, load_fleet, parse_fleet_data

from .identity import atomic_write_text
from .supervisor import Supervisor


# The generation of "there is no fleet.toml". A real value, so a writer can say
# "I expect the file not to exist yet".
ABSENT_GENERATION = "absent"
SETTLE_SECONDS = 0.25


class GenerationConflict(RuntimeError):
    """The file changed since the writer read it (R5: a 409, not an overwrite)."""

    def __init__(self, expected: str, current: str) -> None:
        super().__init__(
            f"fleet config changed since it was read (expected generation {expected[:12]}, "
            f"current is {current[:12]}); re-read it and try again"
        )
        self.expected = expected
        self.current = current


def generation_of(content: bytes | None) -> str:
    if content is None:
        return ABSENT_GENERATION
    return hashlib.sha256(content).hexdigest()


class FleetConfigManager:
    def __init__(self, path: Path = FLEET_CONFIG_PATH) -> None:
        self.path = path
        self._write_lock = threading.Lock()

    def load(self) -> dict[str, WorkerConfig]:
        return load_fleet(self.path)

    def validate(self, content: str) -> dict[str, WorkerConfig]:
        data = tomllib.loads(content)
        return parse_fleet_data(data)

    def read(self) -> bytes | None:
        """One read of the whole file; ``None`` when it does not exist."""
        try:
            return self.path.read_bytes()
        except FileNotFoundError:
            return None

    def write(
        self, content: str, expected_generation: str | None = None
    ) -> dict[str, WorkerConfig]:
        """Validate, then atomically replace. Nothing is touched on a bad candidate."""
        parsed = self.validate(content)
        with self._write_lock:
            if expected_generation is not None:
                current = generation_of(self.read())
                if current != expected_generation:
                    raise GenerationConflict(expected_generation, current)
            atomic_write_text(self.path, content)
        return parsed

    def content_generation(self) -> str | None:
        content = self.read()
        return None if content is None else generation_of(content)

    def snapshot(self) -> dict[str, object]:
        raw = self.read()
        content = raw.decode("utf-8") if raw is not None else ""
        parsed = self.validate(content) if raw is not None else {}
        return {
            "generation": generation_of(raw),
            "content": content,
            "workers": {k: asdict(v) for k, v in parsed.items()},
        }


class FleetWatcher:
    def __init__(
        self,
        manager: FleetConfigManager,
        supervisor: Supervisor,
        *,
        clock: Callable[[], float] = time.monotonic,
        interval: float = 2.0,
        settle: float = SETTLE_SECONDS,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.manager = manager
        self.supervisor = supervisor
        self.clock = clock
        self.interval = interval
        self.settle = settle
        self.sleep = sleep
        self.next_check = 0.0
        self.last_generation: str | None = None
        self._has_checked = False
        self.last_error: str | None = None
        # The tick thread polls while the control API replaces: one at a time.
        self._mutex = threading.RLock()

    @property
    def generation(self) -> str:
        """The last-known-good generation the supervisor is running."""
        return self.last_generation or ABSENT_GENERATION

    def poll(self, force: bool = False) -> bool:
        with self._mutex:
            return self._poll(force)

    def _poll(self, force: bool) -> bool:
        now = self.clock()
        if not force and now < self.next_check:
            return False
        self.next_check = now + self.interval

        try:
            candidate = self.manager.read()
        except OSError as exc:
            self.last_error = f"could not read fleet config: {exc}"
            return False
        generation = generation_of(candidate)
        if self._has_checked and generation == self.last_generation:
            if not force:
                return False
        elif self._has_checked and not self._is_stable(generation):
            # Still being written. Keep the running generation and look again.
            self.last_error = "fleet config is still changing; waiting for it to settle"
            return False

        running_something = self._has_checked and self.last_generation != ABSENT_GENERATION
        if running_something and not (candidate or b"").strip():
            # A file that vanished or was truncated to nothing is what an
            # in-place write looks like halfway through (and what an editor
            # that deletes-then-recreates leaves for a moment). It is never
            # read as "remove every worker". To stop a worker, set mode = "off".
            self.last_error = (
                f"{self.manager.path.name} is missing or empty; "
                "keeping the running configuration"
            )
            return False

        try:
            configs = self.manager.validate(candidate.decode("utf-8")) if candidate else {}
        except Exception as exc:
            # Never reconcile a partial or invalid candidate (R5).
            self.last_error = f"invalid fleet config, keeping the running configuration: {exc}"
            return False
        try:
            self.supervisor.reconcile(configs)
        except Exception as exc:
            self.last_error = str(exc)
            return False
        self.last_generation = generation
        self._has_checked = True
        self.last_error = None
        return True

    def _is_stable(self, generation: str) -> bool:
        if self.settle > 0:
            self.sleep(self.settle)
        try:
            return generation_of(self.manager.read()) == generation
        except OSError:
            return False

    def replace(
        self, content: str, expected_generation: str | None = None
    ) -> dict[str, WorkerConfig]:
        with self._mutex:
            parsed = self.manager.write(content, expected_generation)
            self.supervisor.reconcile(parsed)
            self.last_generation = generation_of(content.encode("utf-8"))
            self._has_checked = True
            self.last_error = None
            return parsed
