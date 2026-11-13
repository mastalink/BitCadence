> **Historical audit.** This was written against the console before the v0.6.0 redesign. The `img/` names below refer to screenshots that were replaced or removed (the workflow builder, Flow Control and agent fleet screens no longer exist). Helpers, Ask and Connect an AI replaced them.

# BitCadence Friction Inventory & Flow Surfaces Audit

This document presents a comprehensive, hands-on audit of user friction across every user-facing surface of BitCadence, evaluated against two non-technical personas:
- **Persona A (Gen-X / Older Adult):** An intelligent professional who was not raised on computers, feels intimidated by terminals, command prompts, environment variables, and cryptic error messages, and expects clear visual guidance.
- **Persona B (Smartphone-Native Teen):** A user raised entirely on iOS/Android who expects 1-tap actions, zero configuration, no manual copying of ports or URLs, and instant visual gratification.

---

## Part 1: Top 10 Friction Points

Ranked in order of severity and onboarding impact:

| Rank | Friction Point | Primary Cause | Impacted Personas | Severity |
|:---:|---|---|---|:---:|
| **1** | **The "Token & Gateway URL Dance"** | After double-clicking install, user must paste `mco_tok_...` and confirm `http://127.0.0.1:18789`. If lost, requires hunting in `.env`. | Persona A & B | **CRITICAL** |
| **2** | **"Keep This Black Window Open"** | Server launches as a raw terminal window. Closing it immediately terminates the backend. | Persona A & B | **CRITICAL** |
| **3** | **Connecting AI Models via MCP Requires Manual JSON Editing** | Users must find hidden paths like `%APPDATA%\Claude\claude_desktop_config.json` and hand-write JSON blocks with paths and tokens. | Persona A & B | **CRITICAL** |
| **4** | **Jargon-Heavy Roles & Targeting** | Creating a job asks for a "Target Role" (`codex`, `claude`, `gemini`) without explaining what models or capabilities map to them. | Persona A & B | **HIGH** |
| **5** | **Running Background Workers Requires Terminal Daemon** | Making an agent execute tasks requires typing `mco listen --role codex --instance ...` in a separate open CMD window. | Persona A & B | **HIGH** |
| **6** | **Two Divergent Flow Builders** | The console has a Workflow Builder tab (`/console`), while `/flow` has an entirely separate Design Mode with different controls. | Persona A & B | **HIGH** |
| **7** | **Hidden Prerequisites for Approvals** | If a user registers an agent without an approver role, the Approve button fails with an obscure `403` error naming missing scopes. | Persona A | **MEDIUM** |
| **8** | **Authoring Recurring Loops Requires Hand-Editing YAML & Cron** | Setting up automated tasks requires editing `~/.mco/schedules.yaml` and memorizing 5-field cron syntax (`0 2 * * *`). | Persona A & B | **MEDIUM** |
| **9** | **Autonomous Scores Require Raw JSON Schema Authoring** | Using the Score Conductor requires authoring complex multi-level JSON files with explicit digests and capability arrays. | Persona A & B | **MEDIUM** |
| **10** | **Secret Store Keychain Discrepancy Across Platforms** | Seamless auto-unlock works on Windows Credential Manager, but silently halts on Linux/macOS unless an environment variable is exported. | Persona A | **MEDIUM** |

---

## Part 2: Comprehensive Friction Inventory Table

Every user-facing task taking more than 2 steps or requiring technical knowledge:

