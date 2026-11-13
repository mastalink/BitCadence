# Workers & Background Daemon Listening

## Goal
Connect external AI models (Anthropic Claude Desktop, OpenAI Codex, Google Antigravity, local scripts) to BitCadence dropboxes using the `mco listen` polling daemon, event wake triggers, and the Model Context Protocol (MCP).

---

## Step-by-Step Instructions

### 1. The Worker Model
Helpers in BitCadence do not expose exposed HTTP endpoints or wait for inbound RPC calls. Instead, workers **poll their designated dropboxes** for mail:
1. A job arrives for role `codex`.
2. A running worker requests a lease atomically (`mco_lease` / `POST /api/jobs/lease_next`).
3. If won, the worker executes the task, streams events, and writes results back.

### 2. Spawning a Background Listener
To start a worker daemon that polls for jobs:

```powershell
# 1. Register the helper
mco register --name worker-codex-1 --role codex

# 2. Export the minted token
$env:MCO_AGENT_TOKEN = "mco_tok_..."

# 3. Start the polling daemon
mco listen --role codex --instance worker-codex-1
```

The daemon runs continuously, emitting log lines whenever work is checked, claimed, or completed.

### 3. Event-Driven Wake (`mco wake`)
If you prefer not to have a persistent polling process consuming memory:
Use `mco wake` to spawn your worker script only when pending work arrives in the mailbox:

```powershell
mco wake --role codex --exec "python run_worker.py"
```

### 4. Connecting via MCP (Model Context Protocol)
BitCadence includes a native MCP server, enabling Claude Desktop, Codex, and Antigravity to treat BitCadence dropboxes as native tools:

Add this configuration to your client's MCP configuration (e.g., `claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "bitcadence": {
      "command": "mco",
      "args": ["mcp"],
      "env": {
        "MCO_AGENT_TOKEN": "mco_tok_...",
        "MCO_GATEWAY_URL": "http://127.0.0.1:18789"
      }
    }
  }
}
```

The AI assistant automatically gains access to:
- `mco_inbox` — List pending jobs addressed to this helper.
- `mco_lease` — Atomically claim a task.
- `mco_complete` — Mark finished work with structured handoff.
- `mco_fail` — Report errors and trigger escalation.
- `mco_send` — Drop work into another helper's mailbox.
- `mco_remember` / `mco_recall` — Read and write Drumline shared context.

---

## What You'll See

- **Automatic Lease Collision Avoidance:** If three workers run `mco listen` simultaneously for the same role, exactly one worker wins each task lease. The others receive empty responses and sleep until the next poll cycle.
- **Lease Heartbeat Renewal:** Long-running jobs automatically send renewal heartbeats to prevent the gateway lease reaper from reclaiming the task prematurely.

---

## If It Goes Wrong

### 1. "Worker loop exits with 401 Unauthorized"
- **Cause:** `MCO_AGENT_TOKEN` is missing, expired, or invalid.
- **Fix:** Verify the token using `mco status` and ensure the variable is set in the worker's session.

### 2. "Worker claims job but crashes during execution"
- **Cause:** Unhandled exception in worker code.
- **Fix:** The gateway lease reaper detects expired leases after the TTL (default: 5 minutes) and automatically resets the job status to `pending` (if retries remain) or routes it to the designated `escalate_to_role`.
