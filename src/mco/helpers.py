"""Helpers in plain words: a friendly name, a health light with a word, what each
one is doing, and "Fix it" for the two problems that look fine from outside.

Shared by `bitcadence helpers` / `bitcadence fix` and the console's Helpers page
(mco.orchestrator.helpers_routes), so both show and repair the same things.

Fix it detects first (a dry run that changes nothing), and repairs only when the
caller says it was confirmed:
  * a locked worker log - another process holds <instance>.log open
  * a duplicate wake process - two `wake` processes for the same instance
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable, List, Optional

LOCKED_LOG = "locked_log"
DUPLICATE_WAKER = "duplicate_waker"

_NAME_RE = re.compile(r"^[A-Za-z0-9._:-]{1,64}$")
KNOWN_AIS = [("claude", "Claude"), ("codex", "ChatGPT / Codex"), ("antigravity", "Gemini / Antigravity")]


class HelperError(Exception):
    """A problem worth showing as-is: the message is already plain words."""


# ─────────────────────────────────────────────────────────────────────────────
# Friendly names, health light, what it is doing
# ─────────────────────────────────────────────────────────────────────────────
def friendly_name(instance_id: str) -> str:
    """'claude-worker_3' -> 'Claude worker 3'. The id itself still works everywhere."""
    words = re.sub(r"[-_.:]+", " ", str(instance_id or "")).split()
    if not words:
        return "A helper"
    text = " ".join(words)
    return text[0].upper() + text[1:]


# state -> (light, word). The word always travels with the colour.
_LIGHTS = {
    "working": ("green", "Working"),
    "standby": ("green", "Ready"),
    "broken": ("red", "Stuck"),
    "offline": ("grey", "Not connected"),
    "disabled": ("grey", "Paused"),
}


def health(helper: dict, findings: Iterable["Finding"] = ()) -> dict:
    """The light and the word. A fix-it finding turns it red even when the helper
    looks online - the whole point of watching for locked logs."""
    if any(f.instance == helper.get("instance_id") for f in findings):
        return {"light": "red", "word": "Stuck"}
    state = helper.get("state") or helper.get("effective_status") or "offline"
    if state == "online":
        state = "standby"
    light, word = _LIGHTS.get(state, ("grey", "Not connected"))
    return {"light": light, "word": word}


def _job_title(job: dict) -> str:
    return (job.get("title") or "a job").strip()


def doing(helper: dict, jobs: Iterable[dict] = (), findings: Iterable["Finding"] = ()) -> str:
    """What the helper is doing, in one plain sentence."""
    for finding in findings:
        if finding.instance == helper.get("instance_id"):
            return finding.short
    instance = helper.get("instance_id")
    state = helper.get("state") or "offline"
    if state == "working":
        for job in jobs:
            if job.get("leased_by_instance_id") == instance and job.get("status") in ("leased", "in_progress"):
                return f"Working on: {_job_title(job)}"
        return "Working on a job"
    if state == "broken":
        reason = helper.get("state_reason")
        return "Isn't picking up its jobs" + (f" ({reason})" if reason else "")
    if state == "disabled":
        return "Paused. It won't take new work until you turn it back on"
    if state == "offline":
        return "Not connected right now"
    return "Waiting for a job"


def describe(helpers: Iterable[dict], jobs: Iterable[dict] = (), findings: Iterable["Finding"] = ()) -> List[dict]:
    """The plain view of each helper. Never includes a token or a credential."""
    jobs = list(jobs)
    findings = list(findings)
    out = []
    for h in helpers:
        instance = h.get("instance_id") or ""
        light = health(h, findings)
        out.append({
            "id": instance,
            "name": friendly_name(instance),
            "role": h.get("role") or "",
            "light": light["light"],
            "word": light["word"],
            "doing": doing(h, jobs, findings),
            "last_heard": time_ago(h.get("last_seen_seconds")),
            "can_fix": any(f.instance == instance for f in findings),
        })
    return out


# ─────────────────────────────────────────────────────────────────────────────
# Fix it: detect (dry run), then repair after a confirm
# ─────────────────────────────────────────────────────────────────────────────
@dataclass
class Proc:
    pid: int
    cmdline: List[str]
    started: float = 0.0
    open_files: List[str] = field(default_factory=list)
    ppid: int = 0

    @property
    def text(self) -> str:
        return " ".join(self.cmdline).lower()

    @property
    def is_ours(self) -> bool:
        return "mco" in self.text or "bitcadence" in self.text


@dataclass
class Finding:
    kind: str
    instance: str
    summary: str              # what is wrong, in plain words
    short: str                # one line for the helper card
    would: str                # what the repair will do (the dry-run line)
    question: str             # the Y/confirm question
    pids: List[int] = field(default_factory=list)       # processes the repair stops
    kept: Optional[int] = None                          # the copy that stays
    unsafe: List[int] = field(default_factory=list)     # holders we will not stop

    def to_json(self) -> dict:
        return {"kind": self.kind, "helper": self.instance, "name": friendly_name(self.instance),
                "summary": self.summary, "would": self.would, "question": self.question,
                "stops": len(self.pids)}


def list_processes() -> List[Proc]:
    """Every process we are allowed to look at. Anything we cannot read is skipped."""
    import psutil

    procs = []
    for p in psutil.process_iter(["pid", "ppid", "cmdline", "create_time"]):
        try:
            cmdline = p.info.get("cmdline") or []
            if not cmdline:
                continue
            files = []
            # Listing open files is slow (and can stall on Windows) for processes we
            # have no reason to suspect, so only look at Python and BitCadence ones.
            if any(word in " ".join(cmdline).lower() for word in ("python", "mco", "bitcadence")):
                try:
                    files = [f.path for f in p.open_files()]
                except (psutil.AccessDenied, psutil.NoSuchProcess, OSError):
                    files = []
            procs.append(Proc(p.info["pid"], list(cmdline), p.info.get("create_time") or 0.0, files,
                              p.info.get("ppid") or 0))
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    return procs


def logs_dir() -> Path:
    return Path.home() / ".mco" / "logs"


def _slug(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "-", value.strip()).strip("-").lower() or "default"


def log_paths(instance: str, directory: Optional[Path] = None) -> List[Path]:
    """<instance>.log, plus the service-style name the installer uses
    (BitCadence-wake-<role>-<instance>.log)."""
    directory = directory or logs_dir()
    slug = _slug(instance)
    found = []
    try:
        for path in directory.glob("*.log"):
            stem = path.stem.lower()
            if stem in (instance.lower(), slug) or stem.endswith("-" + slug) and "wake" in stem:
                found.append(path)
    except OSError:
        pass
    return found


def _norm(path: str) -> str:
    return os.path.normcase(os.path.abspath(path))


def _wake_instance(proc: Proc) -> str:
    """The instance a `mco wake` process is watching, or ''."""
    args = proc.cmdline
    if "wake" not in [a.lower() for a in args] or not proc.is_ours:
        return ""
    for i, arg in enumerate(args):
        if arg == "--instance" and i + 1 < len(args):
            return args[i + 1]
        if arg.startswith("--instance="):
            return arg.split("=", 1)[1]
    return ""


def scan(instances: Iterable[str], *, procs: Optional[List[Proc]] = None,
         directory: Optional[Path] = None, own_pid: Optional[int] = None) -> List[Finding]:
    """Detect only. Never changes anything."""
    instances = [i for i in dict.fromkeys(instances) if i]
    if not instances:
        return []
    procs = list_processes() if procs is None else procs
    own_pid = os.getpid() if own_pid is None else own_pid
    procs = [p for p in procs if p.pid != own_pid]
    findings: List[Finding] = []
    for instance in instances:
        name = friendly_name(instance)
        all_wakers = [p for p in procs if _wake_instance(p) == instance]
        # A Windows venv's python.exe/pythonw.exe is a launcher that re-runs the same
        # command line in the base interpreter as its child. That child is the same
        # copy, not a second one; counting it made healthy helpers "Stuck", and Fix it
        # would have stopped the real worker (2026-10-09).
        waker_pids = {p.pid for p in all_wakers}
        wakers = sorted((p for p in all_wakers if p.ppid not in waker_pids),
                        key=lambda p: (p.started, p.pid))
        if len(wakers) > 1:
            extras = wakers[1:]
            extra_pids = {p.pid for p in extras}
            # Stop each extra copy's own launcher child too, so no orphan keeps running.
            stop_pids = [p.pid for p in extras] + [p.pid for p in all_wakers if p.ppid in extra_pids]
            findings.append(Finding(
                DUPLICATE_WAKER, instance,
                f"{name} has {len(wakers)} copies running at once.",
                f"Another copy of {name} is already running",
                f"Keep the oldest copy and stop the other {len(extras)}.",
                f"Stop the extra {'copy' if len(extras) == 1 else 'copies'} of {name}?",
                pids=stop_pids, kept=wakers[0].pid))
        legit = waker_pids
        logs = {_norm(str(p)) for p in log_paths(instance, directory)}
        holders = [p for p in procs if p.pid not in legit and logs & {_norm(f) for f in p.open_files}]
        if holders:
            ours = [p for p in holders if p.is_ours]
            theirs = [p for p in holders if not p.is_ours]
            findings.append(Finding(
                LOCKED_LOG, instance,
                f"{name} can't save its notes because another copy has the file open.",
                f"Can't save its notes: another copy has the file open",
                (f"Stop the other copy ({len(ours)}) so {name} can save its notes."
                 if ours else f"Another program has the notes file open. I won't stop it for you."),
                f"Close the other copy holding {name}'s notes?",
                pids=[p.pid for p in ours], unsafe=[p.pid for p in theirs]))
    return findings


def _stop(pid: int) -> None:
    import psutil

    try:
        proc = psutil.Process(pid)
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except psutil.TimeoutExpired:
            proc.kill()
    except psutil.NoSuchProcess:
        pass


def repair(findings: Iterable[Finding], *, confirmed: bool = False,
           stop: Optional[Callable[[int], None]] = None) -> List[str]:
    """Without `confirmed` this is the dry run: it only says what it would do."""
    stop = stop or _stop
    lines = []
    for f in findings:
        if not confirmed:
            lines.append(f"Would: {f.would}")
            continue
        if not f.pids:
            lines.append(f"{friendly_name(f.instance)}: nothing I can safely stop. Close the other program, then try again.")
            continue
        for pid in f.pids:
            stop(pid)
        lines.append(f"Stopped the extra copy. {friendly_name(f.instance)} is working again."
                     if f.kind == DUPLICATE_WAKER else
                     f"Closed the other copy. {friendly_name(f.instance)} can save its notes again.")
    return lines


# ─────────────────────────────────────────────────────────────────────────────
# Add a helper - the one path both `bitcadence helpers add` and the console use
# ─────────────────────────────────────────────────────────────────────────────
def mask(token: str) -> str:
    return f"mco_tok_...{token[-4:]}"


def add_helper(name: str, role: str, *, register: Optional[Callable[[str, str], str]] = None) -> dict:
    """Register the helper and save its credential on this computer. Returns the
    name, role and a masked credential - never the full token."""
    import typer

    name, role = (name or "").strip(), (role or "").strip().lower()
    if not name or not role:
        raise HelperError("A helper needs a name and an AI to power it.")
    if not _NAME_RE.match(name) or not _NAME_RE.match(role):
        raise HelperError("Use letters, numbers, dots, dashes or underscores for the name (up to 64 characters).")
    from mco import cli
    from mco.waker import agent_token_path

    path = agent_token_path(name)  # validates the name before anything changes
    if register is None:
        def register(n, r):  # noqa: E306
            try:
                _refuse_existing(n)
                return cli.register_agent_identity(name=n, role=r)
            except typer.Exit as exc:
                raise HelperError("I couldn't add the helper. Run: bitcadence fix") from exc
    prior = None
    registered = False
    try:
        prior = cli._registry_row(name)
    except Exception:  # noqa: BLE001 - only used to undo a failed add
        prior = None
    try:
        token = register(name, role)
        registered = True
        # Two places can hold the credential. The helper is usable if either
        # does; only when neither does is the registration undone, so nobody is
        # left with a registered helper whose token nobody holds.
        in_store, store_error = False, None
        try:
            cli.get_config().set(f"MCO_SECRET_AGENT_TOKEN_{name.upper()}", token, encrypt=True)
            in_store = True
        except Exception as exc:  # noqa: BLE001
            store_error = exc
        in_file, file_error = False, None
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as token_file:
                os.chmod(path, 0o600)
                token_file.write(token)
            in_file = True
        except Exception as exc:  # noqa: BLE001
            file_error = exc
        if not (in_store or in_file):
            raise RuntimeError(f"Couldn't save the helper's credential: {store_error or file_error}") from (
                store_error or file_error)
    except BaseException:
        if registered:
            try:
                cli._undo_registration(name, prior)
            except Exception:  # noqa: BLE001
                pass
        raise
    if in_store and in_file:
        saved = f"Saved to {path} and the encrypted secret store."
    elif in_store:
        saved = f"Saved to the encrypted secret store. (Couldn't write {path}: {file_error})"
    else:
        saved = f"Saved to {path}. (Couldn't use the encrypted secret store: {store_error})"
    return {"name": friendly_name(name), "id": name, "role": role, "credential": mask(token),
            "saved_to": str(path), "saved_message": saved}


def _refuse_existing(name: str) -> None:
    """Adding must never silently replace an existing helper's sign-in."""
    from mco.orchestrator.routes import get_db_client

    db = get_db_client()
    try:
        rows = db.table("agent_registry").select("instance_id").eq("instance_id", name).execute().data if db else []
    except Exception:  # noqa: BLE001 - registration reports real database problems
        rows = []
    if rows:
        raise HelperError(f"There's already a helper called {friendly_name(name)}. Pick another name.")


def time_ago(seconds: Optional[float]) -> str:
    if seconds is None:
        return "never"
    seconds = int(seconds)
    if seconds < 15:
        return "just now"
    if seconds < 90:
        return f"{seconds} seconds ago"
    if seconds < 5400:
        return f"{round(seconds / 60)} minutes ago"
    return f"{round(seconds / 3600)} hours ago"


__all__ = ["HelperError", "Finding", "Proc", "describe", "friendly_name", "health", "doing", "scan",
           "repair", "add_helper", "mask", "time_ago"]
