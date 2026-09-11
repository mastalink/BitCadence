"""
Governed scheduling for the Job Board: launchers, schedules, and loops.

Three concepts, deliberately layered:

- **Launcher** - a named, reusable launch target. Either a single job (role +
  instructions) or a workflow file. This is the thing you can invoke by hand
  (`mco launch nightly-audit`) *and* the thing a schedule points at, so a
  scheduled run and a manual run are provably the same operation.
- **Schedule** - binds a launcher to a time trigger (cron or interval). Fires
  indefinitely until disabled.
- **Loop** - a schedule that knows how to stop. Loops MUST declare a bound
  (`max_iterations` and/or `until`); an unbounded self-repeating agent task is
  how fleets burn budget and drift, so the parser refuses to build one.

Why this lives in BitCadence instead of cron/Task Scheduler: every launch goes
through the job board, which means a scheduled run inherits the same governance
as a hand-submitted one - approval gates, retry budgets, role/instance isolation,
and an audit trail that records *which schedule* created the job. `cron` can
start a process; it cannot tell you six weeks later who authorized the thing that
ran at 3am, or stop after the tenth iteration because a human said so.

Config lives in `~/.mco/schedules.yaml` (the declarative sibling of
`~/.mco/fleet.toml`, which governs which *workers* run - this governs what *work*
gets created).

Example:

    launchers:
      nightly-audit:
        role: reviewer
        title: Nightly dependency audit
        instructions: Audit dependencies for new CVEs and open a PR if any are found.
        requires_approval: false
      release:
        workflow: workflows/release-pipeline.yaml

    schedules:
      nightly-audit:
        launcher: nightly-audit
        cron: "0 3 * * *"          # 03:00 daily
        timezone: America/New_York
      hourly-health:
        launcher: nightly-audit
        every: 1h
        overlap: skip               # skip | allow  (default: skip)

    loops:
      triage-backlog:
        launcher: nightly-audit
        every: 30m
        max_iterations: 10          # a bound is REQUIRED
        until: "2026-12-31T00:00:00Z"
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Optional

import yaml

logger = logging.getLogger("mco.scheduler")

SCHEDULES_CONFIG_PATH = Path.home() / ".mco" / "schedules.yaml"

# Overlap policies: what to do when the previous run of this schedule is still
# in flight. Default `skip` because the common failure mode for agent work is a
# pile-up of duplicate jobs racing each other on the same repo.
OVERLAP_POLICIES = {"skip", "allow"}

# How far forward we're willing to search for the next cron match. Five years,
# because "0 0 29 2 *" (Feb 29 only) can legitimately be up to ~4 years out and
# must still resolve. The day-skipping in next_cron_time keeps this cheap.
_MAX_SEARCH_MINUTES = 5 * 366 * 24 * 60

_CRON_SHORTCUTS = {
    "@yearly": "0 0 1 1 *",
    "@annually": "0 0 1 1 *",
    "@monthly": "0 0 1 * *",
    "@weekly": "0 0 * * 0",
    "@daily": "0 0 * * *",
    "@midnight": "0 0 * * *",
    "@hourly": "0 * * * *",
}

_DURATION_RE = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*([smhdw])\s*$", re.IGNORECASE)
_DURATION_UNITS = {"s": 1, "m": 60, "h": 3600, "d": 86400, "w": 604800}


class ScheduleConfigMissing(FileNotFoundError):
    """Raised when no schedules.yaml exists yet."""


class ScheduleConfigError(ValueError):
    """Raised when a schedules.yaml is present but invalid."""


# ── duration + cron parsing ───────────────────────────────────────────────────

def parse_duration(value: Any, where: str = "every") -> float:
    """Parse '30s' / '15m' / '2h' / '1d' / '1w' (or a bare number of seconds).

    Returns seconds as a float. Raises ScheduleConfigError on anything else -
    silently coercing a typo'd interval into 'every 0 seconds' would turn a
    schedule into a spin loop against a metered model.
    """
    if isinstance(value, bool):
        raise ScheduleConfigError(f"{where} must be a duration like '15m', not a boolean")
    if isinstance(value, (int, float)):
        seconds = float(value)
    else:
        text = str(value).strip()
        match = _DURATION_RE.match(text)
        if match:
            seconds = float(match.group(1)) * _DURATION_UNITS[match.group(2).lower()]
        else:
            # A bare number is seconds - YAML `every: 90` already arrives as an
            # int, so the quoted "90" form must mean the same thing.
            try:
                seconds = float(text)
            except ValueError:
                raise ScheduleConfigError(
                    f"{where}: '{value}' is not a duration - use 30s, 15m, 2h, 1d, or 1w"
                ) from None
    if seconds <= 0:
        raise ScheduleConfigError(f"{where} must be greater than zero (got {value})")
    return seconds


def _parse_cron_field(spec: str, low: int, high: int, where: str) -> set[int]:
    """Expand one cron field ('*', '5', '1-10', '*/15', '1-10/2', 'a,b') to a set."""
    allowed: set[int] = set()
    for part in str(spec).split(","):
        part = part.strip()
        if not part:
            raise ScheduleConfigError(f"{where}: empty value in '{spec}'")
        step = 1
        if "/" in part:
            part, _, step_text = part.partition("/")
            if not step_text.isdigit() or int(step_text) < 1:
                raise ScheduleConfigError(f"{where}: step must be a positive integer in '{spec}'")
            step = int(step_text)
            part = part or "*"
        if part == "*":
            start, end = low, high
        elif "-" in part.lstrip("-"):
            start_text, _, end_text = part.partition("-")
            start, end = _cron_int(start_text, where, spec), _cron_int(end_text, where, spec)
        else:
            start = end = _cron_int(part, where, spec)
        if start < low or end > high or start > end:
            raise ScheduleConfigError(
                f"{where}: '{part}' is out of range {low}-{high} in '{spec}'"
            )
        allowed.update(range(start, end + 1, step))
    if not allowed:
        raise ScheduleConfigError(f"{where}: '{spec}' matches nothing")
    return allowed


def _cron_int(text: str, where: str, spec: str) -> int:
    text = text.strip()
    if not text.isdigit():
        raise ScheduleConfigError(f"{where}: '{text}' is not a number in '{spec}'")
    return int(text)


@dataclass(frozen=True)
class CronExpr:
    """A parsed 5-field cron expression: minute hour day-of-month month day-of-week."""

    minutes: frozenset[int]
    hours: frozenset[int]
    days: frozenset[int]
    months: frozenset[int]
    weekdays: frozenset[int]
    raw: str
    # True when day-of-month and day-of-week are both restricted. Standard cron
    # semantics: in that case the two are OR'd, not AND'd (a quirk, but the one
    # every operator's muscle memory expects).
    day_union: bool = False

    def matches(self, moment: datetime) -> bool:
        if moment.minute not in self.minutes or moment.hour not in self.hours:
            return False
        if moment.month not in self.months:
            return False
        # Python: Monday=0..Sunday=6. Cron: Sunday=0..Saturday=6.
        weekday = (moment.weekday() + 1) % 7
        dom_ok = moment.day in self.days
        dow_ok = weekday in self.weekdays
        return (dom_ok or dow_ok) if self.day_union else (dom_ok and dow_ok)


def parse_cron(expression: str, where: str = "cron") -> CronExpr:
    """Parse a 5-field cron expression or an @shortcut (@daily, @hourly, ...)."""
    text = str(expression).strip()
    if not text:
        raise ScheduleConfigError(f"{where}: cron expression is empty")
    lowered = text.lower()
    if lowered in _CRON_SHORTCUTS:
        text = _CRON_SHORTCUTS[lowered]
    elif lowered.startswith("@"):
        raise ScheduleConfigError(
            f"{where}: unknown shortcut '{text}' - "
            f"use one of {', '.join(sorted(_CRON_SHORTCUTS))} or a 5-field expression"
        )

    fields = text.split()
    if len(fields) != 5:
        raise ScheduleConfigError(
            f"{where}: expected 5 fields (minute hour day month weekday), got {len(fields)}: '{text}'"
        )
    minute, hour, dom, month, dow = fields
    return CronExpr(
        minutes=frozenset(_parse_cron_field(minute, 0, 59, f"{where} minute")),
        hours=frozenset(_parse_cron_field(hour, 0, 23, f"{where} hour")),
        days=frozenset(_parse_cron_field(dom, 1, 31, f"{where} day-of-month")),
        months=frozenset(_parse_cron_field(month, 1, 12, f"{where} month")),
        # Cron allows 7 for Sunday; normalize it to 0 so matches() stays simple.
        weekdays=frozenset(
            0 if d == 7 else d for d in _parse_cron_field(dow, 0, 7, f"{where} weekday")
        ),
        raw=text,
        day_union=(dom.strip() != "*" and dow.strip() != "*"),
    )


def next_cron_time(expr: CronExpr, after: datetime, tz: Optional[str] = None) -> datetime:
    """First moment strictly after `after` that matches `expr`.

    Searching minute-by-minute is the boring, provably-correct approach; we skip
    whole days when the date fields can't match, which keeps even a
    "3am on Feb 29" expression well under a millisecond.
    """
    moment = _to_zone(after, tz).replace(second=0, microsecond=0) + timedelta(minutes=1)
    limit = moment + timedelta(minutes=_MAX_SEARCH_MINUTES)
    while moment < limit:
        if not _date_could_match(expr, moment):
            # Jump to midnight of the next day rather than crawling 1440 minutes.
            moment = (moment + timedelta(days=1)).replace(hour=0, minute=0)
            continue
        if expr.matches(moment):
            return moment
        moment += timedelta(minutes=1)
    raise ScheduleConfigError(
        f"cron '{expr.raw}' has no matching time within a year - is the date valid?"
    )


def _date_could_match(expr: CronExpr, moment: datetime) -> bool:
    if moment.month not in expr.months:
        return False
    weekday = (moment.weekday() + 1) % 7
    dom_ok = moment.day in expr.days
    dow_ok = weekday in expr.weekdays
    return (dom_ok or dow_ok) if expr.day_union else (dom_ok and dow_ok)


def _to_zone(moment: datetime, tz: Optional[str]) -> datetime:
    """Normalize to an aware datetime in `tz` (UTC when unset)."""
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    if not tz:
        return moment.astimezone(timezone.utc)
    try:
        from zoneinfo import ZoneInfo
    except ImportError as exc:  # pragma: no cover - stdlib since 3.9
        raise ScheduleConfigError(f"timezone support unavailable: {exc}") from exc
    try:
        return moment.astimezone(ZoneInfo(tz))
    except KeyError as exc:
        # Distinguish "you typo'd the zone" from "this machine has no tz database"
        # - on Windows the latter is the usual cause and needs a different fix.
        try:
            import tzdata  # noqa: F401
            hint = "check the spelling against the IANA list (e.g. America/New_York)"
        except ImportError:
            hint = "this machine has no IANA timezone database - install the 'tzdata' package"
        raise ScheduleConfigError(f"unknown timezone '{tz}': {hint}") from exc
    except Exception as exc:
        raise ScheduleConfigError(f"timezone '{tz}' could not be resolved: {exc}") from exc


def parse_timestamp(value: Any, where: str) -> datetime:
    """Parse an ISO-8601 timestamp; naive values are treated as UTC."""
    if isinstance(value, datetime):
        moment = value
    else:
        text = str(value).strip().replace("Z", "+00:00")
        try:
            moment = datetime.fromisoformat(text)
        except ValueError as exc:
            raise ScheduleConfigError(
                f"{where}: '{value}' is not an ISO-8601 timestamp (e.g. 2026-12-31T00:00:00Z)"
            ) from exc
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


# ── models ────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Launcher:
    """A named, reusable launch target.

    Four kinds, exactly one per launcher:
      - `role`     -> one job on the governed board
      - `workflow` -> a whole DAG of jobs
      - `app`      -> a local program or GUI (detached from the scheduler)
      - `url`      -> a page in the default browser

    `app` and `url` are local convenience actions: they start something on this
    machine and produce **no board job and no audit entry**. Use them to open
    the console or a desktop agent app, not to do governed work.
    """

    name: str
    role: Optional[str] = None
    title: Optional[str] = None
    instructions: Optional[str] = None
    instance: Optional[str] = None
    workflow: Optional[str] = None
    requires_approval: bool = False
    max_retries: int = 0
    escalate_to_role: Optional[str] = None
    app: Optional[str] = None
    args: tuple[str, ...] = ()
    url: Optional[str] = None

    @property
    def is_workflow(self) -> bool:
        return bool(self.workflow)

    @property
    def is_local(self) -> bool:
        """True for `app`/`url` launchers - local actions, not board work."""
        return bool(self.app or self.url)

    def describe(self) -> str:
        if self.is_workflow:
            return f"workflow {self.workflow}"
        if self.url:
            return f"open {self.url}"
        if self.app:
            extra = f" {' '.join(self.args)}" if self.args else ""
            return f"run {self.app}{extra}"
        target = self.role or "?"
        if self.instance:
            target = f"{target}/{self.instance}"
        return f"job -> {target}"


@dataclass(frozen=True)
class Schedule:
    """A launcher bound to a time trigger.

    Exactly one of `cron` / `every` is set. `kind` is "schedule" for an
    open-ended repeater and "loop" for a bounded one - loops are the same
    machinery with a mandatory stop condition, not a separate subsystem.
    """

    name: str
    launcher: str
    cron: Optional[CronExpr] = None
    every: Optional[float] = None
    timezone: Optional[str] = None
    enabled: bool = True
    overlap: str = "skip"
    kind: str = "schedule"
    max_iterations: Optional[int] = None
    until: Optional[datetime] = None
    # Stop when this role's queue is clear - the "work the backlog until it's
    # done" loop. Always paired with max_iterations as a hard cap, because a
    # condition that never comes true is an unbounded loop wearing a disguise.
    until_empty: Optional[str] = None
    # Governance override: force an approval gate on everything this fires,
    # even when the launcher itself doesn't require one.
    requires_approval: Optional[bool] = None

    @property
    def is_loop(self) -> bool:
        return self.kind == "loop"

    def describe_trigger(self) -> str:
        if self.cron:
            base = f"cron {self.cron.raw}"
            return f"{base} ({self.timezone})" if self.timezone else base
        return f"every {format_duration(self.every or 0)}"

    def describe_bound(self) -> str:
        if not self.is_loop:
            return "-"
        bits = []
        if self.max_iterations is not None:
            bits.append(f"max {self.max_iterations}")
        if self.until is not None:
            bits.append(f"until {self.until.isoformat()}")
        if self.until_empty:
            bits.append(f"until {self.until_empty} queue empty")
        return ", ".join(bits) or "-"


@dataclass
class ScheduleState:
    """Mutable runtime state for one schedule, persisted between ticks."""

    name: str
    iterations: int = 0
    last_run_at: Optional[datetime] = None
    last_job_ids: list[str] = field(default_factory=list)
    exhausted_reason: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "iterations": self.iterations,
            "last_run_at": self.last_run_at.isoformat() if self.last_run_at else None,
            "last_job_ids": list(self.last_job_ids),
            "exhausted_reason": self.exhausted_reason,
        }

    @classmethod
    def from_dict(cls, raw: dict) -> "ScheduleState":
        last = raw.get("last_run_at")
        return cls(
            name=str(raw.get("name") or ""),
            iterations=int(raw.get("iterations") or 0),
            last_run_at=parse_timestamp(last, "last_run_at") if last else None,
            last_job_ids=list(raw.get("last_job_ids") or []),
            exhausted_reason=raw.get("exhausted_reason"),
        )


def format_duration(seconds: float) -> str:
    """Human-readable duration ('90m' -> '1h 30m'). Used in CLI tables."""
    seconds = int(seconds)
    if seconds < 60:
        return f"{seconds}s"
    parts = []
    for unit, size in (("w", 604800), ("d", 86400), ("h", 3600), ("m", 60), ("s", 1)):
        if seconds >= size:
            count, seconds = divmod(seconds, size)
            parts.append(f"{count}{unit}")
    return " ".join(parts[:2])


# ── config loading ────────────────────────────────────────────────────────────

_LAUNCHER_FIELDS = {
    "role", "title", "instructions", "instance", "workflow",
    "requires_approval", "max_retries", "escalate_to_role",
    "app", "args", "url",
}
# Exactly one of these decides what a launcher *is*.
_LAUNCHER_TARGETS = ("role", "workflow", "app", "url")
_SCHEDULE_FIELDS = {
    "launcher", "cron", "every", "timezone", "enabled", "overlap",
    "max_iterations", "until", "until_empty", "requires_approval",
}


def sample_config() -> str:
    """A commented starter file, written by `mco schedule init`."""
    return """# BitCadence schedules - what work gets created, and when.
