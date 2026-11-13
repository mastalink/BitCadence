"""Schedules in plain words: "Every weekday at 2:00 AM".

Spec: design/redesign-v1/07-schedules.html and CLI.md. One module behind the
console's Schedules page (mco.orchestrator.schedules_routes) and the
``bitcadence schedule`` verbs, so both show and write the same thing. The file
people never have to see is ``~/.mco/schedules.yaml``; this module reads it with
the real scheduler parser and appends new entries as text so every comment in a
hand-written file survives.
"""

from __future__ import annotations

import os
import re
import time as _time
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

from mco import friendly, scheduler

FREQUENCIES = ("day", "weekday", "days", "hour")
FREQUENCY_LABELS = {"day": "Every day", "weekday": "Every weekday", "days": "Certain days", "hour": "Every hour"}
DAY_NAMES = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"]
_DAY_KEYS = {name[:3].lower(): i for i, name in enumerate(DAY_NAMES)}  # cron: Sunday is 0
_DAY_KEYS.update({"tues": 2, "weds": 3, "thur": 4, "thurs": 4})
_EXAMPLE = 'every weekday at 2 AM'


class ScheduleWordsError(ValueError):
    """The words could not be turned into a schedule. ``friendly`` says what to do."""

    def __init__(self, message: str, error: Optional[friendly.FriendlyError] = None):
        super().__init__(message)
        self.friendly = error or friendly.FriendlyError(message, "", "bad_schedule")

    def __str__(self) -> str:
        return self.friendly.render()


def _path() -> Path:
    return scheduler.SCHEDULES_CONFIG_PATH


# ── time and zone ───────────────────────────────────────────────────────────
def parse_time(text: str) -> tuple[int, int]:
    """'2 AM', '2:30pm', '14:00' or '02:00' -> (hour, minute) on a 24-hour clock."""
    # Strip in Python instead of wrapping the pattern in \s* (CodeQL: polynomial
    # backtracking on long runs of spaces); a real time is never 16+ characters.
    cleaned = str(text or "").strip().lower()
    m = re.fullmatch(r"(\d{1,2})(?::(\d{2}))?\s*([ap])?\.?m?\.?", cleaned) if len(cleaned) <= 16 else None
    if not m:
        raise ScheduleWordsError(f"I didn't understand the time {text!r}.")
    hour, minute, meridiem = int(m.group(1)), int(m.group(2) or 0), m.group(3)
    if minute > 59:
        raise ScheduleWordsError(f"I didn't understand the time {text!r}.")
    if meridiem:
        if not 1 <= hour <= 12:
            raise ScheduleWordsError(f"I didn't understand the time {text!r}.")
        hour = hour % 12 + (12 if meridiem == "p" else 0)
    elif hour > 23 or (m.group(2) is None and hour > 12):
        raise ScheduleWordsError(f"I didn't understand the time {text!r}.")
    elif m.group(2) is None and hour < 13:
        # "2" alone could be morning or afternoon: ask rather than guess.
        raise ScheduleWordsError(f"Is {text.strip()} in the morning or the afternoon? Say AM or PM.")
    return hour, minute


def clock_words(hour: int, minute: int) -> str:
    return f"{hour % 12 or 12}:{minute:02d} {'AM' if hour < 12 else 'PM'}"


def detect_timezone() -> str:
    """This computer's IANA time zone, so the person never has to pick one."""
    name = os.environ.get("TZ", "").strip()
    if name and "/" in name:
        return name
    try:
        import tzlocal  # type: ignore

        found = str(tzlocal.get_localzone())
        if "/" in found:
            return found
    except Exception:  # noqa: BLE001 - optional helper; fall through to the fixed offset
        pass
    offset = -(_time.altzone if _time.localtime().tm_isdst > 0 else _time.timezone) // 3600
    if offset == 0:
        return "UTC"
    return f"Etc/GMT{'-' if offset > 0 else '+'}{abs(offset)}"


