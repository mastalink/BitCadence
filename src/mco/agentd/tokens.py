"""Owner-only credentials for the local agent daemon capabilities."""

from __future__ import annotations

import csv
import os
from pathlib import Path
import secrets
import subprocess
import tempfile


AGENTD_TOKEN_PATH = Path.home() / ".mco" / "agentd.token"
AGENTD_LOG_TOKEN_PATH = Path.home() / ".mco" / "agentd.logs.token"


class AgentdTokenStore:
    """Create and read separate control and log bearer credentials."""

    def __init__(
        self,
        control_path: Path = AGENTD_TOKEN_PATH,
        logs_path: Path = AGENTD_LOG_TOKEN_PATH,
    ) -> None:
        self.control_path = control_path
        self.logs_path = logs_path

    def control_token(self) -> str:
        return self._read_or_create(self.control_path)

    def logs_token(self) -> str:
        return self._read_or_create(self.logs_path)

    @staticmethod
    def _read_or_create(path: Path) -> str:
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            token = path.read_text(encoding="utf-8").strip()
        except FileNotFoundError:
            token = ""
        if not token:
            token = secrets.token_urlsafe(48)
            fd, temporary_name = tempfile.mkstemp(
                dir=path.parent, prefix=f".{path.name}.", suffix=".tmp"
            )
            temporary = Path(temporary_name)
            try:
                os.fchmod(fd, 0o600)
                with os.fdopen(fd, "w", encoding="utf-8", newline="") as handle:
                    handle.write(token + "\n")
                    handle.flush()
                    os.fsync(handle.fileno())
                try:
                    os.link(temporary, path)
                except FileExistsError:
                    token = path.read_text(encoding="utf-8").strip()
                else:
                    temporary.unlink()
            finally:
                temporary.unlink(missing_ok=True)
        _harden_owner_only(path)
        if not token:
            raise RuntimeError(f"agentd token file is empty: {path}")
        return token


def _harden_owner_only(path: Path) -> None:
    os.chmod(path, 0o600)
    if os.name != "nt":
        return
    sid = current_user_sid()
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    result = subprocess.run(
        ["icacls", str(path), "/inheritance:r", "/grant:r", f"*{sid}:F"],
        capture_output=True,
        text=True,
        creationflags=flags,
    )
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or "icacls failed"
        raise RuntimeError(f"could not restrict {path} to SID {sid}: {detail}")


def current_user_sid() -> str:
    """Return the current Windows user's SID without relying on localized labels."""
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    result = subprocess.run(
        ["whoami", "/user", "/fo", "csv", "/nh"],
        capture_output=True,
        text=True,
        creationflags=flags,
    )
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or "whoami failed"
        raise RuntimeError(f"could not determine current user SID: {detail}")
    row = next(csv.reader([result.stdout.strip()]), [])
    if len(row) < 2 or not row[1].startswith("S-"):
        raise RuntimeError("whoami returned no Windows user SID")
    return row[1]