# (Its sibling ~/.mco/fleet.toml governs which workers run.)
#
# Every launch below goes through the job board, so it inherits approval gates,
# retry budgets, and the audit trail. Run `mco schedule list` to see next fire
# times, and `mco launch <name>` to fire one by hand.

launchers:
  nightly-audit:
    role: reviewer
    title: Nightly dependency audit
    instructions: |
      Audit project dependencies for newly published CVEs.
      Open a PR if any are found; report "clean" otherwise.

  # `url` and `app` launchers open things on THIS machine. They are local
  # conveniences - they create no board job and no audit entry.
  # console:
  #   url: http://127.0.0.1:18789/console
  # claude-desktop:
  #   app: "C:/Program Files/Claude/Claude.exe"

schedules:
  nightly-audit:
    launcher: nightly-audit
    cron: "0 3 * * *"
    timezone: America/New_York

# Loops are schedules that stop. A bound is required.
# loops:
#   triage-backlog:
#     launcher: nightly-audit
#     every: 30m
#     max_iterations: 10
"""


def load_config(path: Path = SCHEDULES_CONFIG_PATH) -> tuple[dict[str, Launcher], dict[str, Schedule]]:
    """Read and validate schedules.yaml. Raises ScheduleConfigMissing if absent."""
    if not path.is_file():
        raise ScheduleConfigMissing(str(path))
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise ScheduleConfigError(f"{path} is not valid YAML: {exc}") from exc
    if not isinstance(data, dict):
        raise ScheduleConfigError(f"{path} must be a YAML mapping")
    return parse_config(data)


def parse_config(data: dict[str, Any]) -> tuple[dict[str, Launcher], dict[str, Schedule]]:
    """Validate a parsed schedules document into launchers + schedules."""
    launchers = _parse_launchers(data.get("launchers") or {})
    schedules: dict[str, Schedule] = {}

    for name, raw in (data.get("schedules") or {}).items():
        schedules[str(name)] = _parse_schedule(str(name), raw, kind="schedule")
    for name, raw in (data.get("loops") or {}).items():
        name = str(name)
        if name in schedules:
            raise ScheduleConfigError(
                f"'{name}' is defined as both a schedule and a loop - names must be unique"
            )
        schedules[name] = _parse_schedule(name, raw, kind="loop")

    for schedule in schedules.values():
        if schedule.launcher not in launchers:
            raise ScheduleConfigError(
                f"{schedule.kind} '{schedule.name}' references unknown launcher "
                f"'{schedule.launcher}' (defined: {', '.join(sorted(launchers)) or 'none'})"
            )
    return launchers, schedules


def _parse_launchers(raw_all: Any) -> dict[str, Launcher]:
    if not isinstance(raw_all, dict):
        raise ScheduleConfigError("'launchers' must be a mapping of name -> definition")
    launchers: dict[str, Launcher] = {}
    for name, raw in raw_all.items():
        name = str(name)
        if not isinstance(raw, dict):
            raise ScheduleConfigError(f"launchers.{name} must be a mapping")
        unknown = set(raw) - _LAUNCHER_FIELDS
        if unknown:
            raise ScheduleConfigError(
                f"launchers.{name}: unknown field(s) {', '.join(sorted(unknown))}"
            )
        present = [field for field in _LAUNCHER_TARGETS if raw.get(field)]
        if len(present) > 1:
            raise ScheduleConfigError(
                f"launchers.{name}: set exactly one of "
                f"{', '.join(_LAUNCHER_TARGETS)} - got {', '.join(present)}"
            )
        if not present:
            raise ScheduleConfigError(
                f"launchers.{name}: needs one of 'role' (a job), 'workflow' "
                "(multi-step), 'app' (a local program), or 'url' (a page)"
            )
        kind = present[0]
        if kind == "role" and not (raw.get("title") or raw.get("instructions")):
            raise ScheduleConfigError(
                f"launchers.{name}: a job launcher needs a 'title' or 'instructions'"
            )

        args = raw.get("args") or []
        if args and not raw.get("app"):
            raise ScheduleConfigError(f"launchers.{name}: 'args' only applies to an 'app' launcher")
        if not isinstance(args, (list, tuple)):
            raise ScheduleConfigError(
                f"launchers.{name}: 'args' must be a list, e.g. [--flag, value]"
            )

        url = _opt_str(raw.get("url"))
        if url and not re.match(r"^(https?|file)://", url, re.IGNORECASE):
            # Anything else (javascript:, data:, custom handlers) is a foot-gun
            # to hand a browser from a config file that a scheduler executes.
            raise ScheduleConfigError(
                f"launchers.{name}: 'url' must start with http://, https://, or file://"
            )

        launchers[name] = Launcher(
            name=name,
            role=_opt_str(raw.get("role")),
            title=_opt_str(raw.get("title")),
            instructions=_opt_str(raw.get("instructions")),
            instance=_opt_str(raw.get("instance")),
            workflow=_opt_str(raw.get("workflow")),
            requires_approval=bool(raw.get("requires_approval", False)),
            max_retries=int(raw.get("max_retries") or 0),
            escalate_to_role=_opt_str(raw.get("escalate_to_role")),
            app=_opt_str(raw.get("app")),
            args=tuple(str(a) for a in args),
            url=url,
        )
    return launchers


def _parse_schedule(name: str, raw: Any, kind: str) -> Schedule:
    label = f"{kind}s.{name}"
    if not isinstance(raw, dict):
        raise ScheduleConfigError(f"{label} must be a mapping")
    unknown = set(raw) - _SCHEDULE_FIELDS
    if unknown:
        raise ScheduleConfigError(f"{label}: unknown field(s) {', '.join(sorted(unknown))}")

    launcher = raw.get("launcher")
    if not launcher:
        raise ScheduleConfigError(f"{label}: 'launcher' is required")

    has_cron, has_every = "cron" in raw, "every" in raw
    if has_cron and has_every:
        raise ScheduleConfigError(f"{label}: set either 'cron' or 'every', not both")
    if not has_cron and not has_every:
        raise ScheduleConfigError(f"{label}: needs a 'cron' expression or an 'every' interval")

    overlap = str(raw.get("overlap", "skip")).lower()
    if overlap not in OVERLAP_POLICIES:
        raise ScheduleConfigError(
            f"{label}: overlap must be one of {', '.join(sorted(OVERLAP_POLICIES))}"
        )

    max_iterations = raw.get("max_iterations")
    if max_iterations is not None:
        max_iterations = int(max_iterations)
        if max_iterations < 1:
            raise ScheduleConfigError(f"{label}: max_iterations must be at least 1")
    until = parse_timestamp(raw["until"], f"{label}.until") if raw.get("until") else None
    until_empty = _opt_str(raw.get("until_empty"))

    if until_empty and kind != "loop":
        raise ScheduleConfigError(
            f"{label}: 'until_empty' is a stop condition, so it only applies to loops"
        )
    # A condition that never comes true is an unbounded loop wearing a disguise,
    # so until_empty always needs a hard iteration cap behind it.
    if until_empty and max_iterations is None:
        raise ScheduleConfigError(
            f"{label}: 'until_empty' also needs 'max_iterations' as a hard cap - "
            "a queue that never drains would otherwise loop forever."
        )

    # The whole point of the loop/schedule distinction: a loop must be able to end.
    if kind == "loop" and max_iterations is None and until is None:
        raise ScheduleConfigError(
            f"{label}: a loop must declare 'max_iterations' and/or 'until'. "
            "An unbounded loop is just a schedule - define it under 'schedules:' if that's what you meant."
        )

    requires_approval = raw.get("requires_approval")
    return Schedule(
        name=name,
        launcher=str(launcher),
        cron=parse_cron(raw["cron"], f"{label}.cron") if has_cron else None,
        every=parse_duration(raw["every"], f"{label}.every") if has_every else None,
        timezone=_opt_str(raw.get("timezone")),
        enabled=bool(raw.get("enabled", True)),
        overlap=overlap,
        kind=kind,
        max_iterations=max_iterations,
        until=until,
        until_empty=until_empty,
        requires_approval=None if requires_approval is None else bool(requires_approval),
    )


def _opt_str(value: Any) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


# ── due calculation ───────────────────────────────────────────────────────────

def next_run_at(
    schedule: Schedule,
    state: Optional[ScheduleState] = None,
    now: Optional[datetime] = None,
) -> Optional[datetime]:
    """When this schedule should next fire, or None if it never will again.

    None means genuinely finished - disabled, past its `until`, or out of
    iterations - which is what lets `mco schedule list` show a loop winding
    down instead of pretending it's still live.
    """
    now = now or datetime.now(timezone.utc)
    if not schedule.enabled:
        return None
    if exhaustion_reason(schedule, state, now):
        return None

    if schedule.cron:
        anchor = (state.last_run_at if state and state.last_run_at else now)
        candidate = next_cron_time(schedule.cron, max(anchor, now - timedelta(minutes=1)), schedule.timezone)
    else:
        interval = timedelta(seconds=schedule.every or 0)
        # No history: an interval schedule fires immediately on first tick, which
        # is what "every 30m" means to an operator who just enabled it.
        if not state or not state.last_run_at:
            candidate = now
        else:
            candidate = state.last_run_at + interval

    if schedule.until and candidate > schedule.until:
        return None
    return candidate


def exhaustion_reason(
    schedule: Schedule,
    state: Optional[ScheduleState],
    now: Optional[datetime] = None,
) -> Optional[str]:
    """Why this schedule is finished, or None if it's still live."""
    now = now or datetime.now(timezone.utc)
    # A recorded completion is authoritative and sticky: a loop that ended
    # because its queue drained must not resurrect when the queue refills.
    # Clear it with `mco schedule reset <name>`.
    if state and state.exhausted_reason:
        return state.exhausted_reason
    if schedule.until and now > schedule.until:
        return f"passed its until ({schedule.until.isoformat()})"
    if schedule.max_iterations is not None and state and state.iterations >= schedule.max_iterations:
        return f"completed all {schedule.max_iterations} iterations"
    return None


def is_due(
    schedule: Schedule,
    state: Optional[ScheduleState] = None,
    now: Optional[datetime] = None,
) -> bool:
    """True when this schedule should fire at `now`."""
    now = now or datetime.now(timezone.utc)
    due = next_run_at(schedule, state, now)
    return due is not None and due <= now


def due_schedules(
    schedules: Iterable[Schedule],
    states: dict[str, ScheduleState],
    now: Optional[datetime] = None,
) -> list[Schedule]:
    """Every schedule due to fire at `now`, in stable name order."""
    now = now or datetime.now(timezone.utc)
    return sorted(
        (s for s in schedules if is_due(s, states.get(s.name), now)),
        key=lambda s: s.name,
    )
