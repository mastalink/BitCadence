# Fleet reliability

The gateway expires legacy dependency waits after seven days when a prerequisite
is missing, stopped, cyclic, or itself old and never started. The job becomes
`halted`, with a reason and audit event. A running prerequisite or an outstanding
approval is preserved. Score-owned graphs remain under their conductor.
`MCO_STALE_JOB_DAYS` changes the threshold; zero disables this maintenance.

The Job Board's **Preview stale jobs** control lists old never-started queued
work and expired dependency waits. **Cancel stale jobs** applies only that list,
within 15 minutes, using the same approver roles and scope as ordinary cancel.
Previewing records audit evidence but changes no jobs. Newly leased work is
skipped. No stale jobs are cancelled merely by installing this change.

## Work requirements

Send job requirements through `mco_send(required_capabilities=[...],
allowed_roles=[...])`, or through REST in `input_payload.routing`:

```json
{"routing":{"required_capabilities":["gpu-render"],"allowed_roles":["grok"]}}
```

Declare capabilities once in the gateway configuration, by worker instance:

```dotenv
MCO_WORKER_CAPABILITIES={"grok-beast":["gpu-render","long-session"]}
```

Inbox admission, direct leasing and delivery fallbacks all enforce requirements.
Unknown capabilities and malformed requirements fail closed. A fallback is
pinned to the matching instance; another worker of that role cannot claim it.
Host restrictions and identity-bound review gates continue to apply. Untagged
legacy jobs retain their configured fallback behavior; producers of render jobs
must send the requirements above (or `no_reroute: true` for a fixed worker).
This change does not infer requirements from prose or modify existing jobs.

## Quota and worker state

A fenced credit/quota failure (including HTTP 402) leaves the work queued and
records **Out of quota** for the worker. It does not consume a job retry or send
a failure escalation. The worker cannot take new work during the cooldown;
compatible fallbacks can. After one hour it can try again automatically.
`MCO_QUOTA_COOLDOWN_SECONDS` changes that period. Replaying a failure report does
not extend it. Maintenance recovers a crash between the failure receipt and
returning the job to the queue. Hosted storage commits these as separate steps;
the durable receipt and recovery sweep make that interruption recoverable.

A lease holder is **Busy**, including during long work with a stale heartbeat.
Untaken jobs and past reroutes remain visible as delivery warnings; they do not
prove a worker is broken. Explicit worker faults and existing process diagnostics
remain faults. A socket or heartbeat alone does not clear quota evidence.

Wake loops take a local OS file lock for each role and instance before connecting
or spawning work. A second copy exits; a crash releases the lock. Different
workers remain independent. This protects updated wake processes on one host;
it does not terminate pre-upgrade duplicates or provide a distributed lock.

The console requests the same full unarchived job list as `mco_jobs`. The server
pages the database and applies visible-row limits after hiding archived jobs,
so older active work is no longer hidden behind a fixed 200-row window.

## Rollout

1. Review and merge the PR, then update the gateway and worker installations.
2. Set capability declarations and update render-job producers to attach their
   requirements. Review any already queued untagged specialized work before
   relying on capability-aware fallbacks. No database migration is required.
3. Restart the gateway and fleet through the normal operator procedure. Ensure
   old wake copies stop during that restart; the new lock covers updated copies.
4. Refresh the console. Use an approver connection to preview stale work and
   decide whether to cancel it. Automatic dependency expiry uses `halted`, so
   expired jobs remain visible and can be retried or reviewed for cleanup.

No merge, deployment, restart, or live cancellation is part of the PR authoring
workflow. Integration tests use temporary stores, not the live board.
