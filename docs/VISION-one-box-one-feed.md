# One box, one feed — where BitCadence goes next

**Status:** vision brief, for fleet critique. Not a spec. Argue with it.
**Author:** Joe + MOSES/Claude, 2026-09-02

---

## The fact that started this

On 2026-09-01 at 19:04 UTC the entire fleet went offline — beast and mac, every
role, inside forty seconds. It stayed down for **24 hours**. Nothing noticed.
Nothing told anyone. It was found by a human squinting at an agent list.

The gateway was running the whole time. It answered `/healthz` with a 200 while
its entire workforce was dead.

That is not a bug to fix. It is the product's shape being wrong. Everything
below follows from it.

---

## The thesis

> **BitCadence should be one text box and one feed.**
> You type what you want. The fleet decides who does it. You watch it happen.
> Everything else is progressive disclosure.

Three lenses, honestly applied:

- **Jobs:** the iPod's genius was the wheel, not the storage. BitCadence's wheel
  is a single input and a single chronological feed. Today's "New Job" composer
  is a form with eight fields — a form is what you ship when you have not yet
  decided what matters.
- **Disney:** Magic Kingdom hides its service tunnels underground so a guest
  never sees a delivery truck. BitCadence currently shows the guest Task
  Scheduler, `fleet.toml`, token files, and MCP configs. All of that is utilidor.
  It goes under the park.
- **Musk:** the best part is no part. Before optimising the job form, ask whether
  the job form should exist. Before adding a status page, ask why anything is
  ever in an unknown state.

---

## Ten ideas

### 1. The Pi is the heart, not a worker (contrarian)

Everyone's instinct with a spare Pi is "run a small model on it." Wrong. A Pi 5
draws a few watts and **never sleeps**. Beast and the mac sleep, lock, reboot,
and get closed. That is precisely why the fleet died.

Move the gateway, the scheduler, and a watchdog onto a Pi. It becomes the
nervous system; beast and the Jetson stay the muscle. The second Pi is a warm
standby. Nothing else in this document works if the coordinator lives on a
machine that turns off.

**This is the "constant communication" answer, literally.**

### 2. The watchdog that speaks first

The fleet was dark for a day and the system said nothing. Presence data already
exists (`last_seen_at`, `MCO_AGENT_OFFLINE_AFTER`). A watchdog on the Pi that
notices "every worker went offline inside 60 seconds" and pushes a
notification turns a 24-hour outage into a 90-second one.

Silence must never be indistinguishable from health. Today it is.

### 3. The audit trail *is* the feed

Do not build an activity stream. There is already an immutable, hash-chained,
append-only record of everything that happened. Render it as prose:

> `codex` picked up **fix CI on main** · 2m · opened PR #48 · **waiting on you**

One data source, two views: a feed for humans, an audit trail for regulators.
Same bytes. That is elegant, it is free, and it makes the compliance story a
side effect of the UX rather than a separate feature.

### 4. Delete roles from the user's vocabulary (contrarian)

Today you address `role: codex`. But you do not care *who* does the work — you
care that it is done well, cheaply, and with a record. Roles should be a
**capability declaration by the worker**, not an address chosen by the human.

You type "review this PR." The board routes on weights, liveness, and cost. If
you want to pin it, that is an advanced disclosure — not the default path.

### 5. Accounts are a routable resource (nobody else models this)

Beast and the mac hold *separate* ChatGPT and Antigravity accounts, and *shared*
Grok and Claude accounts. That means separate rate limits — real, exploitable
capacity that is currently invisible to the scheduler.

Model the account as a first-class resource with its own budget and cooldown.
Then "beast's Claude is rate-limited, mac's Claude is fresh" becomes a routing
decision instead of a failed job. No competitor models this because no
competitor runs on a founder's two desktops.

### 6. Weighted routing with governed escalation (already half-built)

`escalate_to_role` and `max_retries` already exist. That is cost-aware routing
with an audit trail: try the local model, escalate to frontier on failure.

The refinement is weights plus liveness plus account budget — and the key
property is that **the routing is inspectable because it is dumb.** A weight is
a number a human set, sitting in the audit trail next to the gate that
constrained it. A classifier's decision cannot be explained to an auditor.

Every escalation also writes a labelled outcome — *local tried, failed, frontier
succeeded, on this class of work* — which makes the weights tunable from your
own data. A black-box router has no trustworthy record of its own misses.

### 7. OpenTelemetry is the entire enterprise integration (leverage)

Dynatrace, Grafana, and Datadog all consume OTel. Do not build three
integrations. Emit one:

| BitCadence concept | OTel concept |
|---|---|
| job | trace |
| lease → completion | span |
| escalation | span link |
| approval gate | span event |
| agent instance | service |
| Drumline recall | span attribute |

One emitter, three logos on the slide, zero bespoke vendor work. And it lands
the EU AI Act hook: provable, exportable records of automated decisions in the
tool the customer's SREs already stare at all day. `MCO_LOG_JSON` proves the
plumbing instinct is already there — this is the same move one level up.

