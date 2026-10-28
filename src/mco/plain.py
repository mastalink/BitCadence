"""Plain-verb layer for the CLI: start, status, ask, approve, fix and friends.

Spec: design/redesign-v1/CLI.md. Every function here takes a gateway ``client``
(GatewayClient-shaped) so the menu and the verbs run the *same* code and tests
can drive them with a fake gateway. The old ``mco`` commands are untouched
aliases; this module only adds the friendly front door.
"""

from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, List, Optional

from mco import friendly

WAITING = "needs_approval"
RUNNING = {"leased", "in_progress"}
KILL_SWITCH = "MCO_KILL_SWITCH"

# Apps `bitcadence connect` knows how to find: see mco.connect_ai.
def _connect_targets() -> dict[str, list[Path]]:
    from mco import connect_ai
    return connect_ai.targets()


# ─────────────────────────────────────────────────────────────────────────────
# Output helpers
# ─────────────────────────────────────────────────────────────────────────────
def say(text: str = "") -> None:
    """Plain print: no markup, so NO_COLOR and pipes behave. Characters the
    terminal can't show (a job title with a dash, say) never raise."""
    try:
        print(text)
    except UnicodeEncodeError:
        enc = getattr(sys.stdout, "encoding", None) or "ascii"
        print(text.encode(enc, errors="replace").decode(enc, errors="replace"))


def interactive() -> bool:
    return bool(sys.stdin and sys.stdin.isatty() and sys.stdout and sys.stdout.isatty())


def confirm(question: str, *, default: bool = True, yes: bool = False) -> bool:
    """One question, default in brackets, Enter accepts. Never blocks a script:
    with no terminal the answer is "no" unless --yes was given."""
    if yes:
        return True
    if not interactive():
        return False
    hint = "[Y/n]" if default else "[y/N]"
    try:
        answer = input(f"{question} {hint} ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        say()
        return False
    if not answer:
        return default
    return answer in {"y", "yes"}


def ask_text(question: str) -> str:
    if not interactive():
        return ""
    try:
        return input(f"{question} ").strip()
    except (EOFError, KeyboardInterrupt):
        say()
        return ""


def fail(exc: BaseException, *, app_hint: str = "") -> "typer_exit":  # noqa: F821
    """Print the plain error and exit 1. --debug / MCO_DEBUG=1 adds the trace."""
    import typer

    err = friendly.translate(exc, app_hint=app_hint)
    say(err.render())
    if friendly.debug_enabled():
        import traceback

        traceback.print_exception(type(exc), exc, exc.__traceback__)
    raise typer.Exit(code=1)


# ─────────────────────────────────────────────────────────────────────────────
# Snapshot of the board: one read, used by status, fix and the menu
# ─────────────────────────────────────────────────────────────────────────────
@dataclass
class Problem:
    summary: str
    question: str = ""
    repair: Optional[Callable[[], str]] = None
    hint: str = ""


@dataclass
class Snapshot:
    reachable: bool = True
    waiting: List[dict] = field(default_factory=list)
    running: List[dict] = field(default_factory=list)
    failed: List[dict] = field(default_factory=list)
    pending: List[dict] = field(default_factory=list)
    helpers: List[dict] = field(default_factory=list)
    paused: bool = False
    error: Optional[friendly.FriendlyError] = None

    def to_json(self) -> dict:
        def brief(job: dict) -> dict:
            return {
                "id": job.get("id"),
                "title": job.get("title"),
                "status": job.get("status"),
                "role": job.get("target_agent_role"),
            }

        return {
            "running_gateway": self.reachable,
            "paused": self.paused,
            "needs_you": [brief(j) for j in self.waiting],
            "running": [brief(j) for j in self.running],
            "failed": [brief(j) for j in self.failed],
            "helpers": [
                {"name": h.get("instance_id"), "role": h.get("role"), "state": h.get("state") or h.get("effective_status")}
                for h in self.helpers
            ],
            "problems": len(find_problems(self)),
            "error": self.error.render() if self.error else None,
        }


def take_snapshot(client) -> Snapshot:
    try:
        jobs = client.jobs() or []
    except Exception as exc:  # noqa: BLE001 - translated for the person
        return Snapshot(reachable=False, error=friendly.translate(exc))
    snap = Snapshot()
    snap.waiting = [j for j in jobs if j.get("status") == WAITING]
    snap.running = [j for j in jobs if j.get("status") in RUNNING]
    snap.pending = [j for j in jobs if j.get("status") == "pending"]
    snap.failed = [j for j in jobs if j.get("status") == "failed"]
    try:
        snap.helpers = client.agents() or []
    except Exception:  # noqa: BLE001 - helpers are a nicety for the summary
        snap.helpers = []
    try:
        groups = (client.settings() or {}).get("groups") or {}
        for rows in groups.values():
            for row in rows:
                if row.get("key") == KILL_SWITCH:
                    snap.paused = str(row.get("value")).lower() == "true"
    except Exception:  # noqa: BLE001
        pass
    return snap


def _online_roles(helpers: List[dict]) -> set:
    return {
        (h.get("role") or "").lower()
        for h in helpers
        if h.get("effective_status", h.get("status")) == "online" and h.get("state") != "disabled"
    }


def _title(job: dict) -> str:
    return (job.get("title") or job.get("id") or "Untitled").strip()


def _is_recent(job: dict, hours: int = 24) -> bool:
    """Failures older than a day are history, not something to fix right now."""
    raw = job.get("updated_at") or job.get("completed_at") or job.get("created_at")
    if not raw:
        return True
    try:
        when = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except ValueError:
        return True
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - when).total_seconds() <= hours * 3600


