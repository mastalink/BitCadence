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

## 9. First-apply risks

Listed in `infra/aws/README.md`. The one that matters most: the PostgREST seam. If it fights you, `store_backend = "local"` and move on — every pavilion except P5 runs identically.
