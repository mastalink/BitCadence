# Scheduling — Launchers, Schedules, and Loops

Cron can start a process. It cannot tell you six weeks later *who authorized the
thing that ran at 3am*, stop after the tenth iteration because a human said so,
or pause a run behind an approval gate.

BitCadence's scheduler creates **jobs on the governed board**, so recurring
work inherits everything a hand-submitted job gets: approval gates, retry
budgets, role/instance isolation, and an audit trail that records which schedule
created it.

Two config files, two different questions:

| File | Question it answers |
|---|---|
| `~/.mco/fleet.toml` | Which **workers** run, and how they wake |
| `~/.mco/schedules.yaml` | What **work** gets created, and when |

---

## Quick start

```bash
mco schedule init             # write a starter ~/.mco/schedules.yaml
mco schedule list             # every schedule + its next fire time
mco launch <name>             # fire one by hand, right now
mco schedule tick --dry-run   # show what would fire, create nothing
mco schedule run              # run the scheduler in the foreground

mco schedule disable <name>   # pause without deleting the definition
mco schedule enable  <name>
mco schedule reset   <name>   # clear history so a finished loop can rerun

mco service install-scheduler # survive reboot (see "Running", below)
```

`enable`/`disable` edit the config surgically, preserving your comments and
formatting — they are not a parse-and-rewrite.

---

## The three concepts

### Launcher — *what* to run

A named, reusable launch target. One of four kinds — a job, a workflow, a local
program, or a URL.

```yaml
launchers:
  nightly-audit:
    role: reviewer                 # which role's dropbox
    title: Nightly dependency audit
    instructions: |
      Audit dependencies for new CVEs. Open a PR if any are found.
    requires_approval: false       # optional governance
    max_retries: 2
    escalate_to_role: human

  release:
    workflow: workflows/release-pipeline.yaml   # a whole DAG instead

  # Local GUI/app kinds — see the caveat below
  console:
    url: http://127.0.0.1:18789/console         # opens in the default browser
  claude-desktop:
    app: "C:/Program Files/Claude/Claude.exe"   # a local program
  editor:
    app: code
    args: ["--new-window", "."]
```

`mco launch nightly-audit` fires it immediately. This is **the same code path** a
scheduled fire uses — so testing a launcher by hand proves the 3am run too.

#### GUI and app launchers — read this

`app` and `url` launchers start something **on this machine**. They are local
conveniences, and three things follow from that:

1. **They create no board job and no audit entry.** Governed work goes through
   `role`/`workflow` launchers; these do not. Don't use them for work you need
   to prove happened.
2. **Processes are started fully detached** — a GUI app outlives the tick that
   started it, and a long-running app never holds the scheduler open or dies
   with it.
3. **A GUI launched by the scheduler *service* may not appear on your desktop.**
   Boot services run in their own session (notably Windows session 0), so
   windows they open can be invisible to the logged-in user. Schedule GUI
   launchers only when you run the scheduler in your own session
   (`mco schedule run`), not as an installed service.

`url` accepts only `http://`, `https://`, and `file://`. Schemes like
`javascript:` and `data:` are rejected at parse time — handing arbitrary schemes
to a browser from a config file a scheduler executes is a foot-gun.

**Just want the console?** There's a direct command, no config needed:

```bash
mco gui                 # open the full console in your browser
mco gui --dashboard     # the minimal dashboard instead
mco gui --print         # print the URL, don't open anything
```

It checks the port first and tells you to run `mco start` rather than opening a
dead tab.

### Schedule — *when* to run it

Binds a launcher to a trigger. Fires indefinitely until disabled.

```yaml
schedules:
  nightly-audit:
    launcher: nightly-audit
    cron: "0 3 * * *"              # 5-field cron, or @daily/@hourly/@weekly
    timezone: America/New_York     # optional; UTC when omitted

  health-check:
    launcher: nightly-audit
    every: 30m                     # 30s / 15m / 2h / 1d / 1w
    overlap: skip                  # skip (default) | allow
    enabled: true
    requires_approval: true        # force a gate on everything this fires
```