def find_problems(snap: Snapshot, client=None, *, scan_helpers: bool = False) -> List[Problem]:
    """What is wrong, in plain words, with a repair when one is safe to run."""
    problems: List[Problem] = []
    if not snap.reachable:
        return problems
    if scan_helpers:
        problems.extend(approver_problems())
    if scan_helpers:
        problems.extend(helper_problems(snap.helpers))
    for helper in snap.helpers:
        if helper.get("state") == "broken":
            name = helper.get("instance_id") or helper.get("role") or "A helper"
            reason = helper.get("state_reason")
            problems.append(Problem(
                f"{name} looks stuck" + (f": {reason}." if reason else "."),
                hint="Restart it from its own window, then run: bitcadence status",
            ))
    for job in snap.failed:
        if not _is_recent(job):
            continue
        job_id = job.get("id")
        repair = None
        if client is not None and job_id:
            repair = (lambda jid=job_id: _retry(client, jid))
        problems.append(Problem(
            f"\"{_title(job)}\" didn't finish.",
            "Try it again?",
            repair,
        ))
    online = _online_roles(snap.helpers)
    stuck_roles = sorted({
        (j.get("target_agent_role") or "").lower() for j in snap.pending
        if (j.get("target_agent_role") or "").lower() not in online
    } - {""})
    for role in stuck_roles:
        problems.append(Problem(
            f"Work is waiting for a {role} helper, but none is free.",
            hint="Start one with: bitcadence helpers add",
        ))
    return problems


def approver_problems() -> List[Problem]:
    """The account on this computer can't approve: found, and fixable in one key."""
    from mco import approver

    account = approver.own_account()
    if not approver.is_missing(account):
        return []
    return [Problem(
        friendly.not_approver().what,
        "Fix it?",
        lambda acct=account: approver.grant(acct),
    )]


def helper_problems(agents: List[dict], *, findings=None) -> List[Problem]:
    """Locked logs and duplicate wake processes. Detection is a dry run; the repair
    only runs when the person says yes."""
    from mco import helpers

    if findings is None:
        try:
            findings = helpers.scan(a.get("instance_id") for a in agents)
        except Exception:  # noqa: BLE001 - a process scan must never break `fix`
            findings = []
    out = []
    for f in findings:
        out.append(Problem(
            f.summary + " " + f.would,
            f.question,
            (lambda finding=f: " ".join(helpers.repair([finding], confirmed=True))),
            hint="Run: bitcadence helpers fix",
        ))
    return out


