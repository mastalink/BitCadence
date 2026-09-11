# EPCOT — the BitCadence demo that runs itself

**Status:** design + runbook, v1. Infrastructure in `infra/aws/`.
**What it is:** a complete BitCadence deployment in AWS — gateway, Bedrock-backed workers, ServiceNow and Dynatrace pavilions, Grafana — that **injects faults into itself on a schedule**, records what happened in storage nothing can delete, and publishes the result. Nothing runs on a PC.
**What it is for:** proving, with evidence a stranger can verify, that the governance claims hold under failure — and showing plainly where they do not yet.

---

## 1. Why EPCOT

Walt Disney's original EPCOT was not the park that exists today. It was meant to be a real, working city — a permanent laboratory where new systems were introduced, tested, and demonstrated in front of the public, continuously, and never declared finished. Its plan was radial: a dense core, rings of activity around it, a green belt, and an industrial park where companies would show their newest work operating for real. Its machinery — deliveries, utilities, vehicles — ran underground so that the people above only ever saw the place working.

Those are not decorations for this demo. They are its design rules:

| EPCOT principle | What it means here |
|---|---|
| **A working city, not an exhibit** | This is a real BitCadence installation with real workers doing real jobs, not a recording. A prospect can log in. |
| **Radial: hub → ring → green belt → industry** | Gateway and ledger at the core; workers in the ring; observability in the belt; partner integrations in the park. |
| **Machinery underground** | VPC, IAM, secrets, NAT, KMS — the utilidor. A visitor sees the feed, the console, the dashboards. Never a subnet. |
| **Industry demonstrates live** | ServiceNow, Dynatrace, Grafana each get a *pavilion*: a scenario where their integration is exercised, not described. |
| **Always becoming** | The city breaks itself every six hours and heals. A demo that only works when nothing goes wrong proves nothing. |

## 2. The city map

```
                      ┌──────────── THE GREEN BELT ────────────┐
                      │  ADOT collector → Managed Prometheus    │
                      │  → Managed Grafana   (→ Dynatrace OTLP) │
                      └────────────────────────────────────────┘
   ┌─────────────┐    ┌───────────────── THE RING ─────────────┐    ┌────────────────────┐
   │  PAVILIONS  │    │  worker-claude   worker-reviewer        │    │   EVIDENCE VAULT   │
   │  ServiceNow │◄──►│  worker-servicenow  worker-dynatrace   │    │  S3 Object Lock    │
   │  Dynatrace  │    │  (Fargate, Bedrock via task role)      │    │  COMPLIANCE mode   │
   └─────────────┘    └──────────────────┬─────────────────────┘    │  + KMS             │
                                         │                          │                    │
                      ┌──────────────────▼─────────────────────┐    │  ledger/  mirror   │
                      │              THE HUB                   │───►│  runs/    bundles  │
                      │  gateway  ·  ledger (RDS + PostgREST)  │    └────────────────────┘
                      └──────────────────┬─────────────────────┘
                                         │ /healthz
   ┌─────────────────────────────────────▼─────────────────────────────────────────────┐
   │  DEAD-MAN  Route53 health check → CloudWatch (us-east-1) → SNS → email / SMS     │
   │  Shares no infrastructure with the hub. The phone receives; it never checks.      │
   └───────────────────────────────────────────────────────────────────────────────────┘
   ┌───────────────────────────────────────────────────────────────────────────────────┐
   │  THE CLOCK  EventBridge Scheduler → conductor task → FIS experiments → evidence    │
   └───────────────────────────────────────────────────────────────────────────────────┘
   ═══════════════════════════════ THE UTILIDOR (underground) ═════════════════════════
     VPC · private subnets · NAT · security groups · IAM roles · Secrets Manager · KMS
```

**Two backends, one variable.** `store_backend = "postgres"` runs the ledger in RDS behind PostgREST (the gateway speaks the Supabase client, not bare Postgres — this is the surface it already understands). `store_backend = "local"` runs the embedded LocalStore on EFS. The first proves the enterprise posture; the second is the guaranteed-first-apply path.

## 3. The pavilions

