# Wiring the 3 GUIs to the MCO dropbox (MCP)

Each coding GUI talks to the BitCadence dropbox through the **`mco mcp`** stdio
server, authenticating as a registered agent. No `mco listen` daemon required —
each app uses its own scheduler to check its inbox.

## Prerequisites
1. The gateway must be running: `mco serve` (default `http://127.0.0.1:18789`).
2. Each agent must be registered (`mco register --name <instance> --role <role>`)
   and you must paste its **current** access token into the config below.
   (Re-running `register` rotates the token — use the latest.)

> **Windows / `uv` gotcha.** If `mco <cmd>` errors with
> `can't open file '...\main.py'` or runs from
> `AppData\Roaming\uv\...\python.exe`, a global `uv`/`pipx` shim is shadowing
> the venv's `mco`. Fix once with `scripts\setup.ps1`, then invoke the CLI
> shim-proof: `scripts\mco.ps1 <cmd>` (or
> `.venv\Scripts\python.exe -m mco.cli <cmd>`). The MCP `command` paths below
> point at the venv's `mco.exe` directly, so the GUIs are unaffected.

> **Tokens from `.env`.** `mco workflow|approve|sync|...` read
> `MCO_AGENT_TOKEN` / `MCO_GATEWAY_URL` from your `.env` and secret store (not
> just the OS environment) — set the token in `.env` once instead of
> `set MCO_AGENT_TOKEN=...` in every shell.

## Install per app
Replace `mco_tok_REPLACE_WITH_*` with each agent's real token first.

- **Claude Code** — copy `claude/.mcp.json` into your project root as `.mcp.json`
  (or run `claude mcp add mco -- C:\AI\BitCadence\.venv\Scripts\mco.exe mcp`
  and set the env vars). Role `claude`, instance `coding-beast-claude`.
- **Codex** — append `codex/config.toml` to `~/.codex/config.toml`.
  Role `codex`, instance `coding-beast-codex`.
- **Antigravity** — add `antigravity/mcp_config.json`'s `mco` entry to
  Antigravity's MCP config. Role `antigravity`, instance `coding-beast-gemini`.

## Tools exposed
`mco_inbox` · `mco_lease(task_id)` · `mco_complete(task_id, output)` ·
`mco_fail(task_id, error)` · `mco_send(to_role, title, instructions, to_instance?)` ·
`mco_agents`

## Scheduler prompt (run on each app's interval)
> Call `mco_inbox`. For every job addressed to you: `mco_lease(task_id)`; if it
> succeeds, do the work in `input_payload.prompt`; then `mco_complete(task_id, <result>)`
> (or `mco_fail(task_id, <error>)`). Use `mco_send(...)` to hand work to another agent.

## Security
- Tokens are bearer credentials — keep them out of version control. The files
  here use placeholders on purpose.
- The gateway enforces the dropbox rule: anyone may send to anyone, but you can
  only lease/complete/poll mail addressed to you.