def _retry(client, job_id: str) -> str:
    res = client.retry(job_id)
    return f"Done. It's back in line ({((res or {}).get('job') or {}).get('status', 'pending')})."


# ─────────────────────────────────────────────────────────────────────────────
# Verbs (the menu calls these same functions)
# ─────────────────────────────────────────────────────────────────────────────
def greeting(now: Optional[datetime] = None) -> str:
    hour = (now or datetime.now()).hour
    return "Good morning." if hour < 12 else "Good afternoon." if hour < 18 else "Good evening."


def do_status(client, *, as_json: bool = False) -> int:
    snap = take_snapshot(client)
    if as_json:
        say(json.dumps(snap.to_json(), indent=2))
        return 0
    if not snap.reachable:
        say((snap.error or friendly.translate(RuntimeError("unreachable"))).render())
        return 0
    say(greeting())
    if snap.paused:
        say("Everything is paused. Run `bitcadence resume` to carry on.")
    problems = find_problems(snap)
    say(f"  Needs you ({len(snap.waiting) + len(problems)})")
    for i, job in enumerate(snap.waiting, 1):
        say(f"    {i}. {_title(job)}?")
    for problem in problems:
        say(f"    - {problem.summary}")
    if not snap.waiting and not problems:
        say("    Nothing right now.")
    say(f"  Running ({len(snap.running)})")
    for job in snap.running:
        say(f"    {_title(job)}")
    if not snap.running:
        say("    Nothing running.")
    nxt = []
    if snap.waiting:
        nxt.append("`bitcadence approve`")
    if problems:
        nxt.append("`bitcadence fix`")
    if nxt:
        say("Run " + " or ".join(nxt) + ".")
    return 0


def _pick_role(snap: Snapshot) -> str:
    online = sorted(_online_roles(snap.helpers) - {"operator", "human"})
    if online:
        return online[0]
    roles = sorted({(h.get("role") or "").lower() for h in snap.helpers} - {"", "operator", "human"})
    return roles[0] if roles else "claude"


def do_ask(client, text: str, *, yes: bool = False, remove: Optional[List[int]] = None,
           ask_end: bool = False, repeat: str = "") -> int:
    from mco import ask_plan
    text = (text or "").strip()
    if not text:
        text = ask_text("What would you like done?")
    if not text:
        say('Tell me what you want, for example: bitcadence ask "tidy the install docs"')
        return 1
    snap = take_snapshot(client)
    if not snap.reachable:
        say((snap.error or friendly.translate(RuntimeError("unreachable"))).render())
        return 1
    try:
        plan = ask_plan.draft_plan(text, _pick_role(snap))
        numbers = sorted(set(remove or []), reverse=True)  # highest first so numbers stay valid
        ids = [plan["steps"][n - 1]["id"] for n in numbers if 1 <= n <= len(plan["steps"])]
        if len(ids) != len(numbers):
            raise ask_plan.PlanError(f"There is no step {max(numbers)}. The plan has {len(plan['steps'])}.")
        plan = ask_plan.apply_tweaks(plan, remove=ids, ask_end=ask_end, repeat=repeat)
    except ask_plan.PlanError as e:
        say(str(e))
        return 1
    say("Here's the plan:")
    for line in ask_plan.plan_lines(plan):
        say(line)
    say(f"  Who: your {plan['steps'][0]['role']} helper")
    say("  It waits for your OK before it runs.")
    if not confirm("Start this?", yes=yes):
        say("Okay, I didn't start anything.")
        return 0
    try:
        ask_plan.submit_plan(client.send, plan, first_step_waits=True)
    except ask_plan.PlanError:
        say("That didn't go through.")
        say("Run: bitcadence fix")
        return 1
    if plan.get("repeat"):
        try:
            ask_plan.save_repeat(plan)
            say(f"Scheduled: {plan['repeat']['words']}. See it with: bitcadence schedule")
        except ask_plan.PlanError as e:
            say(f"Started, but I couldn't set the repeat. {e}")
    say("Started. It's waiting for your OK. Run `bitcadence approve` when you're ready.")
    return 0