| # | Task | UI Steps | CLI Steps | Technical Knowledge Required | Point of Failure / Confusion for Personas | Severity | Screenshot Ref |
|:---:|---|:---:|:---:|---|---|:---:|---|
| **1** | **Install & First Run** | 2 | 5 | Shell execution, virtual environments, path variables | **Persona A:** Intimidated by scrolling command lines. Fears black window is a virus.<br>**Persona B:** Wonders why there isn't an `.exe` installer or App Store download. | **Medium** | `img/20-install-cli-start.png` |
| **2** | **Connecting Web Console** | 4 | 3 | Tokens, localhost, ports, bearer auth | **Persona A:** Doesn't understand clipboard; loses token if clipboard cleared.<br>**Persona B:** Expects automatic login or single sign-on. Doesn't know what `127.0.0.1` means. | **Critical** | `img/02-console-settings.png` |
| **3** | **Creating a Gated Task** | 5 | 1 | JSON payload, agent roles, retry budgets | **Persona A:** Confused by "Target role" — doesn't know who Codex or Claude are.<br>**Persona B:** Expects a chat prompt bar like ChatGPT or iMessage, not a multi-field database form. | **High** | `img/14-job-create-modal.png` |
| **4** | **Deciding an Approval Gate** | 3 | 1 | Governance states, gate interlocks | **Persona A:** Clicks approve but doesn't realize workers must be running to execute it.<br>**Persona B:** Wonder why they have to click approve if the AI is supposed to be autonomous. | **Medium** | `img/05-console-approvals.png` |
| **5** | **Authoring a Workflow (Console Builder)** | 6 | 2 | DAG structures, step IDs, dependency lists | **Persona A:** Confused by cards and wiring lines. Doesn't know how to write instructions.<br>**Persona B:** Expects visual templates like Canva or Shortcuts; finds empty boxes intimidating. | **High** | `img/07-console-workflows.png` |
| **6** | **Authoring a Workflow (Flow Control Design)** | 8 | N/A | Drag-and-drop, port crosshairs, acyclic graphs | **Persona A:** Dragging port crosshairs requires precise mouse control; fails to connect.<br>**Persona B:** Wonder why this isn't integrated into the main console interface. | **High** | `img/12-flow-control-design.png` |
| **7** | **Registering an Agent** | 4 | 1 | Instance IDs, role scopes, SHA-256 tokens | **Persona A:** Closes dialog before copying token; permanently locked out of agent token.<br>**Persona B:** Expects QR code or 1-tap device pairing like AirDrop or Apple Watch. | **High** | `img/17-agent-register-panel.png` |
| **8** | **Connecting External Agent (Claude/Codex via MCP)** | 7 | 4 | JSON editing, config paths, environment variables | **Persona A:** Cannot locate hidden `%APPDATA%` folders. Breakers JSON syntax with missing commas.<br>**Persona B:** Has never edited a JSON file in their life. Refuses to use app. | **Critical** | `img/08-console-agents.png` |
| **9** | **Running a Background Worker** | 4 | 2 | Terminal processes, environment variables, polling loops | **Persona A:** Closes command window; agent disappears from fleet.<br>**Persona B:** Expects background execution to "just work in the cloud". | **Critical** | `img/19-desktop-control-window.png` |
| **10** | **Scheduling Recurring Loops** | 6 | 3 | YAML editing, cron 5-field syntax, service install | **Persona A:** Terrified of `0 2 * * *`. Doesn't understand UTC time.<br>**Persona B:** Expects a clock picker like iPhone Alarms ("Repeat every day at 9am"). | **High** | `img/10-console-activity-audit.png` |
| **11** | **Saving Drumline Memory** | 4 | 1 | Kind categories (`fact`, `decision`, `lesson`), tags | **Persona A:** Doesn't understand difference between a "fact" and a "lesson".<br>**Persona B:** Wonders why memories aren't auto-indexed from chat without manual entry. | **Medium** | `img/09-console-drumline-memory.png` |
| **12** | **Triggering Emergency Kill Switch** | 3 | 2 | Panic button concept, lease draining | **Persona A:** Relieved by big red switch, but confused why in-flight jobs keep running.<br>**Persona B:** Understands the switch, but forgets to turn it back off when finished. | **Medium** | `img/06-console-governance.png` |
| **13** | **Configuring LLM Provider Keys** | 5 | 2 | API keys, secret vaults, provider URLs | **Persona A:** Doesn't know what an API key is or where to get one from Anthropic/OpenAI.<br>**Persona B:** Expects "Sign in with Google / Apple" subscription billing. | **High** | `img/02-console-settings.png` |
| **14** | **Reassigning a Failed Job** | 4 | 1 | Clone linking, role capabilities, audit lineage | **Persona A:** Doesn't understand why they can't just "fix the bug" in place.<br>**Persona B:** Expects an automatic "Try Again" button that uses a smarter model. | **Medium** | `img/15-job-detail-drawer.png` |
| **15** | **Authoring a Score Contract** | 10 | 4 | JSON Schema, cryptographic digests, locks, budget cents | **Persona A & B:** Both completely blocked. Score authoring is strictly for distributed systems engineers. | **High** | `img/16-autonomy-scores-modal.png` |
| **16** | **Installing Desktop GUI & Dependencies** | N/A | 3 | PowerShell execution policy, Python path resolution, pip target folders | **Persona A:** Blocked by default Windows ExecutionPolicy `Restricted` error. Fears running scripts.<br>**Persona B:** Expects a double-click `.exe` installer; refuses to run terminal commands to install an app. | **High** *(Needs Terminal)* | `img/19-desktop-control-window.png` |
| **17** | **Configuring Autostart on Sign-in** | N/A | 3 | Absolute file paths, `pythonw.exe` vs `python.exe`, Windows Startup folder | **Persona A:** Cannot locate where `pythonw.exe` is installed. Does not know how to pass arguments.<br>**Persona B:** Expects a simple toggle switch in Settings ("Launch on startup") like Discord or Spotify. | **High** *(Needs Terminal)* | `img/19-desktop-control-window.png` |
| **18** | **Declarative Fleet Orchestration (`fleet.toml`)** | 3 | 4 | TOML table syntax, file path escaping, worker modes (`waker`, `poll`, `off`) | **Persona A:** Clicking "Fleet settings" opens a terrifying code file in Notepad. Syntax error crashes app.<br>**Persona B:** Expects an "Add Agent" button with dropdowns, not hand-editing configuration files. | **High** *(Needs Terminal & Editor)* | `img/19-desktop-control-window.png` |
| **19** | **Installing Background Sidecars with S4U Logon** | N/A | 3 | Windows UAC, Administrator elevation, Task Scheduler S4U security model | **Persona A:** Blocked by "Access is denied". Panics at "Run as administrator" blue prompt.<br>**Persona B:** Never uses a terminal or admin elevation. Unattended background workers fail silently. | **Critical** *(REQUIRES ADMIN)* | `img/08-console-agents.png` |
| **20** | **Elevated System Admin Pack (`admin-pack.ps1`)** | N/A | 4 | Windows Administrator rights, service account creation, ACL manipulation, firewall rules | **Persona A:** Cannot find "Run as Administrator" in Start Menu search. Panics when asked to type `YES`.<br>**Persona B:** Completely alienated by command-line system administration. | **Critical** *(REQUIRES ADMIN)* | `img/06-console-governance.png` |
| **21** | **Authoring & Wiring `claude-cio` Sidecar Policy** | N/A | 5 | Policy kernels, spend arithmetic, CLI flags (`--spend-cents`), token files | **Persona A:** Does not understand how an AI deputy makes decisions. Overwhelmed by spend caps.<br>**Persona B:** Wonders why there isn't an iOS-style "Ask to Buy" notification sent to their phone. | **High** *(Needs Terminal & Tokens)* | `img/05-console-approvals.png` |
| **22** | **Diagnosing "Online/Standby" Locked Log Lockout** | N/A | 6 | File sharing locks, Windows process inspection, PID resolution, process termination | **Persona A:** Completely misled by the green status light. Thinks it works while jobs sit forever.<br>**Persona B:** Sees nothing happening, assumes app is broken, abandons it. | **Critical** *(Needs Terminal & Diagnostics)* | `img/08-console-agents.png` |
| **23** | **Detecting & Resolving Duplicate Wake Processes** | N/A | 5 | Windows WMI/CIM process queries, Task Scheduler syntax, race conditions, worktree locks | **Persona A:** Baffled by duplicate tasks, random errors like `409 Conflict` or git `index.lock`.<br>**Persona B:** Expects the app to prevent or merge duplicate instances automatically. | **Critical** *(Needs Terminal & WMI)* | `img/19-desktop-control-window.png` |
| **24** | **Migrating Scheduled Tasks to Desktop App** | 2 | 3 | Task Scheduler vs Desktop Supervisor process ownership | **Persona A:** Doesn't understand what "Move workers into app" means. Fears it might delete work.<br>**Persona B:** Confused why there are multiple ways workers run in the first place. | **Medium** *(Multi-step migration)* | `img/19-desktop-control-window.png` |

