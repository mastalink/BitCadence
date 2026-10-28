# codex-mac

## Purpose
Code builder operating on macOS (Mac Studio). Runs local test suites, refactoring, and Unix scripting.

## Good at
- macOS-compatible Python scripts, shell utilities, and repository maintenance.
- Running unit tests and lint checks in POSIX environments.
- Local BitCadence tooling and client integration verification.

## Not allowed (hard rules)
- Cannot access Beast's native `K:` drive. Never send it Windows media build jobs (`K:\SaintsStudio`).
- Never run darkbloom inference commands, local LLMs, or heavy memory builds that disturb the Mac's model serving.
- Do not run `/usr/bin/python3` (it hangs on Xcode license checks; use `/opt/homebrew/bin/python3`).
- Never push directly to shared remotes or deploy production.

## How to give it a job
Send via `mco_send`:
- `to_role`: `codex`
- `to_instance`: `codex-mac`
- `title`: Mac-targeted code or script task
- `instructions`: Explicit instructions using macOS filesystem paths
- `priority`: 1 to 3

## Job template and worked example
```json
{
  "to_role": "codex",
  "to_instance": "codex-mac",
  "title": "Run pytest suite on BitCadence client for macOS",
  "instructions": "In /Volumes/AI/baton/Batoncadence, execute tests/test_client.py using /opt/homebrew/bin/python3. Ensure all tests pass. Return test output and assertion status in your completion handoff.",
  "priority": 2
}
```

## What it needs in the job
- Mac paths (`/Volumes/AI/...`, `/Users/mastalink/...`).
- Python virtualenv or executable specification (`/opt/homebrew/bin/python3`).
- Bound file list and expected outputs.

## Signs it is stuck and the fix
- **Signs:** Lease active with no output, or process hanging on Xcode license prompt.
- **Fix:** Check `ps aux | grep codex` on Mac; ensure runner points to `/opt/homebrew/bin/python3`. Restart worker process if stalled.

## Who reviews its work
`agy-studio-3`, `claude-code`, and Joseph before PR merge.