# ── words <-> cron ──────────────────────────────────────────────────────────
def build_cron(frequency: str, hour: int, minute: int, days: Optional[List[int]] = None) -> str:
    if frequency == "day":
        return f"{minute} {hour} * * *"
    if frequency == "weekday":
        return f"{minute} {hour} * * 1-5"
    if frequency == "hour":
        return f"{minute} * * * *"
    if frequency == "days":
        picked = sorted({int(d) % 7 for d in (days or [])})
        if not picked:
            raise ScheduleWordsError("Pick at least one day.")
        return f"{minute} {hour} * * {','.join(str(d) for d in picked)}"
    raise ScheduleWordsError(f"I don't know how often {frequency!r} is.")


def _join_days(days: List[int]) -> str:
    names = [DAY_NAMES[d] for d in days]
    if len(names) == 1:
        return names[0]
    return ", ".join(names[:-1]) + " and " + names[-1]


def describe_cron(raw: str) -> str:
    """'0 2 * * 1-5' -> 'Every weekday at 2:00 AM'. Anything unusual gets a safe sentence."""
    fields = str(raw).split()
    if len(fields) != 5:
        return "On a custom schedule"
    minute, hour, dom, month, dow = fields
    if dom != "*" or month != "*" or not minute.isdigit():
        return "On a custom schedule"
    if hour == "*":
        if dow == "*":
            return "Every hour" if int(minute) == 0 else f"Every hour, {int(minute)} minutes past"
        return "On a custom schedule"
    if not hour.isdigit():
        return "On a custom schedule"
    at = clock_words(int(hour), int(minute))
    if dow == "*":
        return f"Every day at {at}"
    if dow in ("1-5", "1,2,3,4,5"):
        return f"Every weekday at {at}"
    if re.fullmatch(r"[0-7](,[0-7])*", dow):
        days = sorted({int(d) % 7 for d in dow.split(",")})
        # Monday first reads more naturally for a week.
        days.sort(key=lambda d: (d - 1) % 7)
        return f"Every {_join_days(days)} at {at}"
    return "On a custom schedule"


def describe_every(seconds: float) -> str:
    seconds = int(seconds or 0)
    for unit, size in (("week", 604800), ("day", 86400), ("hour", 3600), ("minute", 60)):
        if seconds >= size and seconds % size == 0:
            n = seconds // size
            return f"Every {unit}" if n == 1 else f"Every {n} {unit}s"
    return "Every few minutes"


def describe(schedule: scheduler.Schedule) -> str:
    return describe_cron(schedule.cron.raw) if schedule.cron else describe_every(schedule.every or 0)


def parse_phrase(text: str) -> dict:
    """'every weekday at 2 AM' -> {frequency, hour, minute, days}. Raises ScheduleWordsError."""
    bad = ScheduleWordsError(f"I didn't understand {text!r}.", friendly.bad_schedule(text))
    lowered = re.sub(r"\s+", " ", str(text or "").strip().lower())
    if not lowered:
        raise bad
    lowered = lowered.replace("daily", "every day").replace("weekdays", "every weekday")
    m = re.fullmatch(r"(?:every|each) (.+?)(?: at (.+))?", lowered)
    if not m:
        raise bad
    what, at = m.group(1), m.group(2)
    try:
        if what == "hour":
            hour, minute = 0, (parse_time(at)[1] if at else 0)
            return {"frequency": "hour", "hour": 0, "minute": minute, "days": []}
        if not at:
            raise bad
        hour, minute = parse_time(at)
    except ScheduleWordsError as exc:
        if exc is bad:
            raise
        raise ScheduleWordsError(str(exc.friendly.what), friendly.FriendlyError(
            exc.friendly.what, f'Try "{_EXAMPLE}".', "bad_schedule")) from exc
    if what == "day":
        return {"frequency": "day", "hour": hour, "minute": minute, "days": []}
    if what == "weekday":
        return {"frequency": "weekday", "hour": hour, "minute": minute, "days": []}
    picked = []
    for word in re.split(r"\s*(?:,|and|&)\s*", what):
        key = word.strip().rstrip("s") if word.strip() not in _DAY_KEYS else word.strip()
        key = key[:4] if key[:4] in _DAY_KEYS else key[:3]
        if key not in _DAY_KEYS:
            raise bad
        picked.append(_DAY_KEYS[key])
    return {"frequency": "days", "hour": hour, "minute": minute, "days": sorted(set(picked))}


