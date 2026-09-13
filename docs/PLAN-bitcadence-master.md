# BitCadence Master Plan

**Status:** v1, for fleet critique and operator decision. Everything in it is arguable; the order is not.
**Authors:** Joe Arroyo, MOSES/Claude. Built on reviewer-beast's critique of `VISION-one-box-one-feed.md`.
**Date:** 2026-09-02

---

## Part I — The thesis in one page

**BitCadence is the governed runtime for the agents you already have.** You type what you want into one box. The fleet decides who does it, stops where your judgment matters, and leaves a record an auditor would sign. Everything else — routing, retries, escalation, machines, tokens — is opened piece by piece, the first time it is needed and never the first time it is possible.

Three lenses, each applied once and honestly:

- **Jobs.** Subtract until one control remains. The wheel, not the storage. The job form has eight fields; a form is what ships before a decision is made.
- **Disney.** The guest never sees the utilidor. Task Scheduler, `fleet.toml`, token files, MCP configs — all of it goes underground.
- **Musk.** Delete the part before optimizing it. But *the physics decide what can be deleted* — and the physics say a lease that is not fenced will run the same job twice.

**What changed since the first draft.** The fleet was asked to attack the vision brief. It found that the current lease protocol accepts writes from a stale worker after a job has been re-leased — two machines do not merely *think* they own a job, the API takes both. That is not a UX problem. It is the floor, and it is not level. **So the order of this plan is the reviewer's, not the founder's:** make the floor level, make silence impossible, then build the beautiful thing on top.

**The market fact that sets the clock.** The EU AI Act's human-oversight and record-keeping obligations became **enforceable in August 2026**. It is September. Every enterprise running agents in or into the EU is, today, either compliant or exposed. BitCadence's feature set — human-in-the-loop gates, immutable audit, vendor-neutral routing — is not a roadmap for that regulation. It is the regulation's checklist, already running. The pitch is no longer "get ready." It is **"you are currently out of compliance, and here is the runtime that fixes it without replacing your agents."**

---

## Part II — The product

### II.1 The surface: one box, one feed

**Level 1 — what a new user sees, and what an operator sees on 95% of days:**

- **The box.** Free text. "Review PR #48." "Summarize overnight errors and tell me if anything needs a human." "Draft the pilot follow-up to Acme and stop before sending."
- **The feed.** A chronological stream of what happened, in prose, with the actor named:

  > `codex-beast` picked up **Review PR #48** · 2m · opened a review · **waiting on you**
  > *Routed here because: mac's Claude is rate-limited until 14:10; local Qwen is not permitted for repository data (policy `prod-code`).*

  That second line is the **routing receipt** — every automatic decision explains itself inline. It is the single feature that makes automatic routing trustworthy to a human and defensible to an auditor, and it costs almost nothing once routing emits it.

- **The gate.** When a job stops for judgment, the feed item becomes the control: Approve / Reject / Change. On the phone, that is a push and two taps.

**Level 2 — appears when it matters, surfaced by events not menus:** retries, escalation paths, cost so far, why a job is slow, which worker is offline.

**Level 3 — opened deliberately:** the Machine tab (services, workers, health divergence, logs), `fleet.toml`, routing weights, account budgets, connections, raw audit, the ledger.

**The rule:** a control appears the first time it is needed, not the first time it is possible.

### II.2 Intent first, constraints visible

The reviewer's correction to "delete roles" is adopted in full. **Automatic routing is the default; worker identity stays in the model.** A human must be able to pin or constrain when only one machine holds the repo, secret, license, or device; when policy limits which model may see which data; when a reviewer must be independent of the author; when an incident needs a worker drained; when a result must reproduce on the same model and host; when a hard cost ceiling matters more than speed.

So the box takes intent. Progressive disclosure offers **role, instance, trust tier, locality, account, cost ceiling** as visible constraints. Policy can require any of them. And every automatic choice emits a routing receipt into both the feed and the ledger. Hiding the knobs is good UX; hiding who acted is bad operations and bad audit.

### II.3 Three editions, one codebase

The reviewer's finding that LocalStore "cannot be the fleet-scale heart" becomes a product ladder rather than a limitation.

| | **Appliance** | **Team** | **Enterprise** |
|---|---|---|---|
| Who | one operator, one home/lab | a squad or a pilot customer | regulated org, multiple teams |
| Store | LocalStore (SQLite, embedded) | Postgres | replicated Postgres, RLS |
| Tenancy | single, declared | single | multi-tenant with per-tenant keys |
| Coordinator | one always-on node + external dead-man | replaceable gateways | replaceable gateways, leader-owned scheduler |
| Identity | local token files | machine enrollment, short-lived creds | mTLS, SSO/OIDC, SCIM, rotation, revocation |
| Telemetry | local logs, ntfy | OTel emitter | OTel emitter + tested vendor packs |
| Audit | hash chain, optional HMAC | hash chain, HMAC required | HMAC required, evidence store, retention/legal hold |
| Price | free, MIT | pilot: $1.5–3k/mo | license / per-seat / corp-dev |
| Drumline | **first-class** | **first-class** | **first-class** |

Drumline is in every edition. Collective memory is the product, not an upsell — that is already policy, and this plan does not touch it.

**Joe's house is the Appliance edition's reference deployment.** Pilots get Team. Enterprise is what the corp-dev conversations are about.

---

## Part III — The technical program

Seven workstreams, in the reviewer's order. Each has a one-line acceptance test, because a workstream without one is a wish.

### WS1 — Fenced leases and idempotent completion *(blocker; everything routes through this)*

**Today:** `LocalStore._lease_task()` and the Postgres `lease_task` RPC make the initial claim atomic. `reclaim_stale_leases()` uses a fixed 15-minute TTL with no renewal, and only runs when another worker polls. `handle_job_update()` updates by job ID alone — it does not check `leased_by_instance_id`, the current state, or any epoch. A partitioned worker finishing a 20-minute task can complete a job that was re-leased to someone else, and the API accepts it.

**Build:** renewable leases with an unguessable lease ID and a monotonic **fencing epoch**. Completion is compare-and-set on (job, state, owner, epoch). Long jobs renew; expired workers are fenced even if they return. External side effects carry an idempotency key from (job, attempt). A written partition policy: what continues disconnected, what stops, what reconciles.

**Accept when:** a test partitions worker A mid-job, re-leases to B, then lets A return — and A's completion is rejected with the epoch mismatch in the audit trail.

### WS2 — Honest readiness and the external dead-man

**Today:** `/healthz` returns 200 if the process has a database object and the kill switch is off. It does not check the database, the scheduler, the notification path, or whether any worker is alive. It returned 200 for 24 hours over a dead fleet. Lease recovery is opportunistic on worker polling, so a fleet with no workers has no reaper.

**Build:** `/readyz` that queries the store, the scheduler, and a worker-quorum threshold, and fails honestly. A lease reaper that runs on a timer, not on polling. And a **dead-man in a different failure domain** — a colocated watchdog cannot report its own host's death. Concretely: a Cloudflare Worker (already in the account, free tier, separate infrastructure) that expects a heartbeat from the gateway every 60 seconds and pushes to the phone when two are missed. The Pi cannot see itself die; Cloudflare can.

**Accept when:** pulling the coordinator's power produces a phone notification inside 3 minutes, with no BitCadence process involved in sending it.

### WS3 — The coordinator: appliance or replicated, decided by RPO/RTO

**Today:** the gateway runs as a foreground `mco serve` on a Windows desktop that sleeps. The reviewer is right that moving it to a Pi *moves* the single point of failure rather than removing it, and that a second Pi is not a "warm standby" without replication, election, fencing, and a rehearsed promotion.

**Build, Appliance profile:** one Pi 5, **USB SSD not SD card**, on a UPS, `systemd --user` with `Restart=always`, nightly consistent SQLite snapshot to the second Pi, a *rehearsed* restore, and the WS2 dead-man. The second Pi is a **cold, tested replacement** — call it warm only when it has earned the word.

**Build, Team/Enterprise profile:** state in Postgres (replicated for Enterprise), gateways stateless and replaceable, scheduler leadership held by a lease in the database using the WS1 fencing machinery.

