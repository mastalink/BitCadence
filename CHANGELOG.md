# Changelog

All notable changes. Format: [Keep a Changelog](https://keepachangelog.com); versioning: semver.

## [Unreleased]

### Changed
- **Renamed: BatonCadence is now BitCadence** (`bitcadence.ai`). The tagline is
  now *"Every agent. One beat."* — the drumline metaphor stays, the relay-race
  pun goes. The Python distribution is `bitcadence`; **the CLI is still `mco`**,
  every `MCO_*` environment variable is unchanged, and config still lives in
  `~/.mco/`, so existing installs keep working without edits.
- **Services register under the new brand** — `BitCadence-gateway`,
  `bitcadence-gateway.service`, `com.bitcadence.gateway`. Machines installed
  under the old names are still discovered, addressed, and uninstallable:
  `mco service status|restart|logs|uninstall` resolve `BatonCadence-*` units,
  tasks, and plists alongside the new ones. **To move a machine onto the new
  names, run `mco service uninstall` then `mco service install`** — an
  in-place upgrade leaves the old-named service running (and working).

### Fixed
- **Security (dropbox isolation):** `POST /api/jobs/lease` now enforces the
  same addressee rule as the inbox and the completion endpoint - an agent may
  only lease jobs addressed to its own role (or to its specific instance).
  Previously the lease endpoint checked only org isolation, so any authenticated
  agent could lease another agent's pending job; completion was still blocked,
  so the job would strand in `leased` state and record a false actor in the
  audit trail. The check mirrors the inbox filter, so a same-role sibling
  instance is still blocked from an instance-targeted job.

## [0.3.0] - 2026-07-01

The parity release: everything the API can do, the CLI and the console can
do too - plus the external security audit remediations.

### Added
- **`/metrics`** Prometheus endpoint (jobs by status, approval queue depth,
  agents registered/online, kill-switch, database state); optional
  `MCO_METRICS_TOKEN`. `MCO_LOG_JSON` for structured JSON logs.
- **`mco service install/uninstall/status`**: boot-persistent gateway via
  Windows Task Scheduler, Linux systemd --user, or macOS launchd.
- **`mco upgrade`**: schema migration runner - LocalStore needs none;
  Postgres auto-applies via DATABASE_URL + psycopg (recorded in
  schema_migrations) or emits a combined script for the Supabase SQL editor.
  Migrations now ship as package data.
- **CLI parity**: `mco recall` / `mco remember` (Drumline from the
  terminal), `mco settings` (the Control Panel whitelist without a
  browser), `mco reset-token` / `mco deregister`, `mco orgs`, and
  `mco watch` (live WebSocket event tail with automatic reconnect).
- **Console: Memory screen** - search/tag recall of the Drumline shared
  context plus an add-entry composer.
- **Console: Activity screen** - the cross-job immutable audit trail with
  client-side stats (24h throughput, failure rate, approval latency),
  backed by the new `GET /api/events` feed (org-scoped, job-enriched).
- **Console: Fleet admin** - register agents (one-time token), rotate
  tokens, remove agents, and move agents between orgs (host operator,
  one-way by design); **Tenancy card** to view/create orgs.
- **Console: connector operations** - health dots, and "Sync now" next to
  "Test connection"; new Connectors settings group (ServiceNow/Dynatrace)
  with a server-side `test-connector` probe.
- **Console live updates** over `/ws/broadcast` with polling fallback
  (4s -> 30s safety net while the socket is up). The gateway accepts
  token-only WebSocket auth (identity resolved from the token hash).
- **Drumline dedup**: identical title|content|role returns the existing
  entry (SHA-256 `content_hash`; migration `2026-07_drumline_dedup.sql`;
  graceful fallback pre-migration). `mco doctor` warns when the migration
  is pending.
- Reproducible console build: `scripts/build_console.py extract|build|verify`
  with editable sources in `src/mco/console_src/` (verified in CI).

### Security (external audit remediation)
- Drumline content is sanitized before storage: prompt-injection syntax is
  neutralized in place (angle brackets to lookalikes, broken code fences,
  tool-call markers dropped) - content survives, the teeth don't.
- Tenant isolation for Drumline recall pushed into the SQL query for named
  orgs, with the Python filter retained as defense-in-depth.
- WebSocket disconnects are race-safe; `max_retries=0` is honored;
  `supabase` pinned `<2.0.0`; routes/auth import cycle removed.

### Changed
- Drumline recall recency bias capped at 20% so relevance dominates.
- Console job retry uses the dedicated `POST /api/jobs/{id}/retry`.
- CI test matrix now covers Python 3.11 and 3.14.

## [0.2.0] - 2026-06-12

The enterprise-suite release: RBAC, SSO delegation, editions, the Context
Exchange, a full web Control Panel, and an SDK.

### Added
- **Scoped-token RBAC**: every endpoint declares scopes; `mco register
  --scope` issues least-privilege tokens; role-derived defaults keep
  pre-RBAC installs behaving identically.
- **Trusted-header SSO delegation** (enterprise): authenticate humans via
  the reverse proxy you already run (oauth2-proxy, Cloudflare Access,
  Authelia) - off by default, optional constant-time proxy secret.
- **Edition model**: community / team / enterprise in one codebase,
  inferred from config or pinned with `MCO_EDITION`; `mco edition` prints
  the feature matrix.
- **Context Exchange**: structured handoffs ({summary, decisions, files,
  gotchas, follow_ups}) stored verbatim and weighted above mined context;
  workflow runs stamp every step, and workers inject the whole predecessor
  thread deterministically - cross-vendor continuity.
- **Web Control Panel** at `/dashboard`: lock screen, agent
  register/rotate/edit/delete with tokens shown exactly once, workflow
  YAML submission, server-driven settings (governance, Drumline, presence,
  tenancy, edition, security, notifications), connector health/sync,
  retry on failed jobs, MCP/worker connect instructions per token.
- **Agent presence health checks**: polling is the heartbeat; agents
  silent past `MCO_AGENT_OFFLINE_AFTER` (default 300 s) report offline with
  "seen Xm ago" in both the dashboard and `mco agents`.
- **Org allowlist** (`MCO_ORGS`): tenants are minted deliberately in
  Settings, never implicitly by a typo at registration.
- **Agent SDK** (`mco.sdk.BatonAgent`): a worker in fifteen lines with
  atomic leasing, thread-aware prompts, structured handoffs, and
  governance-correct failure reporting.
- **Air-gapped install**: `make-offline-bundle` scripts produce a
  repo+wheels archive; installers auto-detect `offline/wheels` and install
  with `--no-index`.
- **Lifecycle commands**: `mco start` (detached background gateway with
  health-checked startup), `mco restart`, `mco doctor` (end-to-end install
  diagnosis), `mco --version`.
- One-command installers (`curl | bash`, `iwr | iex`) with existing-install
  detection; Hermes-style install UX on the website and README.
- SECURITY.md, CodeQL, dependabot, release pipeline (PyPI + GHCR).

### Fixed
- Setup can no longer orphan the secret store (key is persisted before the
  store is created); locked-store warnings are single and actionable.
- `mco stop` no longer crashes (psutil was missing from dependencies).
- WebSocket honors `MCO_LOCAL_TOKEN` parity with HTTP auth; the worker's
  raw shell executor is opt-in (`MCO_ENABLE_SHELL_EXECUTOR`).
- Web agent list shows all orgs to the host operator (parity with
  `mco agents`).

### Changed
- Distribution renamed `mco` -> `batoncadence` (the CLI is still `mco`).
- Config home is global (`~/.mco/.env`): `mco` behaves the same from any
  directory.

## [0.1.0] - 2026-06-10

Initial MVP: job board with atomic leasing, multi-vendor executors
(claude/codex/gemini), approval gates, immutable audit trail, escalation,
DAG workflows, Drumline shared context, MCP server, ServiceNow/Dynatrace
connectors, embedded LocalStore, dashboard, 151-test suite.
