# local-operator

## Purpose
Local administrative identity on Beast. Used for manual CLI operations, local test harnesses, offline canary runs (`s06a`), and supervisor restarts.

## Good at
- Direct command-line executions on Beast without LLM overhead.
- Running adversarial proof suites and isolated canary harnesses (`test_canary_*.py`).
- Performing local database migrations and offline store inspections.

## Not allowed (hard rules)
- Not an unattended autonomous worker; do not route continuous production jobs to this identity.
- Never prints or commits secrets.
- Never runs unauthorized destructive database sweeps.

## How to give it a job
Send via `mco_send`:
- `to_role`: `admin`
- `to_instance`: `local-operator`
- `title`: Administrative manual action
- `instructions`: Clear operator command, target environment, and expected receipt
- `priority`: 4 to 5

## Job template and worked example
```json
{
  "to_role": "admin",
  "to_instance": "local-operator",
  "title": "Execute isolated local canary harness s06a",
  "instructions": "From Beast PowerShell, run tests/canary/run_s06a.ps1 with isolated local gateway on port 18997. Verify 7 canary injection outcomes and confirm no touches to live port 18789.",
  "priority": 5
}
```

## What it needs in the job
- Direct authorization from the owner.
- Explicit shell scripts and working directories.

## Signs it is stuck and the fix
- **Signs:** Shows offline in `mco_agents` (expected when manual operator CLI is closed).
- **Fix:** Launch interactive terminal session on Beast.

## Who reviews its work
The owner (owner).
