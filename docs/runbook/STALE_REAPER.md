# Staged stale-job reaper

This feature is **inactive by default**. No timer, worker grant, retirement,
reroute, cancellation or notification is installed by this change. Activation
belongs to a separately approved rollout. The report command is read-only.

The policy uses an operator-owned JSON file with these fields:

```json
{
  "enabled": false,
  "ladder": ["builder", "economy", "local"],
  "owner_ids": ["human-owner"],
  "retired_instances": [],
  "deputy_id": "watch-officer",
  "stop_file": "/operator/config/STOP"
}
```

Use deployment-specific identities and paths only in private configuration.
Do not commit that configuration or the board report. `owner_ids` and the
capacity ladder must be explicit; an empty or duplicate ladder is rejected.

## Rules

- Only unarchived, pending, never-taken jobs are eligible. A lease, start,
  completion or failure in audit history excludes the job even if it later
  returns to pending. Missing creator identity or invalid clocks fail closed.
- Owner-created and human/administrative work is protected. Explicit addressees
  (`target_agent_id`), pin flags, `no_reroute`, review gates, dependency-bound
  jobs and work flagged as unmerged are also protected. Automation deletes no
  files, branches, worktrees, jobs or audit events.
- At six hours pending, propose the next lower role in the configured ladder
  with a same-organization, online, recent-heartbeat, non-retired worker.
  Offline destinations are excluded. A missing lower tier produces a hold,
  not a cancellation deadline. This report intentionally uses heartbeats;
  historical sockets are unavailable.
- A recorded delivery/reaper reroute starts a full 24-hour window. A job
  still untaken after that window may be cancelled. Retry, approval,
  dependency release and reassignment reset the pending interval; a reroute
  before the reset cannot authorize cancellation.
- Registry retirement sets the existing `disabled` status and a `retired`
  marker. Delivery presence then excludes that instance. An instance with a
  leased/in-progress attempt is held for a later sweep rather than interrupted.
  Only identities explicitly listed in private policy are retired; temporary
  offline status is not sufficient reason to retire an instance.
- Every applied action and its receipt share a LocalStore transaction.
  Receipt failure rolls back the entire sweep. Concurrent claims and pins
  serialize with the fresh eligibility check. Repeating a sweep is safe.
  Remote/PostgreSQL mutation is refused until an equivalent atomic RPC exists.
- The configured global STOP file prevents actions; the default is
  `~/.mco/STOP`. No other stop file is consulted. Live rollout must point this
  at the fleet's single global stop file.

## Read-only report

```powershell
$env:PYTHONPATH = 'src'
python -m mco.orchestrator.stale_reaper --database <board.db> --policy <private-policy.json> --output <new-private-report.json>
```

The command opens SQLite using `mode=ro`, enables `query_only`, and reads the
three tables in one transaction. It never constructs or migrates LocalStore.
It refuses to overwrite evidence. The report lists every decision with its
reason, retirement proposals, current cancellation proposals and a 14-day
threshold replay. Historical presence/pins cannot be reconstructed from a
current registry, so retrospective proposals are explicitly conditional. The
conservative replay excludes taken/completed jobs rather than inventing their
former protection state. This is not a complete historical counterfactual.

## Staged Deputy capability

`POST /api/jobs/{job_id}/cancel-stale` requires both:

1. An explicit `jobs:cancel-stale` registry scope and exact `deputy_id` match.
2. A server-owned policy selected by `MCO_STALE_REAPER_POLICY`, with
   `enabled: true`, no STOP file and no active gateway kill switch.

The scope is not added to any role's defaults. An `admin` scope alone does not
authorize this endpoint. The endpoint does not accept eligibility assertions,
arbitrary reasons, policy overrides or reroute timestamps from the caller.
It loads the job and audit trail again inside the write transaction and
allows only an eligible stale cancellation in the caller's organization.
The broad existing cancel API is unchanged.

Trusted maintenance has a separate library entry point:
`sweep_local(db, policy, dry_run=True)`. Applying it requires both an enabled
policy and explicit `dry_run=False`. There is no CLI apply switch or schedule.
Do not expose this maintenance function as a Deputy tool; the Deputy gets only
the narrow endpoint. An activated sweep should run at the approved cadence
after the existing delivery policy has been reconciled: that older watchdog
has a much shorter retry/reroute interval, and this change does not alter it.

## Morning report and rollout handoff

Applied cancellation receipts are persisted as `stale_reaper_cancelled` audit
events with `morning_report: true`, actor, prior role/state, deadline and time.
The sweep returns the actual cancellations in `morning_report`; the read-only
report includes actual receipts from the last 14 days separately from proposals.
The approved morning-report producer can consume those receipts. No report is
sent to a person or public notification channel by this module.

Before activation, the rollout owner must validate the private capacity ladder
against deployed role aliases, resolve protected jobs addressed to retired
instances without lifting pins automatically, obtain the independent review,
and gate the grant, server policy, maintenance cadence and morning-report
consumer together. No grant, deployment, schedule or existing configuration
is changed by the work step.
