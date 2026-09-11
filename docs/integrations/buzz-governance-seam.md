# Governing Block's Buzz agents with BitCadence

*Verified against Buzz source on 2026-08-03 (local clone at `C:\AI\buzz`).
Status: **Buzz is INSTALLED AND RUNNING locally** (6 Docker services + relay +
desktop app). Governance seam design + config confirmed; **not yet wired
live** — that's the next step.*

## The one-sentence thesis

Buzz gives an AI agent a cryptographic **identity** and a channel to
collaborate in. It does **not** give it a **boundary** — Buzz's own README says
agents are "scoped by identity, not by permission flags," and approvals are
"infra exists, glue still drying." BitCadence supplies the boundary: approval
gates, a kill switch, role/instance isolation, and immutable audit. This doc is
how the two connect.

## The seam: `BUZZ_ACP_MCP_COMMAND`

Buzz drives each AI agent through the **ACP harness** (`buzz-acp`), which bridges
Buzz channel events ↔ an agent subprocess (goose / codex / claude-code). The
harness attaches **exactly one MCP server sidecar** to every agent session,
configured by a single env var:

```
BUZZ_ACP_MCP_COMMAND=<path to a no-arg executable>
```

Confirmed in `crates/buzz-acp/src/lib.rs::build_mcp_servers` and
`crates/buzz-acp/src/config.rs:261`:

- The value is a **single executable path**. The harness invokes it with
  **`args: vec![]`** — *no arguments are passed.* So `python -m mco.mcp_server`
  cannot be used directly; it must be wrapped in a no-arg launcher (below).
- Buzz's own default for this slot is `buzz-dev-mcp` — a sandboxed shell/file
  MCP server. Swapping it for a BitCadence-fronted MCP is how the agent's
  **actions** become governed while Buzz keeps owning identity + chat.