---

### Critical Friction Analysis: Elevation & Terminal Barriers

A focused audit of sidecar management and desktop operations reveals a severe divide between the GUI vision and operational reality:

#### 1. Tasks Blocked by "Run as Administrator" (UAC Elevation)
- **Installing Unattended Sidecars (`background = true` / S4U Logon):** Windows Task Scheduler refuses to register S4U logon tasks from unattended or standard user shells without Administrator elevation (`Access is denied`).
- **Local Service Accounts & ACL Hardening (`admin-pack.ps1`):** Creating dedicated service accounts and locking them out of private directories (`%USERPROFILE%`, personal documents, SSH keys) requires full local Administrator rights.
- **Port Narrowing & Firewall Rules:** Narrowing inbound ports to localhost (`127.0.0.1`) requires elevated PowerShell.
- **Impact on Personas:**
  - **Persona A (Older Adult):** Freezes at Windows UAC prompts. Cannot locate "Run as administrator" in the Start Menu and assumes system warnings indicate dangerous activity.
  - **Persona B (Teen):** Has never encountered an administrative prompt or terminal on mobile devices. Assumes the software is fundamentally broken when permission errors occur.

#### 2. Tasks Forcing Users into a Terminal / PowerShell
- **Desktop Shortcut & Dependency Setup:** `install_desktop.ps1` requires running a command line with explicit `-Python` paths.
- **Autostart at Sign-in:** `desktop_autostart.ps1` requires passing `-Pythonw "C:\path\to\pythonw.exe"`. Neither persona knows where their virtual environment's `pythonw.exe` binary resides.
- **Fleet Reconciliation (`fleet.toml`):** Changing worker allocation requires editing TOML syntax in Notepad and running `mco fleet apply`.
- **Silent Failure Diagnostics (Locked Logs & Duplicate Wakers):** When a worker appears "online" but fails silently, diagnosing locked files (`~/.mco/logs/<instance>.log`) or detecting duplicate wakers requires complex PowerShell commands (`Get-Process`, `Get-CimInstance Win32_Process`, `schtasks /query`).

