# deploy/fleet - rebuild the worker fleet on a fresh Beast

Sanitized copies of the machine-local config that lives in `~/.mco/` on the live Beast.
No tokens, secrets, `.env`, OAuth files or per-slot `homes/` are in here, and `install.ps1` never creates or prints one.

| Piece | What it is |
|---|---|
| `bin/*-run.cmd` | One launcher per worker. Fetches a worktree/log, then starts the worker CLI (Codex, Grok, OpenCode, Claude, Antigravity). |
| `bin/*-wake.cmd` | Optional hand-run wake daemon for a worker (reads its token live from `~/.mco/tokens/<name>.token`). |
| `bin/claude-worker-run.ps1`, `antigravity-worker-run.ps1` | Headless runners for the Claude and Antigravity workers. |
| `bin/prepare-codex-worker.ps1` | Builds the sandboxed launch command for the Codex-family workers. |
| `bin/agy-slot-run.ps1` | Runner for the Gemini slots `agy-studio-5..8` (own home per slot). |
| `prompts/*.txt` | The current worker prompts, word for word. Only the user paths changed. |
| `fleet.toml` | Worker table (role, exec script, limits). |
| `agy-workers/login-agy.ps1` | One-time Google sign-in per Gemini slot. |
| `agy-workers/activate-beast-slots.ps1` | Creates or removes the wake tasks for the Gemini slots. |

## Placeholders

`{{HOME}}` becomes `%USERPROFILE%` as a backslash path (`C:\Users\you`); `{{HOME_FWD}}` is the same with forward slashes.
`install.ps1` fills both. Paths into the repo checkout (`C:\AI\baton\Batoncadence`) and `C:\AI\content-studio` are left as-is: check the repo out there or edit after install.

## Install

1. Check out the repo at `C:\AI\baton\Batoncadence` and build its `.venv`.
2. In an **admin** PowerShell: `deploy\fleet\install.ps1` (add `-Force` to overwrite existing files; originals are saved as `*.bak-install`; `-SkipTasks` copies files only).
3. It registers one `BitCadence-wake-<role>-<instance>` boot task per worker.
4. Run `mco register --name <worker> --role <role>` for each worker it lists. That is the only step that makes tokens.
5. Gemini slots: `login-agy.ps1`, then `activate-beast-slots.ps1`.

## Not included yet

Helpers the scripts call that are not templated here: `new-run-log.ps1`, `codex-worker-policy.py`, `stealth-beast-run.ps1`, `fleet-supervisor.ps1`, `scripts/fleet-worktree.ps1` (this one is in the repo). Copy the first three from the live `~/.mco/bin/` when rebuilding.