- The harness injects two env vars into the sidecar process:
  `BUZZ_RELAY_URL` and `BUZZ_PRIVATE_KEY` (the agent's Nostr secret, bech32).
  → A BitCadence-fronted MCP therefore *also* receives the agent's Buzz
  identity, and could post gate decisions back into the channel as that agent.

```
Buzz channel ──(buzz-acp harness)──► agent subprocess (claude/codex/goose)
                                          │
                    BUZZ_ACP_MCP_COMMAND ─┘   ← the seam (one env var)
                                          │
              default: [ buzz-dev-mcp ]  ──swap──►  [ bitcadence-mcp wrapper ]
              raw shell/file, no gates              python -m mco.mcp_server
                                                    gates · kill switch · audit · Drumline
```

## The no-arg wrapper

`BUZZ_ACP_MCP_COMMAND` needs a single executable. Create `bitcadence-mcp` (POSIX)
and/or `bitcadence-mcp.cmd` (Windows) that launches BitCadence's stdio MCP server:

`bitcadence-mcp` (bash):
```bash
#!/usr/bin/env bash
# BitCadence MCP server as a Buzz ACP sidecar. No args (buzz-acp passes none).
# stdio is the MCP transport — do NOT print to stdout from this wrapper.
exec /path/to/BitCadence/.venv/Scripts/python.exe -m mco.mcp_server
```

`bitcadence-mcp.cmd` (Windows):
<!-- Local checkout path, not a brand string: the GitHub repo was renamed to
     BitCadence but the working directory is still Batoncadence. Do not let a
     find-and-replace rewrite it. -->
```bat
@echo off
"C:\AI\baton\Batoncadence\.venv\Scripts\python.exe" -m mco.mcp_server
```

The MCP server (`src/mco/mcp_server.py`) already speaks stdio and supports both
MCP 1.x and the 2.0 (2026-07-28) spec.

## ⚠️ Gateway isolation — do NOT point the experiment at production

The production BitCadence gateway (`:18789`) has **8 live agents** and real
jobs. The Buzz experiment MUST target a **separate local/experiment gateway**,
via the MCO env the wrapper inherits (e.g. `MCO_GATEWAY_URL` / auth token). A
gated action from a Buzz agent will create *real* jobs on whatever gateway it
hits — see the memory note that approving a live-board job authorizes real
execution. Stand up a throwaway gateway for the experiment first.

## Two directions of integration

1. **Buzz agent → governed actions** (this doc's seam): set
   `BUZZ_ACP_MCP_COMMAND=bitcadence-mcp`. Agent chats freely in Buzz; every
   *consequential* tool call routes through BitCadence gates + kill switch +
   audit. This is the enterprise "final rule with cutoff button."

2. **BitCadence fleet → Buzz members**: run a `buzz-acp` harness per fleet
   agent, each with its own `BUZZ_PRIVATE_KEY` (Nostr identity) and
   `BUZZ_ACP_CHANNELS`, so BitCadence's agents appear as first-class Buzz
   channel members and can chat with each other and with humans. Combine with
   (1) and the same agents are both collaborators in Buzz and governed by BitCadence.

## v1 vs v2 of the sidecar

- **v1 (replace):** `BUZZ_ACP_MCP_COMMAND=bitcadence-mcp`. Agent's tools = BitCadence's
  governed MCP tools (job board, approve, audit, Drumline, kill switch). It
  loses raw shell — which is the point: actuation happens through governed jobs,
  not unsupervised `exec`.
- **v2 (compose):** a governance-proxy MCP that exposes buzz-dev-mcp's
  shell/file tools but interposes `kill_switch_active()` + `needs_approval`
  before any consequential call, and mirrors every call into BitCadence's audit. Same
  Buzz identity, same channel — the deploy just hits a gate instead of running.
  This is the "what governed looks like" scenario in
  `founder/POST-governing-buzz-agents.md`.

## Local install notes (Windows) — four fixes to reproduce the run

Buzz is *nix/Hermit-oriented; getting it running on Windows took four fixes.
After these, **`just dev` works end-to-end** and can be re-run anytime — all
four changes persist.

1. **Dead Hermit shims.** On Windows, Buzz's `bin/` Hermit shims are checked out
   as **plain text files** (git wrote the symlink target as content), so
   `bin/just` runs the literal text `.just-1.46.0.pkg` → "command not found".
   Fix: replaced the dev-critical shims (`just`, `cargo`, `node`, `npm`, `npx`,
   `pnpm`) with thin passthroughs to the native tools, so every `just` recipe
   works. (README blesses native Rust 1.88+/Node 24+/pnpm 10+/just as an
   alternative to Hermit.)

2. **Health port 8080 occupied** (by a local python process, PID 4860). The
   recipe's `lsof` pre-check no-ops on Git Bash, so it surfaced only at bind
   time (`os error 10048`). Fix: `BUZZ_HEALTH_PORT=8090` persisted in `.env`.

3. **`beforeDevCommand` used `exec`** (`exec ./node_modules/.bin/vite …`), a
   POSIX shell builtin cmd.exe rejects. Fix: changed to `pnpm exec vite …` in
   `scripts/instance-env.sh` (both the default and worktree config strings) —
   cross-platform under both `sh -c` and `cmd /C`.

4. **Sidecar binaries missing `.exe`.** `_ensure-sidecar-stubs` touches empty
   stubs named `<sidecar>-<triple>` without `.exe`, but Tauri on Windows
   requires `<sidecar>-<triple>.exe`, so `build.rs` panicked ("resource path …
   doesn't exist"). Fix: copied the **real** built sidecars into
   `desktop/src-tauri/binaries/` with `.exe` naming — so the app bundles
   functional `buzz-acp` / `buzz-dev-mcp` / etc., not stubs. All six in
   `externalBin` (incl. `buzz-backend-kubernetes`, which is listed
   unconditionally) must be present.

Backend was brought up natively (no Hermit): `docker compose up -d` (6 services
healthy) + `cargo run -p buzz-admin -- migrate` + `seed-local-community.sh`.

### Restarting later
`cd C:\AI\buzz && just dev` — brings up relay + desktop. Docker services persist
across reboots via Docker Desktop; if stopped, `docker compose up -d` first.
Health probe: `http://127.0.0.1:8090/_readiness`. Relay WS: `ws://localhost:3000`.
