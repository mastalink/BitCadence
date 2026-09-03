# BitCadence 0.4.0 — "the claims become true"

**Version:** `0.4.0` (from `0.3.0`). Minor, not major: no breaking change for
users. The CLI is still `mco`, every `MCO_*` variable is unchanged, config still
lives in `~/.mco/`. What changes is that the governance claims stop being
aspirational.

**Ship condition:** the EPCOT demo's board reads **all green** on a live AWS
deployment running this version. Not "tests pass" — the pavilions.

---

## 1. Why this release exists

Between 1 and 3 September the fleet reviewed its own product and found that
several things BitCadence sells are not yet true:

- Two workers can both claim one job and the API accepts **both** completions.
- The **kill switch does not stop work already running**, and pulling it leaves
  **no audit record** — for a product whose pitch is "every decision is
  recorded," the most consequential human decision is the unrecorded one.
- The health endpoint reported **200 for 24 hours over a dead fleet**.
- Audit events are written best-effort and **silently dropped on failure**.
- A partitioned worker's finished work is **lost, not late** — never retried.
- Job pushes go to **guessable public ntfy topics**.

0.4.0 closes exactly those. It does not add features. Anything that is not a
broken claim is deferred to 0.5.0 on purpose.

## 2. The findings ledger

Every finding from this cycle, its source, and where it is closed. Nothing is
dropped silently; deferrals are named.

| # | Finding | Source | Closed by |
|---|---|---|---|
| F1 | Completion accepted from a worker whose lease was reclaimed (`handle_job_update` updates by job id alone; WS path too) | reviewer, ADR 0002 | **WS-A** |
| F2 | Reaper is read-then-update; two gateways can both requeue one job | reviewer, plan | **WS-A** |
| F3 | No lease renewal; fixed 15-min TTL; reaper only runs when a worker polls | reviewer | **WS-A** |
| F4 | Epoch can roll backward after a snapshot restore (ABA) | reviewer, plan | **WS-A** |
| F5 | Handlers accept an arbitrary status string; no enforced transitions | reviewer, plan | **WS-A** |
| F6 | External side effects have no idempotency key | reviewer, plan | **WS-A** |
| F7 | Kill switch does not halt in-flight work; workers have no interruption point | mine, EPCOT build | **WS-B** |
| F8 | `put_settings()` never calls `record_event()` — **no setting change is audited** | mine, confirmed + broadened by reviewer | **WS-B** |
| F9 | SDK drops a completion that fails mid-partition — lost, not late | mine, EPCOT build | **WS-B** |
| F10 | `record_event()` swallows write failures; state can commit without evidence | reviewer | **WS-C** |
| F11 | Chain lock is process-local; a second gateway can fork the chain | reviewer | **WS-C** |
| F12 | Chain cannot detect a deleted tail; "immutable" overclaims for Appliance | reviewer | **WS-C** |
| F13 | `/healthz` checks a db object and a flag; returned 200 over a dead fleet | mine + reviewer | **WS-D** |
| F14 | Lease recovery is opportunistic, so a fleet with no workers has no reaper | reviewer | **WS-D** |
| F15 | Job pushes ignore `NTFY_TOPIC` and go to guessable public topics | prior finding, still open | **WS-D** |
| F16 | Conductor scored its own key assertion PASS for the wrong reason | reviewer, EPCOT | ✅ fixed `7ee7c19` |
| F17 | P4's 240 s sleep is not a sound bound | reviewer, EPCOT | ✅ fixed `7ee7c19` |
| F18 | P5 does not perform the advertised tamper | reviewer, EPCOT | **WS-E** |
| F19 | Evidence mirror is async — a control hole, not a disclaimer | reviewer, EPCOT | **WS-C** + **WS-E** |
| F20 | Workers and humans shared one checkout; finished work nearly lost | mine, observed 5× | ✅ fixed `b57a72c` |
| F21 | AES-GCM AAD and uuid5 namespaces renamed — silent data loss | mine, code review | ✅ fixed `5cb2fdd` |
| F22 | Regulatory claim asserted a deadline that had moved | reviewer | ✅ withdrawn |