def plan(frequency: str, time_text: str = "", days: Optional[List[int]] = None) -> dict:
    """What the person picked, as the sentence they will see and the cron it becomes."""
    if frequency == "hour":
        hour, minute = 0, (parse_time(time_text)[1] if time_text else 0)
    else:
        hour, minute = parse_time(time_text or "02:00")
    cron = build_cron(frequency, hour, minute, days)
    return {"cron": cron, "words": describe_cron(cron)}


def preview_schedule(cron: str, timezone_name: Optional[str] = None, *, now: Optional[datetime] = None) -> dict:
    """Validate the selected zone and preview with the existing scheduler; no writes."""
    zone = timezone_name if timezone_name is not None else detect_timezone()
    # Restrict this value before it can be interpolated into a YAML entry.
    if not isinstance(zone, str) or not re.fullmatch(r"[A-Za-z0-9_+\-]+(?:/[A-Za-z0-9_+\-]+)*", zone) or len(zone) > 128:
        raise ScheduleWordsError("Choose a valid IANA time zone, such as America/New_York or UTC.")
    try:
        when = scheduler.next_cron_time(scheduler.parse_cron(cron), now or datetime.now(timezone.utc), zone)
    except scheduler.ScheduleConfigError as exc:
        raise ScheduleWordsError(f"I couldn't use time zone {zone!r}. Choose an IANA time zone such as America/New_York or UTC.") from exc
    return {"timezone": zone, "next_run": when.isoformat(),
            "next_words": when.strftime("%Y-%m-%d %H:%M %Z (UTC%z)")}


# ── reading the file ────────────────────────────────────────────────────────
def next_run_words(schedule: scheduler.Schedule, state=None, now: Optional[datetime] = None) -> str:
    now = now or datetime.now(timezone.utc)
    if not schedule.enabled:
        return "Off"
    when = scheduler.next_run_at(schedule, state, now)
    if when is None:
        return "Finished"
    try:
        local = when.astimezone(scheduler._to_zone(now, schedule.timezone).tzinfo)
    except Exception:  # noqa: BLE001 - a wording nicety must never break the page
        local = when
    today = scheduler._to_zone(now, schedule.timezone).date()
    day = {0: "today", 1: "tomorrow"}.get((local.date() - today).days)
    at = clock_words(local.hour, local.minute)
    if day:
        return f"{day} at {at}"
    return f"{local.strftime('%A')} at {at}"


def _label(launcher: scheduler.Launcher, name: str) -> str:
    return (launcher.title or name.replace("-", " ").replace("_", " ")).strip()


def launchers_available() -> list[dict]:
    """What can be scheduled: the launchers already in the file, by their plain titles."""
    try:
        launchers, _ = scheduler.load_config(_path())
    except (scheduler.ScheduleConfigMissing, scheduler.ScheduleConfigError):
        return []
    return [{"id": name, "label": _label(l, name)} for name, l in sorted(launchers.items()) if not l.is_local]


def list_schedules(now: Optional[datetime] = None) -> dict:
    """Everything the Schedules page and `bitcadence schedule` show. No YAML, no cron."""
    from mco import launcher as launcher_mod

    out = {"schedules": [], "can_schedule": launchers_available(), "timezone": detect_timezone(), "problem": ""}
    try:
        launchers, schedules = scheduler.load_config(_path())
    except scheduler.ScheduleConfigMissing:
        return out
    except scheduler.ScheduleConfigError as exc:
        out["problem"] = friendly.translate(exc).render()
        return out
    states = launcher_mod.load_state()
    for name in sorted(schedules):
        s = schedules[name]
        launcher = launchers[s.launcher]
        out["schedules"].append({
            "id": name,
            "name": _label(launcher, s.launcher),
            "when": describe(s),
            "next": next_run_words(s, states.get(name), now),
            "on": s.enabled,
        })
    return out


# ── writing the file ────────────────────────────────────────────────────────
def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-") or "schedule"


