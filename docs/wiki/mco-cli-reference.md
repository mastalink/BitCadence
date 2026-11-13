# Complete `mco` CLI Reference

Every command, argument and option below was taken from each command's `--help` output. Run `python docs/wiki/_review/verify_cli_examples.py` to re-check every `mco` example in this wiki against the live CLI (dry parse only; nothing is executed). Notation: `ARG` is a positional argument, `[ARG]` is optional, and `(repeatable)` options may be given more than once.

---

## 1. Gateway Lifecycle & Status

### `mco serve`
Start the FastAPI WebSocket/REST gateway in the foreground.
- `--host TEXT` (default `127.0.0.1`), `--port INTEGER` (default `18789`)

### `mco start`
Start the gateway in the background (the pair of `mco stop`).
- `--host TEXT` (default `127.0.0.1`), `--port INTEGER` (default `18789`)

### `mco stop`
Stop a running gateway, found by port.
- `--port INTEGER` (default `18789`), `--force` / `-f`

### `mco restart`
Stop the background gateway if it is running, then start it.
- `--host TEXT` (default `127.0.0.1`), `--port INTEGER` (default `18789`)

### `mco status`
Print health checks and diagnostics.
- `--all`

### `mco doctor`
Diagnose an install end to end: Python, config, secret store, database, gateway, agents, vendor CLIs. Exit code 1 if anything is broken.
- `--port INTEGER` (default `18789`)

### `mco setup`
Guided walkthrough or a jump-anywhere settings menu.
- `--guided`, `--menu`

### `mco settings`
View or change gateway settings (the Control Panel, from the terminal).
- `[KEY] [VALUE]`: no arguments lists every setting; `KEY` alone reads one; `KEY VALUE` writes one.
- `--unset`: clear `KEY` back to its default.

### `mco edition`
Show the active edition (community/team/enterprise) and the feature matrix.

### `mco upgrade`
Apply schema migrations to the configured backend.
- `--apply`

---

## 2. Desktop & GUI Doorways

### `mco gui`
Open the console in your browser.
- `--flow` (old option kept so scripts keep working; it opens the console), `--dashboard`, `--print` (print the URL)

### `mco tray`
Status light and a door into the console (Windows tray, macOS menu bar, Linux AppIndicator).

---

## 3. Job Board & Task Lifecycle

### `mco send`
Drop a job into an agent's dropbox.
- `TO_ROLE`: target role (`codex`, `claude`, `gemini`, ...).
- `--title` / `-t TEXT` (required), `--message` / `-m TEXT`
- `--instance TEXT`: target one instance of the role.
- `--approve`: hold the job at the human approval gate before any worker picks it up.
- `--retries INTEGER`, `--escalate TEXT`, `--priority INTEGER`

### `mco workflow`
Submit a declarative YAML workflow (a DAG of jobs).
- `FILE`, `--dry-run`

### `mco approve`
Approve a job paused at the human-in-the-loop gate. `JOB_ID`

### `mco reject`
Reject a paused job (terminal). `JOB_ID`, `--reason TEXT`

### `mco retry`
Re-queue a failed or rejected job to `pending` (approver-role token). `JOB_ID`

### `mco cancel`
Call off a job that has not finished (approver-role token). `JOB_ID`, `--reason TEXT`

### `mco archive` / `mco unarchive`
Archive a completed, failed, rejected or cancelled job, or undo that. `JOB_ID`

### `mco duplicates`
List other jobs that look like the same work. `JOB_ID`

### `mco reassign`
Clone a failed job onto a new target, link both rows, and archive the old one.
- `JOB_ID`, `--to-role TEXT` (required), `--to-instance TEXT`, `--instructions TEXT`

### `mco jobs rank`
Score available client jobs for fit, value and risk using Jev and hard filters.
- `[POSTINGS_PATH]`: JSON or CSV of postings.
- `--top` / `-n INTEGER` (default `20`), `--out` / `-o PATH`, `--source` / `-s TEXT`, `--query` / `-q TEXT`

`mco jobs` has no other subcommands. To list jobs, use the console Job Board or the `mco_jobs` MCP tool.

---

## 4. Audit & Tamper Evidence

### `mco audit`
Print a job's tamper-evident audit trail, oldest event first.
- `JOB_ID`
- `--verify`: walk the hash chain and report OK or the first broken link.
- `--checkpoint PATH`: verify against a previously exported signed checkpoint.