Each pavilion is one scenario the conductor runs. Each states what it proves, how the fault is injected, the assertions, **what passes today**, and the evidence it files. Pavilions marked ● **RED** are expected to fail against the current code; the demo records that honestly, and the failure is the roadmap, live.

### P1 · The Stop Button

*Proves: a human can halt the system, and the halt is recorded.*

**Fault:** the conductor, acting as an operator, sets `MCO_KILL_SWITCH=true` via `PUT /api/settings` while a worker is mid-job.

| # | Assertion | Today |
|---|---|---|
| a | `POST /api/jobs` returns 503 — intake is refused | **PASS** |
| b | `POST /api/jobs/lease` returns 503 — no new leases | **PASS** |
| c | The in-flight worker stops within 30 s and its job is not marked completed | ● **RED** |
| d | The ledger records who flipped the switch and when | ● **RED** — `put_settings()` calls `config.set()` and never `record_event()`; the halt leaves no audit row |
| e | ServiceNow receives an incident "Fleet halted by operator" (pavilion enabled) | via connector |
| f | The Grafana `mco_kill_switch` gauge goes to 1 within one scrape interval | **PASS** |

**Why (d) is red — a new finding from building this:** the settings endpoint persists the change and stops. For a product whose pitch is "every automated decision is recorded," the single most consequential human decision — *halt everything* — is the one with no record. Fix: `put_settings()` emits a system-level audit event (actor, key, old, new) for every change, and the kill switch specifically emits one per leased job it halts.

**Why (c) is red:** `kill_switch_active()` guards intake and leasing only. Its own docstring says in-flight work may finish and report. The worker loop never re-checks the gateway between the lease and the completion. The cloud worker in `infra/aws/worker/worker.py` *does* check between streamed chunks — but the gateway does not yet change a leased job's state when the switch flips, so the check finds nothing. Fixing this is a gateway change: on kill-switch activation, mark every `leased` job `halted`, and reject the stale worker's completion with a fenced CAS (the same machinery as WS1). Until then, the pavilion shows red, and that red is the most important thing on the status page.

### P2 · Worker Killed Mid-Job

*Proves: a dead worker's job is reclaimed and completed exactly once.*

**Fault:** FIS `aws:ecs:stop-task` on one task tagged `bc:component=worker` while it holds a lease.

| # | Assertion | Today |
|---|---|---|
| a | The lease is reclaimed after the stale-lease TTL | **PASS** (TTL is 15 min by default — the conductor waits) |
| b | Another worker leases and completes the job | **PASS** |
| c | The ledger shows attempt 1 expired and attempt 2 completed, in order | **PASS** |
| d | The job is completed **once** — no duplicate side effect | PASS today only because attempt 1 is dead; see P3 |

### P3 · Partition — the stale writer

*Proves: a worker that was cut off cannot finish a job that was given to someone else.*

**Fault:** FIS `aws:network:disrupt-connectivity` on one private subnet for five minutes. The worker there keeps working blind; the gateway reclaims and re-leases its job; the partition heals; the original worker reports completion.

| # | Assertion | Today |
|---|---|---|
| a | The stale worker's completion is **rejected** | ● **RED** — accepted; `handle_job_update()` updates by job ID alone |
| b | The rejection is in the ledger with the expected vs. actual lease | ● **RED** — no lease ID or epoch exists yet |
| c | Exactly one completion event exists for the job | ● **RED** — two writers are accepted |

This is the reviewer's #1 finding running in production, on a clock. It is the reason WS1 (fenced leases) is first in the master plan. When WS1 lands, P3 turns green with no change to the pavilion.

### P4 · The Hub Goes Dark

*Proves: the control plane restarts on its own, workers reconnect, and if it stays down, a human is told by something that is not the control plane.*

**Fault:** FIS `aws:ecs:stop-task` on the gateway. Then, separately, the conductor scales the gateway service to zero for four minutes to exercise the dead-man.

| # | Assertion | Today |
|---|---|---|
| a | ECS restarts the gateway task; `/healthz` returns 200 within 90 s | **PASS** |
| b | Every worker is `online` again within two poll intervals of the restart | **PASS** |
| c | With the gateway held down > 3 min, the Route53/CloudWatch alarm enters ALARM and SNS delivers | **PASS** (delivery is confirmed by the alarm's state history; the email is the human's) |
| d | The alarm returns to OK after recovery and says so | **PASS** |

