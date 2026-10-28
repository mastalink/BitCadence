# chief-beast

## Purpose
Watch officer and stall janitor (Codex on Beast). Detects stalls and dead workers across the job board and forwards a one-line alert to Deputy `grok-beast`.

## Good at
- Spotting stuck leases, timeouts, and dead worker heartbeats.
- Scanning queue health and identifying orphaned dependencies.
- Writing clean, concise stall alerts.

## Not allowed (hard rules)
- Not for intake. Never send routine escalations or repairs directly to `chief-beast`.
- Never dispatches repair jobs or creates escalation chains.
- Never runs media builds or executes code changes.
- Never overrides owner rules.

## How to give it a job
Send via `mco_send`:
- `to_role`: `chief`
- `to_instance`: `chief-beast`
- `title`: Periodic queue watchdog scan or stall check
- `instructions`: Specific queue inspection parameters
- `priority`: 1 to 2

## Job template and worked example
```json
{
  "to_role": "chief",
  "to_instance": "chief-beast",
  "title": "Scan active leases for timeouts exceeding 60 minutes",
  "instructions": "Call mco_jobs. Inspect all active leases. If any lease is older than 60 minutes without heartbeat, send a single one-line alert job to to_role: grok, to_instance: grok-beast naming the job ID and worker. Do not dispatch repairs.",
  "priority": 1
}
```

## What it needs in the job
- Target board scan interval and threshold.
- Reporting instructions.

## Signs it is stuck and the fix
- **Signs:** Backlog of misrouted jobs accumulating under role `chief` (as happened in incident 49/50).
- **Fix:** Clear misrouted jobs and redirect queue stalls to `grok-beast` per `DEPUTY_CHIEF.md`. Ensure `MCO_CHAIN_STALL_TO_ROLE=grok`.

## Who reviews its work
Deputy `grok-beast`.
