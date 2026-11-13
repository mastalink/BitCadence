# Everyday commands (start here)

BitCadence has a short set of plain commands for everyday use. Type `bitcadence` on its own to get a menu
you can arrow through, or use the words below. Every older `mco` command still works exactly as before.

## The menu

```
bitcadence
```

It opens a menu with live counts ("Approvals (2 waiting)", "Fix problems (1)"). Arrow keys or `j`/`k` move, and
numbers 1–9 jump to an item. In scripts, CI, or with `MCO_NO_MENU=1`, it prints help instead and never waits for
input. `bitcadence --no-menu` does the same on purpose.

## The everyday words

| You want to… | Type | What happens |
|---|---|---|
| Start BitCadence | `bitcadence start` | Starts in the background, with no window left open |
| See what needs you | `bitcadence status` | A plain summary of what's waiting, what's running and any problems |
| Get something done | `bitcadence ask "tidy the install docs"` | Drafts a plan, shows it, and asks before anything runs |
| OK waiting work | `bitcadence approve` | Walks through what's waiting, one at a time |
| Fix what's wrong | `bitcadence fix` | Finds problems and offers each repair. Y/n each time |
| Connect an AI app | `bitcadence connect claude` | Finds the app (claude, gemini or cursor) and writes its settings |
| Pause everything | `bitcadence pause` | Stops work in progress and holds new work |
| Carry on | `bitcadence resume` | Picks up after a pause |
| See your helpers | `bitcadence helpers` | Who's ready, busy or stuck |
| Add a helper | `bitcadence helpers add` | Asks what it needs and saves its credentials safely (you never see the full token) |
| See schedules | `bitcadence schedule list` | Every schedule and loop, with its next run time |

**Skipping the questions:** most of these accept `--yes` (or `-y`) for scripts, for example `bitcadence fix --yes`.

**More detail:** `bitcadence status --details` shows the technical diagnostics (the old `mco status` view), and
`bitcadence status --json` and `bitcadence helpers --json` are for scripts.

**Run a saved plan file:** `bitcadence ask --file plan.yaml` loads a workflow file and submits it for real.
It isn't a dry run; use `mco workflow plan.yaml --dry-run` to preview one.

**Anything else:** `bitcadence advanced <command>` runs any original command, for example
`bitcadence advanced audit <job-id>`.

## When something goes wrong

Errors say what happened in one sentence, plus the next command to run, usually `bitcadence fix`.
Use `bitcadence --debug status` to see technical error details; global options go before the command.

## Still on the roadmap

The current CLI includes the approver-rights repair prompt and schedule-add wizard. See the
[complete reference](mco-cli-reference.md) for exact arguments and effects. Pausing one helper
and the redesigned desktop mockups are separate from the shipped everyday commands; do not
assume a design document describes an installed feature.