**Accept when (Appliance):** destroy Pi #1, restore on Pi #2 from last night's snapshot, fleet reconnects, and the audit chain verifies with no gap larger than the snapshot interval. Written RPO/RTO on the wall.

### WS4 — Cost ledger, tenant boundaries, machine identity

**Today:** nothing records provider, model, tokens, price, or budget owner. `max_retries` limits attempts, not dollars. `/metrics` counts jobs, not spend. Schedules and loops can multiply a blind spot into a runaway bill. `org_id` exists in the schema but tenancy is enforced in route handlers against a high-privilege database credential, with no row-level security. The shared vault uses one master key for every tenant. Worker tokens are files under `~/.mco/tokens` with no provisioning, rotation, or revocation across five machines.

**Build:**
- **Usage ledger** with a three-phase budget contract: *estimate and reserve* before lease, *meter* during, *settle* after. Hard limits per job, workflow, account, user, tenant; concurrency caps; daily/monthly ceilings; an emergency spend breaker that does not depend on the scheduler being healthy.
- **Accounts as a routable resource.** The mac's separate ChatGPT and Antigravity logins are separate rate limits — real capacity the scheduler cannot currently see. Model the account with its own budget and cooldown so "beast's Claude is limited, mac's is fresh" is a routing decision, not a failed job.
- **Tenancy:** row-level security in Postgres, per-tenant vault keys with demonstrated rotation, LocalStore declared single-tenant.
- **Machine identity:** enrollment, short-lived scoped credentials, mTLS or equivalent between workers and gateway, revocation that actually reaches the worker.

**Accept when:** a loop with a $5 ceiling stops at $5 with the breaker's reason in the feed; a tenant-A token cannot read a tenant-B row *at the database*, not just at the route.

### WS5 — Intent-first routing with receipts

Only now. Weighted routing over liveness, account budget, cost ceiling, trust tier, and locality — with every decision emitting a routing receipt (chosen worker/model/account, constraints applied, alternatives rejected, estimated cost, reason). The routing is **inspectable because it is dumb**: a weight is a number a human set, sitting in the ledger next to the gate that constrained it. Every escalation writes a labelled outcome, which makes the weights tunable from your own data — a black-box router has no trustworthy record of its own misses.

**Accept when:** an auditor can reconstruct why any job went where it went from the ledger alone.

### WS6 — Canonical event ledger, separate feed projection

The reviewer's correction is adopted: **share events, not bytes.** An auditor needs completeness, exact actors, durable order, integrity proof, restricted evidence, retention. A human needs a few current, comprehensible, deduplicated, redacted, permission-filtered items. The concrete failure already exists in the code: `handle_job_update()` puts the full `error_message` in the audit detail, so a credential-bearing URL in a failure would either leak into the feed or be redacted out of the evidence.

**Build:** an immutable canonical domain-event ledger with atomic state-change-plus-event writes and database-level serialization (today `record_event()` swallows failures and the chain lock is process-local, so a second gateway can fork it). HMAC required above Appliance. Sensitive evidence behind a stricter boundary, referenced by digest. Then a **feed projection** that summarizes, coalesces ("failed after 20 attempts"), redacts, localizes, and may evolve.

**Accept when:** the feed shows one redacted line for a 20-failure job while the ledger holds all 20 exact transitions, and the chain verifies.

### WS7 — OTel semantic layer, then one vendor pack at a time

The reviewer is right that OTel makes export *portable*, not the integration *free*. Datadog wants its Collector distribution and connector; Dynatrace wants scoped tokens, HTTP/protobuf, and topology enrichment; Grafana wants Alloy plus dashboards and alerts. A day-long job is a poor fit for one open trace. OTLP permits duplicates, which disqualifies it as the audit source of truth.

**Build:** one **semantic instrumentation layer** — each execution attempt is a span, state changes are correlated events, queue depth / lease age / fleet liveness / tokens / spend are metrics, all keyed by job ID, with a redaction policy for prompts, outputs, and Drumline attributes. Then a **vendor pack** per logo: tested collector config, attribute mapping, dashboards, alerts, runbook. **Dynatrace first** — it is next in the corp-dev sequence and the pack *is* the partner-team demo. The hash-chained ledger stays authoritative; OTel is the window, never the record.

**Accept when:** a pilot's SRE sees a BitCadence approval gate as an event on the same Dynatrace timeline as their microservices, and the fleet's spend as a metric they can alert on.

### Already in flight and how it fits

