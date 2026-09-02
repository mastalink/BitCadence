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