### 8. Progressive disclosure, defined concretely

- **Level 1 — a box and a feed.** Type work. Watch it happen. Approve when
  asked. This is 95% of days and it is the *only* thing a new user sees.
- **Level 2 — appears when it matters.** Approval gates, retries, escalation
  paths. Surfaced by events, not by a menu.
- **Level 3 — opened deliberately.** The Machine tab, `fleet.toml`, routing
  weights, connections, raw audit.

The rule: **a control appears the first time it is needed, not the first time it
is possible.**

### 9. It has to reach the phone

Constant communication that requires sitting at a desk is not constant. A PWA
serving the same box-and-feed, with push **only** for approval gates, puts the
fleet in a pocket.

Prerequisite: job pushes currently ignore `NTFY_TOPIC` and go to guessable
public `ntfy.sh/mco-<role>` topics. That is a live data leak and it must be
fixed before push is turned on for anything real.

### 10. The MOSES seam

Keep them separate and connect them with one bridge:

- **MOSES + Genesis** — who Joe is, what he wants, his life. *Identity memory.*
- **BitCadence + Drumline** — making it happen and proving it happened.
  *Work memory.*

MOSES decides **what**. BitCadence governs **how** and records **what actually
happened**. The bridge is narrow: MOSES posts jobs and reads the feed. Merging
them would produce something that is bad at both.

---

## Hardware, assigned by physics rather than by habit

| Machine | Job | Why |
|---|---|---|
| **Pi 5 #1** | gateway, scheduler, watchdog | never sleeps; watts, not hundreds of watts |
| **Pi 5 #2** | warm standby, push relay | the coordinator needs a spare |
| **Jetson** | always-on small inference | CUDA at idle power; the "is this worth waking beast for?" triage |
| **beast** | heavy work — Qwen 3.8, ComfyUI, MiniMax, the frontier CLIs | GPUs and RAM |
| **mac mini** | second capacity pool, *separate accounts* | parallel rate limits, second OS for cross-platform proof |
| **$5,100 credits** | burst and the hosted demo | do not spend it on anything a Pi can do |

The organising principle: **the cheap thing that never dies runs the nervous
system; the expensive things that sleep do the work.**

---

## Ranked by (impact ÷ effort)

| # | Idea | Effort | Impact | Risk |
|---|---|---|---|---|
| 1 | Pi as always-on gateway + watchdog | **Low** | **High** | Low — moves an existing service |
| 2 | Watchdog notification on fleet silence | **Low** | **High** | Low — presence data exists |
| 3 | Audit trail rendered as the feed | Low | High | Low — read-only over existing data |
| 7 | OTel emitter | Medium | **High** | Low — additive; unlocks 3 vendors |
| 8 | Progressive disclosure of the console | Medium | High | Medium — real design work |
| 5 | Accounts as routable resource | Medium | Medium | Medium — needs a budget model |
| 4 | Roles as capability, not address | High | High | **High** — changes the core mental model |
| 9 | PWA + push | Medium | Medium | **Blocked** on the ntfy topic leak |

---

## Where to start

**The Pi.** Not because it is the most exciting, but because it is the only item
that is cheap, provable in a weekend, and load-bearing for everything else. A
fleet that is dark for 24 hours makes every other idea theoretical.

Sequence: Pi gateway → watchdog → feed → OTel → progressive disclosure.

---

## Questions for the fleet

Argue with this. Specifically:

1. **Is idea 4 right or reckless?** Removing roles from the user's vocabulary is
   the biggest mental-model change here. Make the case against it.
2. **Can a Pi 5 actually carry the gateway** at this fleet's job volume, or is
   this a romantic answer that falls over at scale?
3. **What breaks in idea 3** when the audit trail and the feed are the same
   object? Where does an auditor's need diverge from a human reader's?
4. **Is OTel genuinely enough** for Dynatrace *and* Grafana *and* Datadog, or
   does each still need bespoke work in practice?
5. **What did this brief miss entirely?** Cost control, multi-tenant, the Jetson,
   secrets across five machines, what happens when the Pi itself dies.

---

## Fleet critique - reviewer-beast

**Verdict:** keep the one-box/one-feed product thesis, but do not use it to hide
control-plane choices that determine trust, cost, and correctness. The proposed
sequence is backwards. Before the feed, OTel, or automatic routing, BitCadence
needs a lease protocol that survives partitions, an externally observed health
contract, and an explicit durability/recovery design. Idea 1 is reasonable as a
single-user appliance profile but wrong as the fleet architecture. Ideas 3 and
7 confuse a shared source/transport with a finished product. Idea 4 should make
automatic routing the default, not delete worker identity from the model.

Ranked by how much each finding should change the plan:

| Rank | Finding | Required plan change |
|---|---|---|
| 1 | A partition can create two valid-looking owners of one job | Block fleet-scale routing on renewable, fenced leases and idempotent completion |
| 2 | The Pi proposal moves the single point of failure; it does not remove it | Specify failure detection, restart, backup, election, and recovery before selecting hardware |
| 3 | Hiding roles can hide security, reproducibility, and separation-of-duty constraints | Keep intent-first routing, but make constraints, pins, and the routing receipt first-class |
| 4 | An audit ledger and a human feed cannot be the same object | Share canonical domain events, then build separate ledger and feed projections |
| 5 | There is no cost-control substrate for cost-aware routing | Add metering, reservation, hard limits, and circuit breakers before weighted routing |
| 6 | OTel is an export format, not three enterprise integrations | Build one semantic emitter plus tested vendor deployment packs and product content |
| 7 | Tenant and secret boundaries stop at the gateway | Design isolation and machine identity across storage, workers, and telemetry |
| 8 | LocalStore is deliberately a small local backend, not a fleet database | Bound it to the appliance profile; use a server database for multi-node operation |
| 9 | The Jetson assignment has no contract or evidence | Benchmark it as an optional worker before putting triage on the critical path |

### 1. Blocker: the current lease is not safe under partitions

The database claim itself is atomic: `LocalStore._lease_task()` serializes a
pending-to-leased transition under one process's `threading.Lock`, and the
Postgres `lease_task` RPC performs a conditional update. That prevents two
healthy pollers from winning the initial race. It does **not** establish
exclusive ownership for the duration of the work.

`routes.reclaim_stale_leases()` uses `started_at` and a fixed 15-minute default,
has no lease renewal, and runs only when another worker polls. After a network
partition, worker A can continue performing a 20-minute side effect while the
gateway requeues its job and worker B leases it. Worse, `PUT /api/jobs/{id}`
checks whether the caller matches the job's target role or target instance, but
`handlers.handle_job_update()` updates by job ID alone. It does not compare the
caller with `leased_by_instance_id`, require the current status to be leased,
or present a lease generation. A stale same-role worker can therefore complete
or fail the job after it has been re-leased. Two machines do not merely *think*
they own the job; the API will accept both as writers.

Replace the TTL-only lease with a renewable lease carrying an unguessable lease
ID and a monotonically increasing fencing epoch. Completion must be a compare-
and-set on job ID, current state, owner, and epoch. Long-running workers renew;
expired workers are fenced even if they return. External side effects need an
idempotency key derived from job plus attempt. Define the partition policy
plainly: which work may continue disconnected, which must stop, and which
requires reconciliation. Until that exists, automatic rerouting increases the
blast radius.

### 2. Idea 1 is sound as a box, romantic as an availability design

A Pi 5 has ample CPU for the current household fleet. The risk is not compute;
it is pretending that "never sleeps" means "does not fail." Power supplies,
storage, the switch, the router, the process, and the Pi itself all fail. A
watchdog colocated with the gateway cannot report the death of its own host.
The second Pi is not a warm standby until there is replication, leader election,
fencing, a tested promotion path, and a way to avoid both Pis accepting writes.
None is present in `service.py` or `fleet.py`.

The implementation makes the gap concrete:

- `service._gateway_spec()` sets `restart_on_failure=False`, but the Linux
  renderer still gives every non-poll service `Restart=always` (and launchd
  keeps the gateway alive). That is useful process recovery on a Pi, not host,
  storage, database, network, or notification-path recovery.
- `/healthz` reports that the process has a database object and whether the kill
  switch is set. It does not query the database, scheduler, notification path,
  or worker quorum. It can still return 200 with zero workers, exactly as it did
  during the incident.
- `fleet.py` manages worker services on one machine. It has no fleet-wide
  reconciler, remote health model, standby state, or failover mechanism.
- Lease recovery is opportunistic on worker polling, so a fleet with no live
  workers also has no lease reaper.

For the local appliance edition, use one Pi with a UPS, a quality USB SSD rather
than an SD card, process restart, periodic consistent snapshots, restore drills,
and an external dead-man monitor in a different failure domain. The second Pi
can be a cold, rehearsed replacement before it is called warm. For multi-node or
enterprise operation, put state in replicated Postgres and run replaceable
gateways; do not replicate a live SQLite file or place it on a network share.
Choose the hardware only after defining RPO, RTO, leader ownership, and the
failure signal that still works when the whole site is dark.

### 3. Strongest case against Idea 4: a worker is sometimes a constraint, not an implementation detail

The normal path should absolutely be "review this PR." The mistake is turning
that UX default into "delete roles from the user's vocabulary." A human must be
able to constrain or pin execution when:

- only one machine has the repository, device, licensed tool, account, or
  secret;
- policy permits data to reach one model, region, tenant, or trust tier but not
  another;
- a reviewer must be independent of the worker that authored the change;
- an incident requires draining or quarantining a suspect agent/version;
- a result must reproduce a prior run on the same model, toolchain, and host;
- conversational or cached state makes continuity more valuable than load
  balancing; or