### `mco audit-checkpoint`
Export a signed checkpoint. Keep it outside the database/backup volume.
- `JOB_ID`, `OUTPUT` (both required, positional)

### `mco restore-fence`
After a DB restore, pause work and invalidate every pre-restore claim.

---

## 5. Agent Fleet & Identity

### `mco agents`
List registered agents and their online presence.

### `mco register`
Register a client agent and print its access token.
- `--name TEXT` (required), `--role TEXT` (required), `--org TEXT` (default `default`)
- `--scope TEXT` (repeatable): one scope per flag, e.g. `--scope jobs:read --scope jobs:write`.

### `mco reset-token`
Rotate an agent's token; the old one stops working immediately.
- `INSTANCE_ID`, `--save` / `--no-save`

### `mco deregister`
Remove an agent registration; its token stops working immediately.
- `INSTANCE_ID`, `--yes` / `-y`

### `mco orgs`
List orgs available for registration.

---

## 6. Workers, Daemons & MCP

### `mco listen`
Spawn the background daemon client that polls and executes Job Board tasks.
- `--role TEXT` (default `codex`), `--instance TEXT` (default `default_agent`), `--config-file TEXT` (default `agent_config.json`)

### `mco wake`
Run a local command when this agent's inbox has pending jobs.
- `--exec TEXT` (required): the command to run.
- `--role TEXT`, `--instance TEXT`, `--gateway TEXT`, `--token TEXT`, `--min-interval FLOAT` (default `10.0`)

### `mco mcp`
Run the dropbox as an MCP server: stdio by default, HTTP with `--http`.
- `--http`, `--host TEXT` (default `127.0.0.1`), `--port INTEGER` (default `18790`)

### `mco watch`
Live-tail job events from the gateway broadcast feed (Ctrl-C to stop).
- `--raw`

### `mco tail`
Live-tail a filtered mailbox feed from the broadcast socket.
- `--role TEXT`, `--instance TEXT`, `--gateway TEXT`, `--token TEXT`

---

## 7. Drumline Shared Memory & Agent Exchange

### `mco remember`
Append an entry to the Drumline shared context.
- `TITLE`, `CONTENT`, `--kind TEXT` (default `fact`), `--tags TEXT` (comma-separated)

### `mco recall`
Recall the most relevant Drumline entries.
- `[QUERY]`, `--tags TEXT`, `--limit INTEGER` (default `5`), `--role TEXT`

### `mco exchange post`
Post one message to the Agent Exchange. Discussion there is reference, not instructions or approval.
- `KIND`: one of `question`, `proposal`, `blocker`, `reply`, `decision`, `handoff`, `resolution`, `supersession`.
- `BODY`
- `--job TEXT`, `--reply-to TEXT`, `--resolves TEXT`, `--supersedes TEXT`, `--key TEXT` (idempotency key)

### `mco exchange list`
List Exchange messages. Needs `--job` or `--thread`.
- `--job TEXT`, `--thread TEXT`, `--kind TEXT`, `--limit INTEGER` (default `20`)

### `mco exchange promote`
Explicitly promote one exchange message into canonical Drumline context.
- `EXCHANGE_ID`, `--to TEXT` (default `decision`), `--title TEXT`, `--key TEXT`

---

## 8. Scores & the Conductor

Score commands default to the conductor database `~/.mco/score-runs.db` and artifact root `~/.mco/score-artifacts`.

### `mco score scaffold`
Turn a plain-language brief into a validated Score.
- `SPEC`, `--out PATH` (required)

### `mco score intake`
Draft a scope from a client brief, or approve a clean scope into a Score.
- `ARGS...`: `BRIEF.txt` to draft, or `approve SCOPE.json`.
- `--out PATH` (required), `--client TEXT`; for `approve`: `--worktree TEXT`, `--branch TEXT`, `--before-sha TEXT`

### `mco score start`
Initialize a run. Safe to repeat: identical policy and identities resume the same run.
- `SCORE_FILE`, `--run-id TEXT` (required), `--target TEXT` (repeatable), `--org TEXT` (default `default`), `--db PATH`, `--artifact-root PATH`, `--live-repository-write`

### `mco score status`
Show where a run is, per task and phase, with the last events.
- `--run-id TEXT` (required), `--db PATH`, `--artifact-root PATH`

