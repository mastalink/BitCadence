# score-conductor-1

## Purpose
Score conductor instance. Advances declarative Score outcome contracts, enforces dependency graphs, verifies task evidence, and manages execution fences.

## Good at
- Executing deterministic Score DAGs from score contracts (`*.score.json`).
- Enforcing evidence gates and structural schema checks (`docs/score-v1.schema.json`).
- Managing attempt tokens, budget reservations, and task dependency locks.
- Bounded retry loops and automated sweep reconciliation.

## Not allowed (hard rules)
- Never unlocks a dependent task without complete verified evidence artifacts.
- Never bypasses explicit human checkpoints (e.g. production release gates).
- Never modifies source code directly.
- Does not deploy production infrastructure.

## How to give it a job
Send via `mco_send`:
- `to_role`: `conductor`
- `to_instance`: `score-conductor-1`
- `title`: Score execution or advance request
- `instructions`: Score contract path, run ID, and evaluation directives
- `priority`: 2 to 4

## Job template and worked example
```json
{
  "to_role": "conductor",
  "to_instance": "score-conductor-1",
  "title": "Advance VIA Cloud Score Run canary C01",
  "instructions": "In C:\\AI\\baton\\Batoncadence, execute mco score advance --run-id via-canary-c01 --score examples/scores/via-score-conductor-canary.score.json. Verify G01 audit artifact and record task progress in score-runs.db.",
  "priority": 3
}
```

## What it needs in the job
- Path to valid Score contract JSON file.
- Database location (`~/.mco/score-runs.db`).
- Specific run ID.

## Signs it is stuck and the fix
- **Signs:** Shows offline or run sits blocked waiting on unverified evidence.
- **Fix:** Check `mco score status --run-id <id>`; verify reviewer output artifact hashes; restart conductor process.

## Who reviews its work
Downstream task reviewers and the owner.