`/healthz` answers "is the process alive." It does not answer "is the fleet working" — that distinction (WS2's `/readyz`) is not built yet, and this pavilion does not pretend it is.

### P5 · The Tamper

*Proves: altering or deleting evidence is detected, and deletion is impossible.*

**Fault:** the conductor connects to RDS directly — bypassing the gateway entirely, as a malicious DBA would — and rewrites the content of one audit event for a completed job. Then it tries to delete that event's mirrored object from the evidence vault.

| # | Assertion | Today |
|---|---|---|
| a | The product's own `verify_chain()` reports the job's chain broken at exactly that event | **PASS** |
| b | The mirrored object in S3 still holds the original bytes | **PASS** (mirror lag ≤ one shipping interval; stated in the bundle) |
| c | `DeleteObject` on the mirror returns `AccessDenied` — Object Lock, not IAM, refuses | **PASS** |
| d | `PutBucketObjectLockConfiguration` to weaken retention is denied by bucket policy | **PASS** |

Honest scope: the chain proves the rows that remain are unaltered. It cannot by itself prove a deleted *tail* once existed — that is what the S3 mirror and its head checkpoints are for, and today the mirror is asynchronous. The "forward before acknowledge" contract is WS6. `local` backend: this pavilion is recorded **N/A** — the conductor cannot reach EFS.

### P6 · The Runaway Loop

*Proves: spend has a ceiling.*

**Fault:** the conductor posts a scheduled loop with a stated budget of $5 and lets it run.

| # | Assertion | Today |
|---|---|---|
| a | The loop halts when estimated spend reaches the ceiling | ● **RED** — no usage ledger, no budget, no breaker (WS4) |
| b | The halt reason is in the feed | ● **RED** |

The conductor caps this pavilion itself at ten iterations so a red result costs cents, not dollars. It stays on the board as a permanent reminder that "cost-aware routing" has no substrate yet.

### P7 · The Gate

*Proves: the system stops where human judgment is required, and the judgment is recorded.*

**Fault:** none — this is the core governance path, run every cycle so it can never quietly regress.

| # | Assertion | Today |
|---|---|---|
| a | A job with `requires_approval` enters `needs_approval` and no worker can lease it | **PASS** |
| b | The conductor approves as the operator; the job proceeds and completes | **PASS** |
| c | The ledger holds `approved_by`, the timestamp, and the decision, hash-chained | **PASS** |
| d | The evidence pack (`POST /api/governance/evidence-pack`) contains the decision | **PASS** |

## 4. What the regulations ask for, and which pavilion answers

**A correction first.** An earlier draft of the master plan asserted a specific EU AI Act enforcement date and built a sales line on it. The fleet's reviewer showed that claim was wrong as of this document's date. This section therefore maps pavilions to *kinds of obligation* that appear across AI governance regimes — the EU AI Act, NIST AI RMF, ISO/IEC 42001, sector regulators — and **does not assert article numbers, dates, or that any prospect is out of compliance.** Which obligations bind a given deployment depends on role, use case, and risk classification; that is counsel's determination, and this demo produces the evidence they will ask for.

| Obligation category | Pavilion | Evidence artifact |
|---|---|---|
| **Human oversight** — a person can intervene, override, or halt | P1, P7 | `kill-switch-timeline.json`, `approvals.json` |
| **Record-keeping / logging** — automatic, tamper-evident logs of operation | P5, P7 | `chain-verification.json`, `evidence-pack.json`, the `ledger/` mirror |
| **Robustness / resilience** — continues or fails safely under fault | P2, P3, P4 | `fis-experiments.json`, `recovery-timeline.json` |
| **Transparency of automated decisions** — why something was routed or done | P7 (today), P3 (when WS1/WS5 land) | routing receipts in the feed |
| **Access control & integrity** — evidence cannot be altered or destroyed | P5 | `tamper-probe.json` with the `AccessDenied` response |
| **Cost / resource governance** | P6 | ● not yet — WS4 |

Where a pavilion is red, the evidence bundle says so in the same file an auditor would read. That is deliberate: a compliance artifact that hides its own gaps is worth less than one that names them.

## 5. The evidence bundle

Every conductor run writes `runs/<UTC-timestamp>/` to the evidence vault. Bucket-level Object Lock (COMPLIANCE, `evidence_retention_days`) applies to every object; no per-object override can shorten it.

```
runs/2026-09-03T06-00-00Z/
  run.json                 conductor version, pavilions executed, pass/fail per assertion
  evidence-pack.json       the gateway's own export: jobs, decisions, audit events, regulatory_basis
  chain-verification.json  verify_chain() output for every job the run touched
  fis-experiments.json     FIS experiment IDs, start/end, actions, outcomes
  kill-switch-timeline.json  P1: settings change, worker state samples every 5 s, final job state
  recovery-timeline.json   P4: task stop, restart, healthz recovery, worker presence
  tamper-probe.json        P5: the row before/after, verify result, the S3 delete attempt + response
  approvals.json           P7: gate entry, approval, resumption, ledger rows
  connectors.json          ServiceNow incidents and Dynatrace problems created/closed this run
  metrics-snapshot.json    /metrics at run end
ledger/<job_id>/<event_id>.json    every audit event, mirrored continuously by the gateway
ledger/_head/<ts>.json             shipper checkpoints: last event shipped + its hash
```

The status page (`status_page_url`) is rewritten from `run.json` after every run: seven pavilions, green or red, with the timestamp and a link to the bundle prefix. A visitor sees the city's last self-test without a login.

## 6. What passes today — the honest board

| Pavilion | Today | Turns green when |
|---|---|---|
| P1 Stop Button | a, b, f green · **c, d red** | c: gateway halts leased jobs on kill-switch + fenced rejection (WS1 machinery) · d: settings changes emit audit events |
| P2 Worker Killed | green | — |
| P3 Partition | **red** | WS1 fenced leases |
| P4 Hub Dark | green | — (`/readyz` is a WS2 improvement, not a P4 dependency) |
| P5 Tamper | green | — (mirror becomes synchronous in WS6) |
| P6 Runaway Loop | **red** | WS4 usage ledger + breaker |
| P7 Gate | green | — |

Four green, two red, one mixed. **Ship it red.** A prospect who sees P3 red and a dated plan to fix it trusts the green ones more, not less.

## 7. Operating the city

- **Stand it up:** `infra/aws/README.md`. Apply, push three images, confirm the SNS email, run the conductor once.
- **Watch it:** the console at `console_url` (the feed shows every pavilion as it happens), Grafana at `grafana_url`, the public status page.
- **Run a cycle on demand:** `terraform output -raw run_conductor_now`.
- **Change the clock:** `chaos_schedule` in `terraform.tfvars`.
- **Leave it running:** ≈ $170–200/month before credits. The city is meant to be found operating.
- **Tear it down:** everything but the evidence vault. The vault refuses until retention elapses. That is the product.

## 8. What this demo does not claim

- That BitCadence is "compliant" with anything. It produces evidence; compliance is a determination about a deployment, made by people with authority to make it.
- That the ledger is complete under total storage loss. The mirror is asynchronous until WS6; the bundle states the lag.
- That OTel is integrated. The green belt carries the gateway's Prometheus metrics; there is no tracing yet (WS7).
- That the kill switch interrupts running work. It does not. P1(c) is red on purpose until it does.
- That two gateways can run at once. One task, by design, until WS1's fenced reaper exists.

## 8b. Harness calibration — fixed after the fleet critique

reviewer-beast's verdict was that the harness was an uncalibrated instrument
narrating a good design. Two of its findings were bugs in the conductor itself
and are now fixed; the rest are tracked as open work against the demo.

**Fixed — the false green.** `seed_workers()` wrote real worker tokens to
Secrets Manager and rolled the worker services, but ECS had already injected
this conductor's environment, so the process kept Terraform's placeholder
tokens for the whole run. P3's stale-write replay then received a **401** and
scored `rejected -> PASS` for *authentication* rather than for *fencing* -
reporting the demo's single most important assertion as green while no fence
existed. P7's negative lease test passed the same way on run one. Worker tokens
are now read from Secrets Manager at use time, refreshed at startup and again
after seeding.

**Fixed — P4's timing was not a sound bound.** A flat 240-second sleep sits
inside the real budget: three 30 s Route53 checks (~90 s) must fail before the
health status flips, then three 60 s alarm periods (~180 s) must breach, plus
publication latency. On an unfavourable phase a working dead-man scored FAIL.
The pavilion now polls to a deadline computed from both stages and records the
actual detection time.

**Open — P5 does not yet perform the advertised tamper**, and the evidence
mirror's lag is a control hole rather than a disclaimer. Until both are
addressed, P5's green is provisional.

**Open — the settings audit gap is broader than P1 states.** `put_settings()`
never calls `record_event()` for *any* setting, so approver roles, gated roles,
trusted-header auth, connector credentials and tenant configuration all change
without a durable audit row. The fix is a first-class system/configuration
audit stream carrying actor, old value (secrets redacted), new value,
correlation ID and outcome - not merely a per-job kill event.

**Open — the regulatory language still overclaims in places.** Section 4 is
being re-read against that finding.

Until P5 and the token/lag items close, **the honest headline is not
"four green" but "four green, two red, one mixed, and one instrument still
being calibrated."**

## 9. First-apply risks

Listed in `infra/aws/README.md`. The one that matters most: the PostgREST seam. If it fights you, `store_backend = "local"` and move on — every pavilion except P5 runs identically.

## Fleet critique - reviewer-beast

**Verdict: do not call the current board four-green.** The design is worth building, and publishing red is the right instinct, but several green assertions either cannot pass on the current code or can pass for the wrong reason. More seriously, P3 does not reliably create the stale-writer race, P5 cannot reach the database it claims to tamper with, and an all-`NA` pavilion is summarized as `PASS`. Until the harness proves its own preconditions and the evidence reader is version-aware, the demo is an attractive narration around an uncalibrated test instrument.

### P0 — the two flagship fault proofs do not exercise the claimed faults

1. **P3 does not reliably partition the lease holder or leave a second eligible worker.** `chaos.tf` disconnects all traffic for the fixed `private[1]` subnet. ECS is free to place the sole task for `worker_roles[0]` in either private subnet, and the gateway, collector, and conductor use the same pair. Therefore the chosen subnet may contain no lease holder, or it may contain the conductor/gateway as well. Even when it catches the worker, `scope = "all"` also severs that worker's Bedrock stream; the premise that it “keeps working blind” is not established. There is only one task per role, so no other same-role worker can lease the reclaimed job while that task is isolated. The harness must first record the holder's ECS task/subnet, isolate only its gateway path, and prove a distinct eligible attempt completed before replaying the old attempt. Otherwise P3 is a synthetic API replay, not the advertised production race.

2. **P5 cannot reach RDS, and opening that path would still not perform the advertised tamper.** `network.tf` permits port 5432 only from `aws_security_group.gateway`, while the conductor runs with `aws_security_group.workers`. Its baseline `psycopg.connect(DATABASE_URL)` therefore times out and the shim records `ERROR` before any tamper attempt. If that network path is deliberately opened, `2026-06_phase_a_governance.sql` supplies the next independent blocker: `trg_agent_job_events_immutable` raises on every `UPDATE` or `DELETE`. `p5_tamper()` uses the database owner and executes a plain `UPDATE`; it neither disables the trigger nor catches that statement locally. A real DBA-capability exercise must grant the conductor a narrowly scoped path for the run, explicitly cross and restore the preventive control in a `finally`, and distinguish “the trigger blocked tamper” from “tamper succeeded and the detective control found it.” Both are useful results; conflating them is not.

3. **The result reducer paints an inapplicable pavilion green.** `Run.summary()` returns `PASS` when every assertion is either `PASS` or `NA`. On the documented `local` backend P5 marks all four assertions `NA`, so the public page reports P5 `PASS`, not `NA`. The reducer needs an explicit all-`NA` case, and no pavilion should turn green without at least one executed claim. This is a false public claim even if every individual assertion row is honest.

4. **The S3 delete assertion expects the opposite of versioned Object Lock behavior.** The conductor is allowed `s3:DeleteObject`, but calls it without a `VersionId`. In a versioned bucket, that operation normally succeeds by adding a delete marker; Object Lock protects a specified object version and does not prevent new versions or delete markers. An ordinary `GET` then returns 404, even though the retained version still exists. Likewise, writing the same key creates a newer version; it does not overwrite the locked bytes, but it can replace what an unversioned reader sees. AWS documents both behaviors in [Object Lock](https://docs.aws.amazon.com/AmazonS3/latest/userguide/object-lock.html), [DeleteObject](https://docs.aws.amazon.com/AmazonS3/latest/API/API_DeleteObject.html), and [delete markers](https://docs.aws.amazon.com/AmazonS3/latest/userguide/DeleteMarker.html). P5(c) should attempt deletion of the exact retained version, and every manifest/verifier must pin and retrieve `VersionId`; separately test and deny key-hiding delete markers. As written, `DeleteObject` succeeding makes P5 red and disproves “deletion is impossible” at the key-view layer.

5. **A stranger cannot verify what the public page claims.** The status bucket publishes assertion labels; the evidence bucket is private, the page prints an `s3://` prefix rather than a usable immutable-version link, and chain verification uses a secret HMAC held by the same deployment. `chain-verification.json` is therefore the system saying it verified itself. An enterprise proof needs a signed run manifest with version IDs, code/image digests, test inputs, timestamps, and a public or customer-held verification key plus a controlled retrieval path. Until then, “evidence a stranger can verify” and “a link to the bundle prefix” are false.

### P1 — the requested code findings and assertion traces

6. **Kill-switch audit finding (a): confirmed, with a larger scope than stated.** `put_settings()` authorizes an admin, normalizes the value, calls `config.set()`/`config.delete()`, logs to the process log, and returns. It never obtains the job database or calls `record_event()`. The true and false flips therefore produce no durable audit rows. In fact every control-panel setting change has this gap, including approver roles, gated roles, trusted-header authentication, connector credentials, and tenant configuration. A per-job kill event is useful for affected work, but it is not a substitute for a first-class system/configuration audit stream containing actor, old value (redacted for secrets), new value, request/correlation ID, and outcome. P1(d) must also query that system stream; its current search of one job's events would reject the correct system-level design.

7. **SDK finding (b): the conclusion is right, but “both `complete()` and `fail()` raise” is not the code path.** `process_job()` catches only handler exceptions and then calls `fail()`; if that call raises, the exception reaches `run()`, which logs a poll-cycle warning and forgets the attempt. A successful handler takes a separate path: `complete()` is outside the `try`, so if completion raises it also reaches `run()` and is forgotten; `fail()` is never attempted. There is no durable outbox, retry of the terminal report, idempotency key, or reconciliation. Thus a completed result can be silently lost and the real SDK cannot later produce P3's stale return. That silent availability failure deserves its own top-severity pavilion, but it is not categorically worse than accepting a stale writer: lost work destroys availability and audit completeness; stale acceptance can corrupt authoritative state or duplicate irreversible side effects. Treat both as release blockers, with different impact classes.

8. **P2's current completion filter is not the substring test in the question.** It checks the exact event value against `completed`/`job.completed`, or exact `status == "completed"`; a `lease_expired` event whose detail mentions “completed” does not increment it. That narrow point is sound. The assertion still passes with **zero** completion events because it uses `len(completions) <= 1`, while audit writes are explicitly best-effort. P2(c) merely tests the earlier `reclaimed` and `done` booleans; it does not verify an expiry event, a later lease, order, different attempt/worker, or actor. The only configured worker for that role restarts with the same instance ID, so “another worker” is not demonstrated. Nor can a ledger count prove an external side effect happened once. Require exactly one accepted completion, explicit ordered lease-generation events, and an idempotency receipt from the side-effect boundary. P3, unlike P2, really does use the broad `"completed" in json.dumps(event)` filter and can be polluted by unrelated fields.

9. **P3 returns 200 on current main, as the document predicts, if the harness reaches the replay.** The replay authenticates with the token for `worker_roles[0]`. The route permits the call when caller role equals `target_agent_role`; it does not require `leased_by_instance_id` to match, and ignores the body's `agent_instance_id`. `handle_job_update()` then updates by `id` only, even though the row is already completed, appends another `status:completed` event, ACKs, and the HTTP route returns 200. Commit `7ee7c19` fixed the conductor's stale-token false green, so authentication no longer masks this result. One qualification matters: the replay sends `result`, while the route reads `output_payload`; the stale payload is discarded. The probe proves that a stale actor can rewrite terminal status and append a second completion event, not that this particular stale result body replaced the authoritative output.

10. **P7's negative lease test now deterministically scores correct behavior as failure.** Leasing a `needs_approval` job is refused by the `lease_task` RPC as `{ "success": false }` with HTTP 200. `a2_lease_refused` tests only `status_code >= 400`; after `7ee7c19` made the token valid, the correct product behavior is scored `FAIL`. Check the response contract and authenticated identity, not merely an HTTP class. P7(d) is also never asserted: `file_evidence()` fetches a pack, but no code checks that this job's decision is present.

11. **P4's original 240-second sleep was not a reliable upper bound; current main has corrected this part.** Three 30-second failed Route 53 checks can consume roughly 90 seconds before `HealthCheckStatus` turns unhealthy; the CloudWatch alarm then requires three 60-second breaching data points. The conservative sequential budget is therefore roughly 270 seconds in an unfavorable phase, plus publication/evaluation latency. AWS confirms that the health threshold counts consecutive checks and that alarm evaluation uses the configured data-point window ([Route 53 health checks](https://docs.aws.amazon.com/Route53/latest/DeveloperGuide/welcome-health-checks.html), [CloudWatch metric periods](https://docs.aws.amazon.com/AmazonCloudWatch/latest/monitoring/cloudwatch_concepts.html)). Commit `7ee7c19` now polls for up to 390 seconds and records detection time, which is a defensible harness bound.

12. **P4 can pass on unrelated, stale, or late evidence.** It scans every account alarm and accepts any name ending `-site-dark`, rather than the alarm created for this stack. The 90-second restart assertion actually allows 120 seconds (`90 + 30`), and “within two poll intervals” actually waits 120 seconds even though cloud workers poll every five seconds. “Every worker online again” is only a count of any online registry rows; it neither checks the configured instance IDs nor requires a post-restart heartbeat. Alarm state history does not prove SNS email/SMS delivery, and the assertion never inspects a delivery receipt. These claims must be narrowed or instrumented end to end.

13. **The P5 datetime shim should not fail for the reason proposed, but it proves the wrong adapter.** `_content_of()` does include `created_at`. The hash input is the outbound `_now_iso()` string, which uses Python `datetime.isoformat()`; psycopg returns a datetime at PostgreSQL's microsecond precision, and the shim's `isoformat()` reconstructs that same six-digit `+00:00` form. So, once network access exists, the guard will not fail merely because `_Q` converts the datetime. It does **not** show that the production Supabase/PostgREST read path is byte-stable: PostgreSQL's JSON formatter can trim fractional trailing zeroes, while Python preserves six digits when microseconds are nonzero. A timestamp ending in one or more zeroes can therefore verify through `_DB` yet fail through the actual gateway adapter. Canonicalize timestamps to a defined UTC representation before hashing, and test the production adapter with edge values. The current shim bypasses the seam it is supposed to validate.

### P1 — the evidence lag is a control hole, not merely a ten-second disclaimer

14. **What an RDS-controlling attacker can do before shipment:** disable the append-only trigger; alter the new row so S3 preserves attacker-chosen bytes; delete the unshipped tail so no object ever appears; or move its `(created_at, id)` at or below the shipper's in-memory watermark so it is skipped. The HMAC may later reveal altered content to a verifier with the key, but it cannot reveal a deleted unanchored tail. The gateway already acknowledged the business transition before any of these outcomes are known.

15. **“Lag ≤ one shipping interval” is not true.** The loop sleeps ten seconds after each batch of 500, swallows every shipper exception while the gateway stays healthy, exposes no durable outbox/backlog or alarm, and resets its watermark to the epoch on every gateway restart. Lag is ten seconds only in the no-backlog/no-error steady state. On restart, replay writes every database row to the same keys as new S3 versions; after an RDS tamper, a routine restart can make a tampered version the one returned by ordinary `GET`, while the original remains hidden in version history. The bundle's current sentence is therefore an inadequate disclosure of a real completeness and discoverability hole. Say “unbounded and unmonitored asynchronous lag; acknowledged events may never be mirrored,” until a durable forward-before-ack/outbox contract and version-pinned manifest exist.

16. **The watermark can skip honest events even without an attacker.** `record_event()` assigns `created_at` in application code before the insert commits, while the shipper advances an in-memory `(created_at, id)` watermark over committed rows. If request A receives an earlier timestamp and commits after request B has already been mirrored, A lands behind the watermark and is never selected. A durable database sequence or transactional outbox—not an application timestamp—must define shipment order.

### P1 — regulatory language still overclaims

17. **Rename section 4.** NIST calls AI RMF voluntary, and ISO describes ISO/IEC 42001 as an AI management-system standard; neither is a “regulation” ([NIST AI RMF](https://airc.nist.gov/airmf-resources/airmf/), [ISO/IEC 42001](https://www.iso.org/standard/42001)). “Governance frameworks and potentially relevant evidence” is accurate.

18. **Replace “answers” and “produces the evidence they will ask for” with “may contribute evidence, subject to scope and validation.”** The EU AI Act is risk-, system-, and actor-specific; its high-risk requirements cover much more than these runtime pavilions, including risk management, data governance, documentation, accuracy, cybersecurity, post-market monitoring, and role-specific duties ([official regulation](https://eur-lex.europa.eu/eli/reg/2024/1689/oj?locale=en)). A compliance officer will also reject “evidence cannot be altered or destroyed,” “automatic” record-keeping despite best-effort writes and unaudited settings, and resilience mappings that cite a red P3 as an answer.

19. **The no-article-number correction is contradicted by the artifact being advertised.** Section 4 itself contains no article number, but `export_evidence_pack()` emits keys named `eu_ai_act_article_12` and `eu_ai_act_article_14`, and the PDF cover prints those article mappings. P7 claims that pack as evidence. Either validate those mappings with counsel and scope them to the applicable actor/system, or make the export framework-neutral; the prose cannot disclaim article claims while the generated compliance artifact makes them.

### P2 — what an enterprise buyer or Dynatrace partner asks in the first ten minutes

20. **“Show me the Dynatrace pavilion.”** There is none. The conductor never calls Dynatrace, never queries for ingested metrics, problems, dashboards, or Davis analysis, and `connectors.json` contains only two booleans. ServiceNow P1 calls `/api/integrations/servicenow/actions`, but the implemented route is singular `/action`, so the enabled pavilion gets 404. Define partner acceptance evidence: tenant/environment ID, ingest response and latency, metric dimensions, dashboard/problem IDs, deep links, close-loop action, and redacted API audit receipts.

21. **“Which exact build and run can I reproduce?”** The bundle lacks Git SHA, container image digests/SBOM/signatures, Terraform state/config digest, migration versions, AWS account/region/stack identity, FIS template revisions, model ID/version, prompt/response token accounting, clock-skew evidence, and a harness self-test. `:latest` images make historical reproduction impossible.

22. **“What is the security and operating model?”** The public console/API is HTTP unless an optional external Cloudflare step is added; the demo uses one broad local admin bearer token; workers of the same role can update each other's jobs; RDS is single-AZ with deletion protection off; the status JSON exposes internal assertion detail; and no backup-restore, regional failure, KMS/key-loss, secret rotation, token revocation, evidence legal hold, retention-policy approval, or disaster-recovery exercise exists. These can be honest demo limitations, but they must be explicit before a buyer supplies ServiceNow/Dynatrace credentials.

23. **“Does green mean the claim, the mechanism, and the delivery all worked?”** Today it often means only that a status code/string/count matched. Add assertion provenance and preconditions: exact resource IDs, authenticated actor, before/after state, event sequence, independent observable, negative control, timeout calculation, and an `INVALID` outcome when the injected fault did not hit its target. `ERROR` is not enough when a false PASS is possible.

### Recommended re-baseline

Call P1 mixed, P2 **unproven**, P3 **invalid/red**, P4 **unproven**, P5 **invalid/red on Postgres and falsely green on local**, P6 red, and P7 **harness-red despite the underlying gate working**. The first credible milestone is not a prettier public board; it is a calibration run where every pavilion proves its setup, fault target, expected negative control, and evidence version before it is allowed to emit PASS.
