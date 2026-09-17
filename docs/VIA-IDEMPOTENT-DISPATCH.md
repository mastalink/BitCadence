# Via dispatch gateway prerequisite

Via persists an operation UUID before contacting BitCadence. A lost response must
not create another job. Authenticated `GET /api/jobs/capabilities` advertises
`{"create_with_id":1}` so Via can refuse legacy gateways before sending work.

`POST /api/jobs` accepts an optional canonical UUID `id`. The database primary key
chooses the creator atomically; a stored SHA-256 digest binds that ID to the
authenticated tenant, sender and immutable creation input. Identical replay
returns the current job in the existing `{"success":true,"job":...}` envelope.
Changed intent or another owner receives a generic 409. Omitted IDs preserve
legacy creation. Replay does not reset job status, retries, leases or output.

`GET /api/jobs/{id}` returns the bare job to its creator, addressed worker,
unaddressed target role, or same-tenant admin. Unrelated callers receive 404.
This is reconciliation visibility, not permission to lease or publish results.

## Integration gate

Apply the additive `2026-09_job_create_idempotency.sql` migration to PostgreSQL
before using explicit IDs. Both packaged and operator SQL copies are included.
The JSON-backed local store does not require a column migration. A missing cloud
column causes creation to fail; it cannot silently fall back to a random ID.
No running gateway, database or worker was changed by this branch.

This branch is based on `codex/bitcadence-completion` at 41e9905; its existing
security and deployment gates still apply. It does not modify the MCP client or
completion repair owned by other work. Require native PostgreSQL/PostgREST
integration evidence before production adoption; local-store tests alone do not
prove cloud storage behavior.

## Evidence and limits

82 focused tests pass across creation idempotency, job priority/board, governance
and authorization. New coverage includes simultaneous replay, tenant/sender
collision, lost response, legacy callers, completed-job preservation and exact
worker read authorization. This prevents duplicate jobs and repeated normal
creation side effects. Existing job insertion and audit/broadcast are separate
operations: a process crash between them can leave a persisted job without its
creation notification. Workers must reconcile the durable queue; this branch
does not claim transactional exactly-once notification delivery.

Implementation began in an isolated Codex subagent. After its workspace-credit
failure, the parent inspected the changes, expanded regression coverage and ran
the focused checks. No external-provider MCO completion is claimed.

## Score-to-job idempotent dispatch

The Score conductor persists dispatch intent in `score_outbox` before it contacts
BitCadence. Each row carries organization, score, run, digest, task, attempt and
phase stamps. `(org_id, run_id, task_id, phase)` is unique, and `job_id` is the
UUIDv5 of `score-v1:{org}:{run}:{digest}:{task}:{phase}` in `NAMESPACE_URL`.
The outbox, not a worker or caller-supplied random ID, mints and owns that UUID.

`ScoreOutboxDispatcher.enqueue()` adds the same immutable stamps to
`input_payload.score` and forces `agent_jobs.depends_on=[]`. Score dependencies
are conductor state: legacy job dependency release cannot unlock a Score-owned
job. Worker `completed` state is only an input for a later conductor tick; the
dispatcher has no Score accept operation and never mutates `score_tasks`.

## Gateway boundary and replay

Authenticated `GET /api/jobs/capabilities` must advertise
`{"create_with_id":1}`. `POST /api/jobs` accepts the outbox-minted canonical UUID
and binds it to the authenticated tenant, sender and immutable creation intent.
Identical replay returns the current job without resetting status, retries,
leases or output. Changed intent or a pre-existing random UUID collision returns
409. Omitted IDs retain legacy job creation behavior.

The dispatcher moves `planned` rows to `sending` before the network call. If the
gateway commits and the response is lost, the row stays `sending`; the next tick
replays the same UUID and reconciles it to `submitted`. This can repeat the HTTP
request, but it cannot insert a second `agent_jobs` row.

## Integration gate and limits

Apply both `2026-09_job_create_idempotency.sql` and the revised
`2026-09_score_store.sql` before enabling Score dispatch. Packaged and operator
copies are identical. The local store mirrors the outbox natural-key constraint.
A missing cloud column or constraint must fail closed; there is no random-ID
fallback.

This packet does not apply migrations, deploy AWS, submit `via-cloud.score.json`,
or advance a Score run. S02c owns conductor ticks and authenticated transition
ingestion. PostgreSQL/PostgREST integration evidence and independent review are
still required before production adoption.
