# Fleet workers: what makes an agent actually run

Written 2026-09-08 after two registered agents were found offline for five and
seven days with no error anywhere. Everything below is a failure that produces
no visible complaint — the agent simply never works.

## 1. `fleet.toml` is the only list that matters

The desktop supervisor starts **exactly** the workers declared in
`~/.mco/fleet.toml`. An instance can have a registered identity, a valid token,
a runner script, a wake script and a correct MCP config and still never start,
because none of that is consulted without a block:

```toml
[workers.claude-beast]
role = "claude"
instance = "claude-beast"
mode = "waker"          # waker | poll | off
exec = "C:/Users/masta/.mco/bin/claude-beast-run.cmd"
min_interval = 10
```

`mco doctor` reports such an instance as healthy, because from the gateway's
point of view it *is* — it is registered and its token authenticates. Nothing
checks that a registered agent is also a declared worker.

## 2. Registering an existing agent rotates its token

`mco register --name <existing> --role <role>` mints a **new** token and
invalidates the old one. Every consumer holding the old value starts returning
`401`, including consumers that are not obviously "the fleet":

| Consumer | Where the token lives |
|---|---|
| Waker / runner | `~/.mco/tokens/<instance>.token` |
| Claude Code session | `~/.claude.json` → `mcpServers.mco.env.MCO_AGENT_TOKEN` |
| Antigravity IDE | `~/.gemini/antigravity/mcp_config.json` (+ two sibling copies) |
| Grok CLI | `~/.grok/config.toml` → `[mcp_servers.mco.env]` |
| Codex CLI | `~/.codex/config.toml` |

A rotation that updates only the token file leaves every IDE session broken.
Prefer runners that read the token file at launch (as
`claude-worker-run.ps1` does) so a rotation needs no config edit at all.

## 3. Presence is a live connection, not a heartbeat

`mco.cli wake` does not heartbeat. An agent reads `online` only while its CLI
holds an MCP/WebSocket connection, so a correctly armed but idle worker shows
`offline`. Do not treat `offline` as broken, and do not treat `online` as
proof of anything beyond a socket.

These are four different claims, and only the last one is delivery:

1. a supervised process exists
2. its token authenticates
3. it leased a job
4. it completed a job with a receipt

## 4. A role is not its IDE

`ROLE_COMMANDS` in `src/mco/orchestrator/executors.py` defines the headless
executor per role. Two consequences that surprise people:

- **`antigravity` runs the `gemini` CLI**, not the Antigravity IDE. Opening the
  IDE starts no unattended worker; it is a separate manual path over the same
  MCP tools.
- **`grok` has no entry at all.** It works only through its `--exec` script, so
  the built-in `listen` daemon path cannot run it.

## 5. Vendor auth fails per-vendor and only shows up in the worker log

Each worker's log is `~/.mco/logs/<instance>.log`. Observed on 2026-09-08:

- **gemini** — `IneligibleTierError: This client is no longer supported for
  Gemini Code Assist for individuals.` The free OAuth tier is refused, so the
  `antigravity` role cannot run headlessly without a paid key.
- **claude** — `Failed to authenticate: OAuth session expired and could not be
  refreshed.` Reproduces from any plain shell, so it is CLI-level auth, not the
  runner.

Neither surfaces in `mco doctor`, which checks only that the binary is on PATH.

## Verifying a worker end to end

```powershell
mco send <role> --instance <instance> -t "FLEET SMOKE TEST - reply only" `
  -m "Do NOT edit files or commit. Call mco_complete(task_id, output) with your AGENT_INSTANCE_ID, the UTC time, and 'fleet smoke ok'. Then stop."
```

Then confirm the job reaches **completed** — not merely that the agent turned
`online`. A worker that wakes, connects and never leases is the common failure,
and presence alone hides it.