---

## Part 3: Flow Surfaces Audit

### 1. Inventory of Flow Surfaces in BitCadence
The audit identified **five distinct surfaces** where workflows, execution flows, or Scores are displayed or authored:

1. **Overview Dashboard Stepper (`WorkflowStrip`):**
   - **Location:** `http://127.0.0.1:18789/console` (Overview tab).
   - **User Capabilities:** Read-only inspection. Groups active jobs by `workflow` name and renders a horizontal linear stepper showing step completion and current running task.
   - **Interaction:** Clicking any step opens the Job Detail Drawer. Cannot edit, re-order, or add steps.

2. **Console Workflow Builder (`WorkflowBuilder`):**
   - **Location:** `http://127.0.0.1:18789/console` (Workflows tab).
   - **User Capabilities:** Authoring and submitting DAGs. Users can add step cards, edit step IDs, select target roles, type prompt instructions, toggle approval gates, and export/import YAML.
   - **Interaction:** Visual card graph. Step connections can be added. Submits directly via `POST /api/workflows`.

3. **Flow Control Live Board (`/flow`):**
   - **Location:** `http://127.0.0.1:18789/flow` (Live board mode).
   - **User Capabilities:** Real-time operational mimic. Renders every job on the board with true `depends_on` arrows, animated dashed lines for active data flows, and color-coded status badges.
   - **Interaction:** Clicking nodes opens a side inspector where operators can directly click **Approve**, **Reject**, **Retry**, or **Cancel**. Strictly read-only for layout; graph structure is driven 100% by live database state.

4. **Flow Control Design Mode (`/flow`):**
   - **Location:** `http://127.0.0.1:18789/flow` (Design workflow mode).
   - **User Capabilities:** Visual CAD/drafting canvas. Features a grid canvas, step stencil rail, drag-to-place cards, and interactive circular port connectors (`then` → `needs`).
   - **Interaction:** Authoring only. Features local DAG validation, cycle detection, YAML export, and a separate red **Run workflow** confirmation button.

5. **Autonomous Score Conductor (`AutonomyLiveLookModal` & CLI):**
   - **Location:** `http://127.0.0.1:18789/console` (Live look modal) and `mco score`.
   - **User Capabilities:** Monitoring long-running autonomous Score contracts. Shows active goal stages, target agents, progress percentage, and lease status.
   - **Interaction:** Offline CLI compiles and validates JSON contracts. Conductor advances runs automatically based on evidence receipts.

---

### 2. Does an Editable Flow Builder Make Sense?

#### The Verdict: **Partially Yes, but the Current Mental Model is Inverted**

- **Why it makes sense:**
  - **Visual Interlock Verification:** For enterprise operators, compliance auditors, and safety engineers, visually seeing the dependency chain and where the human approval gate sits is irreplaceable. A flowchart is self-documenting in a way raw YAML or JSON never is.
  - **Live Progress Visualization:** Flow Control (`/flow`) with its animated flowing edges is an outstanding operational tool. It immediately communicates bottlenecks (e.g., three tasks blocked waiting for a slow build step).

