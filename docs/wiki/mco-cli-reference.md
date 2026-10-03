# Complete `mco` CLI Reference

This document provides a comprehensive, recursive reference for every command, subcommand, option, and argument available in the BitCadence command-line interface (`mco`).

---

## 1. Gateway Lifecycle & Status

### `mco serve`
Start the BitCadence FastAPI WebSocket/REST API Server in the foreground.
- `--host TEXT`: Host to bind to. (Default: `127.0.0.1`)
- `--port INTEGER`: Port to bind to. (Default: `18789`)

### `mco start`
Start the gateway in the background as a detached process (paired with `mco stop`).
- `--port INTEGER`: Port to bind to. (Default: `18789`)

### `mco stop`
Stop a running background BitCadence gateway process.
- `--port INTEGER`: Port of the target gateway to stop. (Default: `18789`)

### `mco restart`
Restart the background gateway process (stops existing gateway, then starts a new instance).
- `--port INTEGER`: Port to target. (Default: `18789`)

### `mco status`
Print a complete health check, operational diagnostics, active profile, and database path.

### `mco doctor`
Diagnose the installation end-to-end: Python environment, config file, secret vault, database engine, gateway reachability, registered agents, and vendor CLIs. Exits with code 1 if any check fails.

### `mco setup`
Interactive setup walkthrough or jump-anywhere configuration menu for profiles, tokens, databases, and secret vault initialization.

### `mco edition`
Display the active edition (`community`, `team`, or `enterprise`) and print the complete feature availability matrix.

---

## 2. Desktop & GUI Doorways

### `mco gui`
Open the BitCadence console in your default web browser.
- `--flow`: Open directly to the Flow Control canvas (`/flow`).
- `--dashboard`: Open the minimal dashboard (`/dashboard`).

### `mco tray`
Start the Windows system tray / notification area status light and background menu.

---

## 3. Job Board & Task Lifecycle

### `mco send`
Drop a new task or message into an agent role or instance dropbox.
- `TITLE`: Brief title of the task.
- `--role TEXT`: Target agent role (`codex`, `claude`, `gemini`, etc.).
- `--instance TEXT`: Optional specific target instance ID.
- `--instructions TEXT`: Detailed task instructions or prompt.
- `--approval / --no-approval`: Halt at human-in-the-loop gate before worker pickup.
- `--retries INTEGER`: Maximum automatic retries on worker failure.
- `--escalate TEXT`: Role to escalate to upon exhausted retries.
- `--priority INTEGER`: Task queue priority (higher runs first).

### `mco workflow`
Submit a declarative YAML workflow file (DAG of jobs) to the Job Board.
- `FILE`: Path to the YAML workflow definition.

### `mco approve`
Approve a job currently paused at the `needs_approval` human gate.
- `JOB_ID`: The unique ID of the paused job.

### `mco reject`
Reject a job paused at the human gate (moves job to terminal `rejected` state).
- `JOB_ID`: The target job ID.
- `--reason TEXT`: Required rationale for audit log.

### `mco retry`
Re-queue a failed or rejected job back to `pending`.
- `JOB_ID`: The target job ID.

### `mco cancel`
Cancel an in-flight or waiting job before completion.
- `JOB_ID`: The target job ID.

### `mco archive`
Archive a finished, failed, or cancelled job into cold storage view (reversible).
- `JOB_ID`: The target job ID.

### `mco unarchive`
Restore an archived job back to normal board visibility.
- `JOB_ID`: The target job ID.

### `mco duplicates`
List other existing jobs that match the title, description, or payload of this task.
- `JOB_ID`: The target job ID.

### `mco reassign`
Clone a failed job onto a new target role/instance, link the audit records, and archive the old job.
- `JOB_ID`: The failed job ID.
- `--to-role TEXT`: New target role.
- `--to-instance TEXT`: Optional new instance ID.

---

## 4. Audit & Tamper Evidence

### `mco audit`
Print the tamper-evident, append-only audit trail for a job (oldest event first).
- `JOB_ID`: The target job ID.
- `--json`: Output raw audit event array as JSON.

### `mco audit-checkpoint`
Export a cryptographically signed audit checkpoint to preserve external tamper evidence.

### `mco restore-fence`
After restoring a database backup, invalidate all pre-restore claims and leases to prevent replay attacks.

---

## 5. Agent Fleet & Identity Management

### `mco agents`
List all registered agents, their capability roles, and live heartbeat presence statuses.

### `mco register`
Register a new client agent and mint an authenticated access token.
- `--name TEXT`: Unique agent instance ID.
- `--role TEXT`: Agent role (`codex`, `claude`, `admin`, `viewer`, etc.).
- `--scope TEXT`: Comma-separated permissions (`jobs:read`, `jobs:write`, etc.).

### `mco reset-token`
Rotate an agent's access token immediately. The previous token is revoked on execution.
- `INSTANCE_ID`: The agent instance to rotate.

### `mco deregister`
Delete an agent registration row. Its access token stops functioning immediately.
- `INSTANCE_ID`: The agent instance to delete.

### `mco orgs`
List organizations available for multi-tenant registration.

---

## 6. Workers, Daemons & MCP

### `mco listen`
Start a background polling worker daemon that claims and executes jobs for a role.
- `--role TEXT`: Role inbox to monitor.
- `--instance TEXT`: Worker instance identifier.