**Deferred to 0.5.0, explicitly:** cost/usage ledger and spend breaker; tenant
isolation at the database (RLS) and per-tenant keys; OTel semantic layer and
vendor packs; intent-first routing with receipts; the console Machine tab; the
tray. Each is real work, none of them is a *false claim*, so none blocks 0.4.0.

## 3. Workstreams

Four, sized so two agents can work in parallel without touching the same files.

### WS-A — Fenced leases and one owner *(codex-beast)*

Closes **F1–F6**. The floor everything else stands on.

- Lease carries an unguessable `lease_id` and a monotonic `lease_epoch`, plus a
  store-incarnation term so a restored snapshot cannot replay an old epoch (F4).
- Acquire / renew / expire / complete / fail are **compare-and-set** on
  `(job_id, lease_id, lease_epoch, owner, allowed_state)` using database time.
  Every one returns whether it won.
- Expiry is one statement: `UPDATE ... WHERE lease_epoch = expected AND
  lease_expires_at <= now()`. Only the winner advances the epoch and emits the
  requeue event — so multiple reapers are safe and no leader is required.
- Legal transitions enumerated and enforced in the store/RPC, not the handler.
- **Both transports** (HTTP `PUT /api/jobs/{id}` and the WebSocket handler) and
  **both backends** (LocalStore, Postgres RPC).
- `(job_id, attempt)` idempotency key exposed to connectors.

**Files:** `src/mco/localstore.py`, `src/mco/orchestrator/routes.py`,
`handlers.py`, `src/mco/migrations/2026-09_fenced_leases.sql`, tests.

### WS-B — The stop button, and the record of pulling it *(grok-beast)*

Closes **F7–F9**. This is the one the founder singled out.

- **Kill switch halts in-flight work.** On activation, every `leased` job
  transitions to `halted` via WS-A's CAS. A worker returning afterwards is
  fenced, not accepted.
- **Workers get an interruption point.** The SDK re-checks ownership between
  units of work and raises a `Halted` exception that is reported, not swallowed.
- **A system audit stream.** `put_settings()` emits an event for **every**
  setting — actor, key, old value (redacted for secrets), new value, correlation
  id, outcome. Not a kill-switch special case: approver roles, gated roles,
  trusted-header auth, connector credentials and tenant config all currently
  change with no record.
- **Completions are late, never lost.** The SDK retries a failed
  complete/fail with its `lease_id` attached, bounded, so a partitioned worker's
  result is either accepted or explicitly fenced — and either way it is visible.

**Files:** `src/mco/orchestrator/admin_routes.py`, `src/mco/sdk.py`,
`src/mco/orchestrator/audit.py` (new event kind), tests.

### WS-C — Evidence that cannot quietly vanish *(codex-beast, after WS-A)*

Closes **F10–F12, F19**.

- State change and its event are written **atomically** (transactional outbox).
  `record_event()` no longer swallows failures.
- Chain append serialized **in the database**, not a process-local lock.
- Signed **chain-head checkpoints** so a deleted tail is detectable.
- The Appliance audit claim is restated honestly: *tamper-evident with a stated
  backup RPO*, unless events are forwarded off-box before acknowledgement.

### WS-D — Silence becomes impossible *(grok-beast, after WS-B)*

Closes **F13–F15**.

- `/healthz` = "is this process alive." New `/readyz` = "can this gateway
  safely accept work" (store reachable, scheduler heartbeat fresh). Fleet
  liveness is reported as **degraded health**, never as failed readiness — a
  load balancer must not remove the control plane exactly when an operator
  needs it to approve or cancel work.
- The reaper runs on a **timer**, not on worker polling.
- **ntfy topic leak fixed**: honor `NTFY_TOPIC`, never derive a public topic
  from a role name.

### WS-E — The harness tells the truth *(claude)*

Closes **F18**, and re-verifies **F16/F17**.

- P5 performs the real tamper and proves the mirror and the lock.
- Every pavilion re-run against 0.4.0; the board becomes the release gate.

## 4. The demo is the acceptance suite

Do not write separate acceptance criteria. Each pavilion **is** a governance
claim, so a green board is the definition of done.