- **Why an editable drag-and-drop canvas has severe friction:**
  - **The "Blank Canvas Problem":** Asking a user to drag boxes, invent IDs like `step-1`, memorize model roles, and drag crosshair wires between ports is 1990s CAD software UX. Modern users (especially mobile-native teens and busy managers) do not want to wire boxes.
  - **Duplication & Confusion:** Having two separate builders (`/console#workflows` vs `/flow#design`) creates confusion. They share the same underlying YAML schema but use completely different UIs.
  - **Recommendation for Redesign:**
    1. **Merge the surfaces:** Keep `/flow` as the single canonical Flow Control surface.
    2. **Conversational Prompt-to-Flow:** Replace manual box-dragging with a natural-language flow generator powered by Jev:
       > *"User types: 'Research open PRs, run tests, and if green, draft release notes and ask me to publish.' -> Jev automatically compiles the 4-step DAG with the approval gate pre-configured."*
    3. The visual canvas should be used for **reviewing and tweaking**, not building from scratch.

---

### 3. Score Data Available to Auto-Generate a Live Flowchart

Score state is split between the immutable score document and the run's execution tables. Column names below are from `src/mco/migrations/2026-09_score_store.sql` (Postgres) and the task shape validated in `src/mco/orchestrator/scores.py`. The local SQLite conductor (`~/.mco/score-runs.db`) keeps the same concepts but is a separate store; check its schema before binding a UI to it.

| Data Element | Where it actually lives | Representation in a Live Flowchart |
|---|---|---|
| **Nodes (Tasks / Goals)** | `score_documents.document` (JSONB): `tasks[]` with `id`, `goal`, `title`, `role`; run-time rows in `score_tasks` (`run_id`, `task_id`, `attempt`) | Node cards labeled with task id and title |
| **Dependencies** | `depends_on` list on each task inside `score_documents.document`. It is **not** a `score_tasks` column. | Directed arrows from prerequisite to dependent |
| **Execution States** | `score_tasks.status`. Statuses seen in the conductor code include `running`, `review`, `accepted`; the full set is defined in `scores.py`, not in the migration. | Status coloring, mapped from whatever values the conductor emits |
| **Resource Locks** | `resources` list on each task inside the score document. Tasks that share a resource are serialized by the conductor; there is no lock column. | Badge on nodes that share a resource lane |
| **Assigned Workers** | `score_tasks.author_id` and `score_tasks.author_provider`; reviewers in `score_reviews.reviewer_id`; catalog in `score_providers` | Worker chip on the active node |
| **Budget** | `score_runs.authorized_budget_cents`, `score_grants.budget_cents`; per-task `max_cost_cents` in the document | Run-level cost meter |
| **Human Checkpoints** | `checkpoint` flag on the task inside the document; approval recorded in `score_tasks.checkpoint_approved` (boolean) | Shield icon or pause barrier on the node |
| **History** | `score_events` (append-only: `seq`, `run_id`, `kind`, `task_id`, `at`, `actor`, `payload`) | Timeline or replay |

---

### 4. APIs & Events Available for Live Updates

Verified against the gateway routes in `src/mco/cli.py`, `src/mco/orchestrator/routes.py` and `score_gate_routes.py`:

1. **WebSocket Broadcast Feed (`/ws/broadcast`):**
   - The gateway pushes one frame per job event. The envelope is `type` plus a nested `payload`, which carries the event name and the full job row:
     ```json
     {
       "type": "event",
       "payload": {
         "event": "status:completed",
         "job": { "id": "88f57ed8-4069-...", "status": "completed" }
       }
     }
     ```
   - Frames are filtered per connection: non-admin identities only receive jobs addressed to their own role or instance. No latency figure has been measured, so none is claimed here.

2. **Job Event Stream API (`GET /api/jobs/{id}/events`):**
   - Returns the tamper-evident event log for one job.

3. **Project view (`GET /api/jobs/project-view`):**
   - Grouped job data that the Projects dashboard polls.

4. **Score gates and grants (`/api/score/gates`, `/api/score/grants`):**
   - These are the only Score HTTP routes in the gateway today. They expose human gates and grants, not run or task state.

5. **No Score run or event routes yet:**
   - There is no `GET /api/scores/runs/{run_id}` and no `GET /api/scores/{digest}/events`. A live Score flowchart would need new read-only routes over `score_runs`, `score_tasks` and `score_events`, or it can read run state through `mco score status --run-id <run-id>` on the conductor host.

6. **Polling Fallback:**
   - If WebSocket is blocked by a corporate proxy, poll `GET /api/jobs` or `GET /api/jobs/project-view` on a short interval.