def _match_job(waiting: List[dict], ref: str) -> Optional[dict]:
    ref = ref.strip()
    if ref.isdigit() and 1 <= int(ref) <= len(waiting):
        return waiting[int(ref) - 1]
    for job in waiting:
        if job.get("id") == ref or str(job.get("id", "")).startswith(ref):
            return job
    lowered = ref.lower()
    for job in waiting:
        if lowered and lowered in _title(job).lower():
            return job
    return None


def _approve_call(client, job_id: str, *, yes: bool = False):
    """client.approve, with a missing approver right turned into the one-key fix."""
    from mco import approver

    try:
        return client.approve(job_id)
    except Exception as exc:  # noqa: BLE001
        if friendly.translate(exc).kind != "not_approver":
            raise
    say(friendly.not_approver().what)
    if not confirm("Fix it?", yes=yes):
        say("Okay, nothing changed. Run: bitcadence fix")
        raise _Declined()
    try:
        say(approver.grant())
    except approver.CannotRepair as why:
        say(str(why))
        raise _Declined() from why
    return client.approve(job_id)


class _Declined(Exception):
    """The person said no (or the fix is not possible); the message was already shown."""


def _approve_one(client, job: dict, snap: Snapshot, *, yes: bool = False) -> bool:
    try:
        _approve_call(client, job["id"], yes=yes)
    except _Declined:
        return False
    say(f"Approved: {_title(job)}.")
    role = (job.get("target_agent_role") or "").lower()
    if role and role not in _online_roles(snap.helpers):
        err = friendly.no_helper_free()
        say(err.render())
    return True


def do_approve(client, job_ref: str = "", *, yes: bool = False) -> int:
    snap = take_snapshot(client)
    if not snap.reachable:
        say((snap.error or friendly.translate(RuntimeError("unreachable"))).render())
        return 1
    if not snap.waiting:
        say("Nothing is waiting for you.")
        return 0
    if job_ref:
        job = _match_job(snap.waiting, job_ref)
        if job is None:
            # An id we cannot see in the waiting list may still be valid.
            try:
                res = _approve_call(client, job_ref, yes=yes)
            except _Declined:
                return 1
            say(f"Approved: {((res or {}).get('job') or {}).get('title') or job_ref}.")
            return 0
        return 0 if _approve_one(client, job, snap, yes=yes) else 1
    if not interactive() and not yes:
        say(f"{len(snap.waiting)} waiting:")
        for i, job in enumerate(snap.waiting, 1):
            say(f"  {i}. {_title(job)}")
        say("Run `bitcadence approve` in a terminal to go through them, or pass a name or number.")
        return 0
    for job in snap.waiting:
        say(f"{_title(job)}")
        if confirm("Approve this?", yes=yes):
            if not _approve_one(client, job, snap, yes=yes):
                return 1
            continue
        reason = ask_text("What should change? (Enter to leave it waiting)")
        if reason:
            client.reject(job["id"], reason)
            say("Sent back with your note.")
        else:
            say("Okay, left waiting.")
    return 0


def do_fix(client, *, yes: bool = False) -> int:
    snap = take_snapshot(client)
    if not snap.reachable:
        say((snap.error or friendly.translate(RuntimeError("unreachable"))).render())
        if confirm("Start BitCadence now?", yes=yes):
            from mco import quiet

            return quiet.run_start(open_app=False, autostart=False)
        return 0
    problems = find_problems(snap, client, scan_helpers=True)
    if not problems:
        say("Everything looks fine.")
        return 0
    say(f"Found {len(problems)} problem{'s' if len(problems) != 1 else ''}.")
    for problem in problems:
        say(f"  {problem.summary}")
        if problem.repair and confirm(f"  {problem.question}", yes=yes):
            try:
                say(f"  {problem.repair()}")
            except Exception as exc:  # noqa: BLE001
                say("  " + friendly.translate(exc).render())
        elif problem.hint:
            say(f"  {problem.hint}")
    return 0


