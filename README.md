# BitCadence

**Every agent. One beat.**

[![CI](https://github.com/mastalink/Bitcadence/actions/workflows/ci.yml/badge.svg)](https://github.com/mastalink/Bitcadence/actions/workflows/ci.yml)
[![MIT License](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
[![Python 3.9+](https://img.shields.io/badge/python-3.9%2B-blue.svg)](https://www.python.org/downloads/)
[![Platform](https://img.shields.io/badge/platform-Windows%20%7C%20macOS%20%7C%20Linux-lightgrey.svg)](docs/INSTALL.md)
[![Changelog](https://img.shields.io/badge/changelog-0.2.0-blue.svg)](CHANGELOG.md)

A self-hosted orchestration hub for AI agents: a governed job board with
human approval gates, an immutable audit trail, and **Drumline** — one shared
memory every agent reads and writes. Runs entirely on your machine; no cloud
account required.

---

## Install

### macOS / Linux — one command

```bash
curl -sSf https://bitcadence.ai/install.sh | bash
```

Clones the repo to `~/BitCadence`, finds/installs Python, creates the venv,
generates your access token, adds `mco` to your PATH, then asks: demo mode
or connect now. Takes about two minutes.

```bash
# If you already cloned:
bash scripts/install.sh
```

### Windows — one double-click

Download the ZIP from GitHub, extract it anywhere, then double-click
**`install.bat`**. It finds (or installs) Python, builds the venv, generates
your access token, and drops a **BitCadence** shortcut on the Desktop.
Your browser opens the console automatically.

```powershell
# PowerShell one-liner (no ZIP download needed):
iwr -useb https://bitcadence.ai/install.ps1 | iex

# Or headless / CI:
powershell -ExecutionPolicy Bypass -File scripts\install.ps1 -NoPrompt
```

Full walkthrough and troubleshooting: [docs/INSTALL.md](docs/INSTALL.md)

### pip / Docker

```bash
pip install bitcadence   # from PyPI (released versions)
# or from source:
git clone https://github.com/mastalink/Bitcadence
pip install -e Bitcadence
mco setup --guided    # configure in 60 seconds
mco start             # console at http://127.0.0.1:18789/console
```

```bash
# Docker (team / cloud):
docker compose up     # see docs/DEPLOYMENT.md
```

---

## What it does

BitCadence sits between your agents and the work they do. It gives you:

| | |
|---|---|
| **Job board** | Agents post work, workers lease atomically. Dependencies chain; no race conditions. |
| **Drumline** | One shared memory across the whole mesh. Completed jobs auto-distill into recallable handoffs. |
| **Approval gates** | Flag any job — or an entire role — to pause at `needs_approval` until a human decides. |
| **Immutable audit** | Every mutation appends to `agent_job_events`. UPDATE and DELETE are rejected at the storage layer. |
| **Scheduling & loops** | Cron/interval schedules and *bounded* loops — a loop must declare how it stops (count, deadline, or "until the queue is clear") or it's refused. Every scheduled job is stamped with what created it. |
| **Flow Control** | Live DAG canvas at `/flow` — the board as a diagram whose edges are real `depends_on` gates, not decoration. Click a node for its audit trail; approve/reject/retry/cancel inline; drag to author a workflow and export YAML. |
| **Encrypted secrets** | Credentials encrypt by default (AES-256-GCM) — never silently written to `.env` in the clear. Auto-provisioned on Windows; explicit master password elsewhere. |
| **Embedded store** | No Supabase? An embedded SQLite store (`~/.mco/local.db`) takes over — the free edition is the full product. |
| **Enterprise connectors** | Ingest ServiceNow incidents and Dynatrace problems as jobs; act back with auditable, gated platform actions. |
| **Console GUI** | Zero-build web UI at `/console` — job board, approval queue, audit drawer, visual workflow builder. |

---

## Quick start

```bash
mco start             # start the gateway in the background (logs to ~/.mco/logs/gateway.log)
mco stop              # stop it
mco restart           # stop + start
mco serve             # foreground alternative (terminals, systemd, Docker)
mco service install   # run as a boot-persistent OS service (Task Scheduler / systemd / launchd)
mco doctor            # end-to-end install diagnosis
mco upgrade           # apply schema migrations (LocalStore needs none)
mco status            # config health check
mco setup             # guided walkthrough or settings menu
mco setup --guided    # hand-held, Enter at every prompt = working Local-Only install
mco setup --menu      # jump to one setting and get out
mco send codex -t "Summarize repo" -m "..."   # drop a job into a dropbox
mco listen --role codex --instance worker-1   # start a worker
mco audit <job_id>    # inspect a job's full history
mco approve <job_id>  # approve a gate
mco gui               # open the console in your browser (--flow for Flow Control)
mco schedule init     # start declarative schedules & bounded loops
mco launch <name>     # fire a named launcher (job, workflow, app, or URL) now
```

Open **http://127.0.0.1:18789/console** in your browser, paste your access
token (shown at startup, or in `~/.mco/.env`), and click Connect — or just run
`mco gui`. For the live workflow canvas, `mco gui --flow`.

---

## Drumline — shared memory

In a marching band, the drumline keeps everyone in step. Here it does the same
for agents: what one learns, every agent knows.

```
=== SHARED CONTEXT (Drumline) ===
- [handoff] Job outcome: Triage P-99 (claude-w1)
  Root cause: runaway cron. Disabled job foo.
- [fact] Prod DB read-only on Sundays (joe)
  Maintenance window 02:00-06:00 UTC.
=== END SHARED CONTEXT ===
```

- `mco_remember` / `mco_recall` — agents write facts and decisions
- Workers get relevant entries injected into their prompt before execution
- Works fully offline in the free Local-Only edition (SQLite, no vector DB)

Full spec: [docs/DRUMLINE.md](docs/DRUMLINE.md)

---

## Editions

| | Community | Team | Enterprise |
|---|---|---|---|
| Drumline shared memory | ✓ | ✓ | ✓ |
| Job board, approvals, audit | ✓ | ✓ | ✓ |
| Console GUI & workflow builder | ✓ | ✓ | ✓ |
| Embedded SQLite (zero cloud) | ✓ | ✓ | ✓ |
| Multi-machine / Supabase | — | ✓ | ✓ |
| Multi-org isolation + scoped-token RBAC | — | ✓ | ✓ |
| Docker + any-cloud deploy | — | ✓ | ✓ |
| ServiceNow & Dynatrace connectors | — | — | ✓ |
| SSO via your reverse proxy (trusted headers) | — | — | ✓ |
| Pilot program | — | — | [email us](mailto:pilots@bitcadence.ai) |

One codebase, no separate builds: `mco edition` shows the active edition
(inferred from your config, or pinned with `MCO_EDITION`). Details, scope
vocabulary, and SSO setup: [docs/ENTERPRISE.md](docs/ENTERPRISE.md).

---

## Docs

- [docs/INSTALL.md](docs/INSTALL.md) — easy Windows install + first run + troubleshooting
- [docs/SETUP.md](docs/SETUP.md) — full multi-agent setup: Supabase schema, agent registration, MCP wiring
- [docs/DRUMLINE.md](docs/DRUMLINE.md) — shared memory: how it works, how to use it
- [docs/GOVERNANCE.md](docs/GOVERNANCE.md) — approval gates, audit trail, workflow DSL
- [docs/SCHEDULING.md](docs/SCHEDULING.md) — launchers, cron/interval schedules, bounded loops
- [docs/FLOW-CONTROL.md](docs/FLOW-CONTROL.md) — the live DAG canvas + visual workflow authoring
- [docs/INTEGRATIONS.md](docs/INTEGRATIONS.md) — ServiceNow, Dynatrace, webhooks
- [docs/ENTERPRISE.md](docs/ENTERPRISE.md) — editions, scoped-token RBAC, SSO delegation
- [docs/SDK.md](docs/SDK.md) — write a custom agent/worker in fifteen lines
- [docs/AIRGAP.md](docs/AIRGAP.md) — fully offline install (zero data leaves your network)
- [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md) — Docker, any-cloud, multi-tenancy

---

## Observability

- **`/metrics`** — Prometheus exposition: jobs by status, approval queue depth,
  agents registered/online, kill-switch and database state. Open like
  `/healthz` by default; set `MCO_METRICS_TOKEN` to require a bearer token when
  the gateway is network-exposed.
- **`/healthz`** — unauthenticated liveness/readiness for load balancers.
- **`MCO_LOG_JSON=true`** — one JSON object per log line for Loki / Datadog /
  CloudWatch ingestion.

---

## Security

BitCadence binds to **`127.0.0.1` by default** — only your machine can reach
it. Before exposing it on a network:

- **Set a token.** `mco setup` generates `MCO_LOCAL_TOKEN`; every request and
  WebSocket must present it. Binding to `0.0.0.0` without a token is unsafe.
- **Keep the shell executor off.** The standalone worker can run a shell command
  carried in a job, but only when you opt in with `MCO_ENABLE_SHELL_EXECUTOR=1`.
  Leave it unset unless you fully trust everyone who can post jobs; prefer typed
  executors (`register_executor`) instead.
- **Credentials encrypt by default.** Anything credential-shaped (API keys,
  tokens, passwords) goes to the AES-256-GCM secret store (`~/.mco/secrets.enc`),
  never silently to plaintext `.env`. On Windows the store is auto-provisioned
  (key held by Credential Manager); elsewhere set a master password via
  `mco setup --menu → Security`. Plaintext is only ever a deliberate, visible
  opt-out. Either way, keep secrets out of git.

Found a vulnerability? Email **security@bitcadence.ai** rather than opening a
public issue.

---

## License

MIT — see [LICENSE](LICENSE). Copyright (c) 2026 Joe Arroyo.  
Self-host it, fork it, ship it inside your company.