- a user accepts a slower worker to enforce a hard cost ceiling.

The current code proves that worker identity is not superficial. Job creation
requires `target_agent_role`; `target_agent_id` is the explicit pin; pending
polls filter both; and lease authorization rejects a different role or pinned
instance. There is no capability registry, account budget, routing score, or
router today. `max_retries` and `escalate_to_role` are failure policy, not
cost-aware scheduling.

Replace the job form with intent-first submission, but preserve role, instance,
trust tier, locality, account, and cost as visible constraints. Every automatic
choice should emit a **routing receipt**: selected worker/model/account,
constraints applied, alternatives rejected, estimated cost, and reason. Put a
plain-language "use this worker" control in progressive disclosure, allow
policy to require it, and show the chosen worker in the feed. Hiding the knobs
is good UX; hiding who acted is bad operations and bad audit.

### 4. Idea 3 should share events, not bytes

An auditor wants completeness, exact actors, durable ordering, integrity
verification, stable identifiers, restricted raw evidence, retention policy,
and proof of gaps. A human wants a small number of current, comprehensible
items: deduplicated, coalesced, localized, permission-filtered, and sometimes
correctable. Those products diverge even when they begin with the same domain
event.

A concrete failure case already exists. `handle_job_update()` puts the full
`error_message` into the audit event detail. Imagine a worker repeats a failure
20 times and one error includes a credential-bearing URL. The audit side needs
20 exact transitions or a controlled evidence reference; the feed needs one
redacted "failed after 20 attempts" item. If both are the same immutable bytes,
the feed either leaks the credential and floods the reader, or the record is
redacted/coalesced and ceases to be the evidence the auditor expected. Later
erasure, legal hold, and tenant-specific retention make the conflict worse.

The present audit implementation is also not yet a compliance ledger:

- `record_event()` deliberately swallows write failures, so a successful job
  mutation may have no corresponding event.
- Chain serialization uses a process-local per-job lock. A second gateway can
  append from the same tail and fork the chain.
- HMAC signing is optional; without it, a database administrator can recompute
  a replacement chain.
- The Activity endpoint enriches events using only the newest 500 jobs and
  omits a non-default tenant's event when its job is outside that window. That
  is acceptable feed behavior and unacceptable audit behavior.

Use an immutable canonical domain-event ledger with atomic state-change/event
writes and database-level serialization. Build a separate feed projection that
may summarize, redact, group, and evolve. Store sensitive evidence behind a
stricter access and retention boundary, referenced by digest. "One source, two
views" is right; "same object" and "same bytes" are wrong.

### 5. The brief has routing economics without cost control

`max_retries` limits retry count, not dollars. The job schema and audit events
do not record provider, model, input/output tokens, price version, estimated
cost, actual cost, account quota, or budget owner. `/metrics` exposes job and
agent counts but no spend. Therefore the proposed router cannot choose "well,
cheaply" or know whether escalation is allowed. Schedules and loops can multiply
this blind spot into runaway spend.

Add a durable usage ledger and three-phase budget contract: estimate and reserve
before lease, meter during execution where possible, then settle actual usage.
Support hard per-job, workflow, account, user, and tenant limits; concurrency
caps; daily/monthly ceilings; anomaly alerts; and an emergency spend circuit
breaker independent of the normal scheduler. Routing weights should consume
these facts, not invent them.

### 6. Idea 7 is false as written: OTel makes export portable, not the integration free