| Pavilion | Claim it proves | Today | Green when |
|---|---|---|---|
| P1 Stop Button | a human can halt the system, and the halt is recorded | c, d **red** | WS-B |
| P2 Worker Killed | a dead worker's job completes exactly once | green | stays green under WS-A |
| P3 Partition | a disconnected worker cannot corrupt finished work | **red** | WS-A |
| P4 Hub Dark | the control plane recovers; silence reaches a human | green | stays green under WS-D |
| P5 Tamper | evidence cannot be altered or destroyed | provisional | WS-C + WS-E |
| P6 Runaway Loop | spend has a ceiling | **red** | **0.5.0 — stays red, and says so** |
| P7 The Gate | the system stops for judgment, and records it | green | stays green |

**P6 stays red in 0.4.0 and the board says so.** Publishing a red beside a
dated plan is the posture; hiding it would cost more than it buys.

## 5. Build, test, release

**Every workstream, before it is called done:**

1. Unit tests with a fake clock — no real sleeps in supervision or lease tests.
2. **Adversarial concurrency tests**: race two reapers and prove exactly one
   requeue; race cancel against completion; reject renewal after expiry;
   restore an old snapshot and prove a pre-restore lease cannot write.
3. Both backends and both transports, or it is not done.
4. Full suite green on Linux CI (`tests/test_install_sh_tty.py` has 2 known
   Windows-only failures; everything else must pass).

**Release mechanics:**

1. Land all workstreams on `main`, each via PR, each reviewed by `reviewer-beast`.
2. `CHANGELOG.md`: move `[Unreleased]` to `## [0.4.0] - <date>`, grouped
   Added / Changed / Fixed / Security, with a **Upgrade notes** block.
3. `pyproject.toml` → `version = "0.4.0"`.
4. Tag `v0.4.0` — the release workflow triggers on `v*`.
5. Publish `bitcadence` to PyPI (the README and site still show `git clone`
   because the package does not exist yet; that changes here).

**Upgrade notes to write:** the lease schema migration is forward-only; a
0.3.0 worker cannot complete a 0.4.0-leased job (it has no `lease_id`), so
**upgrade the gateway and workers together**. `mco fleet apply` after upgrading.

## 6. The full gamut in AWS

`infra/aws/` already builds the city. For 0.4.0:

1. Build and push the three images at the `v0.4.0` tag.
2. `terraform apply` with `store_backend = "postgres"` — the Team posture, and
   the one that exercises the seam a pilot would run.
3. Wire the ServiceNow and Dynatrace tenants so P1(e) and the pavilions are
   live rather than skipped.
4. Let the conductor run on its schedule. **The published status page becomes
   the release evidence** — a public, timestamped, self-generated record that
   the claims hold under fault.
5. Keep the evidence vault. It is Object Lock COMPLIANCE; nothing deletes it,
   including us. That is the point.

**Before any of that:** the PostgREST seam has never been run. `codex` is
already tasked with exercising it locally (job `b5b7e492`). If it cannot be
made to work on bare Postgres, `store_backend = "local"` ships instead and the
Team posture waits for 0.5.0 — a legitimate outcome, not a failure.

## 7. What 0.4.0 does not claim

- Not multi-tenant. Appliance is single-tenant, declared.
- No cost control. P6 stays red.
- No OTel, no vendor packs.
- No automatic routing. Roles are still addressed explicitly.
- Not "compliant" with anything. It produces evidence; compliance is a
  determination about a deployment, made by people with authority to make it.

## 8. Sequence

**Stage 1 — WS-A.** Nothing else is safe until one owner is enforced.
**Stage 2 — WS-B and WS-D in parallel** (different files, different agents).
**Stage 3 — WS-C**, which depends on WS-A's fencing for its atomicity guarantee.
**Stage 4 — WS-E**, deploy, run the board, publish.

Honest calendar, at a sustainable 7–10 focused hours a week and given the
reviewer's own sizing: **WS-A alone is 2–3 weeks.** The whole release is a
**90-day** commitment, not 30. Say 90 and hit it.