_LIGHT_MARK = {"green": "(+)", "red": "(!)", "grey": "( )"}


def do_helpers(client, *, as_json: bool = False) -> int:
    from mco import helpers

    try:
        agents = client.agents() or []
    except Exception as exc:  # noqa: BLE001
        say(friendly.translate(exc).render())
        return 1
    if as_json:
        say(json.dumps(agents, indent=2, default=str))
        return 0
    if not agents:
        say("No helpers yet. Add one with: bitcadence helpers add")
        return 0
    try:
        findings = helpers.scan(a.get("instance_id") for a in agents)
    except Exception:  # noqa: BLE001
        findings = []
    try:
        active = [j for j in (client.jobs(limit=200) or []) if j.get("status") in ("leased", "in_progress")]
    except Exception:  # noqa: BLE001 - "what it is doing" is a nicety
        active = []
    rows = helpers.describe(agents, active, findings)
    say(f"Helpers ({len(rows)})")
    for row in rows:
        say(f"  {_LIGHT_MARK.get(row['light'], '( )')} {row['name']:<22} {row['word']:<14} {row['doing']}")
    if findings:
        say("Something needs fixing. Run: bitcadence helpers fix")
    return 0


def do_helpers_fix(client, *, yes: bool = False) -> int:
    """Look first (changes nothing), then repair each problem only after a Y."""
    from mco import helpers

    try:
        agents = client.agents() or []
    except Exception as exc:  # noqa: BLE001
        say(friendly.translate(exc).render())
        return 1
    findings = helpers.scan(a.get("instance_id") for a in agents)
    if not findings:
        say("Every helper looks fine.")
        return 0
    say(f"Found {len(findings)} problem{'s' if len(findings) != 1 else ''}.")
    for finding in findings:
        say(f"  {finding.summary}")
        say(f"  {helpers.repair([finding])[0]}")
        if confirm(f"  {finding.question}", yes=yes):
            say("  " + " ".join(helpers.repair([finding], confirmed=True)))
        else:
            say("  Okay, left as it is.")
    return 0


def do_schedules(*, as_json: bool = False, add: bool = True) -> int:
    """Schedules as sentences ("Every weekday at 2:00 AM"). In a terminal it then
    offers to add one; scripts and pipes just get the list."""
    from mco import schedules_plain as sp

    if as_json:
        data = sp.list_schedules()
        say(json.dumps({s["id"]: s["when"] for s in data["schedules"]}, indent=2))
        return 0
    data = sp.list_schedules()
    if data["problem"]:
        say(data["problem"])
        return 1
    if not data["schedules"]:
        say("Nothing is scheduled yet.")
    else:
        say("Your schedules")
        for i, item in enumerate(data["schedules"], 1):
            state = "On" if item["on"] else "Off"
            say(f"  {i}. {item['name']}: {item['when']}  ({state}; next: {item['next']})")
    if add and interactive():
        if confirm("Add a schedule?", default=not data["schedules"]):
            return do_schedule_add()
    return 0


def _choose(question: str, labels: List[str]) -> int:
    """Numbered pick; returns an index or -1 when the person backs out."""
    say(question)
    for i, label in enumerate(labels, 1):
        say(f"  {i}. {label}")
    answer = ask_text("Type a number:")
    if answer.isdigit() and 1 <= int(answer) <= len(labels):
        return int(answer) - 1
    lowered = answer.lower()
    for i, label in enumerate(labels):
        if lowered and lowered in label.lower():
            return i
    return -1


