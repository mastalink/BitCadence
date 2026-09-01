"""Size-capped unified and per-worker child log aggregation."""

from __future__ import annotations

from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path
import re
import threading
from typing import Any


MAX_LOG_BYTES = 10 * 1024 * 1024
BACKUP_COUNT = 3


class LogAggregator:
    def __init__(self, log_dir: Path | None = None) -> None:
        self.log_dir = log_dir or Path.home() / ".mco" / "logs"
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._handlers: dict[str, RotatingFileHandler] = {}

    def attach(self, worker: str, process: Any) -> None:
        """Drain both Popen-style streams without blocking supervision."""
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

    def _drain(self, worker: str, stream_name: str, stream: Any) -> None:
        try:
            while True:
                line = stream.readline()
                if not line:
                    break
                if isinstance(line, bytes):
                    line = line.decode("utf-8", errors="replace")
                self.write(worker, stream_name, line.rstrip("\r\n"))
        finally:
            stream.close()

    def write(self, worker: str, stream: str, message: str) -> None:
        timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        line = f"{timestamp}  {worker}  {stream}  {message}"
        with self._lock:
            self._emit("agentd", line)
            self._emit(self._safe_worker_name(worker), line)

    def _emit(self, name: str, line: str) -> None:
        handler = self._handlers.get(name)
        if handler is None:
            handler = RotatingFileHandler(
                self.log_dir / f"{name}.log",
                maxBytes=MAX_LOG_BYTES,
                backupCount=BACKUP_COUNT,
                encoding="utf-8",
            )
            self._handlers[name] = handler
        handler.stream.write(line + "\n")
        handler.flush()

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
