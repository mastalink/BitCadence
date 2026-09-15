# Score-to-job idempotent dispatch

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