def add_schedule(launcher: str, cron: str, *, timezone_name: Optional[str] = None) -> dict:
    """Append one schedule for ``launcher``. Existing text is never rewritten."""
    path = _path()
    try:
        launchers, schedules = scheduler.load_config(path)
    except scheduler.ScheduleConfigMissing:
        raise ScheduleWordsError("There is nothing to schedule yet. Ask for something first, then schedule it.",
                                 friendly.FriendlyError("There is nothing to schedule yet.",
                                                        'Try: bitcadence ask "..."', "nothing_to_schedule"))
    except scheduler.ScheduleConfigError as exc:
        raise ScheduleWordsError("Your schedules file has something I can't read.") from exc
    if launcher not in launchers or launchers[launcher].is_local:
        known = ", ".join(sorted(n for n, l in launchers.items() if not l.is_local)) or "nothing yet"
        raise ScheduleWordsError(f"I don't know {launcher!r}. I can schedule: {known}.")
    scheduler.parse_cron(cron)  # last guard before anything touches the file
    preview = preview_schedule(cron, timezone_name)
    zone = preview["timezone"]
    base = _slug(launcher)
    name, n = base, 2
    while name in schedules:
        name, n = f"{base}-{n}", n + 1
    entry = (f"  {name}:\n    launcher: {launcher}\n    cron: \"{cron}\"\n    timezone: {zone}\n")

    text = path.read_text(encoding="utf-8")
    lines = text.splitlines(keepends=True)
    at = next((i for i, l in enumerate(lines) if re.fullmatch(r"schedules:\s*(#.*)?", l.rstrip("\r\n"))), None)
    if at is not None:
        lines.insert(at + 1, entry)
        new = "".join(lines)
    elif re.search(r"^schedules:", text, re.M):
        raise ScheduleWordsError("Your schedules file is laid out in a way I won't change on my own. Edit it by hand.")
    else:
        new = text.rstrip("\n") + "\n\nschedules:\n" + entry
    scheduler.parse_config(__import__("yaml").safe_load(new) or {})  # never save a file we could not read back
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(new, encoding="utf-8")
    os.replace(tmp, path)
    return {"id": name, "words": describe_cron(cron), **preview}


def set_enabled(name: str, on: bool) -> None:
    """Turn one schedule on or off. Only the ``enabled:`` line changes; comments survive."""
    path = _path()
    try:
        _, schedules = scheduler.load_config(path)
    except (scheduler.ScheduleConfigMissing, scheduler.ScheduleConfigError) as exc:
        raise ScheduleWordsError("I can't read your schedules right now.") from exc
    if name not in schedules:
        raise ScheduleWordsError(f"I couldn't find a schedule called {name!r}.")
    lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    section, entry_indent, entry_line = None, 0, None
    for index, line in enumerate(lines):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        indent = len(line) - len(line.lstrip())
        if indent == 0:
            section = stripped.rstrip(":") if stripped.endswith(":") else None
            continue
        if section in ("schedules", "loops") and stripped.endswith(":") and stripped.rstrip(":") == name:
            entry_indent, entry_line = indent, index
            break
    if entry_line is None:
        raise ScheduleWordsError("Your schedules file is laid out in a way I won't change on my own. Edit it by hand.")
    value = "true" if on else "false"
    field_indent, cursor = entry_indent + 2, entry_line + 1
    while cursor < len(lines):
        line = lines[cursor]
        if line.strip() and (len(line) - len(line.lstrip())) <= entry_indent:
            break
        if line.strip().startswith("enabled:"):
            lines[cursor] = f"{' ' * (len(line) - len(line.lstrip()))}enabled: {value}\n"
            break
        if line.strip():
            field_indent = len(line) - len(line.lstrip())
        cursor += 1
    if cursor >= len(lines) or not lines[cursor].strip().startswith("enabled:"):
        lines.insert(entry_line + 1, f"{' ' * field_indent}enabled: {value}\n")
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text("".join(lines), encoding="utf-8")
    os.replace(tmp, path)


def ensure_file() -> bool:
    """True when a schedules file exists to add to."""
    return _path().is_file()

