# BitCadence completion candidate — September 2026

This candidate implements the findings in the Fable/reviewer release plan.
It is **not a published 0.4.0 release or a verified AWS deployment**. The live
AWS chaos board remains the release gate; P6 spending enforcement is still
explicitly deferred in the original plan.

## Implemented and exercised

| Findings | Candidate behavior | Evidence |
|---|---|---|
| F1–F5 | Full owner/ID/epoch/incarnation proof, legal transitions, renewal, atomic reaping, restore fencing; HTTP and WebSocket enforce the same policy | Real SQLite and PostgreSQL acceptance; stale socket and disabled-identity tests |
| F6 | An attempt idempotency key reaches SDK/listener workers | SDK/listener contract; external services must implement deduplication using the supplied key |
| F7–F8 | Stop pauses acquisition and halts active jobs; audited settings persist from the console | Gateway acceptance plus stop-during-evidence-outage regression |
| F9 | Exponential result retries, idempotent terminal receipts, and disk-backed replay after process restart | Lost-response/restart/fenced-replay tests |
| F10–F11 | Job state and its outbox evidence commit together; DB-serialized chain append; storage errors propagate | Rollback, concurrent connections, real PostgreSQL RPC and outbox recovery tests |
| F12 | Signed checkpoints detect a removed tail when verified against an externally retained checkpoint | Tail deletion test; `audit-checkpoint` and `audit --checkpoint` |
| F13–F14 | Independent maintenance timer; separate process liveness and store/maintenance readiness; missing workers reported degraded | Readiness and timer tests |
| F15 | Notifications use only the configured destination; no topic means no push | Eight notification entry points exercised |
| F18 | P5 actually changes SQL data inside a rolled-back transaction and confirms the trigger is restored; object tests target a version | Real SQL tamper test; S3 calls mocked locally |
| F19 | ECS enables synchronous locked-S3 acknowledgement of attempt evidence, with durable DB outbox and asynchronous repair | Sink outage/recovery/unversioned-response tests; actual S3 retention still requires AWS rehearsal |

The pre-existing fixes F16, F17, F20, F21, and F22 remain in the base history.
The P1/P2/P3 harness now checks exact event types and fencing responses rather
than interpreting authentication errors or unrelated events as success.

The console saves actual gateway settings, selects registered worker roles,
shows halted/cancelled jobs, retries halted work explicitly, and displays an
offline state after a failed connection. A browser run created an approval-gated
checksum job, approved it, and observed real completion by `review-checksum`.

## Run the isolated local instance

From the candidate checkout, with BitCadence dependencies installed:

```powershell
python scripts/run_review.py --port 18890
```

Open `http://127.0.0.1:18890/console`. Connection details live in the ignored
`.codex/review/connection.json`. The isolated config and DB are under that same
directory. This does not use the normal fleet database. The checksum worker
performs SHA-256 calculations; AI jobs require configured AI workers.

Stop a foreground instance with Ctrl+C. A background instance writes its PID
to `.codex/review/pid`; verify the process command line is `scripts/run_review.py`
before stopping it. Settings → Stop work pauses intake and halts attempts;
turning it off leaves halted jobs for deliberate Retry in the job board.

## Recovery and deployment contract

- Upgrade gateway and workers together. Old clients do not carry the proof
  required by newly acquired attempts. The old PostgreSQL `lease_task` RPC is
  intentionally disabled; modern clients use `mco_lease`.
- After restoring a database, keep workers disconnected and run
  `mco restore-fence`. It pauses work and rotates the store incarnation. Review
  halted jobs before resuming. Snapshot restore detection is an operator step,
  not something an embedded database can infer from its own restored contents.
- Set an audit signing key, export `mco audit-checkpoint JOB checkpoint.json`,
  and retain the file outside the database/backup volume. Verify with
  `mco audit JOB --checkpoint checkpoint.json`. Local hash chains alone cannot
  prove that their tail was not deleted. Keep signing keys stable across history.
- Result spools live under `~/.mco/results` or `MCO_RESULT_SPOOL_DIR`, separated
  by gateway/identity. They contain results and lease proofs; protect that
  directory. `.rejected` files preserve outputs refused by the gateway. Mount
  persistent storage if results must survive container replacement; an ECS
  task's ephemeral filesystem does not provide that guarantee.
- Python handlers must call `agent.checkpoint()` between side effects.
  Background renewal detects lost ownership, but cannot forcibly interrupt
  arbitrary Python or undo an external action. Built-in subprocess cancellation
  terminates the owned process tree; Bedrock streaming checks between events.
- S3 and database commits are not distributed transactions. A sink failure
  leaves committed state plus outbox evidence, but returns no successful attempt
  acknowledgement. Exact result replay recovers the response. Emergency stop
  fences locally before waiting on S3. A lost lease acknowledgement may leave
  an unstarted lease until expiry.

## AWS rollout still required

AWS CLI v2 and Terraform are installed. The configured profile authenticated,
but certificate and IAM Identity Center discovery were denied. Use a deployment
profile authorized for the resources in `infra/aws`; do not paste credentials
into chat. Supply an alert email, hostname, and issued regional ACM certificate.
`us-east-1` is the template default. Bedrock listing alone does not prove model
invocation access.

The Terraform candidate validates locally. It requires HTTPS, redirects HTTP,
pins immutable image tags, probes readiness, and gives only the conductor and
gateway database network access. Gateway, worker, and conductor Docker images
have built locally. Follow [AWS deployment steps](../infra/aws/README.md), review
the actual plan, configure DNS, and run the live probes before publishing.

No cloud resources, release tag, or package publication are asserted here.
Object Lock retention and autonomous chaos have persistent operational effects;
the reviewed Terraform plan is the record of what will actually be deployed.

## Repeatable verification

Local verification on 2026-09-05: **840 tests passed** in the full suite with
PostgreSQL and cloud-test dependencies enabled, followed by **2 passing recovery
CLI tests**. Console source round-trip and Terraform validation passed. The
browser stop probe halted an active attempt and its late completion returned
HTTP 409. These results do not substitute for live AWS chaos/retention tests.

```powershell
python -m pytest -q
python scripts/build_console.py verify
terraform -chdir=infra/aws validate
```

`tests/test_release_acceptance.py` runs SQLite locally and PostgreSQL when
`BC_TEST_POSTGREST_URL` is set. `scripts/check_postgres.py` requires a disposable
`BC_TEST_DATABASE_URL`, applies bootstrap twice, and waits for the PostgREST
contract. The dedicated GitHub workflow supplies both services and runs the
tamper test with `boto3` and `psycopg`. Local mocked S3 assertions must not be
reported as verified live AWS Object Lock behavior.