- **ADR 0002 (agentd, one supervisor per user session, PRs #46/#47)** complements WS2–WS3: it is the worker-side supervision the coordinator needs, and it inherits the WS1 fencing for per-worker locks. Rework continues; it does not wait.
- **Console cancel/reassign** (shipped) is the first Level-2 control.
- **`pythonw` fix** (shipped) is the first utilidor going underground.

---

## Part IV — Joe's fleet, assigned by physics and corrected by evidence

| Machine | Assignment | Reasoning | Status |
|---|---|---|---|
| **Pi 5 #1** | Appliance coordinator: gateway, scheduler, lease reaper | never sleeps; watts, not hundreds | needs USB SSD, UPS, WS2/WS3 |
| **Pi 5 #2** | cold, rehearsed replacement + snapshot target | "warm" is earned by a drill, not declared | after WS3 restore drill |
| **Cloudflare Worker** | dead-man monitor | different failure domain; already paid for | WS2 |
| **beast** | heavy work: Qwen 3.8 (unsloth), ComfyUI, MiniMax, frontier CLIs | GPUs and RAM | today |
| **mac mini** | second capacity pool with **separate accounts** | parallel rate limits; second OS for cross-platform proof | today; accounts modeled in WS4 |
| **Jetson** | **optional** inference worker, advisory only | no contract, no benchmark; a slide until measured | benchmark before any critical-path role |
| **$5,100 credits** | hosted **Team-edition demo** a prospect can log into in 60 seconds, seeded with a working fleet | turns the 20-minute walkthrough into self-serve; a Pi cannot do this | after WS3 |

**The Jetson honestly.** Which model, quantization, tokens/second, idle and load watts, quality threshold, fallback? Until those are numbers, it runs advisory classification with recorded confidence and an easy override — never the sole path between submission and execution.

**Why the fleet died and what stops it:** the coordinator lived on a desktop; presence had no consumer; readiness lied. WS2 and WS3 are the direct answer. Until they land, the operational rule is simple: **beast does not sleep while the gateway runs on it.**

---

## Part V — Enterprise

**The buyer.** A platform or SRE lead at a regulated company who has agents in production, a compliance deadline that has already passed, and no appetite to replace the agents. They want the record, the gate, and the ability to say "no" mid-flight — in the tools they already have.

**What they are buying.** Not orchestration. *Custody and proof.* The gate that stops the agent before it acts; the ledger that shows who approved what; the routing receipt that explains why the local model was not allowed to see production data; the spend breaker that means the CFO sleeps.

**Integration surface, in order:**

1. **ServiceNow** — already integrated; the incident-to-governed-job path is the existing demo.
2. **Dynatrace** — WS7's first vendor pack; the corp-dev conversation and the product are the same artifact.
3. **Datadog, Grafana** — subsequent packs on the same semantic layer.
4. **PagerDuty, Atlassian, GitLab** — escalation and work-tracking seams.

**Compliance posture, stated plainly:** BitCadence provides human-in-the-loop enforcement at runtime, an immutable HMAC-signed record of every automated decision, per-tenant key custody, and exportable evidence. It does not certify anyone; it makes certification survivable.

**What we do not claim:** SOC 2 (not yet), FedRAMP (not in scope), that OTel alone is an integration (it is not), that the Appliance edition is multi-tenant (it is declared single-tenant).

---

## Part VI — The business

Grounded in decisions already on record, not invented here.

**Stance.** Joe is not leaving the day job. Every funding path is filtered by "does this require going full-time?" Priced rounds, YC, accelerators: deferred. Credits, open-source support, non-dilutive grants, no-equity advisory: pursued. The goal is to get BitCadence into someone's hands — license, acquisition, or an operating partner who runs it while Joe steps back — not a lonely grind to a flip.

**This plan's business consequence:** *if Joe is to be hands-off eventually, the product must be operable by someone who is not Joe.* One box, one feed, progressive disclosure is therefore not a taste — it is the precondition for delegation, for an operating partner, and for an acquirer's diligence. Simplicity is the exit strategy expressed as a UI.

**Revenue, now.** Paid design-partner pilots, $1.5–3k/month, 3-month minimum, white-glove setup. Every pilot is Team edition on the hosted demo path funded by the credits. **The EU AI Act being live is the opener** for every conversation from this week on.

**Corp-dev.** ServiceNow → Dynatrace → Atlassian → PagerDuty → Datadog → GitLab. The Dynatrace vendor pack is the first tangible thing to put in front of a partner team. The reviewer's critique is itself an asset in those rooms: "we found our own lease protocol was not partition-safe, here is the fencing design" is what engineering-led acquirers want to hear.

**Operating partner.** The Swedish co-founder candidate runs Europe first — where the AI Act makes the pitch loudest — with real vesting equity. The product's simplicity is what makes that handoff possible.

**Open source.** MIT core stays. The trademark and the Enterprise edition are what is sold. Drumline stays in every edition.

**Credits.** NVIDIA Inception reopens January 2027. Google for Startups is re-applyable now with the LLC and EIN in place. SAM.gov finishes when the business bank account does. NSF STTR, not SBIR, if the PI conflict resolves that way.

---

## Part VII — MOSES

Two systems, one narrow bridge, never merged.

- **MOSES + Genesis: identity memory.** Who Joe is, what he wants, his family, his faith, the single cross-surface conversation. Confirmed facts only.
- **BitCadence + Drumline: work memory.** Making it happen, proving it happened, what every agent learned doing it.

MOSES decides **what**. BitCadence governs **how** and records **what actually occurred**. The bridge is exactly two operations: MOSES posts a job; MOSES reads the feed. A merged system would be bad at both — a personal AI that logs to an audit ledger is creepy; a governance runtime that knows your sons' names is a liability.

---

## Part VIII — Sequencing

**Next 30 days — make the floor level.**
WS1 fenced leases. WS2 honest readiness + Cloudflare dead-man + timer-driven reaper. Pi #1 on SSD and UPS running the gateway. Rework #46/#47 against ADR 0002 rev 2. Fix the ntfy public-topic leak (prerequisite for any push). **Beast does not sleep until the Pi takes over.**

**Days 30–90 — make it sellable.**
WS3 restore drill, written RPO/RTO. WS4 usage ledger and spend breaker; accounts as a resource. Team edition on Postgres. Hosted demo on credits. First two pilots opened with the AI-Act opener. Reapply Google for Startups.

**Days 90–180 — make it trustworthy at a distance.**
WS5 routing with receipts. WS6 ledger/feed split, HMAC required above Appliance. Machine identity and per-tenant keys. Dynatrace vendor pack and the first partner-team meeting. PWA with push for gates only.

**Days 180–365 — make it someone else's.**
Datadog and Grafana packs. Operating partner in seat for Europe. Enterprise edition diligence-ready: RLS proven at the database, rotation demonstrated, evidence store, retention. Corp-dev conversations that have a product behind them.

---

## Part IX — What kills this

- **Building the beautiful thing first.** The feed before the fence means a gorgeous UI over a runtime that double-executes. The order is the whole plan.
- **"Warm standby" that has never been promoted.** A second Pi that has not been restored to is a paperweight with a hopeful name.
- **Runaway spend from a loop.** Until WS4, a scheduled loop calling a frontier model is an unmetered liability. Cap it manually today.
- **Selling Appliance as Enterprise.** LocalStore is single-tenant, full-scan, one-lock. It is a superb zero-dependency edition and a terrible fleet database. Say which one a customer is getting.
- **Founder as single point of failure.** The same finding as the Pi, one level up. The plan's answer is the same: simplicity, documentation, an operating partner, and drills.
- **Silence.** The fleet was dead for a day and nothing spoke. Until WS2 lands, assume every quiet hour is an outage.

---

## Part X — Decisions needed from Joe

1. **RPO / RTO for the Appliance edition.** How much can be lost, how long can it be down. WS3 cannot choose hardware without these two numbers.
2. **Is `grok-beast` and `opencode-beast` `mode = "off"` deliberate?** Both are off in `fleet.toml`; jobs addressed to grok will never run as configured.
3. **USB SSD and UPS for Pi #1** — a small purchase that WS3 depends on.
4. **Which pilot gets the AI-Act opener first.** The sequence in Part VI assumes the existing pipeline; name the first two.
5. **Approver scope for the MOSES/Claude board identity.** Today it cannot cancel or reassign its own mistaken jobs. Either grant `jobs:approve` to that identity or keep the CLI as the only path — but decide.
6. **Bless the order.** The reviewer's sequence replaces the founder's. If that is wrong, say where.

---

## Questions for the fleet

Attack Parts III and VIII especially:

1. Is WS1's fencing design sufficient, or does it need a lease *lease* — a leader that owns the reaper — before it is safe under a gateway partition too?
2. Is the Cloudflare dead-man genuinely a different failure domain from a home network, or does it share the router?
3. Is 30 days for WS1 + WS2 + Pi cutover honest for one person building before the house wakes up?
4. What in Part V would a Dynatrace partner engineer laugh at?
5. What did this plan still miss?

---

## Critique v1 - reviewer-beast

**Verdict:** the product sequence is much better than the vision brief, but the plan still overclaims the market deadline, understates the lease protocol, and promises a stronger audit guarantee than the Appliance recovery design can preserve. The findings below are ranked by how much they should change the plan.

### 1. CRITICAL — The market clock is wrong as of this plan's date

Part I and Part VI say the EU AI Act's human-oversight and record-keeping obligations became enforceable in August 2026, and build the sales opener around "you are currently out of compliance." That is no longer a defensible general claim. The EU's AI Omnibus entered into force on 27 July 2026 and moved the high-risk-system rules to **2 December 2027 for Annex III systems** and **2 August 2028 for high-risk systems embedded in Annex I products**. August 2026 remains material for Article 50 transparency obligations and for enforcement of provisions already applicable, but it is not the blanket deadline this plan describes. See the European Commission's current [AI Act timeline](https://digital-strategy.ec.europa.eu/en/policies/regulatory-framework-ai) and [AI Omnibus notice](https://digital-strategy.ec.europa.eu/en/news/ai-omnibus-enters-force).

**Required plan change:** replace the breach-alarm opener with a classification-first claim: "some duties are live; high-risk governance deadlines are approaching; BitCadence builds the operational evidence now." A prospect's use case, role (provider/deployer), risk classification, and applicable article must be established before anyone says it is out of compliance. This weakens the artificial September emergency but strengthens credibility with enterprise legal and compliance teams.

### 2. CRITICAL — WS1 needs database-serialized reaping, not necessarily a reaper leader

The current code confirms the underlying finding and makes it broader:

- `LocalStore._lease_task()` atomically changes only `pending -> leased`, recording owner and `started_at`; there is no lease ID, epoch, expiry, or renewal.
- `routes.reclaim_stale_leases()` does a full read of leased jobs and later updates each expired job **by ID only**. Two gateways can both decide that the same row expired, both requeue it, and both write `lease_expired` events.
- `handlers.handle_job_update()` updates **by job ID only**. The HTTP route verifies that the job is addressed to the caller's role or instance, not that the caller owns the current attempt. The WebSocket handler is a second mutation path with the same unfenced update.

Compare-and-set on `(job, state, owner, epoch)` is sufficient to serialize a completion only inside one uninterrupted authoritative database history. It is not the complete WS1 contract. Every acquire, renew, expire, retry, cancel, and terminal write must carry and condition on `(job_id, lease_id, lease_epoch, owner, allowed_state)`, use database time, and return whether it won. In particular, expiration must be one statement equivalent to `UPDATE ... WHERE lease_epoch = expected AND lease_expires_at <= database_now()`. Only the winner may advance the attempt/epoch and emit the requeue event.

If reaping is implemented that way, **multiple reapers are safe and a lease leader is not needed for job correctness**; extra reapers merely do redundant scans. A database-held leader lease is still needed before multiple scheduler processes can emit schedules exactly once, and it reduces reaper load, but that is a separate WS3 concern. WS1 may therefore ship before WS3 for the single-gateway Appliance profile. Team/Enterprise may not advertise replaceable active gateways until the reaper CAS exists; if WS1 leaves the current read-then-update reaper, it must wait.

The plan's unguessable `lease_id` must also appear in the CAS, not merely in prose. A restored nightly snapshot can roll an integer epoch backward; a new post-restore attempt can then reuse an old epoch (the ABA problem). A fresh random lease ID or a store-incarnation term prevents a pre-crash worker from matching after restore. The partition test must cover that restore case as well as ordinary re-lease.

**Required acceptance tests:** run the stale-worker test through both HTTP and WebSocket paths; race two reapers and prove exactly one requeue/attempt increment/event; race cancel against completion; reject renewal after expiry; restore an old database snapshot and prove a pre-restore lease cannot write. Also state that split-brain writable databases are outside WS1: application fencing cannot repair a non-linearizable store.

### 3. CRITICAL — Appliance audit is tamper-evident with bounded loss, not an immutable complete ledger

A nightly snapshot can lose a day's suffix. The surviving rows may still be immutable and their hashes may still verify, but the record is not complete. Worse, the current per-job chain cannot detect deletion of its tail: `verify_chain()` starts at genesis and validates only the rows that remain, with no external signed head, event count, or global high-water mark to prove later rows once existed. The acceptance test "chain verifies with no gap larger than the snapshot interval" is therefore not testable from the restored snapshot alone; a missing suffix looks like a valid shorter chain.

**Required plan change:** choose one of two honest Appliance contracts.

1. Call it a **tamper-evident retained audit history with a stated backup RPO** (for example, up to 24 hours of events may be lost after total storage failure). Remove claims that every decision is durably retained.
2. Preserve the stronger claim by forwarding every committed event or signed chain-head checkpoint to an independent append-only/off-site sink before acknowledging the governed transition. Then rehearse loss of the entire house site, not just Pi #1.

HMAC does not add durability, and an HMAC key stored and backed up beside the database does not provide independent anchoring. A second Pi in the same house is a hardware-replacement target, not disaster recovery for fire, theft, power damage, or operator error. Encrypted off-site backup, key recovery, restore authorization, and a measured RPO/RTO belong in WS3.

### 4. HIGH — Thirty days is not an honest commitment for the listed scope

The 30-day window contains at least five release-sized changes: a cross-backend lease protocol and migration; renewal changes in every worker/client path; concurrent partition tests; honest readiness plus scheduler/worker health; an external dead-man and secure notification route; a Pi service/network/storage migration; ADR 0002 rework; and the ntfy leak. `service.py` also shows that gateway and scheduler are independent OS services, while `fleet.py` manages worker services separately. `/readyz` cannot infer scheduler health merely because the gateway process or database object exists; the scheduler needs a durable leadership/heartbeat signal that the gateway can query.

At a sustainable **7–10 focused hours per week**, this is roughly **90–120 hours, or 12–16 calendar weeks including deployment and failure drills**. The honest committed number is 90 days, with 120 days as the outside bound—not 30.

**Keep in the first 30 days:** the written WS1 protocol; schema changes for LocalStore and Postgres; fenced acquire/renew/expire/update on both HTTP and WebSocket; adversarial concurrency tests; the liveness/readiness split; the ntfy privacy fix; and a managed external dead-man if one can be configured rather than built.

**Move out of the first 30 days:** custom Cloudflare dead-man code, production Pi cutover, backup/restore drill, and remaining #46/#47 rework. Pi cutover should follow fencing and a rehearsed rollback, not run beside their development. If keeping the Pi in 30 days is non-negotiable, WS1 is the only other engineering commitment; everything else falls out.

### 5. HIGH — WS2 names the right failure domain but underspecifies the monitor

A Cloudflare Worker genuinely is outside the home's router, ISP, and power domain. The sentence "does it share the router?" has a simple answer: **no**. But merely receiving a heartbeat at a Worker is not a dead-man. Cloud-side durable state plus a cloud-side alarm/cron must notice that invocations stopped, and the alert delivery must not depend on BitCadence or the home network.

The minimum whole-site-dark detector is:

- an independently hosted timer/monitor;
- a freshness assertion from the site (authenticated heartbeat) or an external pull of a meaningful endpoint;
- durable last-seen/deadline state with duplicate-alert suppression and recovery notification;
- a second-provider notification path to a phone expected to have cellular service; and
- a scheduled end-to-end test that deliberately stops heartbeats and proves delivery.

The phone should be the receiver, **not the checker**. Phone background execution, battery state, OS suspension, and home Wi-Fi make it a poor monitor. Also distinguish the probes: `/healthz` should answer only "is this process alive?"; `/readyz` should answer "can this gateway safely accept work?"; fleet capability and scheduler leadership should be separately exposed as degraded operational health. Making readiness fail solely because no worker quorum exists can cause a load balancer to remove the control plane precisely when the operator needs it to approve, cancel, or repair work. Readiness should evaluate required capabilities for the deployment, not count generic workers.

### 6. HIGH — A Dynatrace partner will laugh at the word "timeline" without a deployable contract

The existing `DynatraceConnector` is a Problems API v2 poll/action adapter: it lists open problems and can comment or close them. It emits no OpenTelemetry data. The current `/metrics` endpoint is a hand-built Prometheus snapshot containing job counts, approval depth, and agent count; it has no spend, lease age, attempt latency, correlation model, traces, or Dynatrace topology. Calling the future result "an approval gate as an event on the same Dynatrace timeline as their microservices" skips every hard integration decision.

A partner engineer will ask, specifically:

- Is the supported target Dynatrace SaaS with Grail, ActiveGate, or Managed, and which versions are tested?
- Is data sent directly or through the supported Dynatrace OTel Collector? Dynatrace's OTLP ingest requires HTTP/protobuf, separate signal paths/scopes, and deliberate cumulative-to-delta handling for metrics. See the current [OTLP endpoint contract](https://docs.dynatrace.com/docs/ingest-from/opentelemetry/otlp-api) and [Collector configuration](https://docs.dynatrace.com/docs/ingest-from/opentelemetry/collector/configuration).
- Which standard attributes are used (`service.name`, `service.instance.id`, GenAI conventions), which BitCadence attributes are namespaced, and how are `job_id`, workflow/run/attempt, tenant, environment, and downstream `traceparent` correlated? A shared timestamp is not causality.
- Is an approval transition a span event, log, custom event, or business event? A gate that waits hours should not keep one span open. Attempts can be spans; durable state changes should be separately queryable records linked by stable IDs.
- What are the sampling, retry, queue, backpressure, cardinality, retention, and Dynatrace Platform Subscription cost budgets? `job_id` is useful on spans/logs but disastrous as an unbounded metric dimension unless the queries and cost are designed.
- Where are secrets and prompts removed before export? What `dt.security_context`, Grail bucket/OpenPipeline routing, record permissions, and field restrictions enforce tenant boundaries? Dynatrace documents record-level and field-level controls in its [Grail permission model](https://docs.dynatrace.com/docs/platform/grail/organize-data/assign-permissions-in-grail).
- Where are the versioned Collector config, DQL dashboard/notebook, Davis alert or Workflow definitions, synthetic demo data, support matrix, and a test against a real tenant?

**Required plan change:** make the vendor pack acceptance test installable and measurable: a clean Dynatrace tenant receives a seeded BitCadence run through a versioned Collector config; DQL reconstructs job -> attempt -> downstream service; a lease-age or spend threshold raises the supplied alert; tenant-scoped users cannot query another tenant's telemetry; redaction tests prove prompts/secrets never arrive; and the runbook repeats the installation from zero. Until that exists, Part V has a connector plus an integration aspiration, not a Dynatrace product.

### 7. HIGH — Correctness and evidence are scheduled too far apart

WS6 says state change plus event will become atomic later, while WS1's acceptance test already requires an epoch-mismatch rejection "in the audit trail." Today `record_event()` is a separate best-effort write that deliberately swallows failures, and its chain lock is process-local. Lease acquisition, job mutation, retry, dependent unlock, and audit append are separate operations. A crash can therefore commit the business state without its evidence, or evidence without a related later action.

**Required plan change:** pull the minimal transactional outbox/domain-event write into WS1. WS6 may still build the canonical schema, evidence boundary, and feed projection later, but the state-and-evidence atomicity invariant cannot wait if the ledger is used to prove fencing. Rejected stale writes need an append path that records expected/current lease metadata without mutating the job and without accepting worker-supplied actor identity.

### 8. MEDIUM — Remaining omissions that need named owners and tests

- **External side effects:** `(job, attempt)` is only an idempotency key if ServiceNow, Dynatrace, email, and every future connector durably honor it. For APIs without native idempotency, define an outbox, reconciliation policy, and "unknown outcome" state. Fencing a database completion does not unsend an email or unclose a problem.
- **State-machine authority:** enumerate legal transitions and enforce them in the database/RPC. Current handlers accept an arbitrary status string and perform secondary retry/unlock work after the update. Fencing only `completed` leaves stale progress, failure, and retry paths open.
- **Snapshot consistency:** specify SQLite backup API/`VACUUM INTO` or an equivalent WAL-aware method, encryption, checksums, retention, and restore of secrets/configuration as well as `local.db`. Copying the main file while WAL is active is not a backup design.
- **Pi boot semantics:** `systemd --user` needs lingering or a real login session; prove cold boot after power loss, DNS/tunnel recovery, clock synchronization, disk-full behavior, UPS signaling, log rotation, and unattended security updates.
- **Failure budgets:** define SLOs and error budgets for lease acquisition, renewal, notification latency, data loss, and restore time. "Inside three minutes" is one target, not an operability model.
- **Edition promotion:** define the supported migration from Appliance SQLite to Team Postgres, including event IDs/order, chain verification, secrets, rollback, and compatibility. "One codebase" is not yet a migration path.

### Revised sequencing recommendation

**Days 0–30:** correct the regulatory language; freeze the lease/state-machine protocol; implement fenced acquire/renew/expire/all worker updates with lease ID + epoch on both backends and both transports; move minimal atomic evidence into WS1; fix ntfy privacy; split liveness/readiness/degraded health; configure the smallest external dead-man.

**Days 31–90:** deploy and harden Pi #1; implement off-site, WAL-consistent encrypted backup; add the restore-incarnation fence; rehearse complete-site restore; finish ADR 0002 rework; publish measured RPO/RTO. Do not call the audit complete or immutable beyond that RPO.

**Days 91–180:** Team Postgres, cost ledger/breaker, account capacity, database-enforced tenancy, and a hosted demo. Start design-partner discovery now, but do not make a legally categorical AI-Act accusation.

**After the semantic/event model is stable:** build the Dynatrace pack against a real tenant, with installable artifacts and DQL proof. Do not put its acceptance date in the same window as the foundational ledger redesign.

---

## Findings from building the EPCOT demo (2026-09-03)

Building `docs/DEMO-epcot.md` and `infra/aws/` against the real code surfaced two gaps
that belong in WS1's scope, not in a later workstream:

1. **The kill switch is not audited.** `put_settings()` persists `MCO_KILL_SWITCH` via
   `config.set()` and never calls `record_event()`. The single most consequential human
   decision the product supports - halt everything - leaves no ledger row. WS1 must add a
   system-level audit event for every settings change (actor, key, old, new), and the
   kill switch must additionally emit one per leased job it halts.
2. **The kill switch does not halt in-flight work, and workers cannot be told to stop.**
   `kill_switch_active()` guards intake and leasing only; the worker loop has no
   interruption point that the gateway can reach. The fix is the same fencing machinery
   as WS1: on activation, transition every `leased` job to `halted`, and reject a stale
   worker's completion with the fenced CAS. Pavilion P1(c) stays red until then.
3. **The SDK drops a completion that fails during a network partition.** `process_job()`
   catches the exception and tries `fail()`, which also fails; the result is lost, not
   late. This means a partitioned worker's work is silently discarded today, and it also
   means the "stale writer returns" race cannot be reproduced through the SDK - the demo
   replays the stale completion directly with the worker's token instead. WS1 should add
   a bounded completion retry with the lease ID attached, so late completions are
   *fenced* rather than *lost*.

The demo's honesty board (`DEMO-epcot.md` section 6) is the live tracker for when these
turn green.

---

## 90-day sizing - codex

This sizing is from the implementation on `main` at `c0b1026`, not from the workstream
headings alone. “Day” means one focused engineer-day with the repository and test
environment available. It excludes waiting for hardware delivery, app-store/provider
review, and unattended soak time. The estimates include design, migrations, tests,
documentation, and one deployment drill; they do not include the later WS5-WS7 product
work that depends on these foundations.

The first planning correction is arithmetic: all four complete workstreams are not a
90-day one-person commitment. The credible first-90-day cut is WS1, WS2, the Appliance
slice of WS3, and the cost/account slice of WS4. Database tenancy, per-tenant vault keys,
and machine enrollment are a subsequent security program, not small tails on a cost
ledger.

### WS1 - fenced leases and idempotent completion

**What already exists and should be reused**

- `LocalStore._lease_task()` is an atomic `pending -> leased` compare-and-set under the
  embedded store lock. `_RpcCall.execute()` already gives LocalStore the same RPC-shaped
  seam as PostgREST.
- The documented Postgres `lease_task(p_agent_instance_id, p_task_id)` function performs
  the equivalent conditional `UPDATE` using database time. It is currently in
  `docs/SETUP.md`, not a versioned `src/mco/migrations` migration; WS1 must fix that
  deployment gap rather than assuming the function exists everywhere.
- `routes.lease_job()` supplies the authenticated instance, checks org and dropbox
  addressing, calls the RPC, and records/broadcasts the lease. `handlers.handle_job_lease()`
  is the equivalent handler seam.
- `routes.get_lease_ttl_seconds()` and `routes.reclaim_stale_leases()` provide the current
  policy/configuration hook and recovery call site, even though the implementation is an
  unsafe read-then-update scan.
- `routes.update_job_status()` and `handlers.handle_job_update()` are the central REST
  mutation path for progress, completion, failure, retry, dependent unlock, audit, and
  Drumline distillation.
- `GatewayClient.lease()`, `complete()`, and `fail()`, `BitCadenceAgent.process_job()`, and
  `AgentListener._process_single_job()` are the worker-facing paths that must carry and
  renew the lease credential.
- `audit.record_event()` and the existing append-only LocalStore/Postgres event surfaces
  are useful output contracts, but are not yet transactionally coupled to job mutation.

**Where the fence belongs**

Put the authoritative current fence on the `agent_jobs` row: `lease_id` (random,
unguessable), `lease_epoch` (monotonic for that job within one database history),
`lease_expires_at` (database clock), and the existing owner. Do **not** put the current
owner only in a separate lease table: every acquire, renew, expire, cancel, and terminal
transition would then need a cross-table transaction merely to decide who owns the job.
That is harder to express through PostgREST and easier to get partially right in
LocalStore.

An append-only `job_attempts`/domain-event row is still desirable for history, usage,
and reconciliation, but it is evidence *about* the current fence, not the authority for
it. Both representations are acceptable only if the job row is the single source of
truth and the attempt/event append is committed in the same transaction. `lease_id` is
required even with an epoch: restoring an old snapshot can reuse an integer epoch, while
a newly random lease ID prevents the pre-restore worker from matching the new attempt.

Change the Postgres `lease_task` RPC from a boolean into an atomic credential-returning
operation. Its practical contract should be equivalent to:

```text
lease_task(p_agent_instance_id, p_task_id, p_ttl_seconds)
  UPDATE agent_jobs
     SET status = 'leased',
         leased_by_instance_id = p_agent_instance_id,
         lease_id = fresh_random_uuid(),
         lease_epoch = lease_epoch + 1,
         lease_expires_at = database_now() + ttl,
         started_at = coalesce(started_at, database_now())
   WHERE id = p_task_id
     AND status = 'pending'
     AND leased_by_instance_id IS NULL
  RETURNING id, lease_id, lease_epoch, lease_expires_at
```

The RPC should clamp `p_ttl_seconds` to a server policy rather than trust an arbitrary
worker duration. A losing caller returns no row, not a row with `success=false`. The
returned lease ID and epoch are opaque credentials the client must echo on every mutation.
Add sibling atomic operations for `renew_lease`, `expire_lease`, and
`update_leased_job`; each conditions on job ID, current state, owner, lease ID, epoch,
and expiry as appropriate. Reaping must be one database statement using database time,
not a Python scan followed by an ID-only update.

The current WebSocket `job_update` path in `cli.create_app()` is not a second database
mutation path: it merely rebroadcasts a caller-supplied status. It must either be removed
as a worker write surface or load and validate the same lease credential before emitting
an authoritative-looking event. REST remains the persistence path today.

**Concrete work**

1. Freeze the legal state machine and lease/partition policy, including cancel, kill
   switch, retry, expiry, unknown external-side-effect outcome, and restore semantics.
2. Add a versioned schema migration and backfill/default policy for the lease fields;
   move the canonical Postgres lease RPC out of setup prose and into migrations.
3. Implement the four atomic operations for both LocalStore and Postgres/PostgREST, with
   mutation plus minimal domain-event/outbox append in one transaction.
4. Change REST contracts, `GatewayClient`, `BitCadenceAgent`, and `AgentListener` to retain
   the returned lease credential, renew long work, retry terminal delivery boundedly,
   and interpret stale writes as `409 Conflict` rather than a generic failure.
5. Fence every worker-controlled mutation, not just `completed`: `in_progress`, progress,
   failure, and terminal output. Make retry, cancel, kill-switch halt, and expiry win by
   the same state-machine CAS.
6. Define `(job_id, lease_id)` as the attempt idempotency key and add an outbox/unknown
   outcome policy for connectors whose external API does not accept an idempotency key.
7. Add deterministic LocalStore tests and real-Postgres concurrency tests, including
   retry after response loss and restoration of an older snapshot.

**Estimate: 16-24 days.** Protocol/state machine and migration: 3-4; two-backend atomic
operations and audit coupling: 5-7; client/listener renewal and terminal-delivery changes:
3-5; adversarial tests and compatibility/rollout: 5-8. The estimate is most likely wrong
at the transaction boundary: LocalStore stores JSON rows in SQLite while Postgres is
reached through PostgREST, and existing audit/retry/dependency actions are separate
writes. External APIs without native idempotency can add another workstream rather than
another method.

**Acceptance test rewrite**

The current sentence is directionally right but underspecified and cannot pass honestly
while the audit append is best-effort. Use this instead:

> Against both LocalStore and Postgres, worker A leases a job and receives `(lease_id A,
> epoch N, expiry T)`. Advance the authoritative database clock past T; race two reapers
> and prove exactly one expiry transition, one attempt increment, and one committed
> `lease_expired` event. Worker B then receives a different lease ID and epoch N+1.
> Replay A's `in_progress`, `failed`, and `completed` writes through the public REST
> contract and the supported WebSocket write contract, if retained: each returns a
> stable stale-lease conflict, leaves B's row and output unchanged, and atomically records
> one rejection event with expected/current fence metadata. B renews and completes once;
> retrying the same completion after a dropped response is idempotent. Also race cancel
> against completion, reject renewal after expiry, and restore a pre-lease snapshot to
> prove the old random lease ID cannot write after restore.

### WS2 - honest readiness, timed recovery, and external dead-man

**What already exists and should be reused**

- `cli.create_app()` is the right place to expose `/healthz` and `/readyz`; the current
  `/healthz` payload already reports backend and pause state but only checks whether a
  client object was constructed.
- `routes.get_db_client()` memoizes the configured Supabase client or returns LocalStore.
  It is wiring, not a connectivity probe.
- `routes.touch_agent_presence()`, `decorate_presence()`, and
  `get_offline_after_seconds()` already define worker heartbeat/freshness semantics.
- `launcher.run_forever()` and `launcher.tick()` are the existing timer loop and scheduler
  work seams. `service._scheduler_spec()` and `service.install_scheduler()` already make
  that loop boot-persistent on Windows, macOS, and systemd.
- `routes.reclaim_stale_leases()` is the recovery operation to replace internally once
  WS1 makes it safe. The timer should call the new database-atomic reaper, not preserve
  this read-then-update implementation.
- The ntfy notifier and connector health functions are reusable delivery/diagnostic
  surfaces, but the external dead-man must not depend on this gateway or notifier being
  alive at alert time.

**What `/readyz` must actually query**

Keep `/healthz` as event-loop liveness: if FastAPI can answer, it returns 200 and does not
pretend to prove the database. `/readyz` must execute a bounded query against the
authoritative store, validate the expected schema/protocol version and required RPCs,
and report whether job intake/leasing is enabled. For Postgres, a `SELECT 1` proves only
a socket; query a small `mco_runtime_health`/schema-version RPC that also exercises the
schema and write transaction used by leases. For LocalStore, add an equivalent `ping()`
that begins and rolls back a bounded write transaction.

Scheduler health cannot be inferred from the gateway process or its filesystem state.
`launcher.run_forever()` must heartbeat a durable scheduler-instance/leadership row on
each tick, including last success, last error, config digest, and lease expiry. `/readyz`
queries that row when this deployment requires scheduling. Worker health should be a
capability result from fresh `agent_registry.last_seen_at` values (for example, “at least
one `codex` worker”), not an undifferentiated worker count. Dead-man heartbeat delivery
and phone notification should be separately reported as operational/degraded health;
making all control-plane HTTP disappear from a load balancer merely because workers are
offline would remove the interface needed to cancel or repair work.

The timer-driven reaper may live in every gateway process after WS1. With an atomic
database CAS, concurrent reapers are harmless; gateway co-location also means no leases
are being accepted on a host whose reaper loop died with the event loop. Run it as a
FastAPI lifespan task with bounded calls, jitter, cancellation on shutdown, last-success
telemetry, and an exception loop that cannot kill the app. A separate process is an
operational scaling/isolation option, not a correctness requirement. Scheduler
leadership is separate: multiple schedulers can create duplicate schedules unless they
hold a database lease.

**Concrete work**

1. Specify liveness, control-plane readiness, job-execution capability, scheduler health,
   and notification health as distinct fields with stable status-code semantics.
2. Add the store health/schema probe, scheduler lease/heartbeat, and capability-aware
   worker freshness query.
3. Add the gateway lifespan reaper loop on top of WS1's atomic expiry operation, with
   metrics for last success, duration, failures, and oldest expired lease.
4. Configure an external monitor with durable last-seen/deadline state, authenticated
   heartbeats or meaningful pulls, duplicate suppression, recovery messages, and a
   notification provider independent of the home site.
5. Add fault-injection tests plus a scheduled physical power-loss drill.

**Estimate: 7-11 days after WS1.** Probe and health contract: 2-3; scheduler heartbeat
and leadership signal: 1-2; reaper lifecycle/telemetry: 1-2; external monitor,
notification, and drills: 3-4. The largest uncertainty is not FastAPI; it is proving the
cloud timer/state/phone delivery path and deciding which worker capabilities are truly
required for each edition.

**Acceptance test rewrite**

> With the gateway alive, make the database unreachable and prove `/healthz` remains a
> fast 200 liveness response while `/readyz` returns 503 within its timeout and names the
> failed store probe. Stop the scheduler and the last required-capability worker and prove
> their durable heartbeats age into explicit degraded components without making the
> repair/control endpoints unreachable. Lease an already-expired fixture while no worker
> polls and prove the timer reaper transitions it once. Finally, with the external monitor
> showing a fresh authenticated heartbeat, cut power/network to the entire coordinator
> site; after two missed 60-second deadlines, a cloud-side timer sends one phone alert
> through an independent provider within 180 seconds, then one recovery notification
> after the site returns. Preserve monitor logs/timestamps as the test artifact.

### WS3 - coordinator appliance and Team profile

**What already exists and should be reused**

- `routes.get_db_client()` already selects embedded LocalStore or Supabase/PostgREST;
  `LocalStore` persists the appliance ledger in `~/.mco/local.db`.
- `migrations_runner` discovers versioned migrations and tracks their application. That
  is the right basis for repeatable Pi and Team upgrades, once the base schema and lease
  RPC are also versioned rather than living only in `docs/SETUP.md`.
- `service._gateway_spec()`, `_scheduler_spec()`, `_service_systemd_unit_text()`,
  `_linux_install_service()`, `_linux_status()`, and `_linux_restart()` already implement
  `systemd --user` installation and operation. `service.install()` and
  `install_scheduler()` choose Linux without a Windows-specific command path.
- `launcher.save_state()` provides atomic scheduler-state file replacement; the same
  discipline should be used for snapshot manifests and promotion state.
- `mco doctor`, `/api/version`, the console, agent presence, and per-instance token files
  are useful migration/drill checks.

**ARM Linux breakpoints found in the current code/configuration**

- The gateway itself is mostly platform-neutral Python and already has a Linux service
  branch. The immediate migration blocker is state outside the code: the live
  `~/.mco/fleet.toml` uses `C:/Users/.../*.cmd` executables. Those worker entries cannot
  move to ARM; the Pi fleet config must run only coordinator services or use native Linux
  commands.
- A secret store created on Windows commonly auto-unlocks from Windows Credential
  Manager. `SecretStore.auto_unlock()` has no Linux keychain provider; the Pi must receive
  `MCO_MASTER_PASSWORD` through a protected service environment or have credentials
  deliberately re-enrolled. Copying `secrets.enc` alone produces a locked gateway.
- The setup examples are Windows-first (`C:/.../.venv/Scripts/mco.exe` and `.cmd` worker
  wrappers). They need an ARM/Linux appliance runbook and executable-path validation.
- `LocalStore` is SQLite-backed, but no consistent online backup API exists. Copying
  `local.db` while its WAL/transaction state is active is not an acceptable snapshot;
  implement SQLite backup/`VACUUM INTO`, manifest/checksum, encryption, retention, and a
  restore-incarnation fence.
- `systemd --user` only starts at boot without a login when lingering is enabled. The
  generated install message mentions this, but setup does not enforce or test it. The
  unit must also receive the secret/config environment, restart on gateway failure, and
  tolerate network/Tailscale/DNS becoming ready after the process starts.
- The merged agentd core contains the platform-neutral `Supervisor` contract and a
  Windows adapter only. That does not block a coordinator-only Pi, but a Linux worker
  appliance needs a POSIX adapter before agentd can supervise local workers there.
- The Python dependencies are declared OS-independent and Windows-only `tzdata` is
  correctly marker-gated. They still need a clean `linux/arm64` install smoke test so a
  future binary dependency cannot silently invalidate that claim.

**Concrete work**

1. Define and measure Appliance RPO/RTO, then write an idempotent Pi bootstrap that
   installs Python/package, systemd user services, linger, protected environment,
   Tailscale/firewall, log rotation, and version/migration checks.
2. Add WAL-consistent encrypted backup plus off-site/sibling transfer, signed manifest or
   external high-water mark, retention, restore-incarnation rotation, and a restore CLI.
3. Re-enroll secrets and operator/worker tokens on the Pi; do not copy a
   Windows-Credential-Manager-dependent store and call it migrated.
4. Run cold-boot, power-loss, disk-full, clock-skew, network-return, upgrade/rollback, and
   blank-Pi restore drills; capture measured RPO/RTO.
5. For Team, package Postgres migrations, make gateways stateless, add database-held
   scheduler leadership, and prove two replaceable gateways. Enterprise database
   replication is explicitly outside this estimate unless a managed service supplies it.

**Estimate: 9-14 days for the Appliance profile; 7-11 additional days for the Team
profile, or 16-25 days total.** The Appliance estimate reuses the existing Linux service
code but includes backup/restore and failure drills. The Team increment assumes WS1 and
an available managed Postgres. The likely misses are physical: UPS signaling, USB/storage
behavior under sudden power loss, Tailscale/DNS recovery, secret re-enrollment, and the
actual database migration/export path. Enterprise replication/DR can add weeks and must
not be hidden inside “state in Postgres.”

**Acceptance test rewrite**

The current test is not testable as written. A restored snapshot cannot prove that its
missing suffix is no larger than the snapshot interval; a valid shorter chain looks
valid. “Destroy” is also an unsafe and irreproducible verb. Use this instead:

> Record the accepted Appliance RPO and RTO. Commit a known job/event checkpoint and
> publish its event high-water mark or signed chain head outside Pi #1. Produce a
> WAL-consistent encrypted snapshot with manifest and timestamp, then power off and
> remove Pi #1. Starting from a blank Pi #2/SSD and only the documented recovery bundle,
> restore secrets/configuration/database, rotate the store incarnation, boot gateway and
> scheduler without an interactive login, and reconnect one worker. Measure time from
> declared incident to accepted job. Verify every retained event and manifest, compare
> the restored high-water mark with the external checkpoint, and report the exact lost
> interval; pass only when measured RTO and loss are within the written bounds. Replay a
> pre-failure lease and prove the restored incarnation rejects it.

For Team, add a separate test: kill the active gateway and scheduler, prove another
gateway continues serving the same Postgres ledger, and prove exactly one scheduler
leader emits each due run.

### WS4 - usage/cost, accounts, tenancy, and machine identity

**What already exists and should be reused**

- `orchestrator.executors.ROLE_COMMANDS`, `make_cli_executor()`, and `run_argv()` are the
  common local CLI launch seam. Today they capture only final stdout/stderr, so structured
  usage is discarded even when a vendor emits it.
- `AgentListener._execute_task()` is the worker-wrapper boundary for registered
  executors; `BitCadenceAgent.process_job()` is the SDK boundary. Both know job and
  attempt context and can meter a run before sending completion.
- `GatewayClient.lease()/complete()/fail()` are the protocol boundary where a worker can
  receive a reservation and settle usage. `handlers.handle_job_update()` is the current
  authoritative ingestion path, but it cannot discover tokens that the worker never
  sends.
- Scheduler launchers, bounded loops, `max_retries`, the kill switch, `/metrics`,
  `agent_registry`, `org_id`, the encrypted secret store, LLM connections, and edition
  gates are useful policy/configuration surfaces. None currently reserves or settles
  money.

**Where accounting hooks in**

Use all three layers with different authority:

1. The **gateway/lease handler** owns budget policy. It estimates and reserves against a
   tenant/user/workflow/job/account/model budget before returning a lease. An executor
   must never be the authority for whether money may be spent.
2. The **worker wrapper or typed executor** parses provider-native structured events and
   periodically reports cumulative usage against the lease. It is the only layer that
   sees the CLI's real token events. The current text-only `run_argv()` must grow a typed
   result/event interface rather than teaching `handle_job_update()` to scrape prose.
3. The **SDK/client contract** transports normalized usage samples and terminal
   settlement with lease/idempotency credentials. The gateway validates monotonic totals,
   stores raw vendor evidence plus normalized units, prices by versioned price book, and
   atomically releases/charges the reservation.

Provider facts as of the locally installed CLIs on 2026-09-03 (`codex 0.144.1`, Claude
Code `2.1.221`, Gemini CLI `0.41.2`, OpenCode `1.17.13`):

- Codex supports `codex exec --json`; its `turn.completed.usage` event exposes input,
  cached input, cache-write input, output, and reasoning-output tokens. It does not
  provide an authoritative billed dollar amount or provider-returned model identity in
  that event, so BitCadence must record the requested/resolved model separately and price
  conservatively. See the official [Codex event types](https://github.com/openai/codex/blob/main/sdk/typescript/src/events.ts).
- Claude Code supports `-p --output-format json`/`stream-json`; the result includes usage
  metadata and the Agent SDK result contract provides `total_cost_usd`. Preserve the raw
  usage fields too; a vendor total is evidence, not a substitute for an auditable price
  book. See the official [Claude Code CLI reference](https://docs.anthropic.com/en/docs/claude-code/cli-usage).
- Gemini CLI supports `--output-format json` and `stream-json`; the result contains
  aggregate/per-model token statistics and its telemetry exposes token-usage metrics.
  Dollar cost depends on authentication/billing mode and is not a uniform CLI guarantee.
  See the official [Gemini headless reference](https://geminicli.com/docs/cli/headless/).
- OpenCode supports `opencode run --format json`; `step-finish` carries token categories
  and a cost field. Treat it as metering evidence with a reconciliation fallback because
  the raw event stream has had ordering/completeness bugs and some subscription-backed
  providers legitimately report zero cost. See the official [OpenCode CLI reference](https://dev.opencode.ai/docs/cli/)
  and [usage schema](https://github.com/anomalyco/opencode/blob/dev/packages/schema/src/v1/session.ts).
- The `antigravity` role is currently just an alias to `gemini -p` in
  `ROLE_COMMANDS`; it has no distinct accounting adapter. Grok/reviewer/chief are supplied
  by external wrappers in the live fleet and have no typed usage contract in this repo.
  Mark their usage `unmetered/unknown` and deny hard-dollar-budget jobs until an adapter
  proves a source; never silently write zero.

**Concrete work**

1. Define normalized usage, immutable raw evidence, versioned price book, reservation,
   adjustment, settlement, refund/expiry, and unknown-usage schemas. Key every entry by
   org, budget owner, account, model, job, lease/attempt, and vendor event ID.
2. Add transactional budget reservation to the lease RPC/LocalStore operation and an
   emergency breaker checked independently on every new reservation and usage sample.
3. Replace text-only executor results with typed adapters for Codex, Claude, Gemini, and
   OpenCode; add fixtures from pinned CLI versions, monotonic streaming samples, terminal
   settlement, and missing/malformed-usage behavior.
4. Model accounts/cooldowns/quotas as schedulable capacity without storing account secrets
   in the ledger; build operator-visible reconciliation for provider dashboard/invoice
   totals.
5. First-90-day security slice: bind every budget/account row to `org_id` and deny unknown
   ownership. Full WS4 later: Postgres RLS tested with non-service tenant credentials,
   per-tenant vault keys and rotation, machine enrollment, short-lived credentials,
   revocation, and authenticated transport.

**Estimate: 15-22 days for usage ledger, reserve/meter/settle, breaker, and four CLI
adapters; 18-28 additional days for database-enforced tenancy, per-tenant keys, and
machine identity, or 33-50 days for full WS4.** The cost slice is most likely wrong where
subscription CLIs do not map token counts to marginal dollars, a CLI omits its terminal
usage event, or “stop at $5” requires interrupting a process between provider calls. The
security slice is most likely wrong around legacy data migration, key rotation rollback,
certificate enrollment/revocation, and testing against real Postgres roles rather than
the service credential.

**Acceptance test rewrite**

The current `$5` sentence is not deterministic without a price book and reservation
rule, and a post-run token report cannot retroactively prevent the call that crossed the
limit. Use this instead:

> With a pinned synthetic provider, price-book version, and deterministic usage stream,
> give a loop a $5.00 hard ceiling. Before each attempt the gateway atomically reserves
> its worst-case cost; it refuses the first reservation that could exceed $5.00, even
> with two workers racing. Stream monotonically increasing usage, settle each attempt
> exactly once after a simulated lost response/retry, and prove `reserved + settled <=
> $5.00` (or a separately declared one-call overshoot bound for providers that cannot be
> interrupted). The ledger names job, lease, workflow, tenant, budget owner, account,
> provider, requested and observed model, raw tokens, price-book version, calculated
> cost, vendor-reported cost if any, and breaker reason. Unknown/malformed usage fails
> closed for hard-budget work rather than recording zero. A second account with budget
> remains routable when the first is exhausted.

Then run the security acceptance separately:

> Connect directly to Postgres as tenant A and prove RLS rejects select/insert/update of
> tenant B job, usage, account, audit, and context rows; the same probes as B succeed only
> for B. Rotate tenant A's vault key while reads/writes continue, enroll a new machine
> with a short-lived scoped credential, revoke it, and prove both a fresh connection and
> an already-connected worker lose write authority within the documented bound.

### Net schedule and PR #47

| Work | Engineer-days | First-90-day treatment |
|---|---:|---|
| WS1 | 16-24 | Commit; release blocker |
| WS2 | 7-11 after WS1 | Commit; dead-man and drills include wall-clock wait |
| WS3 Appliance | 9-14 | Commit after the WS1 protocol is stable |
| WS3 Team increment | 7-11 | Stretch; do not include Enterprise replication |
| WS4 cost/account slice | 15-22 | Commit only if WS3 Team is deferred or staffing increases |
| WS4 tenancy/keys/machine identity | 18-28 additional | Move after the first 90 days |

That is **47-71 focused engineer-days** for WS1 + WS2 + Appliance + the WS4 cost slice,
before contingency and soak time. It fits 90 calendar days only near full-time and with
scope discipline. At the previously stated 7-10 focused hours per week it is roughly
eight to seventeen months, depending on what a “focused day” actually contains. The
first 30 days should end with the WS1 protocol and most of its
two-backend implementation, not a production Pi cutover.

PR #47 no longer needs rework: it was merged to `main` as `c0b1026` on 2026-09-03 after
the ADR 0002 revision-2 corrections. Its scope remains right. `Supervisor` is a local
process supervisor and should not become the distributed job-lease authority. The plan
creates follow-on work instead: add POSIX/macOS adapters, update the worker SDK/wrappers
it launches to retain/renew/settle WS1 lease credentials, and make graceful agentd stop
give the wrapper a bounded chance to report/abandon its lease before forced termination.
Keep job fencing in the gateway/store protocol and process ownership fencing in agentd;
conflating them would weaken both.

## Chief's read

**Date:** 2026-09-03. **Author:** chief-beast. Under 400 words. Not a restatement.

**This week, one action:** send one already-drafted email to one named human. Nate tonight (`founder/NATE-OUTREACH.md`) if you want a reply; one regulated/Gartner contact (`founder/DESIGN-PARTNER.md`) if you want a check. Not WS1. Not EPCOT. Not another critique. The product is ahead of the sale. A fenced lease does not collect $1.5–3k/mo.

**Part X — what actually blocks.** Only **#4: name the first two humans for the opener.** You cannot send without a name. Decide in five minutes, then click send.

Defer a month with no cost: **#1 RPO/RTO** (WS3 is not this month), **#3 SSD/UPS** (Pi cutover is not this month), **#5 approver scope** (the CLI already works). **#2 grok/opencode off:** leave grok human-driven; do not flip waker to drain a paper queue. **#6 bless the order:** bless the reviewer's engineering sequence (fence before feed). That is not this week's constraint.

**Cut or push past 180 days** (Parts III–VIII): WS5 routing-with-receipts, WS7 OTel/Dynatrace "timeline" pack, Datadog/Grafana packs, PWA, Jetson, Enterprise RLS/mTLS/SCIM/legal hold, the $5,100 hosted Team city, three-edition support as a build program, corp-dev as a scheduled workstream, the Swedish operating partner as an engineering dependency. Keep Drumline in every edition. Keep MIT core. Cap spend manually today (Part IX) — that is a one-line ops rule, not WS4.

**Month-eater:** the EPCOT AWS demo and the hosted-city spend it justifies. It is already spawning PRs (#52, #53), PostgREST seams, and conductor pavilions. It produces a self-testing city, not a conversation. Freeze it until a human is on a calendar.

**30-day window:** founder's optimism. Reviewer math stands: 7–10 focused hours/week cannot ship WS1+WS2+Pi+ADR+#46+#47+ntfy in 30 days. Honest 30 days: one outbound, the ntfy leak if it is still open, and a written WS1 protocol. Honest first money: 30 days of conversations, not 30 days of fencing. The AI-Act "you are out of compliance as of August 2026" opener is also legally wrong after the Omnibus — do not send that sentence.

ASK: none. Send.