def do_schedule_add(what: str = "", when: str = "", *, yes: bool = False) -> int:
    """Asks what, how often, what time. Prints the sentence, then saves. ``when`` is
    plain words like "every weekday at 2 AM" and skips the questions."""
    from mco import schedules_plain as sp

    available = sp.launchers_available()
    if not available:
        say("There is nothing to schedule yet. Ask for something first: bitcadence ask \"...\"")
        return 1
    labels = [a["label"] for a in available]
    if what:
        match = next((a for a in available if what.lower() in (a["id"].lower(), a["label"].lower())), None)
        match = match or next((a for a in available if what.lower() in a["label"].lower()), None)
        if match is None:
            say(f"I don't know {what!r}. I can schedule: {', '.join(labels)}.")
            return 1
    else:
        if not interactive() and not yes:
            say("Tell me what to run: bitcadence schedule add --what \"Nightly audit\" --when \"every weekday at 2 AM\"")
            return 1
        pick = 0 if len(available) == 1 else _choose("What should run?", labels)
        if pick < 0:
            say("Okay, nothing saved.")
            return 1
        match = available[pick]
    try:
        if when:
            picked = sp.parse_phrase(when)
            cron = sp.build_cron(picked["frequency"], picked["hour"], picked["minute"], picked["days"])
        else:
            if not interactive():
                say('Tell me when: --when "every weekday at 2 AM"')
                return 1
            how = _choose("How often?", [sp.FREQUENCY_LABELS[f] for f in sp.FREQUENCIES])
            if how < 0:
                say("Okay, nothing saved.")
                return 1
            frequency, days = sp.FREQUENCIES[how], []
            if frequency == "days":
                names = ask_text("Which days? (for example: Monday, Wednesday, Friday)")
                picked_days = sp.parse_phrase(f"every {names} at 12:00") if names else None
                days = picked_days["days"] if picked_days else []
            at = "" if frequency == "hour" else ask_text("At what time? (for example: 2 AM)")
            cron = sp.plan(frequency, at or "02:00", days)["cron"]
    except sp.ScheduleWordsError as exc:
        say(str(exc))
        return 1
    words = sp.describe_cron(cron)
    say(f"{match['label']}: {words}")
    if not confirm("Save this?", yes=yes):
        say("Okay, nothing saved.")
        return 0
    try:
        sp.add_schedule(match["id"], cron)
    except sp.ScheduleWordsError as exc:
        say(str(exc))
        return 1
    say(f"Saved. {words}.")
    return 0


def do_schedule_toggle(name: str, on: bool) -> int:
    from mco import schedules_plain as sp

    data = sp.list_schedules()
    ref = name.strip().lower()
    item = next((s for s in data["schedules"] if ref in (s["id"].lower(), s["name"].lower())), None)
    item = item or next((s for s in data["schedules"] if ref and ref in s["name"].lower()), None)
    if item is None:
        say(f"I couldn't find a schedule called {name!r}.")
        return 1
    try:
        sp.set_enabled(item["id"], on)
    except sp.ScheduleWordsError as exc:
        say(str(exc))
        return 1
    say(f"{item['name']} is {'on' if on else 'off'}. {item['when']}.")
    return 0


