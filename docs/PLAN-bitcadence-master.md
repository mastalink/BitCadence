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
