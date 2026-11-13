# Complete CLI reference

Use `bitcadence` for everyday work. `mco` is a supported compatibility name for the **same command tree**: every path below works with either executable. The desktop entry point is separate: `bitcadence-desktop` launches the native desktop app; its pages are covered by the [GUI reference](gui-reference.md).

This reference covers every registered command and group recursively, every positional argument, option spelling, parser default, repeatable/variadic parameter, prompt and declared range/path constraint. It includes generated completion/help options and the hidden compatibility option `gui --flow`. Defaults are parser defaults; configuration or the implementation may resolve unset values at runtime. `~` means your home directory; `.mco`, `MCO_` and the Python package `mco` remain supported compatibility names.

## Read this first

- Run `bitcadence` in an interactive terminal for the menu. In scripts/non-interactive use it prints help and exits 2; `bitcadence --no-menu` deliberately prints help and exits 0. `MCO_NO_MENU=1` also disables the menu.
- Put global options before the command: `bitcadence --debug status`. Group options belong before their subcommand, for example `bitcadence helpers --json`.
- Add `--help` to a specific command to inspect it safely, for example `bitcadence score start --help`. Except for the transparent `advanced` forwarder, help exits before operational callbacks. No credentials or production commands were executed to produce this reference.
- Examples containing JOB_ID, INSTANCE_ID, WORKER_NAME, SCHEDULE_NAME, EXCHANGE_ID, CLIENT_WORKTREE, or file names require your actual values/files. They describe intended operations; they are not a script to run from top to bottom. Commands that change state say so in their expected result/recovery notes. For effectful/restricted operations the example may intentionally stop at help.
- `--yes` only exists where listed. It skips that command's questions; it does not add permission. A daemon running, a job submitted, or an exit-0 policy reply does not prove the work succeeded.
- Most failures exit 1; syntax errors typically exit 2. Important exceptions are documented per command (empty results/missing schedule or fleet config/advisory verdicts). Do not infer success from exit code alone.

## Find a command

