"""Size-capped unified and per-worker child log aggregation.

The log contract (ADR 0002 R10):

* **Both streams are drained concurrently**, one thread each. A worker that
  floods stdout while nobody reads stderr (or the reverse) would otherwise
  fill a pipe, block forever, and still look healthy.
* **Memory is bounded.** A line is read at most ``MAX_LINE_BYTES`` at a time,
  so output with no newline cannot grow a buffer without limit. Nothing is
  queued: a reader writes each piece to disk before reading the next, so a
  slow disk slows the worker down (backpressure through the OS pipe) instead
  of growing the daemon.
* **A full disk never stops draining.** When a write fails the line is
  dropped and counted, and the reader keeps reading - losing log lines is
  better than deadlocking the worker on a full pipe.
* Files rotate at ``MAX_LOG_BYTES`` and ``BACKUP_COUNT`` are kept.

Worker output is copied verbatim and is readable through ``/v1/logs``.
**Workers must not log secrets** - see docs/SDK.md.
"""

from __future__ import annotations

from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path
import re
import threading
from typing import Any


MAX_LOG_BYTES = 10 * 1024 * 1024
BACKUP_COUNT = 3
MAX_LINE_BYTES = 16 * 1024


class LogAggregator:
    def __init__(
        self,
        log_dir: Path | None = None,
        *,
        max_bytes: int = MAX_LOG_BYTES,
        backup_count: int = BACKUP_COUNT,
    ) -> None:
        self.log_dir = log_dir or Path.home() / ".mco" / "logs"
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.max_bytes = max_bytes
        self.backup_count = backup_count
        self._lock = threading.Lock()
        self._handlers: dict[str, RotatingFileHandler] = {}
        self._dropped = 0
        self._last_write_error: str | None = None

    def attach(self, worker: str, process: Any) -> list[threading.Thread]:
        """Drain both Popen-style streams without blocking supervision."""
        threads: list[threading.Thread] = []
        for stream_name in ("stdout", "stderr"):
            stream = getattr(process, stream_name, None)
            if stream is None:
                continue
            thread = threading.Thread(
                target=self._drain,
                args=(worker, stream_name, stream),
                name=f"agentd-log-{worker}-{stream_name}",
                daemon=True,
            )
            thread.start()
            threads.append(thread)
        return threads

    def _drain(self, worker: str, stream_name: str, stream: Any) -> None:
        try:
            while True:
                try:
                    line = stream.readline(MAX_LINE_BYTES)
                except (OSError, ValueError):
                    break
                if not line:
                    break
                if isinstance(line, bytes):
                    line = line.decode("utf-8", errors="replace")
                self.write(worker, stream_name, line.rstrip("\r\n"))
        finally:
            try:
                stream.close()
            except (OSError, ValueError):
                pass

    def write(self, worker: str, stream: str, message: str) -> None:
        timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        line = f"{timestamp}  {worker}  {stream}  {message}"
        with self._lock:
            self._emit("agentd", line)
            self._emit(self._safe_worker_name(worker), line)

    def _emit(self, name: str, line: str) -> None:
        data = line + "\n"
        try:
            handler = self._handlers.get(name)
            if handler is None:
                handler = RotatingFileHandler(
                    self.log_dir / f"{name}.log",
                    maxBytes=self.max_bytes,
                    backupCount=self.backup_count,
                    encoding="utf-8",
                )
                self._handlers[name] = handler
            if handler.stream is None:
                handler.stream = handler._open()
            if self.max_bytes > 0:
                handler.stream.seek(0, 2)
                if handler.stream.tell() + len(data.encode("utf-8")) > self.max_bytes:
                    try:
                        handler.doRollover()
                    except OSError as exc:
                        # Could not rotate (a reader has the file open on
                        # Windows). Keep appending; try again on the next line.
                        self._last_write_error = f"rotate {name}.log: {exc}"
                        if handler.stream is None:
                            handler.stream = handler._open()
            handler.stream.write(data)
            handler.flush()
        except (OSError, ValueError) as exc:
            # Disk full, file gone, permission lost. Drop the line, keep
            # draining, and reopen the file next time.
            self._dropped += 1
            self._last_write_error = f"write {name}.log: {exc}"
            broken = self._handlers.pop(name, None)
            if broken is not None:
                try:
                    broken.close()
                except (OSError, ValueError):
                    pass

    def health(self) -> dict[str, object]:
        """Whether log lines are being lost, for /v1/status."""
        return {"dropped_lines": self._dropped, "last_write_error": self._last_write_error}

    def close(self) -> None:
        with self._lock:
            for handler in self._handlers.values():
                try:
                    handler.close()
                except (OSError, ValueError):
                    pass
            self._handlers.clear()

    def tail(self, worker: str | None = None, lines: int = 100) -> list[str]:
        lines = max(0, min(int(lines), 10_000))
        name = self._safe_worker_name(worker) if worker else "agentd"
        path = self.log_dir / f"{name}.log"
        if not path.exists() or lines == 0:
            return []
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            return [line.rstrip("\n") for line in handle.readlines()[-lines:]]

    @staticmethod
    def _safe_worker_name(worker: str | None) -> str:
        safe = re.sub(r"[^A-Za-z0-9_.-]+", "-", worker or "").strip("-.")
        return safe or "unknown"