def do_connect(app: str = "", *, yes: bool = False, disconnect: bool = False, check: bool = False) -> int:
    from mco import connect_ai
    targets = _connect_targets()
    app = (app or "").strip().lower()
    if app == "other":
        say("Add this to the other app's connection settings:")
        say(connect_ai.other_snippet())
        return 0
    if not app:
        names = sorted(targets)
        say("Which AI do you want to connect?")
        for i, name in enumerate(names, 1):
            say(f"  {i}. {connect_ai.label(name)}")
        say(f"  {len(names) + 1}. Another app")
        choice = ask_text("Type a number or a name:")
        if not choice:
            return 1
        if choice.isdigit() and int(choice) == len(names) + 1:
            return do_connect("other")
        app = names[int(choice) - 1] if choice.isdigit() and 1 <= int(choice) <= len(names) else choice.lower()
    name = connect_ai.label(app)
    try:
        if app not in targets:
            raise connect_ai.ConnectError(
                f"I don't know {app!r} yet. I can connect: {', '.join(sorted(targets))}, other.", "unknown")
        if check:
            res = connect_ai.check(app, targets)
            say(res["message"])
            return 0 if res["ok"] else 1
        if disconnect:
            if not confirm(f"Take BitCadence out of {name}'s settings? (a backup is kept)", yes=yes):
                say("Okay, nothing changed.")
                return 0
            res = connect_ai.disconnect(app, targets)
            say(f"{name} is disconnected." if res["changed"] else f"{name} wasn't connected.")
            return 0
        path = next((p for p in targets[app] if p.exists()), None)
        if path is None:
            raise connect_ai.ConnectError("", "not_found")
        already = next((r for r in connect_ai.status({app: targets[app]}) if r["connected"]), None)
        if already:
            say(f"{name} is already connected.")
            return 0
        say(f"I'll add BitCadence to {name}'s settings (a backup is kept).")
        if not confirm("Go ahead?", yes=yes):
            say("Okay, nothing changed.")
            return 0
        connect_ai.connect(app, targets)
    except connect_ai.ConnectError as exc:
        if exc.kind == "not_found":
            say(friendly.app_not_found(app).render())
        else:
            say(str(exc))
        return 1
    say(f"Connected. Restart {name} to see it.")
    return 0


def do_pause(client, *, yes: bool = False) -> int:
    say("This stops work in progress and holds new work until you resume.")
    if not confirm("Pause everything?", default=False, yes=yes):
        say("Okay, nothing paused.")
        return 0
    client.settings_put({KILL_SWITCH: "true"})
    say("Paused. Run `bitcadence resume` when you're ready.")
    return 0


def do_resume(client) -> int:
    client.settings_put({KILL_SWITCH: None})
    say("Resumed. Work will pick up again.")
    return 0


def _setting_value(client, key: str):
    for rows in ((client.settings() or {}).get("groups") or {}).values():
        for row in rows:
            if row.get("key") == key:
                return row.get("value")
    return None


def mask_token(token: str) -> str:
    return f"mco_tok_...{token[-4:]}" if token else "not set"


def do_settings(client, *, show_advanced: bool = False) -> int:
    """Everyday settings in plain labels. Tokens, ports and addresses appear only
    with ``show_advanced`` (and then the token is masked)."""
    from mco import cli
    from mco.config import get_config

    try:
        paused = str(_setting_value(client, KILL_SWITCH)).lower() == "true"
        phone = bool(_setting_value(client, "NTFY_TOPIC"))
    except Exception as exc:  # noqa: BLE001 - translated for the person
        say(friendly.translate(exc).render())
        return 1
    say("Settings")
    say(f"  Pause everything: {'On (helpers are not taking new work)' if paused else 'Off'}")
    say(f"  Tell me on my phone: {'On' if phone else 'Off'}")
    if not show_advanced:
        say("Pause or resume with `bitcadence pause` / `bitcadence resume`.")
        say("For people who like details: bitcadence settings --show-advanced")
        return 0
    config = get_config()
    say("")
    say("Details")
    say(f"  Where it listens: {config.get('MCO_GATEWAY_URL') or 'http://127.0.0.1:18789'}")
    say(f"  This computer's sign-in: {mask_token((config.get('MCO_AGENT_TOKEN') or config.get('MCO_LOCAL_TOKEN') or '').strip())}")
    say("")
    cli.manage_settings()
    return 0


def do_remember(client, text: str, content: str = "") -> int:
    text = (text or "").strip()
    if not text:
        say('Tell me what to remember, for example: bitcadence remember "Sam likes the blue folder"')
        return 1
    title, body = (text, content) if content else (text if len(text) <= 60 else text[:57] + "...", text)
    client.remember(title=title, content=body, kind="fact")
    say("Remembered.")
    return 0
