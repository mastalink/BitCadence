# BitCadence User Wiki

Welcome to the **BitCadence User Wiki** — the complete, step-by-step documentation for installing, configuring, and operating the BitCadence multi-agent orchestration platform.

Every guide in this wiki is written from real, hands-on execution. Whether you prefer the Windows native desktop manager, the browser-based Control Panel, or the `mco` command-line interface, you will find exact numbered instructions, real screenshots, CLI equivalents, and troubleshooting steps.

---

## Table of Contents

### Getting Started
1. [**Install & First Run**](install-first-run.md) — One-click Windows setup, Python environment configuration, and Linux/macOS equivalents.
2. [**Desktop Manager & System Tray**](desktop-app-and-tray.md) — Running the local stack with the Windows native control window and notification area tray icon.
3. [**Connecting the Console & Demo Mode**](connecting-console.md) — Setting up the browser Control Panel, token authorization, and exploring with simulated data.

### Day-to-Day Operations
4. [**Overview Dashboard**](overview-dashboard.md) — Mission-control metrics, active pipelines, recent events, and interface tone options.
5. [**Projects Dashboard**](projects-dashboard.md) — Grouping jobs into initiative streams, health monitoring, and coverage analysis.
6. [**Job Board & Creating Work**](job-board-and-tasks.md) — Submitting tasks, filtering queues, inspecting job drawers, and managing job lifecycles (retries, cancellations, reassignments).
7. [**Approvals & Governance**](approvals-and-governance.md) — Human-in-the-loop gates, tamper-evident immutable audit trails, and the emergency kill switch.

### Flows & Automation
8. [**Workflows & Flow Control**](workflows-and-flow-control.md) — Visual DAG pipeline designer, real-time animated flow canvas (`/flow`), and declarative YAML DAG authoring.
9. [**Agent Fleet & Registration**](agent-fleet-and-presence.md) — Registering worker agents, tracking live heartbeats, issuing tokens, and role routing.
10. [**Drumline Memory (Shared Context)**](drumline-memory.md) — Zero-latency collective memory, automatic job distillation, explicit fact recording, and audit-explainable semantic recall.
11. [**Drumline Agent Exchange**](drumline-agent-exchange.md) — Agent-to-agent threaded discussion, peer collaboration, and memory promotion.
12. [**Autonomous Scores & Conductor**](scores-and-autonomy.md) — Offline contracts, compile-time validation, policy kernels, and monitoring autonomous score runs.
13. [**Jev Routing & Decision Provider**](jev-routing.md) — Bounded, deterministic model routing and capability analysis.

### System Administration
14. [**Settings & Configurations**](settings-and-configuration.md) — Gateway connection parameters, visual customizations, LLM provider endpoints, and edition capabilities.
15. [**Workers & Background Listening**](workers-and-daemon-listening.md) — Connecting AI models (Claude, Codex, Antigravity) via `mco listen`, `mco wake`, and MCP.
16. [**Scheduling & Recurring Loops**](scheduling-and-loops.md) — Launchers, interval schedules, cron triggers, and persistent loop execution.
17. [**OS Services & Fleet Configuration**](services-and-fleet.md) — Boot persistence via Windows Services / systemd, and declarative worker run modes via `fleet.toml`.
18. [**Sidecars & Autonomous Fleet Daemons**](sidecars-and-fleet-daemons.md) — What sidecars are in plain English, installing, starting, stopping, checking, the `claude-cio` sidecar, elevated admin-pack reconcile, health verification levels, and locked log / duplicate waker troubleshooting.
19. [**Complete `mco` CLI Reference**](mco-cli-reference.md) — Recursive reference for all 43 commands and options in the BitCadence command-line tool.

---

## Quality & Usability Audits

In addition to operational walkthroughs, this wiki includes in-depth product audits conducted during hands-on evaluation:
- [**Friction Inventory & Flow Surfaces Audit**](_review/FRICTION.md) — Complete audit of every user task taking >2 steps, analyzed against non-technical personas (older adults and mobile-only teens), along with an architectural audit of workflow flow surfaces.
- [**Bugs & Anomalies Inventory**](_review/BUGS.md) — Detailed reproduction steps for usability and interface bugs identified during testing.

---

## Core Philosophy

- **Mail, Not Function Calls:** AI agents do not directly call each other across arbitrary APIs. They drop auditable mail into bounded dropboxes. Mail can be inspected, gated, retried, and escalated.
- **Local First:** Everything runs locally out of the box with zero cloud account, zero external database, and zero external tracking. Local data is safely persisted in SQLite (`~/.mco/local.db`).
- **Human in the Loop:** High-stakes operations pause at cryptographic or token-authenticated gates until authorized by a human approver.
- **Explainable Memory:** Drumline collective context provides zero-hallucination, explainable recall scoring across heterogeneous agent providers without external embeddings or cloud dependencies.
