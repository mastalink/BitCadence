# via-dispatch-beast

## Purpose
Manages idempotent dispatch of Via data packets, Score triggers, and queue reconciliation between Via and the BitCadence gateway.

## Good at
- Generating canonical UUID-bound dispatch jobs to prevent duplicate actions.
- Reconciling `score_outbox` rows and verifying completion states.
- Ensuring retry safety across network drops and gateway reboots.

## Not allowed (hard rules)
- Never dispatches jobs without a persisted operation UUID.
- Never directly alters production deployment infrastructure.
- Never ignores 409 Conflict responses from the gateway.

## How to give it a job
Send via `mco_send`:
- `to_role`: `via-dispatch`
- `to_instance`: `via-dispatch-beast`
- `title`: Dispatch reconciliation or batch queue task
- `instructions`: Batch ID, target Score run or job parameters, and idempotency key
- `priority`: 2 to 4

## Job template and worked example
```json
{
  "to_role": "via-dispatch",
  "to_instance": "via-dispatch-beast",
  "title": "Dispatch verified Lorain County parish batch to Score pipeline",
  "instructions": "Read verified batch at C:\\AI\\via-catholica\\data\\verified\\lorain_20261024.json. Generate canonical UUID e8b12... and submit to POST /api/jobs with create_with_id capability. Verify receipt and update score_outbox ledger.",
  "priority": 3
}
```

## What it needs in the job
- Batch file path and hash.
- Canonical UUID idempotency token.
- Gateway endpoint reference (`http://127.0.0.1:18789`).

## Signs it is stuck and the fix
- **Signs:** Shows offline in `mco_agents` (e.g. offline since initial registration).
- **Fix:** Start dispatch supervisor on Beast or trigger queue sweep via `codex-beast`.

## Who reviews its work
`codex-beast`, `claude-code`, and the owner.