### `mco wake`
Start a lightweight waker process that spawns an execution command only when pending jobs arrive.
- `--role TEXT`: Role inbox to watch.
- `--command TEXT`: Shell command to invoke.

### `mco mcp`
Run the BitCadence agent dropbox as a Model Context Protocol (MCP) server over stdio or HTTP.
- `--http`: Use SSE/HTTP transport instead of stdio.
- `--port INTEGER`: Port for HTTP transport.

### `mco watch`
Live-tail broadcast events from the gateway's WebSocket feed (`Ctrl+C` to exit).

### `mco tail`
Live-tail a filtered mailbox feed for a specific role or instance.

---

## 7. Drumline Shared Memory & Agent Exchange

### `mco remember`
Append an explicit record into Drumline collective memory.
- `TITLE`: Title of the memory record.
- `CONTENT`: Body content.
- `--kind TEXT`: Record kind (`fact`, `decision`, `lesson`, `handoff`, `artifact`).
- `--tags TEXT`: Comma-separated tag list.

### `mco recall`
Query and rank the most relevant entries from Drumline collective memory.
- `QUERY`: Natural language or keyword query string.
- `--limit INTEGER`: Maximum entries to return. (Default: `5`)

### `mco exchange post`
Post a message or proposal to the non-authoritative Drumline Agent Exchange.
- `--title TEXT`: Discussion thread title.
- `--content TEXT`: Proposal or message body.
- `--kind TEXT`: Kind (`proposal`, `objection`, `clarification`, `consensus`).

### `mco exchange list`
List active agent collaboration threads and message counts.

### `mco exchange promote`
Promote an agreed Agent Exchange consensus directly into durable Drumline memory.
- `EXCHANGE_ID`: The exchange thread ID.
- `--kind TEXT`: Target memory kind (`decision` or `lesson`).

---

## 8. Autonomous Scores & Conductor

### `mco score status`
Print status of the Autonomous Score Conductor, registered runs, and worker leases.

### `mco score list`
List registered score contracts and in-flight run executions.

### `mco score tick`
Advance the score conductor sweep engine, evaluating dependencies and dispatching work packets.

### `mco score start`
Initialize and start execution of an autonomous score contract.
- `SCORE_FILE`: Path to the score JSON contract.

### `mco score grant-local`
Grant local policy authority and budget reservations for a score run.
- `RUN_ID`: Target score run ID.

### `mco score scaffold`
Generate a boilerplate score contract for a new project.

### `mco score intake`
Intake and compile a score document into digest-bound work packets.

### `mco score deliver`
Deliver an evidence artifact receipt for a score task.

---

## 9. Scheduling & Recurring Loops

### `mco launch`
Immediately fire a configured launcher by name.
- `LAUNCHER_NAME`: Name defined in `~/.mco/schedules.yaml`.

### `mco schedule init`
Create a default starter `~/.mco/schedules.yaml` configuration file.

### `mco schedule list`
List all configured schedules, next fire times, and interval details.

### `mco schedule enable`
Enable a previously paused schedule.
- `NAME`: Schedule name.

### `mco schedule disable`
Pause a schedule without modifying or deleting its definition.
- `NAME`: Schedule name.

### `mco schedule reset`
Reset iteration history for a completed bounded loop so it can run again.
- `NAME`: Schedule name.

### `mco schedule tick`
Simulate or execute an immediate scheduling evaluation pass.
- `--dry-run`: Evaluate triggers without creating jobs.

### `mco schedule run`
Run the scheduling daemon in the foreground.

---

## 10. Service & Fleet Configuration

### `mco service install`
Install the BitCadence gateway as an OS service (Windows Task / Linux systemd).

### `mco service install-scheduler`
Install the background scheduler loop as a boot-persistent OS service.

### `mco service install-waker`
Install the event waker daemon as an OS service.

### `mco service uninstall`
Remove installed OS services.

### `mco service status`
Query status, PID, and health of installed OS services.

### `mco service restart`
Restart running OS services.

### `mco service logs`
View unified log stream for installed OS services.

### `mco fleet apply`
Apply declarative worker deployment modes defined in `~/.mco/fleet.toml`.

### `mco fleet status`
Inspect worker run states declared in `fleet.toml`.

### `mco fleet set`
Update a worker's mode in `fleet.toml`.
- `WORKER`: Worker identifier.
- `KEY`: Setting key (e.g., `mode`).
- `VALUE`: Setting value (`listen`, `wake`, `off`).

---

## 11. Connectors & Enterprise Extensions

### `mco connectors`
List configured enterprise connectors (ServiceNow, Dynatrace, Webhook) and health status.

### `mco sync`
Pull open platform objects (incidents or problems) onto the Job Board.
- `CONNECTOR`: Target connector name (`servicenow`, `dynatrace`).

### `mco platform`
Run a direct control action on an enterprise connector (requires approver-role token).
- `CONNECTOR`: Connector name.
- `ACTION`: Action name (e.g., `resolve_incident`).

### `mco upgrade`
Apply database schema migrations to the configured local or cloud backend.
- `--apply`: Execute pending migrations.

---

## 12. Decision Providers & Policy

### `mco jev route-model`
Ask Jev for a structured, bounded model routing recommendation.
- `PROMPT`: Task description or prompt.

### `mco cio check`
Run deterministic policy checks backing the CIO sidecar brief.

### `mco jobs rank`
Score and rank pending client jobs for fit, value, and execution risk.