`overlap: skip` means a schedule won't fire again while its previous run is still
in flight — the default, because the common failure mode for agent work is a
pile-up of duplicate jobs racing each other on the same repo.

### Loop — a schedule that stops

Same machinery, **mandatory bound**. A loop must declare `max_iterations`,
`until`, or both.

```yaml
loops:
  triage-backlog:
    launcher: nightly-audit
    every: 30m
    max_iterations: 10
    until: "2026-12-31T00:00:00Z"

  drain-codex-queue:
    launcher: nightly-audit
    every: 5m
    until_empty: codex        # stop when that role's queue is clear
    max_iterations: 20        # required alongside until_empty
```

**`until_empty`** is the "work the backlog until it's done" loop — it stops as
soon as the named role has no unfinished jobs. It *always* requires
`max_iterations` as a hard cap, because a queue that never drains would
otherwise loop forever: the condition is a stop *hint*, the count is the
guarantee.

If the gateway can't be reached during that check, the loop **keeps going**
rather than declaring victory — silently ending a loop on a failed lookup is
the worse failure.

An unbounded self-repeating agent task is how fleets burn budget and drift, so
the parser **refuses to build one**:

```
loops.forever: a loop must declare 'max_iterations' and/or 'until'.
An unbounded loop is just a schedule - define it under 'schedules:' if that's what you meant.
```

The bound is enforced at runtime too, not just documented — once a loop hits its
limit it stops firing and `mco schedule list` shows *why*.

**Completion is sticky.** A loop that finished stays finished, even if the
condition that ended it reverses (a drained queue refilling, say). That's
deliberate — an "until done" loop that quietly resurrects isn't bounded. Use
`mco schedule reset <name>` to deliberately clear its history and let it run
again.

---

## Cron syntax

Standard 5-field: `minute hour day-of-month month day-of-week`

| Form | Meaning |
|---|---|
| `*` | every value |
| `5` | exactly 5 |
| `1-10` | range |
| `*/15` | every 15th |
| `8-18/4` | every 4th within a range |
| `1,3,5` | list |

Shortcuts: `@hourly` `@daily` `@midnight` `@weekly` `@monthly` `@yearly`

Day-of-week is `0`–`7` with both `0` and `7` meaning Sunday. When **both**
day-of-month and day-of-week are restricted they are OR'd, not AND'd — the
standard cron quirk (`0 0 1 * 5` = the 1st of the month *or* any Friday).

> **Windows:** timezone names need the IANA database, which Windows doesn't ship.
> It's installed automatically as a dependency (`tzdata`); if you see
> *"this machine has no IANA timezone database"*, run `pip install tzdata`.

---

## Running the scheduler

Two ways, pick one:

**Foreground daemon** — ticks on an interval until interrupted:
```bash
mco schedule run --interval 30
```

**Single pass** — drive it from an existing cron / Task Scheduler entry instead:
```bash
mco schedule tick
```

**As a boot-persistent service** — the one you actually want in production:
```bash
mco service install-scheduler --interval 30
mco service status BitCadence-scheduler
mco service logs   BitCadence-scheduler
```

This installs through the same machinery as the gateway and wakers, so it works
identically on Windows Task Scheduler, systemd, and launchd, and restarts on
failure.

A bad config or an unreachable gateway won't kill the daemon; it logs and keeps
ticking. The failure operators actually suffer is a scheduler that quietly died
three weeks ago.

---

## Where state lives

`~/.mco/schedule-state.json` — iteration counts, last fire times, and the job ids
each fire created. Written atomically, and deliberately separate from
`schedules.yaml`: the config is yours to edit and version-control, the state is
the runtime's to own. If it's ever corrupted the scheduler starts fresh rather
than refusing to run.

---

## Auditing a scheduled run

Every job a launcher creates is stamped with its origin:

```json
{
  "origin": {
    "launcher": "nightly-audit",
    "schedule": "nightly-audit",
    "trigger": "schedule",
    "iteration": 7,
    "launched_at": "2026-07-28T07:00:00+00:00"
  }
}
```

So `mco audit <job-id>` answers "what created this, and was it the 7th of a
bounded loop or a human pressing the button?" — the question plain cron
structurally cannot.