### `mco score tick`
Plan what can start, dispatch it, accept finished work.
- `--run-id TEXT` (required), `--watch`, `--interval FLOAT` (default `5.0`), `--timeout FLOAT` (default `900.0`), `--db PATH`, `--artifact-root PATH`, `--live-repository-write`

### `mco score list`
List the runs in a conductor database.
- `--db PATH`

### `mco score grant-local`
Issue a locally auditable, interactive grant for a run (Windows SQLite only).
- `SCORE_FILE`, `--run-id TEXT` (required), `--action TEXT` (required, repeatable), `--resource TEXT` (required, repeatable), `--environment TEXT` (default `test`), `--expires-minutes INTEGER` (default `240`), `--expires-hours INTEGER`, `--budget-cents INTEGER`

### `mco score deliver`
Bundle code, tests, README, reviews and a DRAFT client message (never sent).
- `WORKTREE`, `--out PATH` (required), `--evidence PATH`, `--client TEXT`

---

## 9. Scheduling & Launchers

### `mco launch`
Fire a launcher right now, by name.
- `NAME`, `--approve` / `--no-approve`

### `mco schedule init`
Write a starter `~/.mco/schedules.yaml`. `--force` overwrites.

### `mco schedule list`
Show every schedule and loop with its next fire time.

### `mco schedule enable` / `mco schedule disable`
Enable or disable a schedule or loop without editing YAML. `NAME`

### `mco schedule reset`
Clear a schedule's run history so a finished loop can run again. `NAME`, `--yes` / `-y`

### `mco schedule tick`
Run one scheduler pass. `--dry-run`

### `mco schedule run`
Run the scheduler in the foreground until interrupted. `--interval FLOAT` (default `30.0`)

---

## 10. Services & Fleet

### `mco service install`
Install the gateway as an OS service that starts on boot or login.
- `--host TEXT` (default `127.0.0.1`), `--port INTEGER` (default `18789`)

### `mco service install-scheduler`
Install the scheduler as a boot-persistent service. `--interval FLOAT` (default `30.0`)

### `mco service install-waker`
Install a self-restarting waker service for one role or instance.
- `ROLE`, `[EXEC_COMMAND]` (or `--exec TEXT`), `--instance TEXT`, `--min-interval FLOAT` (default `10.0`)

### `mco service status` / `mco service restart` / `mco service uninstall`
Show, restart, or remove installed services. Each takes an optional `[SELECTOR]`.
The service group has no stop subcommand; `uninstall` removes the service definition and does not stop a running process.

### `mco service logs`
Tail a service log. `[SELECTOR]`, `--lines` / `-n INTEGER` (default `80`), `--follow` / `-f`

### `mco fleet status`
Show configured workers and whether each is installed and running.

### `mco fleet apply`
Reconcile OS services to `~/.mco/fleet.toml`.

### `mco fleet set`
Update one worker field in `fleet.toml`.
- `WORKER`, `ASSIGNMENT`: a single `KEY=VALUE`, for example `mode=off`.

---

## 11. Connectors

### `mco connectors`
List configured enterprise connectors and their health.

### `mco sync`
Pull open platform objects (incidents, problems) onto the job board. `CONNECTOR`

### `mco platform`
Run a connector control action directly (approver-role token).
- `CONNECTOR`, `ACTION` (see `mco connectors`), `--params TEXT` (a JSON object, default `{}`)

---

## 12. Decision Providers & Policy

### `mco jev route-model`
Suggest a Claude Code model tier (haiku/sonnet/opus) for a task.
- `--task TEXT` (required), `--context TEXT`, `--deterministic-tier TEXT` (default `sonnet`)

### `mco cio check`
Check one proposal against the CIO sidecar brief's mechanical rules and print a JSON verdict (`approve`, `reject` or `escalate`).
- `--decider TEXT` (required), `--project TEXT`, `--spend-cents INTEGER`, `--proposed-by TEXT`, `--category TEXT` (repeatable)

---

## Examples

```bash
mco send codex --title "Summarize the repository" --message "Review recent commits on main." --approve
mco approve <job-id>
mco audit <job-id> --verify
mco audit-checkpoint <job-id> checkpoint.json
mco settings MCO_KILL_SWITCH true
mco fleet set claude-cio mode=off
mco wake --role codex --exec "python run_worker.py"
mco platform servicenow resolve_incident --params '{"sys_id": "<sys-id>"}'
```
