"""One global kill switch: a STOP file plus a phone command.

Every actor that starts new work (shepherd, watchdog scheduler, Score
conductor sweep, uploader, worker wrappers, agentd supervisor) calls
``stop_active()`` before each action and starts nothing new while it is set.
Work already in flight and already-scheduled private uploads are left alone.

Only the owner clears it: nothing in this module deletes the file. The phone
command can only SET the switch; clearing is deleting the file by hand.
The retired ``~/.mco/leo/STOP`` file is deliberately not a signal.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

ENV_VAR = "MCO_STOP_FILE"
_PHONE_WORDS = {"stop", "stop all", "kill", "kill switch", "halt"}


def stop_path() -> Path:
    override = os.environ.get(ENV_VAR)
    return Path(override) if override else Path.home() / ".mco" / "STOP"


def legacy_paths() -> list[Path]:
    """Old per-project pause files, honoured as aliases. leo/STOP is excluded."""
    raw = os.environ.get("MCO_LEGACY_PAUSE_FILES", "")
    return [Path(p) for p in raw.split(os.pathsep) if p.strip()]


def stop_active() -> bool:
    try:
        if stop_path().exists():
            return True
        return any(p.exists() for p in legacy_paths())
    except OSError:
        # Fail closed: if we cannot tell, do not start new work.
        return True


def stop_reason() -> dict | None:
    if not stop_active():
        return None
    try:
        data = json.loads(stop_path().read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {"raw": True}
    except (OSError, ValueError):
        return {"unreadable_or_empty": True}


def set_stop(source: str, note: str = "") -> Path:
    """Create the STOP file. Idempotent; never overwrites an existing one."""
    path = stop_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    body = {"set_at": datetime.now(timezone.utc).isoformat(), "source": source, "note": note}
    try:
        with open(path, "x", encoding="utf-8") as fh:
            json.dump(body, fh)
    except FileExistsError:
        pass
    return path


def handle_phone_command(text: str, sender_is_owner: bool) -> str:
    """Phone command: owner texting STOP sets the switch. Nothing can clear it."""
    word = " ".join((text or "").strip().lower().split())
    if word in _PHONE_WORDS:
        if not sender_is_owner:
            return "ignored: not the owner"
        set_stop("phone", word)
        return "stopped"
    if word in {"resume", "clear", "go"}:
        return "refused: delete the STOP file by hand (see runbook)"
    return "ignored"


def guard(action: str = "new work") -> bool:
    """True when it is OK to start ``action``; False while the switch is set."""
    return not stop_active()