- [mco advanced](#cli-mco-advanced) — Run any original command, e.g. `bitcadence advanced audit <job-id>`.
- [mco agents](#cli-mco-agents) — List all registered agents and their current online presence status.
- [mco approve](#cli-mco-approve) — Approve what's waiting for you, one at a time.
- [mco archive](#cli-mco-archive) — Archive a completed/failed/rejected/cancelled job (reversible).
- [mco ask](#cli-mco-ask) — Say what you want done. You see the plan and OK it before anything runs.
- [mco audit](#cli-mco-audit) — Print a job's tamper-evident audit trail (oldest event first).
- [mco audit-checkpoint](#cli-mco-audit-checkpoint) — Export a signed checkpoint. Keep it outside the database/backup volume.
- [mco cancel](#cli-mco-cancel) — Call off a job that hasn't finished yet (approver-role token).
- [mco cio](#cli-mco-cio) — Deterministic policy checks backing ops/cio-sidecar-brief.md.
  - [mco cio check](#cli-mco-cio-check) — Check one proposal against the brief's mechanical rules and print a verdict.
- [mco connect](#cli-mco-connect) — Connect an AI app to BitCadence.
- [mco connectors](#cli-mco-connectors) — List configured enterprise connectors and their health (via the gateway).
- [mco deregister](#cli-mco-deregister) — Remove an agent registration. Its token stops working immediately.
- [mco doctor](#cli-mco-doctor) — Diagnose an install end to end: Python, config, secret store, database, gateway, agents, vendor CLIs. Exit code 1 if anything is broken.
- [mco duplicates](#cli-mco-duplicates) — List other jobs that look like the same work as this one.
- [mco edition](#cli-mco-edition) — Show the active edition (community/team/enterprise) and feature matrix.
- [mco exchange](#cli-mco-exchange) — Drumline Agent Exchange: non-authoritative agent discussion.
  - [mco exchange list](#cli-mco-exchange-list) — List Agent Exchange messages (needs --job or --thread).
  - [mco exchange post](#cli-mco-exchange-post) — Post one message to the Agent Exchange.
  - [mco exchange promote](#cli-mco-exchange-promote) — Explicitly promote one exchange into canonical Drumline context (needs context:promote).
- [mco fix](#cli-mco-fix) — Find what's wrong and offer to repair it.
- [mco fleet](#cli-mco-fleet) — Apply declarative per-worker service run modes.
  - [mco fleet apply](#cli-mco-fleet-apply) — Reconcile OS services to ~/.mco/fleet.toml.
  - [mco fleet set](#cli-mco-fleet-set) — Update one worker field in ~/.mco/fleet.toml.
  - [mco fleet status](#cli-mco-fleet-status) — Show configured workers and their installed/running state.
- [mco gui](#cli-mco-gui) — Open the BitCadence console in your browser.
- [mco helpers](#cli-mco-helpers) — Your helpers: who is ready, busy or stuck.
  - [mco helpers add](#cli-mco-helpers-add) — Add a helper (same as `mco register`, asking for what it needs).
  - [mco helpers fix](#cli-mco-helpers-fix) — Find a locked notes file or a second copy of a helper, and offer to fix it.
- [mco jev](#cli-mco-jev) — Ask Jev bounded, annotation-only questions. Never authorizes an effect.
  - [mco jev route-model](#cli-mco-jev-route-model) — Suggest a Claude Code model tier (haiku/sonnet/opus) for a task.
- [mco jobs](#cli-mco-jobs) — BitCadence Job Board: score client jobs for fit, value, and risk.
  - [mco jobs rank](#cli-mco-jobs-rank) — Score every available client job for fit, value and risk using Jev and hard filters.
- [mco launch](#cli-mco-launch) — Fire a launcher right now, by name.
- [mco listen](#cli-mco-listen) — Spawn the background daemon client that polls and executes Job Board tasks.
- [mco mcp](#cli-mco-mcp) — Run the MCO dropbox as an MCP server: stdio (Claude/Codex/Antigravity) or HTTP (remote agents).
- [mco orgs](#cli-mco-orgs) — List orgs available for registration (Control Panel tenancy dropdown, from the terminal).
- [mco pause](#cli-mco-pause) — Pause everything: stop work in progress and hold new work.
- [mco platform](#cli-mco-platform) — Run a connector control action directly (requires an approver-role token).
- [mco reassign](#cli-mco-reassign) — Clone a failed job onto a new target, link both rows, and archive the old one (approver-role token).
- [mco recall](#cli-mco-recall) — Recall the most relevant Drumline shared-context entries.
- [mco register](#cli-mco-register) — Register a new client agent, generating a secure access token.
- [mco reject](#cli-mco-reject) — Reject a job paused at the human-in-the-loop gate (terminal).
- [mco remember](#cli-mco-remember) — Append an entry to the Drumline shared context.
- [mco reset-token](#cli-mco-reset-token) — Rotate an agent's access token. The old token stops working immediately.
- [mco restart](#cli-mco-restart) — Restart the background gateway (stop if running, then start).
- [mco restore-fence](#cli-mco-restore-fence) — After a DB restore, pause work and invalidate every pre-restore claim.
- [mco resume](#cli-mco-resume) — Resume after a pause.
- [mco retry](#cli-mco-retry) — Re-queue a failed or rejected job back to pending (approver-role token).
- [mco schedule](#cli-mco-schedule) — Schedules and loops: what work gets created, and when.
  - [mco schedule add](#cli-mco-schedule-add) — Add a schedule by asking what, how often and what time.
  - [mco schedule disable](#cli-mco-schedule-disable) — Disable a schedule or loop, leaving its definition and history intact.
  - [mco schedule enable](#cli-mco-schedule-enable) — Enable a schedule or loop (without hand-editing YAML).
  - [mco schedule init](#cli-mco-schedule-init) — Write a starter ~/.mco/schedules.yaml.
  - [mco schedule list](#cli-mco-schedule-list) — Show every schedule and loop with its next fire time.
  - [mco schedule off](#cli-mco-schedule-off) — Turn a schedule off.
  - [mco schedule on](#cli-mco-schedule-on) — Turn a schedule on.
  - [mco schedule reset](#cli-mco-schedule-reset) — Clear a schedule's run history so a finished loop can run again.
  - [mco schedule run](#cli-mco-schedule-run) — Run the scheduler in the foreground, ticking until interrupted.
  - [mco schedule tick](#cli-mco-schedule-tick) — Run one scheduler pass (what `mco schedule run` does on a timer).
- [mco score](#cli-mco-score) — Run a Score: start, advance and inspect a conductor run.
  - [mco score deliver](#cli-mco-score-deliver) — Bundle code, tests, README, reviews and a DRAFT client message (never sent).
  - [mco score grant-local](#cli-mco-score-grant-local) — Issue a locally auditable, interactive Score grant on Windows SQLite only.
  - [mco score intake](#cli-mco-score-intake) — Draft a SCOPE from a client brief (with guardrail flags), or `approve` a clean scope into a Score.
  - [mco score list](#cli-mco-score-list) — Runs in this conductor database.
  - [mco score scaffold](#cli-mco-score-scaffold) — Turn a plain-language brief into a validated Score with build, review, and two fix attempts per step.
  - [mco score start](#cli-mco-score-start) — Initialize a run. Safe to repeat: identical policy and identities resume it.
  - [mco score status](#cli-mco-score-status) — Where the run actually is: per task, per phase, with the last events.
  - [mco score tick](#cli-mco-score-tick) — Advance the run: plan what can start, dispatch it, accept finished work.
- [mco send](#cli-mco-send) — Drop a job into an agent's dropbox from the terminal.
- [mco serve](#cli-mco-serve) — Start the BitCadence FastAPI WebSocket/REST API Server.
- [mco service](#cli-mco-service) — Run BitCadence processes as boot-persistent OS services.
  - [mco service install](#cli-mco-service-install) — Install the gateway as an OS service that starts on boot/login.
  - [mco service install-scheduler](#cli-mco-service-install-scheduler) — Install the schedule/loop scheduler as a boot-persistent OS service.
  - [mco service install-waker](#cli-mco-service-install-waker) — Install a self-restarting waker service for one role/instance.
  - [mco service logs](#cli-mco-service-logs) — Tail a service log file.
  - [mco service restart](#cli-mco-service-restart) — Restart the boot-persistent gateway service.
  - [mco service status](#cli-mco-service-status) — Show installed BitCadence services, or one selected service.
  - [mco service uninstall](#cli-mco-service-uninstall) — Remove the boot-persistent service (does not stop a running gateway).
- [mco settings](#cli-mco-settings) — View or change gateway settings. With nothing else, a plain summary.
- [mco setup](#cli-mco-setup) — Configure BitCadence - a guided walkthrough or a jump-anywhere settings menu.
- [mco start](#cli-mco-start) — Start BitCadence quietly in the background (the pair of 'mco stop').
- [mco status](#cli-mco-status) — Plain summary: what needs you, what's running, any problems.
- [mco stop](#cli-mco-stop) — Stop a running BitCadence gateway (by port).
- [mco sync](#cli-mco-sync) — Pull open platform objects (incidents/problems) onto the job board.
- [mco tail](#cli-mco-tail) — Live-tail a filtered mailbox feed from the gateway broadcast socket.
- [mco tray](#cli-mco-tray) — Status light and a door into the console (Windows tray / macOS menu bar / Linux AppIndicator).
- [mco unarchive](#cli-mco-unarchive) — Undo archive.
- [mco upgrade](#cli-mco-upgrade) — Apply schema migrations to the configured backend.
- [mco wake](#cli-mco-wake) — Wake a local worker command when this agent's inbox has pending jobs.
- [mco watch](#cli-mco-watch) — Live-tail job events from the gateway's broadcast feed (Ctrl-C to stop).
- [mco workflow](#cli-mco-workflow) — Submit a declarative YAML workflow (DAG of jobs) to the Job Board.

## Shared recovery

For gateway/credential failures, inspect `bitcadence status --details` and `bitcadence doctor`; check the configured gateway and the exact caller identity. An authorization failure needs an authorized operator, not a different worker's token. Read the specific diagnostic before using an effectful repair/restart. For continuous foreground commands use Ctrl+C to stop observing/driving that process.

<a id="cli-mco"></a>
## `mco`

Root menu and global options. Both installed console entry points (`mco`, `bitcadence`) call `mco.cli:main`.

Safe example: `bitcadence --no-menu`. Expected result: print root help, exit 0.

Implementation: [src/mco/cli.py:73](../../src/mco/cli.py#L73).

| Argument / option | Type and requirement | Default | Meaning / constraints |
|---|---|---|---|
| <a id="cli-mco-param-version"></a>`--version` / `-V` | BOOLEAN; flag; optional | `false` | Show the version and exit. |
| <a id="cli-mco-param-no-menu"></a>`--no-menu` | BOOLEAN; flag; optional | `false` | Print help instead of opening the interactive menu. |
| <a id="cli-mco-param-debug"></a>`--debug` | BOOLEAN; flag; optional | `false` | Show full error details instead of plain-English messages. |
| <a id="cli-mco-param-install-completion"></a>`--install-completion` | BOOLEAN; flag; optional | not set | Install completion for the current shell. |
| <a id="cli-mco-param-show-completion"></a>`--show-completion` | BOOLEAN; flag; optional | not set | Show completion for the current shell, to copy it or customize the installation. |
| <a id="cli-mco-param-help"></a>`--help` | BOOLEAN; flag; optional | not set | Show this message and exit. |

<a id="cli-mco-advanced"></a>
## `mco advanced`

Run any original command, e.g. `bitcadence advanced audit <job-id>`.

Usage example: `bitcadence advanced status --details`

**Expected result:** Forwards all following tokens to the original mco command tree. Every documented path remains directly available under either executable.

**If it fails / before changing state:** With no following command, prints an example and exits 1. This is a forwarding command, not a separate restricted permission mode. It intentionally has no own --help; use the forwarded command's help.

Implementation: [src/mco/cli.py:3454](../../src/mco/cli.py#L3454). Global/group options: [mco](#cli-mco).

All remaining tokens are forwarded, including otherwise unknown options. `bitcadence advanced status --help` displays the forwarded status help. No separate advanced-only option list exists.

| Argument / option | Type and requirement | Default | Meaning / constraints |
|---|---|---|---|

<a id="cli-mco-agents"></a>
## `mco agents`

List all registered agents and their current online presence status.

Usage example: `bitcadence agents`

**Expected result:** Shows registered agent identity, role and presence. Presence alone does not prove that a worker can claim or complete a job.

**If it fails / before changing state:** If the gateway cannot be reached, inspect status and doctor. Check the exact instance's credentials when presence and actual job execution disagree.

Implementation: [src/mco/cli.py:2328](../../src/mco/cli.py#L2328). Global/group options: [mco](#cli-mco).

| Argument / option | Type and requirement | Default | Meaning / constraints |
|---|---|---|---|
| <a id="cli-mco-agents-param-help"></a>`--help` | BOOLEAN; flag; optional | not set | Show this message and exit. |

<a id="cli-mco-approve"></a>
## `mco approve`

Approve what's waiting for you, one at a time.

Usage example: `bitcadence approve`

**Expected result:** Shows waiting jobs one at a time and asks before approval; a supplied selector can be a waiting-list number, name or ID.

**If it fails / before changing state:** Approval releases work for execution. Use the displayed job details to resolve ambiguous names; --yes removes the question. The caller still needs approval authority.

Implementation: [src/mco/cli.py:2640](../../src/mco/cli.py#L2640). Global/group options: [mco](#cli-mco).

| Argument / option | Type and requirement | Default | Meaning / constraints |
|---|---|---|---|
| <a id="cli-mco-approve-param-job-id"></a>`JOB_ID` (positional) | TEXT; optional | `""` (empty) | Job to approve: its name, number in the waiting list, or ID. Leave out to go through what's waiting. |
| <a id="cli-mco-approve-param-yes"></a>`--yes` / `-y` | BOOLEAN; flag; optional | `false` | Approve without asking. |
| <a id="cli-mco-approve-param-help"></a>`--help` | BOOLEAN; flag; optional | not set | Show this message and exit. |

<a id="cli-mco-archive"></a>
## `mco archive`

Archive a completed/failed/rejected/cancelled job (reversible).

Usage example: `bitcadence archive JOB_ID`

**Expected result:** Hides a terminal job from the default board without deleting its history.

**If it fails / before changing state:** Use a completed, failed, rejected or cancelled job ID. Undo with unarchive; an active job must reach a terminal state first.

Implementation: [src/mco/cli.py:2695](../../src/mco/cli.py#L2695). Global/group options: [mco](#cli-mco).

| Argument / option | Type and requirement | Default | Meaning / constraints |
|---|---|---|---|
| <a id="cli-mco-archive-param-job-id"></a>`JOB_ID` (positional) | TEXT; required | required | Terminal job ID to hide from the default board view. |
| <a id="cli-mco-archive-param-help"></a>`--help` | BOOLEAN; flag; optional | not set | Show this message and exit. |

<a id="cli-mco-ask"></a>
## `mco ask`

Say what you want done. You see the plan and OK it before anything runs.

Usage example: `bitcadence ask "Review the install instructions"`

**Expected result:** Builds a visible plan and asks before submitting the selected steps. The first step still waits for a separate approval afterward; run approve when ready. --remove can omit more than one numbered step.

**If it fails / before changing state:** --yes skips plan confirmation, not the first-step approval gate. Exception: --file submits the YAML workflow immediately through the workflow command path; it does not preview or ask. Preview a saved workflow with workflow --dry-run.

Implementation: [src/mco/cli.py:3362](../../src/mco/cli.py#L3362). Global/group options: [mco](#cli-mco).

| Argument / option | Type and requirement | Default | Meaning / constraints |
|---|---|---|---|
| <a id="cli-mco-ask-param-request"></a>`REQUEST` (positional) | TEXT; optional | `""` (empty) | What you'd like done, in plain words. |
| <a id="cli-mco-ask-param-file"></a>`--file` | TEXT; optional | not set | Load a workflow YAML file instead. |
| <a id="cli-mco-ask-param-yes"></a>`--yes` / `-y` | BOOLEAN; flag; optional | `false` | Start without asking. |
| <a id="cli-mco-ask-param-remove"></a>`--remove` | INTEGER; optional; repeatable | `[]` | Leave out step N of the plan (repeat for more). |
| <a id="cli-mco-ask-param-ask-me"></a>`--ask-me` | BOOLEAN; flag; optional | `false` | Always ask me at the end. |
| <a id="cli-mco-ask-param-repeat"></a>`--repeat` | TEXT; optional | `""` (empty) | Make it repeat, e.g. "every Friday at 9 AM". |
| <a id="cli-mco-ask-param-help"></a>`--help` | BOOLEAN; flag; optional | not set | Show this message and exit. |

<a id="cli-mco-audit"></a>
## `mco audit`

Print a job's tamper-evident audit trail (oldest event first).

Usage example: `bitcadence audit JOB_ID --verify`

**Expected result:** Without verification flags, prints events oldest first from the gateway. --verify checks the stored hash chain directly; --checkpoint also enables verification against the saved signed checkpoint.

**If it fails / before changing state:** A broken chain exits 1 and identifies the first broken event. Preserve the evidence. Verification needs the configured data plane even when the gateway is running; an empty event list alone does not establish integrity.

Implementation: [src/mco/cli.py:2560](../../src/mco/cli.py#L2560). Global/group options: [mco](#cli-mco).

| Argument / option | Type and requirement | Default | Meaning / constraints |
|---|---|---|---|
| <a id="cli-mco-audit-param-job-id"></a>`JOB_ID` (positional) | TEXT; required | required | Job ID to inspect. |
| <a id="cli-mco-audit-param-verify"></a>`--verify` | BOOLEAN; flag; optional | `false` | Walk the hash chain and report OK or the first broken link. |
| <a id="cli-mco-audit-param-checkpoint"></a>`--checkpoint` | PATH; optional | not set | Verify against a previously exported signed checkpoint. Existing path is checked for readability. |
| <a id="cli-mco-audit-param-help"></a>`--help` | BOOLEAN; flag; optional | not set | Show this message and exit. |

<a id="cli-mco-audit-checkpoint"></a>
## `mco audit-checkpoint`

Export a signed checkpoint. Keep it outside the database/backup volume.

Usage example: `bitcadence audit-checkpoint JOB_ID checkpoint.json`

**Expected result:** Drains pending audit events and writes a signed checkpoint to a new file.

**If it fails / before changing state:** The file is opened exclusively: an existing output causes failure. Choose a new file and retain it outside the database and backup volume so a restored database can be checked independently.

Implementation: [src/mco/cli.py:2602](../../src/mco/cli.py#L2602). Global/group options: [mco](#cli-mco).

| Argument / option | Type and requirement | Default | Meaning / constraints |
|---|---|---|---|
| <a id="cli-mco-audit-checkpoint-param-job-id"></a>`JOB_ID` (positional) | TEXT; required | required | Job whose stored audit chain will be checkpointed. |
| <a id="cli-mco-audit-checkpoint-param-output"></a>`OUTPUT` (positional) | PATH; required | required | New checkpoint file path; an existing file is refused. Existing path is checked for readability. |
| <a id="cli-mco-audit-checkpoint-param-help"></a>`--help` | BOOLEAN; flag; optional | not set | Show this message and exit. |

<a id="cli-mco-cancel"></a>
## `mco cancel`

Call off a job that hasn't finished yet (approver-role token).

Usage example: `bitcadence cancel JOB_ID --reason "Work is no longer needed"`

**Expected result:** Calls off a non-terminal job and prints its new status.

**If it fails / before changing state:** Requires an approver-role credential. If the job has finished, inspect its audit/history rather than trying to cancel it again.

Implementation: [src/mco/cli.py:2681](../../src/mco/cli.py#L2681). Global/group options: [mco](#cli-mco).

| Argument / option | Type and requirement | Default | Meaning / constraints |
|---|---|---|---|
| <a id="cli-mco-cancel-param-job-id"></a>`JOB_ID` (positional) | TEXT; required | required | Non-terminal job ID to call off. |
| <a id="cli-mco-cancel-param-reason"></a>`--reason` | TEXT; optional | `""` (empty) | Why the job was cancelled. |
| <a id="cli-mco-cancel-param-help"></a>`--help` | BOOLEAN; flag; optional | not set | Show this message and exit. |

<a id="cli-mco-cio"></a>
## `mco cio`

Deterministic policy checks backing ops/cio-sidecar-brief.md.

Safe example: `bitcadence cio --help`. Expected result: list the following subcommands and group options. Select a child command for its operation and recovery instructions.

Subcommands: [check](#cli-mco-cio-check).

Global/group options: [mco](#cli-mco).

| Argument / option | Type and requirement | Default | Meaning / constraints |
|---|---|---|---|
| <a id="cli-mco-cio-param-help"></a>`--help` | BOOLEAN; flag; optional | not set | Show this message and exit. |

<a id="cli-mco-cio-check"></a>
## `mco cio check`

Check one proposal against the brief's mechanical rules and print a verdict.

Usage example: `bitcadence cio check --decider reviewer --proposed-by builder --project demo --spend-cents 0`

**Expected result:** Prints a JSON verdict and reasons from deterministic policy. Verdict is approve, reject or escalate.

**If it fails / before changing state:** Exit 0 does not mean approval: read the verdict field. This check is advisory to the decision process and does not issue grants, spend money or approve a job.

Implementation: [src/mco/cli.py:1504](../../src/mco/cli.py#L1504). Global/group options: [mco](#cli-mco), [mco cio](#cli-mco-cio).

| Argument / option | Type and requirement | Default | Meaning / constraints |
|---|---|---|---|
| <a id="cli-mco-cio-check-param-decider"></a>`--decider` | TEXT; required | required | Identity about to render the verdict (e.g. claude-cio). |
| <a id="cli-mco-cio-check-param-project"></a>`--project` | TEXT; optional | `""` (empty) | Project the spend/plan is against. |
| <a id="cli-mco-cio-check-param-spend-cents"></a>`--spend-cents` | INTEGER; optional | `0` | Proposed spend, in cents. |
| <a id="cli-mco-cio-check-param-proposed-by"></a>`--proposed-by` | TEXT; optional | `""` (empty) | Identity that authored the proposal. |
| <a id="cli-mco-cio-check-param-category"></a>`--category` | TEXT; optional; repeatable | `[]` | Repeatable; a policy category the proposal touches. |
| <a id="cli-mco-cio-check-param-help"></a>`--help` | BOOLEAN; flag; optional | not set | Show this message and exit. |

<a id="cli-mco-connect"></a>
## `mco connect`

Connect an AI app to BitCadence.

Usage example: `bitcadence connect claude --test`

**Expected result:** With --test, checks the app's integration. Without --test, configures the chosen AI app, asking before settings changes; blank target offers a pick list.

**If it fails / before changing state:** Supported target names come from the option help. --disconnect removes the integration, while --yes suppresses questions. Back up any custom app settings before intentionally replacing them.

Implementation: [src/mco/cli.py:3428](../../src/mco/cli.py#L3428). Global/group options: [mco](#cli-mco).

| Argument / option | Type and requirement | Default | Meaning / constraints |
|---|---|---|---|
| <a id="cli-mco-connect-param-target"></a>`TARGET` (positional) | TEXT; optional | `""` (empty) | Which AI: claude, codex, gemini, antigravity, cursor or other. Leave out for a pick list. |
| <a id="cli-mco-connect-param-yes"></a>`--yes` / `-y` | BOOLEAN; flag; optional | `false` | Don't ask before changing its settings. |
| <a id="cli-mco-connect-param-disconnect"></a>`--disconnect` | BOOLEAN; flag; optional | `false` | Take BitCadence back out of that app. |
| <a id="cli-mco-connect-param-test"></a>`--test` | BOOLEAN; flag; optional | `false` | Check that the app is set up. |
| <a id="cli-mco-connect-param-help"></a>`--help` | BOOLEAN; flag; optional | not set | Show this message and exit. |

<a id="cli-mco-connectors"></a>
## `mco connectors`

List configured enterprise connectors and their health (via the gateway).

Usage example: `bitcadence connectors`

**Expected result:** Lists configured connector names, health and available action names.

**If it fails / before changing state:** An empty result prints configuration guidance for ServiceNow/Dynatrace. Configure endpoint and credentials, then restart the gateway; use the displayed names for sync/platform.

Implementation: [src/mco/cli.py:3290](../../src/mco/cli.py#L3290). Global/group options: [mco](#cli-mco).

| Argument / option | Type and requirement | Default | Meaning / constraints |
|---|---|---|---|
| <a id="cli-mco-connectors-param-help"></a>`--help` | BOOLEAN; flag; optional | not set | Show this message and exit. |

<a id="cli-mco-deregister"></a>
## `mco deregister`

Remove an agent registration. Its token stops working immediately.

Usage example: `bitcadence deregister INSTANCE_ID`

**Expected result:** Asks before deleting the registration; its token immediately stops authenticating.

**If it fails / before changing state:** Removal cannot be undone. Cancel the prompt to leave it unchanged; use helpers add/register to create a new identity when required. --yes skips this confirmation.

Implementation: [src/mco/cli.py:3051](../../src/mco/cli.py#L3051). Global/group options: [mco](#cli-mco).

| Argument / option | Type and requirement | Default | Meaning / constraints |
|---|---|---|---|
| <a id="cli-mco-deregister-param-instance-id"></a>`INSTANCE_ID` (positional) | TEXT; required | required | Instance ID of the agent to remove. |
| <a id="cli-mco-deregister-param-yes"></a>`--yes` / `-y` | BOOLEAN; flag; optional | `false` | Skip the confirmation prompt. |
| <a id="cli-mco-deregister-param-help"></a>`--help` | BOOLEAN; flag; optional | not set | Show this message and exit. |

<a id="cli-mco-doctor"></a>
## `mco doctor`

Diagnose an install end to end: Python, config, secret store, database, gateway, agents, vendor CLIs. Exit code 1 if anything is broken.

Usage example: `bitcadence doctor`

**Expected result:** Checks Python, config, secret store, database, gateway, registrations and vendor CLIs. Any broken check produces exit 1.

**If it fails / before changing state:** Follow the failing component's message. A passing diagnostic is installation evidence; verify an actual job before declaring worker execution healthy.

Implementation: [src/mco/cli.py:2047](../../src/mco/cli.py#L2047). Global/group options: [mco](#cli-mco).

| Argument / option | Type and requirement | Default | Meaning / constraints |
|---|---|---|---|
| <a id="cli-mco-doctor-param-port"></a>`--port` | INTEGER; optional | `18789` | Gateway port to probe. |
| <a id="cli-mco-doctor-param-help"></a>`--help` | BOOLEAN; flag; optional | not set | Show this message and exit. |

<a id="cli-mco-duplicates"></a>
## `mco duplicates`

List other jobs that look like the same work as this one.

Usage example: `bitcadence duplicates JOB_ID`

**Expected result:** Lists potential look-alike or linked jobs with status and relationship; an empty list is reported explicitly.

**If it fails / before changing state:** This inspects candidates and does not merge or delete them. Check audit details before deciding which job to retain.

Implementation: [src/mco/cli.py:2717](../../src/mco/cli.py#L2717). Global/group options: [mco](#cli-mco).

| Argument / option | Type and requirement | Default | Meaning / constraints |
|---|---|---|---|
| <a id="cli-mco-duplicates-param-job-id"></a>`JOB_ID` (positional) | TEXT; required | required | Job ID to check for look-alike or linked jobs. |
| <a id="cli-mco-duplicates-param-help"></a>`--help` | BOOLEAN; flag; optional | not set | Show this message and exit. |

<a id="cli-mco-edition"></a>
## `mco edition`

Show the active edition (community/team/enterprise) and feature matrix.

Usage example: `bitcadence edition`

**Expected result:** Prints the active community/team/enterprise edition and feature matrix.

**If it fails / before changing state:** Use the displayed capabilities to explain unavailable features; this command does not change licensing or upgrade the installation.

Implementation: [src/mco/cli.py:2307](../../src/mco/cli.py#L2307). Global/group options: [mco](#cli-mco).

| Argument / option | Type and requirement | Default | Meaning / constraints |
|---|---|---|---|
| <a id="cli-mco-edition-param-help"></a>`--help` | BOOLEAN; flag; optional | not set | Show this message and exit. |

<a id="cli-mco-exchange"></a>
## `mco exchange`

Drumline Agent Exchange: non-authoritative agent discussion.

Safe example: `bitcadence exchange --help`. Expected result: list the following subcommands and group options. Select a child command for its operation and recovery instructions.

Subcommands: [list](#cli-mco-exchange-list), [post](#cli-mco-exchange-post), [promote](#cli-mco-exchange-promote).

Global/group options: [mco](#cli-mco).

| Argument / option | Type and requirement | Default | Meaning / constraints |
|---|---|---|---|
| <a id="cli-mco-exchange-param-help"></a>`--help` | BOOLEAN; flag; optional | not set | Show this message and exit. |

<a id="cli-mco-exchange-list"></a>
## `mco exchange list`

List Agent Exchange messages (needs --job or --thread).

Usage example: `bitcadence exchange list --job JOB_ID --limit 20`

**Expected result:** Reads Agent Exchange messages attached to a job or thread and prints a table of IDs, kinds, authors and message excerpts.

**If it fails / before changing state:** Supply --job or --thread. The gateway must enable MCO_AGENT_EXCHANGE=true and the caller needs the appropriate access. These messages are discussion, not approval.

Implementation: [src/mco/cli.py:2860](../../src/mco/cli.py#L2860). Global/group options: [mco](#cli-mco), [mco exchange](#cli-mco-exchange).

| Argument / option | Type and requirement | Default | Meaning / constraints |
|---|---|---|---|
| <a id="cli-mco-exchange-list-param-job"></a>`--job` | TEXT; optional | `""` (empty) | Filter by job id. |
| <a id="cli-mco-exchange-list-param-thread"></a>`--thread` | TEXT; optional | `""` (empty) | Filter by thread id. |
| <a id="cli-mco-exchange-list-param-kind"></a>`--kind` | TEXT; optional | `""` (empty) | Filter by kind. |
| <a id="cli-mco-exchange-list-param-limit"></a>`--limit` | INTEGER; optional | `20` | Max messages (cap 100). |
| <a id="cli-mco-exchange-list-param-help"></a>`--help` | BOOLEAN; flag; optional | not set | Show this message and exit. |

<a id="cli-mco-exchange-post"></a>
## `mco exchange post`

Post one message to the Agent Exchange.

Usage example: `bitcadence exchange post question "Which checks should we run?" --job JOB_ID`

**Expected result:** Adds a non-authoritative discussion message; reply/resolution/supersession links are explicit options.

**If it fails / before changing state:** Use a valid kind and real message/job IDs. Enable Agent Exchange at the gateway. Posting a decision message does not itself promote it into canonical context or authorize effects.

Implementation: [src/mco/cli.py:2838](../../src/mco/cli.py#L2838). Global/group options: [mco](#cli-mco), [mco exchange](#cli-mco-exchange).

| Argument / option | Type and requirement | Default | Meaning / constraints |
|---|---|---|---|
| <a id="cli-mco-exchange-post-param-kind"></a>`KIND` (positional) | TEXT; required | required | question, proposal, blocker, reply, decision, handoff, resolution, supersession. |
| <a id="cli-mco-exchange-post-param-body"></a>`BODY` (positional) | TEXT; required | required | Message text. Discussion is reference, not instructions or approval. |
| <a id="cli-mco-exchange-post-param-job"></a>`--job` | TEXT; optional | `""` (empty) | Job id to attach the thread to. |
| <a id="cli-mco-exchange-post-param-reply-to"></a>`--reply-to` | TEXT; optional | `""` (empty) | Exchange id being replied to. |
| <a id="cli-mco-exchange-post-param-resolves"></a>`--resolves` | TEXT; optional | `""` (empty) | Exchange id a resolution closes. |
| <a id="cli-mco-exchange-post-param-supersedes"></a>`--supersedes` | TEXT; optional | `""` (empty) | Exchange id a supersession replaces. |
| <a id="cli-mco-exchange-post-param-key"></a>`--key` | TEXT; optional | `""` (empty) | Idempotency key (default: random; reuse it to retry safely). |
| <a id="cli-mco-exchange-post-param-help"></a>`--help` | BOOLEAN; flag; optional | not set | Show this message and exit. |

<a id="cli-mco-exchange-promote"></a>
## `mco exchange promote`

Explicitly promote one exchange into canonical Drumline context (needs context:promote).

Usage example: `bitcadence exchange promote EXCHANGE_ID --to decision`

**Expected result:** Promotes one discussion entry into canonical Drumline context and prints the context ID. The default idempotency key is derived from the message ID and target kind.

**If it fails / before changing state:** Requires context:promote authority. Use a valid target kind and review the message first; this does not grant execution or approval authority.

Implementation: [src/mco/cli.py:2883](../../src/mco/cli.py#L2883). Global/group options: [mco](#cli-mco), [mco exchange](#cli-mco-exchange).

| Argument / option | Type and requirement | Default | Meaning / constraints |
|---|---|---|---|
| <a id="cli-mco-exchange-promote-param-exchange-id"></a>`EXCHANGE_ID` (positional) | TEXT; required | required | Exchange id (a decision or handoff). |
| <a id="cli-mco-exchange-promote-param-target"></a>`--to` | TEXT; optional | `decision` | Context kind: decision, lesson, or handoff. |
| <a id="cli-mco-exchange-promote-param-title"></a>`--title` | TEXT; optional | `""` (empty) | Context title (default: derived from the message). |
| <a id="cli-mco-exchange-promote-param-key"></a>`--key` | TEXT; optional | `""` (empty) | Idempotency key (default: derived from the exchange id). |
| <a id="cli-mco-exchange-promote-param-help"></a>`--help` | BOOLEAN; flag; optional | not set | Show this message and exit. |

<a id="cli-mco-fix"></a>
## `mco fix`

Find what's wrong and offer to repair it.

Usage example: `bitcadence fix`

**Expected result:** Finds problems and offers supported repairs one by one.

**If it fails / before changing state:** Repairs can change the installation. Read each offered repair; --yes accepts all offered repairs without prompting. For a diagnostic-only pass, use doctor/status.

Implementation: [src/mco/cli.py:3379](../../src/mco/cli.py#L3379). Global/group options: [mco](#cli-mco).

| Argument / option | Type and requirement | Default | Meaning / constraints |
|---|---|---|---|
| <a id="cli-mco-fix-param-yes"></a>`--yes` / `-y` | BOOLEAN; flag; optional | `false` | Repair everything without asking. |
| <a id="cli-mco-fix-param-help"></a>`--help` | BOOLEAN; flag; optional | not set | Show this message and exit. |

<a id="cli-mco-fleet"></a>
## `mco fleet`

Apply declarative per-worker service run modes.

Safe example: `bitcadence fleet --help`. Expected result: list the following subcommands and group options. Select a child command for its operation and recovery instructions.

Subcommands: [apply](#cli-mco-fleet-apply), [set](#cli-mco-fleet-set), [status](#cli-mco-fleet-status).

Global/group options: [mco](#cli-mco).

| Argument / option | Type and requirement | Default | Meaning / constraints |
|---|---|---|---|
| <a id="cli-mco-fleet-param-help"></a>`--help` | BOOLEAN; flag; optional | not set | Show this message and exit. |

<a id="cli-mco-fleet-apply"></a>
## `mco fleet apply`

Reconcile OS services to ~/.mco/fleet.toml.

Usage example: `bitcadence fleet apply`

**Expected result:** Reconciles declared worker service modes from ~/.mco/fleet.toml and prints per-worker summaries.

**If it fails / before changing state:** Can install/remove/start service processes. Inspect fleet status and the intended config first. Missing config prints a sample and exits 0; that is not evidence that any worker exists.

Implementation: [src/mco/cli.py:1588](../../src/mco/cli.py#L1588). Global/group options: [mco](#cli-mco), [mco fleet](#cli-mco-fleet).

| Argument / option | Type and requirement | Default | Meaning / constraints |
|---|---|---|---|
| <a id="cli-mco-fleet-apply-param-help"></a>`--help` | BOOLEAN; flag; optional | not set | Show this message and exit. |

<a id="cli-mco-fleet-set"></a>
## `mco fleet set`

Update one worker field in ~/.mco/fleet.toml.

Usage example: `bitcadence fleet set WORKER_NAME mode=waker`

**Expected result:** Changes one declared worker field in ~/.mco/fleet.toml.

**If it fails / before changing state:** The assignment must be KEY=VALUE and the worker must exist. Read fleet status, review the config, then run fleet apply only when ready to reconcile OS services.

Implementation: [src/mco/cli.py:1640](../../src/mco/cli.py#L1640). Global/group options: [mco](#cli-mco), [mco fleet](#cli-mco-fleet).

| Argument / option | Type and requirement | Default | Meaning / constraints |
|---|---|---|---|
| <a id="cli-mco-fleet-set-param-worker"></a>`WORKER` (positional) | TEXT; required | required | Worker table name under [workers]. |
| <a id="cli-mco-fleet-set-param-assignment"></a>`ASSIGNMENT` (positional) | TEXT; required | required | KEY=VALUE assignment, for example mode=waker. |
| <a id="cli-mco-fleet-set-param-help"></a>`--help` | BOOLEAN; flag; optional | not set | Show this message and exit. |

<a id="cli-mco-fleet-status"></a>
## `mco fleet status`

Show configured workers and their installed/running state.

Usage example: `bitcadence fleet status`

**Expected result:** Shows each configured worker's role, instance, mode, installed/running service state and service name.

**If it fails / before changing state:** Missing config prints a sample and exits 0. An empty fleet is reported explicitly. Running service state does not prove a job lease or completion.

Implementation: [src/mco/cli.py:1607](../../src/mco/cli.py#L1607). Global/group options: [mco](#cli-mco), [mco fleet](#cli-mco-fleet).

| Argument / option | Type and requirement | Default | Meaning / constraints |
|---|---|---|---|
| <a id="cli-mco-fleet-status-param-help"></a>`--help` | BOOLEAN; flag; optional | not set | Show this message and exit. |

<a id="cli-mco-gui"></a>
## `mco gui`

Open the BitCadence console in your browser.

Usage example: `bitcadence gui --print`

**Expected result:** With --print, prints the console URL; otherwise opens the browser console.

**If it fails / before changing state:** --dashboard selects the legacy dashboard. Hidden --flow is retained for old scripts and opens the console. Browser launch does not prove a signed-in session or healthy worker.

Implementation: [src/mco/cli.py:1100](../../src/mco/cli.py#L1100). Global/group options: [mco](#cli-mco).

| Argument / option | Type and requirement | Default | Meaning / constraints |
|---|---|---|---|
| <a id="cli-mco-gui-param-flow"></a>`--flow` (hidden compatibility) | BOOLEAN; flag; optional | `false` | Old option, kept so scripts keep working: opens the console. |
| <a id="cli-mco-gui-param-dashboard"></a>`--dashboard` | BOOLEAN; flag; optional | `false` | Open the minimal dashboard instead of the full console. |
| <a id="cli-mco-gui-param-print-only"></a>`--print` | BOOLEAN; flag; optional | `false` | Print the URL instead of opening a browser. |
| <a id="cli-mco-gui-param-help"></a>`--help` | BOOLEAN; flag; optional | not set | Show this message and exit. |

<a id="cli-mco-helpers"></a>
## `mco helpers`

Your helpers: who is ready, busy or stuck.

Usage example: `bitcadence helpers --json`

**Expected result:** Lists helpers and whether they are ready, busy or stuck; --json returns machine-readable output.

**If it fails / before changing state:** Put this group's --json before a subcommand. For a full technical presence list use agents. Investigate the actual failing helper rather than assuming all workers need restarting.

Implementation: [src/mco/cli.py:3390](../../src/mco/cli.py#L3390). Global/group options: [mco](#cli-mco).

| Argument / option | Type and requirement | Default | Meaning / constraints |
|---|---|---|---|
| <a id="cli-mco-helpers-param-as-json"></a>`--json` | BOOLEAN; flag; optional | `false` | Machine-readable list. |
| <a id="cli-mco-helpers-param-help"></a>`--help` | BOOLEAN; flag; optional | not set | Show this message and exit. |

<a id="cli-mco-helpers-add"></a>
## `mco helpers add`

Add a helper (same as `mco register`, asking for what it needs).

Usage example: `bitcadence helpers add`

**Expected result:** Prompts for helper name and role, registers it and saves its credential using the helper-registration path.

**If it fails / before changing state:** This creates an identity and writes credentials. The success text reports where they were saved. Do not paste credential output into public logs; use a distinct instance name per helper.

Implementation: [src/mco/cli.py:3401](../../src/mco/cli.py#L3401). Global/group options: [mco](#cli-mco), [mco helpers](#cli-mco-helpers).

| Argument / option | Type and requirement | Default | Meaning / constraints |
|---|---|---|---|
| <a id="cli-mco-helpers-add-param-name"></a>`--name` | TEXT; required | required (prompted if omitted) | New helper's name; prompted if omitted. Prompts if omitted: What should this helper be called?. |
| <a id="cli-mco-helpers-add-param-role"></a>`--role` | TEXT; required | required (prompted if omitted) | New helper's role; prompted if omitted. Prompts if omitted: What is it good at (its role, e.g. codex)?. |
| <a id="cli-mco-helpers-add-param-help"></a>`--help` | BOOLEAN; flag; optional | not set | Show this message and exit. |

<a id="cli-mco-helpers-fix"></a>
## `mco helpers fix`

Find a locked notes file or a second copy of a helper, and offer to fix it.

Usage example: `bitcadence helpers fix`

**Expected result:** Finds locked notes files or second copies of helpers and offers repairs.

**If it fails / before changing state:** Review the offered repair before answering; --yes skips questions. This is a focused helper repair, whereas fix covers broader problems.

Implementation: [src/mco/cli.py:3421](../../src/mco/cli.py#L3421). Global/group options: [mco](#cli-mco), [mco helpers](#cli-mco-helpers).

| Argument / option | Type and requirement | Default | Meaning / constraints |
|---|---|---|---|
| <a id="cli-mco-helpers-fix-param-yes"></a>`--yes` / `-y` | BOOLEAN; flag; optional | `false` | Repair without asking. |
| <a id="cli-mco-helpers-fix-param-help"></a>`--help` | BOOLEAN; flag; optional | not set | Show this message and exit. |

<a id="cli-mco-jev"></a>
## `mco jev`

Ask Jev bounded, annotation-only questions. Never authorizes an effect.

Safe example: `bitcadence jev --help`. Expected result: list the following subcommands and group options. Select a child command for its operation and recovery instructions.

Subcommands: [route-model](#cli-mco-jev-route-model).

Global/group options: [mco](#cli-mco).

| Argument / option | Type and requirement | Default | Meaning / constraints |
|---|---|---|---|
| <a id="cli-mco-jev-param-help"></a>`--help` | BOOLEAN; flag; optional | not set | Show this message and exit. |

<a id="cli-mco-jev-route-model"></a>
## `mco jev route-model`

Suggest a Claude Code model tier (haiku/sonnet/opus) for a task.

Usage example: `bitcadence jev route-model --task "Review a documentation change"`

**Expected result:** Prints JSON with a suggested Claude Code tier and routing outcome. Does not switch sessions or spawn a worker.

**If it fails / before changing state:** Disabled/unconfigured/network/timeout cases also exit 0. Read outcome, tier and reason; deterministic-tier supplies the advisory fallback (default sonnet).

Implementation: [src/mco/cli.py:1534](../../src/mco/cli.py#L1534). Global/group options: [mco](#cli-mco), [mco jev](#cli-mco-jev).

| Argument / option | Type and requirement | Default | Meaning / constraints |
|---|---|---|---|
| <a id="cli-mco-jev-route-model-param-task"></a>`--task` | TEXT; required | required | Short description of the upcoming task. |
| <a id="cli-mco-jev-route-model-param-context"></a>`--context` | TEXT; optional | `""` (empty) | Optional recent-conversation excerpt for scope. |
| <a id="cli-mco-jev-route-model-param-deterministic-tier"></a>`--deterministic-tier` | TEXT; optional | `sonnet` | Tier used if Jev is disabled, unavailable, or times out. |
| <a id="cli-mco-jev-route-model-param-help"></a>`--help` | BOOLEAN; flag; optional | not set | Show this message and exit. |

<a id="cli-mco-jobs"></a>
## `mco jobs`

BitCadence Job Board: score client jobs for fit, value, and risk.

Safe example: `bitcadence jobs --help`. Expected result: list the following subcommands and group options. Select a child command for its operation and recovery instructions.

Subcommands: [rank](#cli-mco-jobs-rank).

Global/group options: [mco](#cli-mco).

| Argument / option | Type and requirement | Default | Meaning / constraints |
|---|---|---|---|
| <a id="cli-mco-jobs-param-help"></a>`--help` | BOOLEAN; flag; optional | not set | Show this message and exit. |

<a id="cli-mco-jobs-rank"></a>
## `mco jobs rank`

Score every available client job for fit, value and risk using Jev and hard filters.

Usage example: `bitcadence jobs rank postings.json --top 10`

**Expected result:** Loads JSON/CSV postings and prints ranked fit/value/safety/reasons/URLs. --out writes full ranked records and audit receipts.

**If it fails / before changing state:** With --source upwork, needs configured Upwork OAuth and can use --query; without that source a postings file is required. Jev ranking can use its configured provider or deterministic fallback. Ranking does not apply to a job or contact a client.

Implementation: [src/mco/jobs/cli.py:79](../../src/mco/jobs/cli.py#L79). Global/group options: [mco](#cli-mco), [mco jobs](#cli-mco-jobs).

| Argument / option | Type and requirement | Default | Meaning / constraints |
|---|---|---|---|
| <a id="cli-mco-jobs-rank-param-postings-path"></a>`POSTINGS_PATH` (positional) | PATH; optional | not set | Path to JSON or CSV file containing job postings. Existing path is checked for readability. |
| <a id="cli-mco-jobs-rank-param-top"></a>`--top` / `-n` | INTEGER; optional | `20` | Number of top ranked jobs to display in table. |
| <a id="cli-mco-jobs-rank-param-out"></a>`--out` / `-o` | PATH; optional | not set | Path to output JSON file with full ranked records and audit receipts. Existing path is checked for readability. |
| <a id="cli-mco-jobs-rank-param-source"></a>`--source` / `-s` | TEXT; optional | not set | Job source: 'file', 'upwork', etc. |
| <a id="cli-mco-jobs-rank-param-query"></a>`--query` / `-q` | TEXT; optional | not set | Optional search query filter for marketplace API search. |
| <a id="cli-mco-jobs-rank-param-help"></a>`--help` | BOOLEAN; flag; optional | not set | Show this message and exit. |

<a id="cli-mco-launch"></a>
## `mco launch`

Fire a launcher right now, by name.

Usage example: `bitcadence launch LAUNCHER_NAME`

**Expected result:** Runs a named launcher immediately through the scheduled-launch code path; job launchers print job IDs, while local app/URL launchers act locally and queue nothing.

**If it fails / before changing state:** Uses ~/.mco/schedules.yaml. Missing config or an unknown launcher produces guidance; --approve/--no-approve override the launcher's approval gate. Launching can create real jobs or open local applications even when done as a manual test.

Implementation: [src/mco/cli.py:1153](../../src/mco/cli.py#L1153). Global/group options: [mco](#cli-mco).

| Argument / option | Type and requirement | Default | Meaning / constraints |
|---|---|---|---|
| <a id="cli-mco-launch-param-name"></a>`NAME` (positional) | TEXT; required | required | Launcher name from ~/.mco/schedules.yaml. |
| <a id="cli-mco-launch-param-approve"></a>`--approve` / `--no-approve` | BOOLEAN; flag; optional | not set | Override the launcher's approval gate for this run. |
| <a id="cli-mco-launch-param-help"></a>`--help` | BOOLEAN; flag; optional | not set | Show this message and exit. |

<a id="cli-mco-listen"></a>
## `mco listen`

Spawn the background daemon client that polls and executes Job Board tasks.

Usage example: `bitcadence listen --role codex --instance demo-worker --config-file agent_config.json`

**Expected result:** Starts the polling/execution listener for the selected role and identity in the current process.

**If it fails / before changing state:** This is an executor, not a passive status monitor. Supply the correct agent config and credential. Use the exact identity you intend to claim work; stop with Ctrl+C. A caught critical listener error is printed without an explicit nonzero exit, so inspect the output as well as the process status.

Implementation: [src/mco/cli.py:1876](../../src/mco/cli.py#L1876). Global/group options: [mco](#cli-mco).

| Argument / option | Type and requirement | Default | Meaning / constraints |
|---|---|---|---|
| <a id="cli-mco-listen-param-role"></a>`--role` | TEXT; optional | `codex` | Agent execution role (e.g. codex, script). |
| <a id="cli-mco-listen-param-instance"></a>`--instance` | TEXT; optional | `default_agent` | Unique ID of this client listener instance. |
| <a id="cli-mco-listen-param-config-file"></a>`--config-file` | TEXT; optional | `agent_config.json` | Path to local worker config file. |
| <a id="cli-mco-listen-param-help"></a>`--help` | BOOLEAN; flag; optional | not set | Show this message and exit. |

<a id="cli-mco-mcp"></a>
## `mco mcp`

Run the MCO dropbox as an MCP server: stdio (Claude/Codex/Antigravity) or HTTP (remote agents).

Safe inspection example (prints help only): `bitcadence mcp --help`

**Operation result (when run with the required values):** Starts the MCP server over stdio by default; --http selects the HTTP server with the configured bind host/port.

**If it fails / before changing state:** For stdio, let the AI application's MCP client own the process. Do not send terminal chatter into the MCP protocol stream. HTTP binding is separate from the gateway's default port.

Implementation: [src/mco/cli.py:1857](../../src/mco/cli.py#L1857). Global/group options: [mco](#cli-mco).

| Argument / option | Type and requirement | Default | Meaning / constraints |
|---|---|---|---|
| <a id="cli-mco-mcp-param-http"></a>`--http` | BOOLEAN; flag; optional | `false` | Serve streamable HTTP (bearer = MCO_AGENT_TOKEN) instead of stdio. |
| <a id="cli-mco-mcp-param-host"></a>`--host` | TEXT; optional | `127.0.0.1` | HTTP bind address; use a private address such as a Tailscale IP. |
| <a id="cli-mco-mcp-param-port"></a>`--port` | INTEGER; optional | `18790` | HTTP port. |
| <a id="cli-mco-mcp-param-help"></a>`--help` | BOOLEAN; flag; optional | not set | Show this message and exit. |

<a id="cli-mco-orgs"></a>
## `mco orgs`

List orgs available for registration (Control Panel tenancy dropdown, from the terminal).

Usage example: `bitcadence orgs`

**Expected result:** Lists organizations available for registration, which are in use and whether the caller is a host operator.

**If it fails / before changing state:** If unavailable, check gateway connectivity and the caller's tenancy authority before registering into another organization.

Implementation: [src/mco/cli.py:2981](../../src/mco/cli.py#L2981). Global/group options: [mco](#cli-mco).

| Argument / option | Type and requirement | Default | Meaning / constraints |
|---|---|---|---|
| <a id="cli-mco-orgs-param-help"></a>`--help` | BOOLEAN; flag; optional | not set | Show this message and exit. |

<a id="cli-mco-pause"></a>
## `mco pause`

Pause everything: stop work in progress and hold new work.

Usage example: `bitcadence pause`

**Expected result:** Asks before stopping work in progress and holding new work.

**If it fails / before changing state:** This affects the whole system, not one helper. Read the affected work before confirming; --yes removes the question. Resume deliberately with resume.

Implementation: [src/mco/cli.py:3440](../../src/mco/cli.py#L3440). Global/group options: [mco](#cli-mco).

| Argument / option | Type and requirement | Default | Meaning / constraints |
|---|---|---|---|
| <a id="cli-mco-pause-param-yes"></a>`--yes` / `-y` | BOOLEAN; flag; optional | `false` | Don't ask first. |
| <a id="cli-mco-pause-param-help"></a>`--help` | BOOLEAN; flag; optional | not set | Show this message and exit. |

<a id="cli-mco-platform"></a>
## `mco platform`

Run a connector control action directly (requires an approver-role token).

Safe inspection example (prints help only): `bitcadence platform --help`

**Operation result (when run with the required values):** Runs a named connector control action with a JSON parameter object and prints the returned result.

**If it fails / before changing state:** Use connectors to discover supported action names and confirm required parameters in the connector's documentation. Invalid JSON exits 1. Requires an approver-role credential; this can change the external platform.

Implementation: [src/mco/cli.py:3328](../../src/mco/cli.py#L3328). Global/group options: [mco](#cli-mco).

| Argument / option | Type and requirement | Default | Meaning / constraints |
|---|---|---|---|
| <a id="cli-mco-platform-param-connector"></a>`CONNECTOR` (positional) | TEXT; required | required | Connector name (servicenow, dynatrace). |
| <a id="cli-mco-platform-param-action"></a>`ACTION` (positional) | TEXT; required | required | Action name (see 'mco connectors'). |
| <a id="cli-mco-platform-param-params"></a>`--params` | TEXT; optional | `{}` | JSON parameters for the action. |
| <a id="cli-mco-platform-param-help"></a>`--help` | BOOLEAN; flag; optional | not set | Show this message and exit. |

<a id="cli-mco-reassign"></a>
## `mco reassign`

Clone a failed job onto a new target, link both rows, and archive the old one (approver-role token).

Usage example: `bitcadence reassign JOB_ID --to-role codex --to-instance TARGET_INSTANCE`

**Expected result:** Clones eligible failed/rejected/cancelled work to a new target, links old/new jobs and archives the old one.

**If it fails / before changing state:** Requires an approver-role credential. Inspect the old audit first; --instructions overrides rather than appends to the original instructions. The printed new ID is the job to follow.

Implementation: [src/mco/cli.py:2738](../../src/mco/cli.py#L2738). Global/group options: [mco](#cli-mco).

| Argument / option | Type and requirement | Default | Meaning / constraints |
|---|---|---|---|
| <a id="cli-mco-reassign-param-job-id"></a>`JOB_ID` (positional) | TEXT; required | required | Failed/rejected/cancelled job ID to redo elsewhere. |
| <a id="cli-mco-reassign-param-to-role"></a>`--to-role` | TEXT; required | required | Target agent role for the new job. |
| <a id="cli-mco-reassign-param-to-instance"></a>`--to-instance` | TEXT; optional | `""` (empty) | Target agent instance (optional). |
| <a id="cli-mco-reassign-param-instructions"></a>`--instructions` | TEXT; optional | `""` (empty) | Override instructions (default: reuse the original). |
| <a id="cli-mco-reassign-param-help"></a>`--help` | BOOLEAN; flag; optional | not set | Show this message and exit. |

<a id="cli-mco-recall"></a>
## `mco recall`

Recall the most relevant Drumline shared-context entries.

Usage example: `bitcadence recall "release checklist" --limit 5`

**Expected result:** Prints the most relevant Drumline entries, including kind/title/content/tags/author. Blank query reads recent entries.

**If it fails / before changing state:** --tags is comma-separated; --role biases relevance and does not create authority. No matches is a normal empty result. On connection failure the command points to doctor.

Implementation: [src/mco/cli.py:2760](../../src/mco/cli.py#L2760). Global/group options: [mco](#cli-mco).

| Argument / option | Type and requirement | Default | Meaning / constraints |
|---|---|---|---|
| <a id="cli-mco-recall-param-query"></a>`QUERY` (positional) | TEXT; optional | `""` (empty) | Free-text search over shared context (blank = most recent). |
| <a id="cli-mco-recall-param-tags"></a>`--tags` | TEXT; optional | `""` (empty) | Comma-separated tag filter. |
| <a id="cli-mco-recall-param-limit"></a>`--limit` | INTEGER; optional | `5` | Max entries to return. |
| <a id="cli-mco-recall-param-role"></a>`--role` | TEXT; optional | `""` (empty) | Bias results toward this role (role-affine scoring). |
| <a id="cli-mco-recall-param-help"></a>`--help` | BOOLEAN; flag; optional | not set | Show this message and exit. |

<a id="cli-mco-register"></a>
## `mco register`

Register a new client agent, generating a secure access token.

Safe inspection example (prints help only): `bitcadence register --help`

**Operation result (when run with the required values):** Writes the named client agent registration and generates a token shown once. The implementation upserts by instance ID: reusing a name replaces that registration's token, role and supplied fields.

**If it fails / before changing state:** Use helpers add for the everyday guided path and choose a distinct identity per helper. Unknown organizations/scopes are rejected. On an older database without a scopes column, registration can fall back to role-derived scopes with an explicit migration warning; inspect the output before assuming the requested scope restriction was stored.

Implementation: [src/mco/cli.py:2186](../../src/mco/cli.py#L2186). Global/group options: [mco](#cli-mco).

| Argument / option | Type and requirement | Default | Meaning / constraints |
|---|---|---|---|
| <a id="cli-mco-register-param-name"></a>`--name` | TEXT; required | required | Unique name (instance ID) of the agent. |
| <a id="cli-mco-register-param-role"></a>`--role` | TEXT; required | required | Target role of the agent (e.g. antigravity, codex). |
| <a id="cli-mco-register-param-org"></a>`--org` | TEXT; optional | `default` | Tenant org the agent belongs to (multi-tenant installs). |
| <a id="cli-mco-register-param-scope"></a>`--scope` | TEXT; optional; repeatable | not set | Explicit token scope (repeatable, e.g. --scope jobs:read --scope context:read). Omit for role-derived defaults; 'admin' grants everything. |
| <a id="cli-mco-register-param-help"></a>`--help` | BOOLEAN; flag; optional | not set | Show this message and exit. |

<a id="cli-mco-reject"></a>
## `mco reject`

Reject a job paused at the human-in-the-loop gate (terminal).

Usage example: `bitcadence reject JOB_ID --reason "Requirements need revision"`

**Expected result:** Moves a job waiting for human approval into rejected and prints the resulting status.

**If it fails / before changing state:** Use the correct pending approval ID. Rejection is terminal for that attempt; a later intentional retry can requeue eligible rejected jobs.

Implementation: [src/mco/cli.py:2656](../../src/mco/cli.py#L2656). Global/group options: [mco](#cli-mco).

| Argument / option | Type and requirement | Default | Meaning / constraints |
|---|---|---|---|
| <a id="cli-mco-reject-param-job-id"></a>`JOB_ID` (positional) | TEXT; required | required | Job ID awaiting approval. |
| <a id="cli-mco-reject-param-reason"></a>`--reason` | TEXT; optional | `""` (empty) | Why the job was rejected. |
| <a id="cli-mco-reject-param-help"></a>`--help` | BOOLEAN; flag; optional | not set | Show this message and exit. |

<a id="cli-mco-remember"></a>
## `mco remember`

Append an entry to the Drumline shared context.

Usage example: `bitcadence remember "Demo convention" "Use the agreed release checklist" --kind decision --tags demo`

**Expected result:** Appends canonical Drumline context and prints its entry ID. With only one sentence, the sentence becomes content and a shortened title.

**If it fails / before changing state:** Use fact/decision/lesson/handoff/artifact as described by the CLI. This is an authoritative context write, unlike Agent Exchange discussion; review what you are recording first.

Implementation: [src/mco/cli.py:2803](../../src/mco/cli.py#L2803). Global/group options: [mco](#cli-mco).

| Argument / option | Type and requirement | Default | Meaning / constraints |
|---|---|---|---|
| <a id="cli-mco-remember-param-title"></a>`TITLE` (positional) | TEXT; required | required | What to remember (or a short title when you also give the content). |
| <a id="cli-mco-remember-param-content"></a>`CONTENT` (positional) | TEXT; optional | `""` (empty) | The content to remember (optional: the title alone is enough). |
| <a id="cli-mco-remember-param-kind"></a>`--kind` | TEXT; optional | `fact` | Entry kind: fact, decision, lesson, handoff, or artifact. |
| <a id="cli-mco-remember-param-tags"></a>`--tags` | TEXT; optional | `""` (empty) | Comma-separated tags. |
| <a id="cli-mco-remember-param-help"></a>`--help` | BOOLEAN; flag; optional | not set | Show this message and exit. |

<a id="cli-mco-reset-token"></a>
## `mco reset-token`

Rotate an agent's access token. The old token stops working immediately.

Safe inspection example (prints help only): `bitcadence reset-token --help`

**Operation result (when run with the required values):** Rotates an instance credential, prints it once, and saves it to ~/.mco/tokens/INSTANCE.token by default.

**If it fails / before changing state:** The old token immediately stops working. --no-save leaves local token files untouched. Embedded MCP client configs require a separate update even when the token file is saved; avoid rotation during active work without coordinating the consumer.

Implementation: [src/mco/cli.py:3002](../../src/mco/cli.py#L3002). Global/group options: [mco](#cli-mco).

| Argument / option | Type and requirement | Default | Meaning / constraints |
|---|---|---|---|
| <a id="cli-mco-reset-token-param-instance-id"></a>`INSTANCE_ID` (positional) | TEXT; required | required | Instance ID of the agent to rotate. |
| <a id="cli-mco-reset-token-param-save"></a>`--save` / `--no-save` | BOOLEAN; flag; optional | `true` | Write the new token to ~/.mco/tokens/<instance>.token so this machine's waker picks it up. |
| <a id="cli-mco-reset-token-param-help"></a>`--help` | BOOLEAN; flag; optional | not set | Show this message and exit. |

<a id="cli-mco-restart"></a>
## `mco restart`

Restart the background gateway (stop if running, then start).

Usage example: `bitcadence restart`

**Expected result:** Stops the background gateway if present and starts it again with the selected host/port.

**If it fails / before changing state:** Interrupts gateway availability. For a boot-persistent OS service use service restart. Recheck status and browser access after restart rather than treating process launch as full readiness.

Implementation: [src/mco/cli.py:784](../../src/mco/cli.py#L784). Global/group options: [mco](#cli-mco).

| Argument / option | Type and requirement | Default | Meaning / constraints |
|---|---|---|---|
| <a id="cli-mco-restart-param-host"></a>`--host` | TEXT; optional | `127.0.0.1` | The host to bind to. |
| <a id="cli-mco-restart-param-port"></a>`--port` | INTEGER; optional | `18789` | The port the gateway runs on. |
| <a id="cli-mco-restart-param-help"></a>`--help` | BOOLEAN; flag; optional | not set | Show this message and exit. |

<a id="cli-mco-restore-fence"></a>
## `mco restore-fence`

After a DB restore, pause work and invalidate every pre-restore claim.

Safe inspection example (prints help only): `bitcadence restore-fence --help`

**Operation result (when run with the required values):** After a database restore, halts active jobs, rotates the store incarnation to invalidate old claims and leaves work paused.

**If it fails / before changing state:** Recovery operation: run before reconnecting workers. Review halted jobs, clear Stop work in Settings deliberately, then retry selected work. This is not a general-purpose health check.

Implementation: [src/mco/cli.py:2619](../../src/mco/cli.py#L2619). Global/group options: [mco](#cli-mco).

| Argument / option | Type and requirement | Default | Meaning / constraints |
|---|---|---|---|
| <a id="cli-mco-restore-fence-param-help"></a>`--help` | BOOLEAN; flag; optional | not set | Show this message and exit. |

<a id="cli-mco-resume"></a>
## `mco resume`

Resume after a pause.

Usage example: `bitcadence resume`

**Expected result:** Resumes work after a global pause.

**If it fails / before changing state:** Check the reason for the pause and the waiting work first. Resume can make pending work eligible to execute immediately.

Implementation: [src/mco/cli.py:3447](../../src/mco/cli.py#L3447). Global/group options: [mco](#cli-mco).

| Argument / option | Type and requirement | Default | Meaning / constraints |
|---|---|---|---|
| <a id="cli-mco-resume-param-help"></a>`--help` | BOOLEAN; flag; optional | not set | Show this message and exit. |

<a id="cli-mco-retry"></a>
## `mco retry`

Re-queue a failed or rejected job back to pending (approver-role token).

Usage example: `bitcadence retry JOB_ID`

**Expected result:** Moves eligible failed/rejected work back to pending and prints the new status.

**If it fails / before changing state:** Requires an approver-role credential. Inspect the prior failure before retrying; requeueing without addressing the cause can repeat it.

Implementation: [src/mco/cli.py:2670](../../src/mco/cli.py#L2670). Global/group options: [mco](#cli-mco).

| Argument / option | Type and requirement | Default | Meaning / constraints |
|---|---|---|---|
| <a id="cli-mco-retry-param-job-id"></a>`JOB_ID` (positional) | TEXT; required | required | Failed/rejected job ID to re-queue. |
| <a id="cli-mco-retry-param-help"></a>`--help` | BOOLEAN; flag; optional | not set | Show this message and exit. |

<a id="cli-mco-schedule"></a>
## `mco schedule`

Schedules and loops: what work gets created, and when.

Usage example: `bitcadence schedule`

**Expected result:** Shows the everyday schedule view when no subcommand is supplied.

**If it fails / before changing state:** This group has a real default action. Use schedule list for the technical list or --help to inspect its subcommands.

Implementation: [src/mco/cli.py:809](../../src/mco/cli.py#L809). Global/group options: [mco](#cli-mco).

| Argument / option | Type and requirement | Default | Meaning / constraints |
|---|---|---|---|
| <a id="cli-mco-schedule-param-help"></a>`--help` | BOOLEAN; flag; optional | not set | Show this message and exit. |

<a id="cli-mco-schedule-add"></a>
## `mco schedule add`

Add a schedule by asking what, how often and what time.

Usage example: `bitcadence schedule add`

**Expected result:** Prompts for what should run, how often and when, then offers to save the schedule.

**If it fails / before changing state:** The command is implemented; it is no longer roadmap-only. --what and --when supply answers, and --yes skips saving confirmation. Inspect schedule list after saving.

Implementation: [src/mco/cli.py:841](../../src/mco/cli.py#L841). Global/group options: [mco](#cli-mco), [mco schedule](#cli-mco-schedule).

| Argument / option | Type and requirement | Default | Meaning / constraints |
|---|---|---|---|
| <a id="cli-mco-schedule-add-param-what"></a>`--what` | TEXT; optional | `""` (empty) | What should run (its name). Leave out for a pick list. |
| <a id="cli-mco-schedule-add-param-when"></a>`--when` | TEXT; optional | `""` (empty) | In plain words, e.g. "every weekday at 2 AM". |
| <a id="cli-mco-schedule-add-param-yes"></a>`--yes` / `-y` | BOOLEAN; flag; optional | `false` | Save without asking. |
| <a id="cli-mco-schedule-add-param-help"></a>`--help` | BOOLEAN; flag; optional | not set | Show this message and exit. |

<a id="cli-mco-schedule-disable"></a>
## `mco schedule disable`

Disable a schedule or loop, leaving its definition and history intact.

Usage example: `bitcadence schedule disable SCHEDULE_NAME`

**Expected result:** Sets enabled=false for one schedule/loop while preserving its definition, comments and history.

**If it fails / before changing state:** An unknown name prints known names and exits 1. It prevents future firings; it is not a cancellation command for jobs already created.

Implementation: [src/mco/cli.py:994](../../src/mco/cli.py#L994). Global/group options: [mco](#cli-mco), [mco schedule](#cli-mco-schedule).

| Argument / option | Type and requirement | Default | Meaning / constraints |
|---|---|---|---|
| <a id="cli-mco-schedule-disable-param-name"></a>`NAME` (positional) | TEXT; required | required | Schedule or loop name. |
| <a id="cli-mco-schedule-disable-param-help"></a>`--help` | BOOLEAN; flag; optional | not set | Show this message and exit. |

<a id="cli-mco-schedule-enable"></a>
## `mco schedule enable`

Enable a schedule or loop (without hand-editing YAML).

Usage example: `bitcadence schedule enable SCHEDULE_NAME`

**Expected result:** Sets enabled=true for one schedule/loop while preserving definition and history.

**If it fails / before changing state:** Enabling does not clear sticky completion. If an exhausted loop must run again, inspect and deliberately use schedule reset.

Implementation: [src/mco/cli.py:988](../../src/mco/cli.py#L988). Global/group options: [mco](#cli-mco), [mco schedule](#cli-mco-schedule).

| Argument / option | Type and requirement | Default | Meaning / constraints |
|---|---|---|---|
| <a id="cli-mco-schedule-enable-param-name"></a>`NAME` (positional) | TEXT; required | required | Schedule or loop name. |
| <a id="cli-mco-schedule-enable-param-help"></a>`--help` | BOOLEAN; flag; optional | not set | Show this message and exit. |

<a id="cli-mco-schedule-init"></a>
## `mco schedule init`

Write a starter ~/.mco/schedules.yaml.

Usage example: `bitcadence schedule init`

**Expected result:** Writes a starter ~/.mco/schedules.yaml and tells you to edit it, then list schedules.

**If it fails / before changing state:** An existing file is refused with exit 1. --force overwrites it, so preserve existing definitions before intentionally replacing them.

Implementation: [src/mco/cli.py:866](../../src/mco/cli.py#L866). Global/group options: [mco](#cli-mco), [mco schedule](#cli-mco-schedule).

| Argument / option | Type and requirement | Default | Meaning / constraints |
|---|---|---|---|
| <a id="cli-mco-schedule-init-param-force"></a>`--force` | BOOLEAN; flag; optional | `false` | Overwrite an existing config. |
| <a id="cli-mco-schedule-init-param-help"></a>`--help` | BOOLEAN; flag; optional | not set | Show this message and exit. |

<a id="cli-mco-schedule-list"></a>
## `mco schedule list`

Show every schedule and loop with its next fire time.

Usage example: `bitcadence schedule list`

**Expected result:** Shows name/kind/trigger/launcher/next run/run count/bound. Disabled and exhausted states are shown explicitly; technical next-run timestamps are UTC.

**If it fails / before changing state:** Missing config prints the starter guidance and exits 0. Initialize and edit a config; no listed schedule is not evidence that a scheduler service is running.

Implementation: [src/mco/cli.py:882](../../src/mco/cli.py#L882). Global/group options: [mco](#cli-mco), [mco schedule](#cli-mco-schedule).

| Argument / option | Type and requirement | Default | Meaning / constraints |
|---|---|---|---|
| <a id="cli-mco-schedule-list-param-help"></a>`--help` | BOOLEAN; flag; optional | not set | Show this message and exit. |

<a id="cli-mco-schedule-off"></a>
## `mco schedule off`

Turn a schedule off.

Usage example: `bitcadence schedule off SCHEDULE_NAME`

**Expected result:** Turns one named schedule off through the everyday command path.

**If it fails / before changing state:** Check schedule list to confirm the disabled state. Existing jobs remain separate work items.

Implementation: [src/mco/cli.py:859](../../src/mco/cli.py#L859). Global/group options: [mco](#cli-mco), [mco schedule](#cli-mco-schedule).

| Argument / option | Type and requirement | Default | Meaning / constraints |
|---|---|---|---|
| <a id="cli-mco-schedule-off-param-name"></a>`NAME` (positional) | TEXT; required | required | Which schedule (its name). |
| <a id="cli-mco-schedule-off-param-help"></a>`--help` | BOOLEAN; flag; optional | not set | Show this message and exit. |

<a id="cli-mco-schedule-on"></a>
## `mco schedule on`

Turn a schedule on.

Usage example: `bitcadence schedule on SCHEDULE_NAME`

**Expected result:** Turns one named schedule on through the everyday command path.

**If it fails / before changing state:** Check schedule list for its next run and completion bounds. Re-enabling an exhausted loop does not by itself promise a new firing.

Implementation: [src/mco/cli.py:852](../../src/mco/cli.py#L852). Global/group options: [mco](#cli-mco), [mco schedule](#cli-mco-schedule).

| Argument / option | Type and requirement | Default | Meaning / constraints |
|---|---|---|---|
| <a id="cli-mco-schedule-on-param-name"></a>`NAME` (positional) | TEXT; required | required | Which schedule (its name). |
| <a id="cli-mco-schedule-on-param-help"></a>`--help` | BOOLEAN; flag; optional | not set | Show this message and exit. |

<a id="cli-mco-schedule-reset"></a>
## `mco schedule reset`

Clear a schedule's run history so a finished loop can run again.

Usage example: `bitcadence schedule reset SCHEDULE_NAME`

**Expected result:** Asks to clear the named schedule's run history, allowing a finished loop to run again.

**If it fails / before changing state:** Sticky completion prevents accidental resurrection when a queue refills. --yes skips the question. Unknown names fail; a schedule with no history reports that there is nothing to clear.

Implementation: [src/mco/cli.py:1000](../../src/mco/cli.py#L1000). Global/group options: [mco](#cli-mco), [mco schedule](#cli-mco-schedule).

| Argument / option | Type and requirement | Default | Meaning / constraints |
|---|---|---|---|
| <a id="cli-mco-schedule-reset-param-name"></a>`NAME` (positional) | TEXT; required | required | Schedule or loop name. |
| <a id="cli-mco-schedule-reset-param-yes"></a>`--yes` / `-y` | BOOLEAN; flag; optional | `false` | Skip the confirmation prompt. |
| <a id="cli-mco-schedule-reset-param-help"></a>`--help` | BOOLEAN; flag; optional | not set | Show this message and exit. |

<a id="cli-mco-schedule-run"></a>
## `mco schedule run`

Run the scheduler in the foreground, ticking until interrupted.

Usage example: `bitcadence schedule run --interval 30`

**Expected result:** Runs scheduler passes in the foreground until interrupted.

**If it fails / before changing state:** Ctrl+C stops this foreground scheduler. Use a boot-persistent scheduler service for unattended operation; avoid running duplicate scheduler drivers unintentionally.

Implementation: [src/mco/cli.py:1070](../../src/mco/cli.py#L1070). Global/group options: [mco](#cli-mco), [mco schedule](#cli-mco-schedule).

| Argument / option | Type and requirement | Default | Meaning / constraints |
|---|---|---|---|
| <a id="cli-mco-schedule-run-param-interval"></a>`--interval` | FLOAT; optional | `30.0` | Seconds between scheduler passes. |
| <a id="cli-mco-schedule-run-param-help"></a>`--help` | BOOLEAN; flag; optional | not set | Show this message and exit. |

<a id="cli-mco-schedule-tick"></a>
## `mco schedule tick`

Run one scheduler pass (what `mco schedule run` does on a timer).

Usage example: `bitcadence schedule tick --dry-run`

**Expected result:** Reports what is due without creating jobs when --dry-run is used. Without it, executes one real scheduler pass.

**If it fails / before changing state:** Nothing due is a normal result. Reports distinguish fired/would-fire/skipped-overlap/error. Validate the config before wiring a real tick into cron/Task Scheduler.

Implementation: [src/mco/cli.py:1036](../../src/mco/cli.py#L1036). Global/group options: [mco](#cli-mco), [mco schedule](#cli-mco-schedule).

| Argument / option | Type and requirement | Default | Meaning / constraints |
|---|---|---|---|
| <a id="cli-mco-schedule-tick-param-dry-run"></a>`--dry-run` | BOOLEAN; flag; optional | `false` | Show what would fire without creating jobs. |
| <a id="cli-mco-schedule-tick-param-help"></a>`--help` | BOOLEAN; flag; optional | not set | Show this message and exit. |

<a id="cli-mco-score"></a>
## `mco score`

Run a Score: start, advance and inspect a conductor run.

Safe example: `bitcadence score --help`. Expected result: list the following subcommands and group options. Select a child command for its operation and recovery instructions.

Subcommands: [deliver](#cli-mco-score-deliver), [grant-local](#cli-mco-score-grant-local), [intake](#cli-mco-score-intake), [list](#cli-mco-score-list), [scaffold](#cli-mco-score-scaffold), [start](#cli-mco-score-start), [status](#cli-mco-score-status), [tick](#cli-mco-score-tick).

Global/group options: [mco](#cli-mco).

| Argument / option | Type and requirement | Default | Meaning / constraints |
|---|---|---|---|
| <a id="cli-mco-score-param-help"></a>`--help` | BOOLEAN; flag; optional | not set | Show this message and exit. |

<a id="cli-mco-score-deliver"></a>
## `mco score deliver`

Bundle code, tests, README, reviews and a DRAFT client message (never sent).

Usage example: `bitcadence score deliver CLIENT_WORKTREE --out delivery-bundle`

**Expected result:** Bundles the client code/tests/README/review reports and a draft client message into the output directory.

**If it fails / before changing state:** The message is never sent by this command. Confirm the client worktree and optional evidence directory; inspect the produced bundle before handing it over.

Implementation: [src/mco/cli.py:1462](../../src/mco/cli.py#L1462). Global/group options: [mco](#cli-mco), [mco score](#cli-mco-score).

| Argument / option | Type and requirement | Default | Meaning / constraints |
|---|---|---|---|
| <a id="cli-mco-score-deliver-param-worktree"></a>`WORKTREE` (positional) | PATH; required | required | Worktree of the finished client branch. Existing path is checked for readability. |
| <a id="cli-mco-score-deliver-param-out"></a>`--out` | PATH; required | required | Delivery directory. Existing path is checked for readability. |
| <a id="cli-mco-score-deliver-param-evidence"></a>`--evidence` | PATH; optional | not set | Directory of review reports. Existing path is checked for readability. |
| <a id="cli-mco-score-deliver-param-client"></a>`--client` | TEXT; optional | `""` (empty) | Client label used by the delivery bundler. |
| <a id="cli-mco-score-deliver-param-help"></a>`--help` | BOOLEAN; flag; optional | not set | Show this message and exit. |

<a id="cli-mco-score-grant-local"></a>
## `mco score grant-local`

Issue a locally auditable, interactive Score grant on Windows SQLite only.

Safe inspection example (prints help only): `bitcadence score grant-local --help`

**Operation result (when run with the required values):** Issues a local auditable Score grant only after displaying exact authority and receiving a typed confirmation.

**If it fails / before changing state:** Disabled by default. Requires explicitly enabled MCO_LOCAL_HUMAN_GRANTS, interactive Windows terminal and local SQLite store; bearer credentials cannot satisfy the human check. Score file must exist. The grant binds exact digest/run/actions/resources/environment/expiry/budget; it does not replace repository-write opt-in.

Implementation: [src/mco/cli.py:1295](../../src/mco/cli.py#L1295). Global/group options: [mco](#cli-mco), [mco score](#cli-mco-score).

| Argument / option | Type and requirement | Default | Meaning / constraints |
|---|---|---|---|
| <a id="cli-mco-score-grant-local-param-score-file"></a>`SCORE_FILE` (positional) | PATH; required | required | Score JSON file whose canonical digest will be authorized. Path must already exist. Existing path is checked for readability. |
| <a id="cli-mco-score-grant-local-param-run-id"></a>`--run-id` | TEXT; required | required | Run ID this grant may authorize. |
| <a id="cli-mco-score-grant-local-param-action"></a>`--action` | TEXT; required; repeatable | required | Allowed capability; repeat for more than one. |
| <a id="cli-mco-score-grant-local-param-resource"></a>`--resource` | TEXT; required; repeatable | required | Exact allowed resource; repeat for more than one. |
| <a id="cli-mco-score-grant-local-param-environment"></a>`--environment` | TEXT; optional | `test` | Environment named in the grant authority. |
| <a id="cli-mco-score-grant-local-param-expires-minutes"></a>`--expires-minutes` | INTEGER RANGE; optional | `240` | Grant lifetime in minutes unless expires-hours is supplied. Range: 1 to 18960. |
| <a id="cli-mco-score-grant-local-param-expires-hours"></a>`--expires-hours` | INTEGER RANGE; optional | not set | Grant lifetime in hours (1-316); overrides --expires-minutes. Range: 1 to 316. |
| <a id="cli-mco-score-grant-local-param-budget-cents"></a>`--budget-cents` | INTEGER RANGE; optional | `0` | Maximum budget in cents included in this grant. Range: 0 to unbounded. |
| <a id="cli-mco-score-grant-local-param-help"></a>`--help` | BOOLEAN; flag; optional | not set | Show this message and exit. |

<a id="cli-mco-score-intake"></a>
## `mco score intake`

Draft a SCOPE from a client brief (with guardrail flags), or `approve` a clean scope into a Score.

Usage example: `bitcadence score intake BRIEF.txt --out scope.json`

**Expected result:** Drafts a scope from a brief and reports guardrail flags. The approve mode turns a clean reviewed scope into a Score and also updates that scope file.

**If it fails / before changing state:** Approve mode requires exactly approve SCOPE.json plus --worktree, --branch, --before-sha and --out. Correct flagged scope issues first; drafting a scope is not an execution grant.

Implementation: [src/mco/cli.py:1428](../../src/mco/cli.py#L1428). Global/group options: [mco](#cli-mco), [mco score](#cli-mco-score).

| Argument / option | Type and requirement | Default | Meaning / constraints |
|---|---|---|---|
| <a id="cli-mco-score-intake-param-args"></a>`ARGS` (positional) | TEXT; required; one or more values | required | BRIEF.txt to draft a scope, or `approve SCOPE.json`. |
| <a id="cli-mco-score-intake-param-out"></a>`--out` | PATH; required | required | Where to write the scope (draft) or the Score (approve). Existing path is checked for readability. |
| <a id="cli-mco-score-intake-param-client"></a>`--client` | TEXT; optional | `""` (empty) | Client name (draft). |
| <a id="cli-mco-score-intake-param-worktree"></a>`--worktree` | TEXT; optional | not set | approve: client worktree path. |
| <a id="cli-mco-score-intake-param-branch"></a>`--branch` | TEXT; optional | not set | approve: client branch. |
| <a id="cli-mco-score-intake-param-before-sha"></a>`--before-sha` | TEXT; optional | not set | approve: starting commit. |
| <a id="cli-mco-score-intake-param-help"></a>`--help` | BOOLEAN; flag; optional | not set | Show this message and exit. |

<a id="cli-mco-score-list"></a>
## `mco score list`

Runs in this conductor database.

Usage example: `bitcadence score list`

**Expected result:** Lists run ID, digest, organization and status from the selected conductor database.

**If it fails / before changing state:** No runs is normal for an empty/new database. Use the same --db when listing, starting, ticking and inspecting a run.

Implementation: [src/mco/cli.py:1480](../../src/mco/cli.py#L1480). Global/group options: [mco](#cli-mco), [mco score](#cli-mco-score).

| Argument / option | Type and requirement | Default | Meaning / constraints |
|---|---|---|---|
| <a id="cli-mco-score-list-param-database"></a>`--db` | PATH; optional | `~/.mco/score-runs.db` | Conductor SQLite database to list. Existing path is checked for readability. |
| <a id="cli-mco-score-list-param-help"></a>`--help` | BOOLEAN; flag; optional | not set | Show this message and exit. |

<a id="cli-mco-score-scaffold"></a>
## `mco score scaffold`

Turn a plain-language brief into a validated Score with build, review, and two fix attempts per step.

Safe inspection example (prints help only): `bitcadence score scaffold --help`

**Operation result (when run with the required values):** Reads a JSON spec, builds a Score with build/review/fix phases, validates it and writes the output file plus digest.

**If it fails / before changing state:** The spec must exist and be readable. It needs score_id, objective, steps, worktree_path, target_branch and expected_before_sha (see argument details). Invalid spec/compile failures exit 1. Review the output before starting it.

Implementation: [src/mco/cli.py:1402](../../src/mco/cli.py#L1402). Global/group options: [mco](#cli-mco), [mco score](#cli-mco-score).

| Argument / option | Type and requirement | Default | Meaning / constraints |
|---|---|---|---|
| <a id="cli-mco-score-scaffold-param-spec"></a>`SPEC` (positional) | PATH; required | required | JSON spec: score_id, objective, steps[{id,title,brief}], worktree_path, target_branch, expected_before_sha, and optional builders, third_reviewer, allowed_paths, test_command, constraints. Path must already exist. Existing path is checked for readability. |
| <a id="cli-mco-score-scaffold-param-out"></a>`--out` | PATH; required | required | Where to write the Score document. Existing path is checked for readability. |
| <a id="cli-mco-score-scaffold-param-help"></a>`--help` | BOOLEAN; flag; optional | not set | Show this message and exit. |

<a id="cli-mco-score-start"></a>
## `mco score start`

Initialize a run. Safe to repeat: identical policy and identities resume it.

Usage example: `bitcadence score start score.json --run-id demo-run --target builder=builder-instance`

**Expected result:** Initializes a conductor run; an identical policy and identities can resume the same run ID. Reports the Score ID and a suggested tick command.

**If it fails / before changing state:** Provide role=instance for every required role. Changed policy/identity under a reused run ID can fail; inspect the existing run rather than guessing. This prepares a run; it does not prove tasks executed.

Implementation: [src/mco/cli.py:1222](../../src/mco/cli.py#L1222). Global/group options: [mco](#cli-mco), [mco score](#cli-mco-score).

| Argument / option | Type and requirement | Default | Meaning / constraints |
|---|---|---|---|
| <a id="cli-mco-score-start-param-score-file"></a>`SCORE_FILE` (positional) | TEXT; required | required | Path to the score JSON document. |
| <a id="cli-mco-score-start-param-run-id"></a>`--run-id` | TEXT; required | required | Identity of this run; re-using one resumes it. |
| <a id="cli-mco-score-start-param-target"></a>`--target` | TEXT; optional; repeatable | not set | role=instance for every role in the score. |
| <a id="cli-mco-score-start-param-org"></a>`--org` | TEXT; optional | `default` | Organization bound to the conductor run. |
| <a id="cli-mco-score-start-param-database"></a>`--db` | PATH; optional | `~/.mco/score-runs.db` | Conductor SQLite database used for this run. Existing path is checked for readability. |
| <a id="cli-mco-score-start-param-root"></a>`--artifact-root` | PATH; optional | `~/.mco/score-artifacts` | Directory for conductor task artifacts. Existing path is checked for readability. |
| <a id="cli-mco-score-start-param-live-repository-write"></a>`--live-repository-write` | BOOLEAN; flag; optional | `false` | Enable live repository:write adapter execution. Required on every tick of a run with repository:write tasks, not just start — a human-issued grant (see POST /api/score/grants) must also authorize the run's digest/run_id before any commit actually executes. |
| <a id="cli-mco-score-start-param-help"></a>`--help` | BOOLEAN; flag; optional | not set | Show this message and exit. |

<a id="cli-mco-score-status"></a>
## `mco score status`

Where the run actually is: per task, per phase, with the last events.

Usage example: `bitcadence score status --run-id demo-run`

**Expected result:** Prints actual per-task/per-phase dispatch, recent events, acceptance counts and launch state.

**If it fails / before changing state:** Use the same --db/--artifact-root as the run. A timeout is reported as stopped, not completion. Inspect blocked/failed evidence before deciding to tick again.

Implementation: [src/mco/cli.py:1387](../../src/mco/cli.py#L1387). Global/group options: [mco](#cli-mco), [mco score](#cli-mco-score).

| Argument / option | Type and requirement | Default | Meaning / constraints |
|---|---|---|---|
| <a id="cli-mco-score-status-param-run-id"></a>`--run-id` | TEXT; required | required | Run identity to inspect. |
| <a id="cli-mco-score-status-param-database"></a>`--db` | PATH; optional | `~/.mco/score-runs.db` | Conductor SQLite database containing the run. Existing path is checked for readability. |
| <a id="cli-mco-score-status-param-root"></a>`--artifact-root` | PATH; optional | `~/.mco/score-artifacts` | Artifact directory for the run. Existing path is checked for readability. |
| <a id="cli-mco-score-status-param-help"></a>`--help` | BOOLEAN; flag; optional | not set | Show this message and exit. |

<a id="cli-mco-score-tick"></a>
## `mco score tick`

Advance the run: plan what can start, dispatch it, accept finished work.

Usage example: `bitcadence score tick --run-id demo-run`

**Expected result:** Advances planning/dispatch/acceptance once; --watch repeats until settled or timeout.

**If it fails / before changing state:** Ticking can dispatch real work. --live-repository-write must be present on every tick that needs that adapter; an eligible human grant must also authorize the digest/run. Watch exits 1 for blocked/failed/timed out; inspect score status.

Implementation: [src/mco/cli.py:1354](../../src/mco/cli.py#L1354). Global/group options: [mco](#cli-mco), [mco score](#cli-mco-score).

| Argument / option | Type and requirement | Default | Meaning / constraints |
|---|---|---|---|
| <a id="cli-mco-score-tick-param-run-id"></a>`--run-id` | TEXT; required | required | Run identity to advance; must identify an initialized run. |
| <a id="cli-mco-score-tick-param-watch"></a>`--watch` | BOOLEAN; flag; optional | `false` | Keep ticking until the run settles. |
| <a id="cli-mco-score-tick-param-interval"></a>`--interval` | FLOAT; optional | `5.0` | Seconds between ticks in watch mode. |
| <a id="cli-mco-score-tick-param-timeout"></a>`--timeout` | FLOAT; optional | `900.0` | Maximum watch duration in seconds; timeout is not success. |
| <a id="cli-mco-score-tick-param-database"></a>`--db` | PATH; optional | `~/.mco/score-runs.db` | Conductor SQLite database containing the run. Existing path is checked for readability. |
| <a id="cli-mco-score-tick-param-root"></a>`--artifact-root` | PATH; optional | `~/.mco/score-artifacts` | Artifact directory for the run. Existing path is checked for readability. |
| <a id="cli-mco-score-tick-param-live-repository-write"></a>`--live-repository-write` | BOOLEAN; flag; optional | `false` | Enable live repository:write adapter execution. Required on every tick of a run with repository:write tasks, not just start — a human-issued grant (see POST /api/score/grants) must also authorize the run's digest/run_id before any commit actually executes. |
| <a id="cli-mco-score-tick-param-help"></a>`--help` | BOOLEAN; flag; optional | not set | Show this message and exit. |

<a id="cli-mco-send"></a>
## `mco send`

Drop a job into an agent's dropbox from the terminal.

Usage example: `bitcadence send codex --title "Review documentation" --message "Read and report findings only" --approve`

**Expected result:** Creates a job for a role or exact instance and prints its ID/status. --approve holds it at the approval gate.

**If it fails / before changing state:** Without --approve, work may be immediately claimable. Empty --message reuses the title. Higher numeric priority runs first; a successful submission is not proof of completion.

Implementation: [src/mco/cli.py:2430](../../src/mco/cli.py#L2430). Global/group options: [mco](#cli-mco).

| Argument / option | Type and requirement | Default | Meaning / constraints |
|---|---|---|---|
| <a id="cli-mco-send-param-to-role"></a>`TO_ROLE` (positional) | TEXT; required | required | Target role's dropbox (e.g. codex, claude). |
| <a id="cli-mco-send-param-title"></a>`--title` / `-t` | TEXT; required | required | Short job title. |
| <a id="cli-mco-send-param-message"></a>`--message` / `-m` | TEXT; optional | `""` (empty) | Instructions (defaults to the title). |
| <a id="cli-mco-send-param-instance"></a>`--instance` | TEXT; optional | `""` (empty) | Address one specific instance instead of the whole role. |
| <a id="cli-mco-send-param-approve"></a>`--approve` | BOOLEAN; flag; optional | `false` | Pause at the human approval gate before execution. |
| <a id="cli-mco-send-param-retries"></a>`--retries` | INTEGER; optional | `0` | Retry budget on failure. |
| <a id="cli-mco-send-param-escalate"></a>`--escalate` | TEXT; optional | `""` (empty) | Role to escalate to after retries are exhausted. |
| <a id="cli-mco-send-param-priority"></a>`--priority` | INTEGER; optional | `0` | Higher runs first (default 0). Workers take the first job in their inbox, so this is what jumps the queue. |
| <a id="cli-mco-send-param-help"></a>`--help` | BOOLEAN; flag; optional | not set | Show this message and exit. |

<a id="cli-mco-serve"></a>
## `mco serve`

Start the BitCadence FastAPI WebSocket/REST API Server.

Usage example: `bitcadence serve`

**Expected result:** Runs the HTTP/WebSocket gateway in the foreground on 127.0.0.1:18789 by default.

**If it fails / before changing state:** Keep this terminal alive or use start/service install for the appropriate lifecycle. A bind error usually needs inspection of the chosen port/process, not force-killing an unidentified process.

Implementation: [src/mco/cli.py:622](../../src/mco/cli.py#L622). Global/group options: [mco](#cli-mco).

| Argument / option | Type and requirement | Default | Meaning / constraints |
|---|---|---|---|
| <a id="cli-mco-serve-param-host"></a>`--host` | TEXT; optional | `127.0.0.1` | The host to bind to. |
| <a id="cli-mco-serve-param-port"></a>`--port` | INTEGER; optional | `18789` | The port to bind to. |
| <a id="cli-mco-serve-param-help"></a>`--help` | BOOLEAN; flag; optional | not set | Show this message and exit. |

<a id="cli-mco-service"></a>
## `mco service`

Run BitCadence processes as boot-persistent OS services.

Safe example: `bitcadence service --help`. Expected result: list the following subcommands and group options. Select a child command for its operation and recovery instructions.

Subcommands: [install](#cli-mco-service-install), [install-scheduler](#cli-mco-service-install-scheduler), [install-waker](#cli-mco-service-install-waker), [logs](#cli-mco-service-logs), [restart](#cli-mco-service-restart), [status](#cli-mco-service-status), [uninstall](#cli-mco-service-uninstall).

Global/group options: [mco](#cli-mco).

| Argument / option | Type and requirement | Default | Meaning / constraints |
|---|---|---|---|
| <a id="cli-mco-service-param-help"></a>`--help` | BOOLEAN; flag; optional | not set | Show this message and exit. |

<a id="cli-mco-service-install"></a>
## `mco service install`

Install the gateway as an OS service that starts on boot/login.

Safe inspection example (prints help only): `bitcadence service install --help`

**Operation result (when run with the required values):** Installs the gateway using the platform's boot/login service backend and prints the console address/removal command.

**If it fails / before changing state:** Changes OS startup/service configuration. Check service status afterward. It is separate from starting a temporary background gateway with start.

Implementation: [src/mco/cli.py:1678](../../src/mco/cli.py#L1678). Global/group options: [mco](#cli-mco), [mco service](#cli-mco-service).

| Argument / option | Type and requirement | Default | Meaning / constraints |
|---|---|---|---|
| <a id="cli-mco-service-install-param-host"></a>`--host` | TEXT; optional | `127.0.0.1` | Host the service binds to. |
| <a id="cli-mco-service-install-param-port"></a>`--port` | INTEGER; optional | `18789` | Port the service binds to. |
| <a id="cli-mco-service-install-param-help"></a>`--help` | BOOLEAN; flag; optional | not set | Show this message and exit. |

<a id="cli-mco-service-install-scheduler"></a>
## `mco service install-scheduler`

Install the schedule/loop scheduler as a boot-persistent OS service.

Safe inspection example (prints help only): `bitcadence service install-scheduler --help`

**Operation result (when run with the required values):** Installs a scheduler service so schedule passes survive terminal closure/reboot.

**If it fails / before changing state:** Check service status BitCadence-scheduler. An installed gateway does not imply that the scheduler service is installed or running.

Implementation: [src/mco/cli.py:1658](../../src/mco/cli.py#L1658). Global/group options: [mco](#cli-mco), [mco service](#cli-mco-service).

| Argument / option | Type and requirement | Default | Meaning / constraints |
|---|---|---|---|
| <a id="cli-mco-service-install-scheduler-param-interval"></a>`--interval` | FLOAT; optional | `30.0` | Seconds between scheduler passes. |
| <a id="cli-mco-service-install-scheduler-param-help"></a>`--help` | BOOLEAN; flag; optional | not set | Show this message and exit. |

<a id="cli-mco-service-install-waker"></a>
## `mco service install-waker`

Install a self-restarting waker service for one role/instance.

Safe inspection example (prints help only): `bitcadence service install-waker --help`

**Operation result (when run with the required values):** Installs a self-restarting waker service for one role/instance and a configured worker shell command.

**If it fails / before changing state:** Provide the command either positionally or with --exec; positional wins if both are supplied. Missing command exits 1. This launches the configured executor when work is pending, so select the exact identity and command deliberately.

Implementation: [src/mco/cli.py:1696](../../src/mco/cli.py#L1696). Global/group options: [mco](#cli-mco), [mco service](#cli-mco-service).

| Argument / option | Type and requirement | Default | Meaning / constraints |
|---|---|---|---|
| <a id="cli-mco-service-install-waker-param-role"></a>`ROLE` (positional) | TEXT; required | required | Agent role this waker should watch. |
| <a id="cli-mco-service-install-waker-param-exec-command"></a>`EXEC_COMMAND` (positional) | TEXT; optional | not set | Shell command to run when jobs are pending. |
| <a id="cli-mco-service-install-waker-param-exec-option"></a>`--exec` | TEXT; optional | not set | Shell command to run when jobs are pending. |
| <a id="cli-mco-service-install-waker-param-instance"></a>`--instance` | TEXT; optional | not set | Optional agent instance ID this waker should watch. |
| <a id="cli-mco-service-install-waker-param-min-interval"></a>`--min-interval` | FLOAT; optional | `10.0` | Minimum seconds between spawn starts. |
| <a id="cli-mco-service-install-waker-param-help"></a>`--help` | BOOLEAN; flag; optional | not set | Show this message and exit. |

<a id="cli-mco-service-logs"></a>
## `mco service logs`

Tail a service log file.

Usage example: `bitcadence service logs --lines 80`

**Expected result:** Prints recent lines from the selected service's log; --follow streams until Ctrl+C.

**If it fails / before changing state:** Missing logs are reported and exit 0. Use service status to distinguish a never-started service from an idle log. Logs may contain operational details; review before sharing.

Implementation: [src/mco/cli.py:1779](../../src/mco/cli.py#L1779). Global/group options: [mco](#cli-mco), [mco service](#cli-mco-service).

| Argument / option | Type and requirement | Default | Meaning / constraints |
|---|---|---|---|
| <a id="cli-mco-service-logs-param-selector"></a>`SELECTOR` (positional) | TEXT; optional | not set | Service name or waker role. Defaults to the gateway. |
| <a id="cli-mco-service-logs-param-lines"></a>`--lines` / `-n` | INTEGER; optional | `80` | Number of recent log lines to print. |
| <a id="cli-mco-service-logs-param-follow"></a>`--follow` / `-f` | BOOLEAN; flag; optional | `false` | Keep streaming new log lines. |
| <a id="cli-mco-service-logs-param-help"></a>`--help` | BOOLEAN; flag; optional | not set | Show this message and exit. |

<a id="cli-mco-service-restart"></a>
## `mco service restart`

Restart the boot-persistent gateway service.

Safe inspection example (prints help only): `bitcadence service restart --help`

**Operation result (when run with the required values):** Restarts the selected OS-managed service, defaulting to the gateway.

**If it fails / before changing state:** Use a service name or waker role selector. This can interrupt work; inspect status/logs before intentionally restarting a busy worker.

Implementation: [src/mco/cli.py:1765](../../src/mco/cli.py#L1765). Global/group options: [mco](#cli-mco), [mco service](#cli-mco-service).

| Argument / option | Type and requirement | Default | Meaning / constraints |
|---|---|---|---|
| <a id="cli-mco-service-restart-param-selector"></a>`SELECTOR` (positional) | TEXT; optional | not set | Service name or waker role. Defaults to the gateway. |
| <a id="cli-mco-service-restart-param-help"></a>`--help` | BOOLEAN; flag; optional | not set | Show this message and exit. |

<a id="cli-mco-service-status"></a>
## `mco service status`

Show installed BitCadence services, or one selected service.

Usage example: `bitcadence service status`

**Expected result:** Prints installed/running/last-exit state for all discovered services or a selected service.

**If it fails / before changing state:** No services found is a normal exit-0 result. It proves absence from the backend inventory, not that all unrelated background processes are stopped.

Implementation: [src/mco/cli.py:1733](../../src/mco/cli.py#L1733). Global/group options: [mco](#cli-mco), [mco service](#cli-mco-service).

| Argument / option | Type and requirement | Default | Meaning / constraints |
|---|---|---|---|
| <a id="cli-mco-service-status-param-selector"></a>`SELECTOR` (positional) | TEXT; optional | not set | Optional service name or waker role to inspect. |
| <a id="cli-mco-service-status-param-help"></a>`--help` | BOOLEAN; flag; optional | not set | Show this message and exit. |

<a id="cli-mco-service-uninstall"></a>
## `mco service uninstall`

Remove the boot-persistent service (does not stop a running gateway).

Safe inspection example (prints help only): `bitcadence service uninstall --help`

**Operation result (when run with the required values):** Removes boot-persistent registration for the selected service, default gateway.

**If it fails / before changing state:** Does not stop an already running gateway. Inspect processes/status separately and use the appropriate stop command when a shutdown is intended.

Implementation: [src/mco/cli.py:1719](../../src/mco/cli.py#L1719). Global/group options: [mco](#cli-mco), [mco service](#cli-mco-service).

| Argument / option | Type and requirement | Default | Meaning / constraints |
|---|---|---|---|
| <a id="cli-mco-service-uninstall-param-selector"></a>`SELECTOR` (positional) | TEXT; optional | not set | Service name or waker role. Defaults to the gateway. |
| <a id="cli-mco-service-uninstall-param-help"></a>`--help` | BOOLEAN; flag; optional | not set | Show this message and exit. |

<a id="cli-mco-settings"></a>
## `mco settings`

View or change gateway settings. With nothing else, a plain summary.

Usage example: `bitcadence settings`

**Expected result:** Shows a plain settings summary. KEY reads one key, KEY VALUE writes it, --unset resets it, and --show-advanced/--all includes advanced settings.

**If it fails / before changing state:** Writes affect gateway configuration; review the exact setting and any restart guidance. A bare summary is an inspection; adding a value or --unset changes state.

Implementation: [src/mco/cli.py:2898](../../src/mco/cli.py#L2898). Global/group options: [mco](#cli-mco).

| Argument / option | Type and requirement | Default | Meaning / constraints |
|---|---|---|---|
| <a id="cli-mco-settings-param-key"></a>`KEY` (positional) | TEXT; optional | not set | Setting key to read or write (blank = list all). |
| <a id="cli-mco-settings-param-value"></a>`VALUE` (positional) | TEXT; optional | not set | New value for the key (omit with --unset to clear). |
| <a id="cli-mco-settings-param-unset"></a>`--unset` | BOOLEAN; flag; optional | `false` | Clear the key back to its default. |
| <a id="cli-mco-settings-param-show-advanced"></a>`--show-advanced` / `--all` | BOOLEAN; flag; optional | `false` | Show every setting, the address and the masked sign-in. |
| <a id="cli-mco-settings-param-help"></a>`--help` | BOOLEAN; flag; optional | not set | Show this message and exit. |

<a id="cli-mco-setup"></a>
## `mco setup`

Configure BitCadence - a guided walkthrough or a jump-anywhere settings menu.

Usage example: `bitcadence setup`

**Expected result:** Opens the configuration walkthrough/menu. --guided requests the complete walkthrough; --menu jumps straight to settings.

**If it fails / before changing state:** Can save configuration and credentials. Follow the selected path and then run doctor to find any remaining installation problem.

Implementation: [src/mco/cli.py:106](../../src/mco/cli.py#L106). Global/group options: [mco](#cli-mco).

| Argument / option | Type and requirement | Default | Meaning / constraints |
|---|---|---|---|
| <a id="cli-mco-setup-param-guided"></a>`--guided` | BOOLEAN; flag; optional | `false` | Run the full guided walkthrough. |
| <a id="cli-mco-setup-param-menu"></a>`--menu` | BOOLEAN; flag; optional | `false` | Jump straight to the settings menu. |
| <a id="cli-mco-setup-param-help"></a>`--help` | BOOLEAN; flag; optional | not set | Show this message and exit. |

<a id="cli-mco-start"></a>
## `mco start`

Start BitCadence quietly in the background (the pair of 'mco stop').

Usage example: `bitcadence start --no-open`

**Expected result:** Starts the gateway hidden in the background, using ~/.mco/logs/gateway.log. Normally also opens the signed-in app, ensures a status icon and registers login startup on the first run.

**If it fails / before changing state:** Use status and inspect the log if it does not come up. --no-open suppresses browser opening (the tray is still ensured); --no-autostart suppresses first-run login startup registration. stop is the paired shutdown command.

Implementation: [src/mco/cli.py:726](../../src/mco/cli.py#L726). Global/group options: [mco](#cli-mco).

| Argument / option | Type and requirement | Default | Meaning / constraints |
|---|---|---|---|
| <a id="cli-mco-start-param-host"></a>`--host` | TEXT; optional | `127.0.0.1` | The host to bind to. |
| <a id="cli-mco-start-param-port"></a>`--port` | INTEGER; optional | `18789` | The port to bind to. |
| <a id="cli-mco-start-param-no-open"></a>`--no-open` | BOOLEAN; flag; optional | `false` | Don't open the app in the browser. |
| <a id="cli-mco-start-param-no-autostart"></a>`--no-autostart` | BOOLEAN; flag; optional | `false` | Don't set BitCadence to start when you sign in. |
| <a id="cli-mco-start-param-help"></a>`--help` | BOOLEAN; flag; optional | not set | Show this message and exit. |

<a id="cli-mco-status"></a>
## `mco status`

Plain summary: what needs you, what's running, any problems.

Usage example: `bitcadence status --details`

**Expected result:** Without flags, prints a plain summary of waiting/running/problem work. --details returns technical diagnostics; --json is for the plain summary path.

**If it fails / before changing state:** --all includes all resolved configuration keys, including unrelated process environment, and selects diagnostics. --json does not make --details/--all JSON. Review diagnostics before sharing; process health alone does not establish work completion.

Implementation: [src/mco/cli.py:1927](../../src/mco/cli.py#L1927). Global/group options: [mco](#cli-mco).

| Argument / option | Type and requirement | Default | Meaning / constraints |
|---|---|---|---|
| <a id="cli-mco-status-param-details"></a>`--details` | BOOLEAN; flag; optional | `false` | Show the technical diagnostics (database path, profile, settings). |
| <a id="cli-mco-status-param-show-all"></a>`--all` | BOOLEAN; flag; optional | `false` | Show all resolved configuration keys, including unrelated process environment. |
| <a id="cli-mco-status-param-as-json"></a>`--json` | BOOLEAN; flag; optional | `false` | Machine-readable summary. |
| <a id="cli-mco-status-param-help"></a>`--help` | BOOLEAN; flag; optional | not set | Show this message and exit. |

<a id="cli-mco-stop"></a>
## `mco stop`

Stop a running BitCadence gateway (by port).

Safe inspection example (prints help only): `bitcadence stop --help`

**Operation result (when run with the required values):** Finds listening processes by port and terminates them without asking. Normally waits five seconds before escalating to a kill; --force kills immediately.

**If it fails / before changing state:** Inspect the port and intended process first: the implementation targets listeners on that port, not a verified BitCadence process identity. No listener is a normal exit-0 result. This does not cancel queued jobs or remove service registration.

Implementation: [src/mco/cli.py:1799](../../src/mco/cli.py#L1799). Global/group options: [mco](#cli-mco).

| Argument / option | Type and requirement | Default | Meaning / constraints |
|---|---|---|---|
| <a id="cli-mco-stop-param-port"></a>`--port` | INTEGER; optional | `18789` | Port the gateway is running on. |
| <a id="cli-mco-stop-param-force"></a>`--force` / `-f` | BOOLEAN; flag; optional | `false` | Send SIGKILL immediately instead of graceful SIGTERM. |
| <a id="cli-mco-stop-param-help"></a>`--help` | BOOLEAN; flag; optional | not set | Show this message and exit. |

<a id="cli-mco-sync"></a>
## `mco sync`

Pull open platform objects (incidents/problems) onto the job board.

Usage example: `bitcadence sync servicenow`

**Expected result:** Pulls open external incidents/problems into the job board and prints pulled/created/skipped counts.

**If it fails / before changing state:** This creates board work. Run connectors first to check configured names/health. Existing imported objects are skipped rather than all recreated.

Implementation: [src/mco/cli.py:3315](../../src/mco/cli.py#L3315). Global/group options: [mco](#cli-mco).

| Argument / option | Type and requirement | Default | Meaning / constraints |
|---|---|---|---|
| <a id="cli-mco-sync-param-connector"></a>`CONNECTOR` (positional) | TEXT; required | required | Connector name (servicenow, dynatrace). |
| <a id="cli-mco-sync-param-help"></a>`--help` | BOOLEAN; flag; optional | not set | Show this message and exit. |

<a id="cli-mco-tail"></a>
## `mco tail`

Live-tail a filtered mailbox feed from the gateway broadcast socket.

Usage example: `bitcadence tail --role codex --instance demo-worker`

**Expected result:** Streams broadcast job events filtered by target role and instance; Ctrl+C stops the stream.

**If it fails / before changing state:** Uses configured identity/gateway/token when flags are absent. Authentication failure prints guidance; disconnections retry with backoff. An empty feed does not prove an empty inbox.

Implementation: [src/mco/cli.py:3172](../../src/mco/cli.py#L3172). Global/group options: [mco](#cli-mco).

| Argument / option | Type and requirement | Default | Meaning / constraints |
|---|---|---|---|
| <a id="cli-mco-tail-param-role"></a>`--role` | TEXT; optional | not set | Agent role mailbox to tail. |
| <a id="cli-mco-tail-param-instance"></a>`--instance` | TEXT; optional | not set | Agent instance mailbox to tail. |
| <a id="cli-mco-tail-param-gateway"></a>`--gateway` | TEXT; optional | not set | Gateway HTTP URL. |
| <a id="cli-mco-tail-param-token"></a>`--token` | TEXT; optional | not set | Agent or operator bearer token. |
| <a id="cli-mco-tail-param-help"></a>`--help` | BOOLEAN; flag; optional | not set | Show this message and exit. |

<a id="cli-mco-tray"></a>
## `mco tray`

Status light and a door into the console (Windows tray / macOS menu bar / Linux AppIndicator).

Safe inspection example (prints help only): `bitcadence tray --help`

**Operation result (when run with the required values):** Runs the optional system status light/console doorway. --daemon starts it hidden where supported.

**If it fails / before changing state:** Needs the tray desktop dependencies and a usable desktop session. This is an interface, not the gateway or an executor.

Implementation: [src/mco/cli.py:1136](../../src/mco/cli.py#L1136). Global/group options: [mco](#cli-mco).

| Argument / option | Type and requirement | Default | Meaning / constraints |
|---|---|---|---|
| <a id="cli-mco-tray-param-daemon"></a>`--daemon` | BOOLEAN; flag; optional | `false` | The advanced tray that drives the worker daemon. |
| <a id="cli-mco-tray-param-help"></a>`--help` | BOOLEAN; flag; optional | not set | Show this message and exit. |

<a id="cli-mco-unarchive"></a>
## `mco unarchive`

Undo archive.

Usage example: `bitcadence unarchive JOB_ID`

**Expected result:** Makes an archived job visible again in the default board view.

**If it fails / before changing state:** Does not retry or reopen the job. Use audit/status to inspect its existing terminal result.

Implementation: [src/mco/cli.py:2706](../../src/mco/cli.py#L2706). Global/group options: [mco](#cli-mco).

| Argument / option | Type and requirement | Default | Meaning / constraints |
|---|---|---|---|
| <a id="cli-mco-unarchive-param-job-id"></a>`JOB_ID` (positional) | TEXT; required | required | Archived job ID to restore to the default board view. |
| <a id="cli-mco-unarchive-param-help"></a>`--help` | BOOLEAN; flag; optional | not set | Show this message and exit. |

<a id="cli-mco-upgrade"></a>
## `mco upgrade`

Apply schema migrations to the configured backend.

Usage example: `bitcadence upgrade`

**Expected result:** Checks schema upgrade requirements. Local SQLite needs no such migrations; supported Postgres/Supabase execution requires --apply.

**If it fails / before changing state:** Without --apply, prints pending migration SQL/instructions rather than applying it. Where automatic execution is unavailable, writes a combined script for the Supabase SQL editor. Review output before applying schema changes.

Implementation: [src/mco/cli.py:1987](../../src/mco/cli.py#L1987). Global/group options: [mco](#cli-mco).

| Argument / option | Type and requirement | Default | Meaning / constraints |
|---|---|---|---|
| <a id="cli-mco-upgrade-param-apply"></a>`--apply` | BOOLEAN; flag; optional | `false` | Actually apply (default: dry-run / show pending). |
| <a id="cli-mco-upgrade-param-help"></a>`--help` | BOOLEAN; flag; optional | not set | Show this message and exit. |

<a id="cli-mco-wake"></a>
## `mco wake`

Wake a local worker command when this agent's inbox has pending jobs.

Safe inspection example (prints help only): `bitcadence wake --help`

**Operation result (when run with the required values):** Waits for pending work, then starts the configured local worker shell command, enforcing a minimum interval between starts and optional periodic inbox rechecks.

**If it fails / before changing state:** Requires --exec and the exact agent identity/credential. Missing or invalid per-instance credentials fail explicitly instead of silently borrowing the operator credential. Ctrl+C stops the waker; starting an executor is not a job lease/completion proof.

Implementation: [src/mco/cli.py:3245](../../src/mco/cli.py#L3245). Global/group options: [mco](#cli-mco).

| Argument / option | Type and requirement | Default | Meaning / constraints |
|---|---|---|---|
| <a id="cli-mco-wake-param-exec-command"></a>`--exec` | TEXT; required | required | Shell command to run when this worker has pending jobs. |
| <a id="cli-mco-wake-param-role"></a>`--role` | TEXT; optional | not set | Agent role to watch for. |
| <a id="cli-mco-wake-param-instance"></a>`--instance` | TEXT; optional | not set | Agent instance ID to watch for. |
| <a id="cli-mco-wake-param-gateway"></a>`--gateway` | TEXT; optional | not set | Gateway HTTP URL. |
| <a id="cli-mco-wake-param-token"></a>`--token` | TEXT; optional | not set | Agent bearer token. |
| <a id="cli-mco-wake-param-min-interval"></a>`--min-interval` | FLOAT; optional | `10.0` | Minimum seconds between spawn starts. |
| <a id="cli-mco-wake-param-repoll-interval"></a>`--repoll-interval` | FLOAT; optional | `300.0` | Seconds between timer re-checks of the inbox (0 disables). |
| <a id="cli-mco-wake-param-help"></a>`--help` | BOOLEAN; flag; optional | not set | Show this message and exit. |

<a id="cli-mco-watch"></a>
## `mco watch`

Live-tail job events from the gateway's broadcast feed (Ctrl-C to stop).

Usage example: `bitcadence watch`

**Expected result:** Streams formatted job events; --raw emits raw JSON messages. Reconnects with exponential backoff up to 30 seconds.

**If it fails / before changing state:** Ctrl+C stops watching. Authentication failures require the configured MCO_AGENT_TOKEN/MCO_LOCAL_TOKEN to be fixed. This is a live stream, not a replay of all historical events.

Implementation: [src/mco/cli.py:3071](../../src/mco/cli.py#L3071). Global/group options: [mco](#cli-mco).

| Argument / option | Type and requirement | Default | Meaning / constraints |
|---|---|---|---|
| <a id="cli-mco-watch-param-raw"></a>`--raw` | BOOLEAN; flag; optional | `false` | Print raw event JSON instead of formatted lines. |
| <a id="cli-mco-watch-param-help"></a>`--help` | BOOLEAN; flag; optional | not set | Show this message and exit. |

<a id="cli-mco-workflow"></a>
## `mco workflow`

Submit a declarative YAML workflow (DAG of jobs) to the Job Board.

Usage example: `bitcadence workflow plan.yaml --dry-run`

**Expected result:** Loads YAML, validates the dependency graph and prints the ordered plan. --dry-run prevents submission.

**If it fails / before changing state:** Without --dry-run, creates real jobs and prints step-to-job IDs. Correct invalid files/dependencies first. Use the printed IDs to follow outcomes; submission alone is not workflow completion.

Implementation: [src/mco/cli.py:2471](../../src/mco/cli.py#L2471). Global/group options: [mco](#cli-mco).

| Argument / option | Type and requirement | Default | Meaning / constraints |
|---|---|---|---|
| <a id="cli-mco-workflow-param-file"></a>`FILE` (positional) | TEXT; required | required | Path to a workflow YAML file. |
| <a id="cli-mco-workflow-param-dry-run"></a>`--dry-run` | BOOLEAN; flag; optional | `false` | Validate and print the plan without submitting. |
| <a id="cli-mco-workflow-param-help"></a>`--help` | BOOLEAN; flag; optional | not set | Show this message and exit. |

## Keep this reference current

Coverage metadata: [_review/cli-coverage.json](_review/cli-coverage.json). It maps command paths and all option/argument spellings to explicit documentation anchors, including aliases, source locations, constraints and defaults.

From the repository root, using the project's Python environment:

```powershell
python docs/wiki/_review/verify_cli_coverage.py --repo .
python docs/wiki/_review/verify_cli_examples.py
```

The coverage verifier imports the live Typer tree, **never invokes command or parameter callbacks**, compares documented metadata and checks every command/parameter anchor and option spelling. New/changed/removed commands or parameters fail coverage. Its `--dump` flag prints live metadata for a reviewer; it does not silently rewrite the reviewed reference. Example verification separately dry-parses command examples. Neither check establishes live gateway, browser, authorization, worker or external-platform behavior.

Source boundary: this page documents the command tree at repository commit `0f9f36e` (2026-11-13). Runtime values, configured connectors/launchers and organization policy remain installation-specific. Options without help text were explained from their implementation; no undeclared command or future design was promoted into a shipped capability.
