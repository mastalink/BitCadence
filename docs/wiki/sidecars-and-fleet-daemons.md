# Sidecars & Autonomous Fleet Daemons

## Goal
Understand, install, operate, monitor, and troubleshoot BitCadence background sidecars and worker daemons, including the `claude-cio` Chief sidecar, declarative `fleet.toml` management via `mco fleet apply`, and generic elevated administration.

---

## What Sidecars Are (In Plain English)

In human organizations, high-stakes decisions and dispatch cannot halt simply because an executive steps away from their desk. In BitCadence:

- **The Problem:** An interactive AI agent session (such as `claude-desktop` or a developer's IDE session) only runs while the human operator has their computer unlocked and the desktop application running in the foreground. If the operator closes their laptop, locks Windows, or leaves for the evening, the fleet stops: pending approval gates stall, incoming workflow jobs sit unleased in the queue, and background tasks cannot advance.
- **The Solution (Sidecars):** A **sidecar** is an always-on, headless background service that runs alongside the primary agent seat or system component. It acts as an autonomous deputy or event-driven waker:
  1. It monitors incoming mail and job queues in SQLite/gateway memory.
  2. It evaluates strict, deterministic policy checks (e.g., verifying spend budgets, forbidden actions, or test requirements).
  3. When the primary interactive seat is offline, it can approve safe, pre-authorized routine actions or queue/escalate high-risk tasks to the human operator.
  4. When tasks addressed to specialized worker roles arrive, event-driven sidecars wake up the corresponding agent runtime on demand.

In BitCadence, sidecars do not bypass governance — they **enforce** governance 24/7 without requiring an open terminal window on your screen.

---

## Anatomy of the Fleet: Wakers, Runners, and the CIO

BitCadence distinguishes between three interrelated background concepts:

| Component | Role | How It Operates |
|---|---|---|
| **Event Waker (`mco wake`)** | The doorbell listener | A lightweight process holding an event connection to the gateway. When a job addressed to its role/instance arrives, it spawns the heavy worker script. |
| **Worker Runner (`scripts/workers/*`)** | The task executor | The actual shell/PowerShell script (or AI CLI like Codex, Claude, Grok, or Antigravity) that claims the job, checks out a git worktree, runs tests, and submits completion receipts. |
| **`claude-cio` Sidecar** | The Chief deputy | An always-on sidecar for the fleet's Chief seat. Governed by `ops/cio-sidecar-brief.md`, it evaluates mechanical policies via `mco cio check`, approves safe proposals when `claude-desktop` is offline, and escalates out-of-bounds requests. |

---

## Step-by-Step Instructions

### 1. Declarative Fleet Configuration (`fleet.toml`)

All background workers and sidecars are configured declaratively in `~/.mco/fleet.toml`. The system supervisor and `mco fleet apply` consult **only** this file.

Sample configuration (`~/.mco/fleet.toml`):

```toml
# ============================================================================
# BitCadence Fleet Configuration
# ~/.mco/fleet.toml
# ============================================================================

[workers.claude-cio]
role = "chief"
instance = "claude-cio"
mode = "waker"
exec = "python -m mco.orchestrator.cio_runner"
min_interval = 10
poll_interval = 300
background = true       # Installs as unattended Windows Task (S4U logon)

[workers.codex-worker]
role = "codex"
instance = "codex-beast"
mode = "waker"
exec = "C:/AI/BitCadence/scripts/workers/codex-worker-run.ps1"
min_interval = 10
poll_interval = 1800
background = false      # Runs during active interactive desktop session

[workers.research-poller]
role = "claude"
instance = "claude-research"
mode = "poll"           # Runs on a repeating timer rather than instant wake events
exec = "C:/AI/BitCadence/scripts/workers/claude-worker-run.ps1"
poll_interval = 900     # Runs every 15 minutes
```

#### Supported Fields:
- `role`: The target mailbox role (`chief`, `codex`, `claude`, `antigravity`, `grok`).
- `instance`: The unique instance ID of the worker.
- `mode`:
  - `waker`: Event-driven waker that triggers within seconds of job arrival.
  - `poll`: Fixed-interval polling loop.
  - `off`: Configured but disabled; any installed OS service will be uninstalled.
- `exec`: Absolute path or executable command line invoked when work arrives.
- `min_interval`: Minimum seconds between executions (throttling debounce).
- `poll_interval`: Fallback polling interval in seconds.
- `background`: (`true`/`false`, default `false`). On Windows, setting to `true` installs the service with `LogonType = S4U`, allowing it to run unattended when the user is logged off. (Requires administrator privileges during installation).

---

### 2. Applying Fleet Run Modes (`mco fleet apply`)

To reconcile operating system services or scheduled tasks with your `fleet.toml`:

```powershell
mco fleet apply
```

What `mco fleet apply` does:
1. Validates `~/.mco/fleet.toml` against schema and allowed fields.
2. Checks for missing agent tokens in `~/.mco/tokens/<instance>.token`. If a token is missing, it prints an upfront warning:
   ```
   claude-cio: WARNING no token at ~/.mco/tokens/claude-cio.token - the waker will fail to authenticate unless MCO_AGENT_TOKEN is set. Fix: mco reset-token claude-cio
   ```
3. Compares installed OS services against the configuration:
   - Installs new wakers or polling services.
   - Updates changed service configurations.
   - Safely removes services set to `mode = "off"` or omitted from the file.

---

### 3. Checking Fleet Status

Query the reconciled state of all declared workers:

```powershell
mco fleet status
```

Output displays:
- **Worker Name & Instance ID**
- **Role**
- **Mode (`waker`, `poll`, `off`)**
- **Active Service Name** (e.g., `BitCadence-wake-chief-claude-cio`)
- **OS Service Status** (`Running`, `Ready`, `Disabled`, or `Not Installed`)

To check live presence reported by the gateway:

```powershell
mco agents
```

Inspect `last_seen_seconds` and `connected` status.

---

### 4. Stopping and Removing Sidecars

To stop or remove a sidecar cleanly:

1. **Via `fleet.toml`:**
   Change the worker's mode to `off`:
   ```powershell
   mco fleet set claude-cio mode off
   mco fleet apply
   ```
   Or remove the `[workers.claude-cio]` section entirely from `~/.mco/fleet.toml` and run `mco fleet apply`.

2. **Via `mco service` CLI:**
   ```powershell
   # Stop a running service immediately
   mco service stop BitCadence-wake-chief-claude-cio

   # Uninstall the scheduled task from the OS
   mco service uninstall BitCadence-wake-chief-claude-cio
   ```

3. **Via the Desktop Control Window:**
   - In the component table, select the worker row.
   - Click **Stop selected**.
   - Click **Fleet settings** to edit `fleet.toml`, then click **Reload settings**.

---

### 5. The Elevated Admin-Pack Step (Generic Open-Source Guide)

While standard wakers can run in the user's interactive session without administrator privileges, unattended background sidecars (and local CI/CD service runners) require operating system capabilities that standard user accounts cannot grant.

#### Why Administrator Elevation Is Required on Windows:
1. **Unattended Execution (`S4U` Logon):** When `background = true` is set, Task Scheduler registers the task with `LogonType = S4U`. Windows requires Administrator elevation to register S4U tasks without storing cleartext passwords.
2. **Dedicated Service Accounts:** Creating an isolated local user account (e.g., `bc-runner`) to sandbox code execution prevents workers from accessing private personal folders (`%USERPROFILE%\Documents`, SSH keys, or cloud credentials).
3. **Firewall Binding:** Narrowing local model listening ports (e.g., binding model servers strictly to `127.0.0.1`) requires Windows Advanced Firewall modifications.

#### Generic Administrator Script Flow:
When setting up elevated services, maintainers use a generic administrator script (e.g., `admin-pack.ps1`). It must follow these strict security principles:
- **No Personal Paths:** Uses environment variables (`$env:USERPROFILE`, `$env:ProgramData`, `$PSScriptRoot`) rather than hardcoded developer paths.
- **Elevation Check:** Checks for admin rights at startup:
  ```powershell
  $principal = New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())
  if (-not $principal.IsInRole([Security.Principal.WindowsBuiltinRole]::Administrator)) {
      Write-Error "This script must be run as Administrator (right-click PowerShell > Run as administrator)."
      exit 1
  }
  ```
- **Plain-English Confirmation:** Outlines all pending changes and requires typing `YES` before modifying system state.
- **Idempotency:** Safe to run repeatedly; skips tasks that are already configured.
- **Clean `-Undo` Support:** Allows one-step uninstallation of all services, rules, and accounts:
  ```powershell
  powershell -ExecutionPolicy Bypass -File scripts\admin-pack.ps1 -Undo
  ```

---

## Operating the `claude-cio` Sidecar

The `claude-cio` sidecar operates under a formal operating policy defined in `ops/cio-sidecar-brief.md`.

### Presence & Dual-Mode Behavior
Before making any decision, `claude-cio` queries `mco_agents`:

```
                 +-----------------------------------------+
                 |            mco_agents Query             |
                 +-----------------------------------------+
                                      |
                 Is claude-desktop online? (heartbeat < 300s)
                                      |
                      +---------------+---------------+
                      |                               |
                   [ YES ]                         [ NO ]
                      |                               |
             +-----------------+             +-----------------+
             |     Mode A      |             |     Mode B      |
             |   DEFER / CIO   |             |   ACT AS CHIEF  |
             +-----------------+             +-----------------+
             | Posts advisory  |             | Full dispatch   |
             | recommendations |             | authority under |
             | only; leaves    |             | standing policy |
             | binding votes   |             | limits.         |
             | to desktop.     |             |                 |
             +-----------------+             +-----------------+
```

### Policy Checks (`mco cio check`)
When approving plans, `claude-cio` executes deterministic command-line checks before applying LLM reasoning:

```powershell
mco cio check --decider claude-cio --project Via --spend-cents 450 --proposed-by codex-beast --category code_review
```

#### Hard Standing Policies Enforced:
1. **Self-Approval Forbidden:** An agent cannot approve its own proposal. Missing, whitespace, or case-mismatched proposer IDs fail closed.
2. **Project Spend Caps:** Hard monthly limits (e.g., Via: $150, Sim Lab: $25 paper only, Operate: $10). Unrecognized projects or proposals exceeding caps automatically escalate.
3. **Strict Escalation to Humans:** Requires escalation to human operators for:
   - Credential or token modifications.
   - Financial transactions or live broker connections.
   - Deleting databases or household data.
   - Bypassing bot protections or CAPTCHAs.

---

## How to Know a Sidecar is Healthy or Stuck

Do not rely solely on "Green" or "Online" lights in dashboards. True operational health follows **four distinct verification levels**:

```
+------------------------------------------------------------------------+
| Level 1: Supervised Process Exists                                     |
|          OS process or Task Scheduler task is active (PID assigned)    |
+------------------------------------------------------------------------+
                                   |
+------------------------------------------------------------------------+
| Level 2: Token Authenticates                                           |
|          Gateway accepts credentials without HTTP 401 Unauthorized     |
+------------------------------------------------------------------------+
                                   |
+------------------------------------------------------------------------+
| Level 3: Leases Pending Work                                           |
|          Pulls jobs from queue when assigned; state changes to leased  |
+------------------------------------------------------------------------+
                                   |
+------------------------------------------------------------------------+
| Level 4: Delivers Completed Receipts                                   |
|          Executes instructions, runs tests, posts mco_complete         |
+------------------------------------------------------------------------+
```

### End-to-End Fleet Smoke Test
To verify that a sidecar is actually capable of executing work, send an inert, reply-only smoke test:

```powershell
mco send chief --instance claude-cio -t "FLEET SMOKE TEST - reply only" `
  -m "Do NOT edit files or commit. Call mco_complete(task_id, output) with your AGENT_INSTANCE_ID, the UTC time, and 'fleet smoke ok'. Then stop."
```

Then check job progression:
```powershell
mco jobs --limit 5
```
Verify the task moves from **`pending`** to **`leased`** and finally **`completed`**.

---

## If It Goes Wrong (Real Incident Field Guide)

### Incident 1: Worker Shows "Online/Standby" but Silently Never Runs (Locked Log File)

#### The Symptom:
The web console and `mco agents` show the worker with a green dot and status `online` or `standby`. Jobs dispatched to the role sit in the queue indefinitely without being leased. No errors appear on the web dashboard.

#### The Root Cause:
On Windows, file streams use mandatory file sharing locks. If a previous run crashed or a zombie process held an open handle to `~/.mco/logs/<instance>.log` without shared write permissions:
1. The waker process maintains a healthy WebSocket/SSE connection to the gateway (reporting `online`).
2. When a job arrives, the waker attempts to spawn the worker script (`--exec`) and redirect stdout/stderr to `~/.mco/logs/<instance>.log`.
3. Windows denies the file open request (`PermissionError: [Errno 13]` or `The process cannot access the file because it is being used by another process`).
4. The worker executor exits with code 1 immediately without ever claiming or leasing the job.

#### How to Diagnose:
1. Inspect the waker log:
   ```powershell
   Get-Content ~\.mco\logs\BitCadence-wake-<role>-<instance>.log -Tail 30
   ```
2. Test whether the log file is locked:
   ```powershell
   [IO.File]::Open("$env:USERPROFILE\.mco\logs\<instance>.log", "Open", "Write", "None").Close()
   ```
   If this throws an IOException, the file is locked by a lingering process.

#### How to Fix:
1. Find and terminate orphaned processes holding the lock:
   ```powershell
   Get-Process -Name python, pythonw, powershell | Where-Object { $_.Path -like "*BitCadence*" }
   ```
2. Kill the zombie process:
   ```powershell
   Stop-Process -Id <PID> -Force
   ```
3. Restart the waker service:
   ```powershell
   mco service restart BitCadence-wake-<role>-<instance>
   ```

---

### Incident 2: Duplicate Wake Processes Competing for Jobs

#### The Symptom:
When a single job is posted, two different instances attempt to process it simultaneously. You observe:
- HTTP `409 Conflict` lease errors in logs.
- Two git worktree folders created for the same job.
- Git merge collisions (`index.lock` errors) in the repository.
- Alternating heartbeats in `mco agents` with flickering process IDs.

#### The Root Cause:
A waker was registered in multiple supervisors at the same time:
1. Installed as a Windows Scheduled Task or systemd service via `mco service install-waker` or `mco fleet apply`.
2. Simultaneously started manually in a PowerShell terminal via `mco wake --role ...` or `mco listen ...`, OR launched by the Desktop Control Manager (`desktop.pyw`).

Both processes receive the SSE notification simultaneously and race to lease the job.

#### How to Diagnose:
1. Search all running processes for duplicate wakers:
   ```powershell
   Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like "*mco.cli wake*" } | Select-Object ProcessId, CommandLine
   ```
2. Inspect active Task Scheduler tasks:
   ```powershell
   schtasks /query /fo LIST /v | Select-String "TaskName:.*BitCadence-wake"
   ```

#### How to Fix:
1. Terminate all duplicate instances:
   ```powershell
   Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like "*mco.cli wake*" } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }
   ```
2. Decide on a single supervision method:
   - **Either** use the Windows Desktop Control Manager (`scripts/desktop.pyw --start-all`), which automatically handles single-instance deduplication and Job Object cleanup.
   - **Or** use OS Task Scheduler via `mco fleet apply`. If using the Desktop Control Manager, click **Move workers into app** to disable redundant Task Scheduler entries.
3. Verify that exactly one waker process is running per configured worker.