OTel is the right common telemetry envelope. It does not define BitCadence's
job, lease, gate, escalation, approval, policy decision, or audit semantics, so
those remain a custom schema that every backend must map and every release must
version. A job can also last hours or days; treating the whole lifecycle as one
open trace is a poor fit for sampling, partial-trace handling, and operational
queries. [OTLP explicitly permits duplicates after
reconnects](https://opentelemetry.io/docs/specs/otlp/#duplicate-data), which is
fine for telemetry and disqualifies it as the audit source of truth.

The vendors themselves document the bespoke edge:

- [Datadog recommends its Collector distribution or Datadog Exporter and
  Connector](https://docs.datadoghq.com/opentelemetry/setup/collector_exporter/)
  for enrichment and trace metrics, plus API key/site, hostname, tagging,
  batching, and memory configuration. Its direct vendor-neutral OTLP intake is
  still described as Preview and does not populate the Infrastructure Host
  List.
- [Dynatrace's OTLP endpoint](https://docs.dynatrace.com/docs/ingest-from/opentelemetry/otlp-api)
  requires vendor token scopes, TLS, HTTP/protobuf rather than gRPC, and manual
  host/topology enrichment for vanilla OTLP through ActiveGate.
- [Grafana recommends Alloy for production](https://grafana.com/docs/opentelemetry/collector/)
  and still needs backend selection, authentication, resource detection,
  buffering, dashboards, recording rules, and alerts.

OTel does not carry the buyer's dashboards, alert thresholds, notification
routing, SLOs, service ownership, RBAC mapping, retention/residency rules,
cardinality and sampling budgets, incident workflows, legal holds, audit
signatures, or support matrix. It also needs a privacy policy: prompts, outputs,
errors, and Drumline attributes may contain secrets or regulated data.

Replace "zero bespoke vendor work" with **one semantic instrumentation layer
plus vendor packs**. Model each execution attempt as a span, state changes as
correlated events/logs, and queue depth, lease age, fleet liveness, token use,
and spend as metrics, all keyed by a stable job ID. Ship and test a collector
configuration, attribute mapping, dashboards, alerts, and runbook for each
claimed logo. Keep the hash-chained ledger authoritative.

### 7. Multi-tenancy and secrets need end-to-end boundaries

The multi-tenancy migration adds `org_id` columns and indexes, but no database
row-level-security policies. Most REST routes filter in application code while
the gateway uses a high-privilege database credential. That is a useful first
layer, not a tenant-isolation proof. LocalStore defaults every row to `default`
and should be declared single-tenant only. Operator-wide metrics intentionally
aggregate all orgs, which also needs an explicit authorization and cardinality
policy before hosted service.

The shared vault encrypts every tenant's records with one gateway master key;
its `key_version` records a version but there is no demonstrated rotation or
per-tenant KMS boundary. Meanwhile `fleet.py` expects per-instance token files
under `~/.mco/tokens` on each worker host. It can warn that a local file is
missing, but it does not provision, rotate, revoke, or attest credentials across
five machines. Provider credentials needed by worker CLIs remain a separate
distribution problem.

Before calling this multi-tenant, define workload identity and machine
enrollment, mTLS or an equivalent authenticated channel, short-lived scoped
credentials, per-tenant keys, rotation/revocation, worker-side secret handling,
and telemetry redaction. Secrets should not ride in job prompts, audit details,
or OTel attributes. Test isolation at the database and object-storage layers,
not only through route handlers.

### 8. LocalStore cannot be the fleet-scale heart

`localstore.py` says the assumption plainly: local single-operator volumes do
not need an index. Each table is `(pk, JSON)`. A filtered select loads and
decodes every row in Python; ordering is also in Python. Updates and upserts
scan the table, all operations share one process-local lock and one connection,
and every mutation commits immediately. WAL improves local concurrency and
crash behavior; it does not supply replication, cross-process fencing, backups,
or flash-wear management.

On a USB SSD this is adequate for a bounded founder fleet and a good zero-
dependency edition. On SD storage, event-heavy audit/feed writes add wear
and poor tail latency. At enterprise job volume the full scans and serialized
writes become the limit before Pi CPU does. Put explicit scale and retention
limits on LocalStore, compact/archive feed projections without deleting audit
evidence, and move multi-node deployments to Postgres. Do not let "runs on a
Pi" silently choose the consistency model for the product.

### 9. The Jetson is a slide, not yet a component

No implementation outside this brief names the Jetson. `fleet.py` can declare
only role, instance, mode, command, and polling intervals; it has no capability,
model inventory, memory, thermal, power, queue, or price contract. The proposed
"is this worth waking beast for?" triage also creates a new model decision whose
false negatives can suppress important work and whose latency/cost must be
measured.

Treat the Jetson as an optional inference worker until a benchmark answers:
which model, quantization, context limit, tokens/second, idle/load watts,
quality threshold, sandbox, concurrency, and failure fallback? Initially use it
for advisory classification with a recorded confidence and easy override; do
not put it on the sole path between submission and execution.

### Revised order

1. Fenced renewable leases, idempotent side effects, and explicit partition
   behavior.
2. Honest readiness plus an external dead-man alert; restart and recovery
   drills.
3. Choose the single-node Pi appliance or replicated Postgres deployment from
   declared RPO/RTO and scale targets.
4. Cost/usage ledger, tenant boundaries, and machine/secret identity.
5. Intent-first routing with visible constraints, pins, and routing receipts.
6. Canonical event ledger and a separate human feed projection.
7. OTel semantic emitter, then one tested vendor pack at a time.
8. Jetson evaluation; PWA after the control plane can prove it is alive.

## Build assessment - codex

### Estimating basis

These are engineer-days for one person who already knows this repository. They
include implementation, focused tests, documentation, and one local acceptance
run; they exclude hardware shipping, review latency, and enterprise HA. The
assessment is against `main` at `75e820e`. It also checks the live state of PR
#47 rather than the older premise in this brief: that PR now contains the ADR
0002 revision 2 rework, all reported GitHub checks are green, and it remains
open.

| Idea | Smallest honest shippable slice | One-person estimate |
|---|---|---:|
| 1. Pi gateway + scheduler + watchdog | Supported single-Pi appliance on USB SSD, recoverable but not HA | **8-12 days** |
| 2. Fleet-silence watchdog | Debounced fleet incident plus secure push and recovery message | **4-6 days** colocated; **8-12 days** including external dead-man coverage |
| 3. Audit trail as activity feed | Make the existing Activity implementation the primary, narrative feed | **4-7 days** |
| 7. OpenTelemetry emitter | Versioned semantics, OTLP export, propagation, privacy controls, one collector pack | **10-15 days**; add **3-5 days per claimed vendor pack** |
| 8. Progressive disclosure | One-box/one-feed console shell using today's role-based submit contract | **8-12 days**; intent routing is separate and materially larger |

The important scope line is Idea 1: 8-12 days produces a good single-node
appliance with a rehearsed restore. Making Pi #2 a genuinely warm standby adds
replication, leadership, fencing, and failover and is a separate **25-40 day**
project after the lease protocol is safe.

### Idea 1 - put the control plane on a Pi

**What is already reusable.** `service.install()`, `install_scheduler()`, and
`_service_systemd_unit_text()` already dispatch Linux to `systemd --user`, set
`Restart=always`, wait for `network-online.target`, and use `%h` instead of a
Windows path. `scheduler.load_config()`, `launcher.tick()`, and
`launcher.run_forever()` are platform-neutral; schedule state is already
atomically written. `LocalStore` uses SQLite WAL and a process lock and needs no
external database. Configuration, schedules, tokens, logs, the encrypted secret
store, and schedule state all consistently live under `~/.mco`.

**What has to be built and proved.** The appliance needs a reproducible ARM64
install/image, pinned Python dependency smoke tests, an install-time choice of a
dedicated service user versus a logged-in user, and a cold-boot test after power
loss. A `systemd --user` unit does not start headlessly unless lingering is
enabled; the installer currently prints that instruction but does not prove the
result. The gateway and scheduler need explicit dependency and readiness
ordering, log rotation, disk-space alarms, clock synchronization, unattended
update/rollback policy, and tunnel/DNS recovery.

State migration needs a manifest, not a copy of “the database”: `local.db`, its
WAL state, `.env`, `secrets.enc`, the Linux unlock source,
`schedules.yaml`, `schedule-state.json`, per-instance token files, and any
operator TLS/tunnel configuration all matter. A Windows-created secret store is
normally unlocked by Windows Credential Manager; Linux only auto-unlocks it
from `MCO_MASTER_PASSWORD`, so copying `secrets.enc` alone deliberately leaves
the Pi unable to start. Provide a secure re-enrollment/import flow instead of
copying the Windows key.

For durability, run SQLite on a quality USB SSD, set and test a busy timeout and
checkpoint policy, add a SQLite backup-API snapshot rather than copying a live
WAL database, encrypt the off-box backup, and rehearse restore onto replacement
hardware. Test disk-full, corrupt/truncated state, sudden power loss, clock
jump, router outage, and a week-long soak. Bind/auth/TLS also need an explicit
network design: the current loopback default is safe, while exposing the Pi to
the LAN or Internet without a reverse proxy, scoped credentials, and firewall
rules is not.

**Where 8-12 days is most likely wrong.** Native dependency wheels may be fine
on 64-bit Raspberry Pi OS, but that is not covered by current x86 Ubuntu CI.
The bigger uncertainty is operational: filesystem and power behavior under
real failure, and whether remote workers can reliably reach the Pi through the
actual router/tunnel. If “second Pi” means automatic failover rather than a
cold, tested replacement, the estimate is wrong by several weeks.

### Idea 2 - watchdog that speaks first

**What is already reusable.** The gateway writes `last_seen_at`; workers refresh
it through `touch_agent_presence()` and WebSocket authentication. The
`decorate_presence()` and `get_offline_after_seconds()` functions already
derive effective liveness from `MCO_AGENT_OFFLINE_AFTER`. `/api/agents` exposes
that derived state without token hashes. `notifiers.ntfy.notify()` already has
timeouts, optional bearer auth, priority, and tags, and `service.py` can install
a long-running Linux service.

**What has to be built.** Add a persisted incident state machine, not a loop
that sends on every tick: startup grace, previous-online baseline, “all workers
crossed offline within 60 seconds,” debounce, deduplication, maintenance/drain
mode, one alert per incident, and a recovery notification. Record the incident
and notification outcome so silence in the notifier is observable. The current
role-specific notification helpers override the configured topic with
`mco-<role>`; that public-topic leak must be fixed before real payloads are
enabled.

A colocated watchdog covers worker collapse but cannot report that the Pi,
gateway process, power, router, DNS, or notification route died. The honest
version therefore adds a heartbeat to an external dead-man in a different
failure domain. Its acceptance test should kill workers, then the gateway, then
power/network access separately and verify exactly one alert plus one recovery
for each case.

**Where the estimate is most likely wrong.** “All workers offline” is easy;
avoiding false alarms across intentional shutdowns, sleeping laptops, stale
registrations, clock skew, and notification-provider outages is the work. Four
to six days is enough only for the local fleet-collapse detector. Claiming the
original 24-hour failure is solved requires the external path and 8-12 days.

### Idea 3 - make the audit trail the feed

**What is already reusable.** This is further along than the brief implies.
`GET /api/events` already returns the cross-job event stream newest first and
enriches it with job title, status, and target role. `ActivityFeedScreen` already
loads it, searches and filters it, calculates basic statistics, translates
event names into plain copy, and opens the related job. The Overview also has a
small `ActivityFeed`, and `/ws/broadcast` plus polling already provide refresh
signals. Per-job `AuditTrail` remains available for the regulatory view.

**What has to be built.** Replace the Overview/sidebar-first shell with a
cursor-paginated primary feed and a persistent single composer. Add a canonical
presentation mapping for every event type: narrative text, actor, duration,
result reference, severity, and action such as Approve or Open PR. Coalesce
retries and noisy status churn, redact sensitive details, group workflow steps,
preserve accessible raw detail behind disclosure, and resume from a durable
cursor after reconnect rather than synthesizing only changes observed by the
open browser.

The present endpoint limits events before applying `since`, joins against only
the newest 500 jobs, and LocalStore scans/decodes JSON rows. It needs real
pagination and query semantics before the feed can grow. The event vocabulary
is also job-centric: gateway, scheduler, watchdog, agent-presence, and
notification incidents need canonical events if the feed is truly “everything
that matters.” Keep the audit row authoritative and make the human feed a
projection; do not mutate or coalesce the evidence itself.

**Where the estimate is most likely wrong.** Four to seven days is credible for
the founder-fleet UX because the API and screens exist. It is not a compliance
estimate. `record_event()` intentionally swallows write failures, and its chain
serialization is process-local, so atomic state-plus-event persistence and
multi-gateway audit correctness are separate backend projects.

### Idea 7 - OpenTelemetry emitter

**What is already reusable.** Stable job IDs, actors, roles, statuses, audit
events, WebSocket lifecycle events, `MCO_LOG_JSON`, and Prometheus job/agent
gauges provide most source facts. The mutation seams are reasonably
centralized in job creation, lease, `handle_job_update()`, approval/rejection,
retry, and escalation. That is enough to build one internal telemetry adapter
instead of sprinkling vendor SDK calls through routes.

**What has to be built.** Add optional OTel dependencies and configuration;
define and version a low-cardinality BitCadence semantic convention; propagate
trace context through the job row, SDK, MCP boundary, and worker wrapper; emit
one span per execution attempt; represent state transitions as correlated
events/logs; and use links for retry/escalation across attempts. Add queue age,
lease age, fleet liveness, approval wait, retry, and failure metrics. Apply an
explicit allowlist/redaction policy so prompts, outputs, error URLs, secrets,
and Drumline content do not leak into attributes. Buffer/export failures must
never block job state changes, but their drops must themselves be measurable.

Then ship an OTLP Collector reference configuration and end-to-end tests for
one backend. Datadog, Dynatrace, and Grafana each still need authentication,
resource mapping, dashboards, alerts, cardinality/sampling limits, and a
runbook; those are the 3-5 day vendor packs. A trace must not be held open for a
multi-day job, and telemetry must not become the audit authority.

**Where the estimate is most likely wrong.** The emitter is straightforward;
distributed context ownership is not. Today a job crosses HTTP, MCP, database,
poll/wake wrappers, and vendor CLIs, some of which cannot return usage or span
context. Vendor-specific production acceptance and privacy review, not SDK
calls, are what can double the estimate.

### Idea 8 - progressive disclosure of the console

**What is already reusable.** The console source is componentized by screen.
It defaults to plain-language copy, stores an `advanced` preference, hides raw
IDs/payloads/retry details when Advanced is off, and already has Overview,
Activity, approvals, job detail, and a drawer composer. Approval actions and
the audit trail are therefore available to surface contextually rather than
through permanent navigation.

**What has to be built.** Introduce a Level 1 shell with one persistent input,
the narrative feed, inline approval cards, connection/fleet health, responsive
mobile layout, keyboard/focus behavior, empty/error/reconnect states, and a
clear disclosure control. Level 2 should open the relevant gate, retry, or
escalation controls from the event that needs them. Level 3 can retain the
existing Jobs, Workflows, Agents, Governance, Memory, Activity, and Settings
screens behind one deliberate “Manage” entry. Preserve deep links and operator
escape hatches, and test both plain and advanced modes with real gateway data,
not only demo fixtures.

The UI can submit a one-box request only if it still chooses a default role:
both `create_job` paths currently require `target_agent_role`, and the composer
hard-codes codex/claude/gemini choices. Parsing intent and choosing a worker is
Idea 4, not a cosmetic part of Idea 8. The 8-12 day estimate therefore keeps a
visible/default assignment disclosure. Automatic intent routing adds the
capability registry, policy constraints, routing receipt, and safety work and
should not be hidden inside this UI estimate.

**Where the estimate is most likely wrong.** The component reuse is real, but
“one feed” changes navigation, information architecture, mobile behavior, and
how every failure is recovered. If product acceptance includes automatic
routing rather than just progressive presentation, this becomes a 25-plus-day
cross-stack effort, not an 8-12 day console change.

### What Idea 1 actually requires - the non-obvious blockers

1. **Identity and secret portability.** Windows Credential Manager cannot
   unlock the copied Linux secret store. Re-enroll secrets on the Pi and define
   who owns `~/.mco`, service environment files, token files, backups, and TLS
   keys.
2. **Cold-boot ownership.** `systemd --user` plus `loginctl enable-linger` can
   work, but an appliance may want a dedicated system service. That decision
   must align with ADR 0002's per-user supervisor boundary rather than quietly
   creating two ownership models.
3. **Durable state is more than SQLite.** Schedule definitions/state, secrets,
   credentials, logs, notification state, and tunnel identity all need backup
   and restore ordering. Copying `local.db` while WAL is active is not a backup
   procedure.
4. **LocalStore's scale contract.** Every filtered query decodes a whole JSON
   table under one process lock. That is acceptable at this fleet's current
   volume on SSD, but retention and a measured latency ceiling are required.
5. **Clock and network are dependencies.** Cron/timezone schedules, lease age,
   presence, TLS, and signed credentials all depend on correct time. The
   appliance needs NTP recovery and tests for router/DNS/tunnel failure.
6. **The watchdog needs an outside witness.** A process on the Pi cannot report
   loss of Pi power or site connectivity. At least one heartbeat receiver must
   live elsewhere.
7. **Pi #2 is not warm today.** There is no replicated LocalStore, leader
   election, writer fence, promotion protocol, or split-brain prevention.
   Start with encrypted snapshots and a rehearsed cold replacement.
8. **ARM is not in CI.** Linux code is tested on x86 runners; add an ARM64 image
   build and real-hardware smoke/soak evidence before calling the Pi supported.

The obvious Windows-path issue is comparatively small: platform dispatch and
home-relative paths already exist. The material Windows-only assumptions are
in deployed worker commands and the secret-key source, not the gateway's core
Python paths.

### Which idea is secretly hardest?

I agree that Idea 4 is the hardest overall. It changes the required job
contract, polling filters, authorization, capability inventory, account and
cost state, policy constraints, reproducibility, and separation of duties.
`max_retries` plus `escalate_to_role` is not half an automatic router; it is a
post-failure rule applied to a job whose target was already chosen.

The most underestimated “low effort” item is Idea 2. A fleet-silence query is a
day; a watchdog that still reaches Joe when the gateway, Pi, power, router, or
ntfy path is the failed component is an availability system with at least two
failure domains. Among the five sized here, Idea 7 is the largest semantic
swamp because “one emitter” is being asked to imply three supportable vendor
integrations.

### Build order while ADR 0002 and PR #47 are in flight

The Pi plan **complements** agentd; it does not supersede it. Agentd owns worker
processes per logged-in user on beast/mac. The Pi owns the gateway, scheduler,
and watchdog as appliance control-plane services. Putting the gateway under
every worker supervisor would couple worker restarts to control-plane uptime
and create competing owners.

PR #47 should not be paused for this brief. Its current head already says it is
rebased and reworked for ADR 0002 revision 2, and CI is green. Review/merge that
Windows supervisor core on its existing worker-only scope. Add the POSIX
adapter and migration mechanics separately; do not expand #47 into Pi
provisioning or HA.

The practical order is:

1. Fix the ntfy topic override and deploy an external dead-man heartbeat.
2. Build/test the fleet-silence incident state machine and honest readiness.
3. Finish review of #47 while those control-plane pieces proceed; it removes
   Windows worker-service ambiguity but is not a Pi prerequisite.
4. Build the single-Pi appliance image/install, SSD snapshot/restore, and
   cold-boot/soak tests.
5. Move the live gateway only after a rollback rehearsal. Keep Pi #2 cold until
   replication and fencing exist.
6. Promote the existing Activity implementation to the primary feed; then add
   OTel and deeper progressive disclosure.

### The honest contact-with-code test

Five claims in the brief do not survive unchanged:

- “The Pi is low effort” survives only for a single-node appliance. It does not
  include safe migration, external detection, backup/restore, or warm standby.
- “The watchdog on the Pi” cannot report the Pi's own death. It needs an
  external witness.
- “The audit trail is the feed” is directionally right and partly implemented,
  but the feed must be a redacted/coalesced projection, not identical bytes.
- “One emitter, three logos, zero bespoke vendor work” is false. One semantic
  emitter reduces duplication; each logo still needs a tested deployment pack.
- “One text box” has no backend contract today. Job creation requires a role;
  without Idea 4's policy-aware router, the box merely hides a hard-coded
  routing decision.

The product thesis survives. The weekend sequence does not. The first shippable
proof should be: an externally observed single-Pi appliance that cold-boots,
alerts when workers or the appliance disappear, restores from a tested backup,
and presents the already-existing event stream as the primary human feed.
