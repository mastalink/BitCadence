# VIA Score cloud runner acceptance — 2026-09-14

## Decision

The first BitCadence Score cloud adapter is deployed. It refreshes the G01 VIA inventory on a six-hour cadence without a workstation, interactive session, long-lived credential, general agent shell, deployment authority, or source-data mutation capability.

This is an accepted **audit lane**, not a declaration that G01, the general Score conductor, or VIA production is complete.

## Deployed boundary

- Function: `via-score-audit-runner`, Python 3.12 Lambda, 256 MB, 90-second timeout.
- Trigger: enabled EventBridge rule `via-score-audit-schedule`, `rate(6 hours)`.
- State: private, versioned, server-side-encrypted S3 backend with native S3 locking. It is separate from VIA evidence storage.
- Evidence: append-only runner writes only sanitized JSON beneath the pre-existing VIA evidence bucket's `score-audits/` prefix.

The function role allows only: its own CloudWatch log writes; a fixed set of read-only inventory calls; list of the existing `backups/` prefix; append of its reports; and two fixed public VIA API requests implemented in code. It has no permission to read S3 objects, retrieve secrets, access IAM, invoke SSM commands, restart services, deploy, assume another role, or receive a URL/command from its trigger.

## Acceptance evidence

The function was invoked once after deployment under its Lambda role. It returned HTTP 200 and wrote a sanitized artifact under the runner prefix. That artifact reported every fixed check successful: identity, VIA host, SSM presence, CloudFormation inventory, backup inventory, alarms, public health, and Lorain confession query. The artifact's `production_readiness` remains `not_determined` by design.

Offline tests for the handler and existing Score/audit tests passed: 65 tests. Terraform format/validate passed. A subsequent cloud-backed Terraform plan reported no changes.

## What this does not authorize

The next adapter is a separately reviewed deployment lane. It needs an immutable build input, a narrow release wrapper, rollback/idempotency behavior, exact-head test and review evidence, policy-bound spend/authority, and a new accepted Score grant. Do not expand this audit role to serve that purpose.
