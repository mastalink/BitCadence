# BitCadence Platform v2: Coding, Media and AI Ops in one product

**Status:** proposal for owner review. The owner decisions in §9 were recorded on 2026-10-18. The rest is not yet approved, and nothing is built or measured.
**Evidence base:** `mastalink/BitCadence` default branch `main` at
`62bb5c59dc75560af4de7d5082ab5a350ed09f0c` (2026-10-14 21:00 -0400), read 2026-10-18.
Open PRs #148, #153 and #156 were read. There were no open issues.
**Companion files:**
[evidence matrix](PLATFORM-V2-EVIDENCE.md),
[backlog, work packets and local checklist](PLATFORM-V2-BACKLOG.md),
[low-fidelity screens](../design/platform-v2/index.html).

Labels used throughout: **[source]** means the repository source shows it.
**[runtime unknown]** means no evidence that it works end to end on the fleet.
**[assumption]** means a routine choice made here, which the owner can overturn.

---

## 1. Recommendation

**Build one product with one engine. "Coding", "Media" and "AI Ops" are template
families and step kinds. They are not separate apps.** AI Ops is also a
first-class workspace, because someone has to see and run the machinery.

The repository already has most of the hard parts of a reliable engine:
fenced leases, an idempotent outbox, digest-bound grants, independent review,
server-side evidence checks, durable human gates, and an uncertain-effect
state. The problem is that the friendly front door does not use any of it:

- "Ask for something" creates **plain job-board jobs**
  (`src/mco/orchestrator/ask_routes.py:43-60`). It does not create a Score run.
- Score v1 does run live, but only for **read-only audit and git-worktree
  commits**. Paid tasks, multi-attempt tasks and costed tasks are refused
  (`src/mco/orchestrator/score_bridge.py:143-149`).
- **There is no media pipeline in any authorized repository.** Media work today
  lives only on the owner's machines (Beast `K:`), which this design cannot see.

So the highest-value move is not a new UI or a new engine. **Put the friendly
front door on top of the governed engine, then widen the engine one effect at
a time (paid generation, then publishing) behind the same contracts.**

Concretely:

1. **A Flow is the user's object. A Score is its compiled form.** People create,
   read and approve Flows in plain words. Every Flow version compiles to a
   digest-bound Score. Score stays the authority boundary; nobody edits JSON.
2. **Steps are an editable outline, not a node canvas.** Redesign v1 removed
   both drag-and-drop builders on purpose (`design/redesign-v1/STEP-COUNT.md`
   rows 5-6). v2 keeps that decision and allows real edits through a step
   list: add from a catalog, reorder, choose a reviewer, set limits. Wiring
   is implied by order plus named inputs. A read-only drawing is still shown.
3. **Validated templates come first.** A run counts toward the reliability
   target only if it starts from a validated template version. A chat-drafted
   Flow runs as "Experimental" until it passes the same gates.
4. **Every outside effect goes through a Connector with a manifest.** That
   covers GitHub, ComfyUI, ElevenLabs, a deploy target and a publisher. The
   manifest lists capabilities, cost model, idempotency support and the
   reconciliation probe. The engine never runs a command taken from a packet.
5. **Run status is a ladder, not a light.** Accepted, Assigned, Working,
   Output checked, Reviewed, Approved, Delivered. A heartbeat or an HTTP 200
   never advances a step.

### What the person sees, in one sentence per domain

| Domain | Person says | Person gets |
|---|---|---|
| Coding | "Fix the login bug in MyMeals" | Isolated branch, tests run, an independent review, a **draft PR**. Release is a separate, later approval. |
| Media | "Make episode 32 of Embracing the Sofa" | Script, art, narration, edit, music. Each one is versioned, reviewed for technical and creative quality, and shown as a preview. Publishing is a separate approval. |
| AI Ops | "Connect Beast and keep my overnight renders from colliding" | A capability inventory, one owner per GPU, a queue that explains waits, spend limits, and plain recovery buttons. |

---

## 2. End-to-end journeys

Each journey uses the five-step lifecycle: **Create, Flow, Run, Review, Deliver**.
The screen numbers refer to `design/platform-v2/`.

### 2.1 Coding: "Fix the login bug in MyMeals"

1. **Create** (screen 01). Pick the template *Fix a bug*. Choose the
   repository from Library > Connections. Type the symptom in plain words.
2. **Flow** (02). The template fills in: *Reproduce > Fix in an isolated copy >
   Run tests > Independent review > Open draft PR*. The reviewer must be a
   different helper from the builder, and the editor will not save a Flow
   that breaks this rule (`scores.py:98-99`). Limits shown: up to 2 fix rounds
   and up to $3 of model spend. Preflight runs before Start: the repository
   is reachable, a worktree can be made, the test command is known, a
   builder and a reviewer are online, and the spend fits.
3. **Run** (03). Cards climb the status ladder. The test step shows "Output
   checked: 214 passed". If the builder runs out of quota mid-step, the card
   says: *"Claude ran out of quota. Your branch and test results are saved.
   Codex on Beast can continue from the last checkpoint. [Continue with
   Codex]"*.
4. **Review** (04). Diff summary, test receipt, reviewer verdict. The person
   approves *this PR at this commit*. The approval is bound to the artifact
   version, so a new commit voids it.
5. **Deliver**. A draft PR link. "Release to production" is a different
   template with its own gate. Approving the PR never implies a release.

Engine mapping: `score_adapters_live.GitWorktreeCommitAdapter` already does
the governed commit, with branch denylist, exact head, allowed paths and
compensation (`score_adapters_live.py:1-20`, `:56-66`). PR #148 adds
`pr_url` evidence. Gaps: a push-and-open-PR effect adapter, and more than one
attempt per task (the bridge refuses `max_attempts != 1`, `score_bridge.py:146-147`).

### 2.2 Media: "Make episode 32"

1. **Create**. Template *Make an episode*. Pick a series from Library >
   Characters & Voices. This loads the character sheets, voice ids, brand
   kit and the music bed. Enter the premise.
2. **Flow**. *Script > Script review (creative) > Artwork > Narration >
   Animation/edit > Music > Technical check > Creative review (independent) >
   Prayer sign-off (human) > Package deliverables*. Publish is **not** in
   this Flow. It is a separate *Publish an episode* Flow with its own approval.
   Each generating step shows an estimated cost and the host it needs, for
   example "Artwork: ComfyUI on Beast, needs the GPU, about 6 minutes".
3. **Run**. Previews appear as artifacts are made: thumbnails, audio
   waveform, a low-res cut. If the GPU is busy: *"Waiting for the GPU. The
   overnight training job holds it until 2:00 AM. [Wait] [Ask it to yield]
   [Run on another host]"*. The last button only appears when a
   capable host is allowed. Mac Studio is excluded while it is under test.
4. **Review**. Side-by-side versions, a comment pinned to a timecode,
   *Request a correction* with a bounded scope ("fix narration 02:10-02:24
   only", which spends at most one more narration generation). Technical
   checks (loudness, resolution, duration, offsets) pass or fail
   automatically. Creative acceptance is a separate human verdict. Gemini
   "ear review" shows as **Waiting on you: upload the review pack to Gemini**,
   because that relay needs the owner to trigger it and is not an API. The
   pack is kept under 10 MB, with whole-film time offsets.
5. **Deliver**. A versioned package: master, captions, thumbnail and
   provenance manifest. Publishing waits for its own approval.

Engine mapping: none of this exists in source. It needs a ComfyUI connector,
an ElevenLabs connector, an artifact store for large binaries (the evidence
verifier caps artifacts at 16 MB, `score_evidence.py:64`), media evidence
kinds, and paid-effect support in the bridge.

### 2.3 AI Ops: "Connect Beast and stop overnight collisions"

1. **Operations > Hosts** (06). Add a host. The product runs a capability
   probe and shows what it found: GPU and VRAM, installed models, ComfyUI
   version, storage paths, and which helpers can *execute on* it (SSH) or
   only *read* it (SMB).
2. **Operations > Resources**. Mark the GPU as exclusive. Set the daily hard
   cutoff (this already exists as `resource_arbiter.py` `hard_deadline_local`
   [source]; it is not yet wired to leasing [source: no importers]).
3. **Operations > Queue**. Every waiting step shows **why** it waits:
   dependency, approval, budget, GPU held by X, no capable helper, or quota
   resets at a time. These are the same reasons `SandboxRun.blockers()` already
   computes (`scores.py:250-285`), extended with host and quota.
4. **Operations > Spend**. Per-connector month-to-date spend, reservations
   held by running steps, and the cap. Unknown capacity is shown as unknown,
   never as zero (`docs/USAGE-CAPACITY-SIGNAL.md` rule 1).
5. **Operations > Recovery**. A list of runs needing repair, each with one
   suggested action and who is allowed to take it.

### 2.4 Cross-domain: "Ship MyMeals 2.0 with a launch video, then watch the deploy"

One **Project** holds three Flows that share a library:

- *Fix and release* (Coding) produces `release-notes.md@v3` and a draft PR.
- *Launch video* (Media) **consumes** `release-notes.md@v3` as a typed input.
  If the notes change to v4, the video Flow shows "Input changed. Re-run
  from Script?" and does not silently re-render.
- *Watch the deploy* (AI Ops) starts after release approval. It watches health
  for 24 hours and opens an incident card if error rates rise.

Three different approvals happen: merge the PR, release, publish the video.
None of them implies another.

---

## 3. Information architecture

Today's sidebar has 12 entries (`console_src/8ec84a72…js:4-17`). Proposal:
**five places**. They map to the existing phone tab bar.

| Place | Holds | Replaces / absorbs |
|---|---|---|
| **Home** | *Needs you* (approvals, reviews, owner-triggered steps, repairs), then *Running now* | Home, Approvals (as a filter), Activity (as a link) |
| **Projects** | Projects, each with its Flows, Runs and Deliverables | Projects, Ask for something (becomes *New*), Live view |
| **Library** | Templates, Characters & Voices, Brand kits, Repositories, Memory | Drumline (as *Memory*) |
| **Operations** | Hosts, Helpers, Connections, Queue, Resources, Spend, Schedules, Recovery, Audit | Helpers, Connect an AI, Schedules, Governance, Job Board (as *Queue > All jobs*) |
| **Settings** | Account, people and roles, pause everything, advanced | Settings |

Naming (plain tone on the left, expert tone on the right, using the existing tone toggle):

| Plain | Expert / internal |
|---|---|
| Flow | Score (compiled) |
| Step | Task / work packet |
| Run | Score run |
| Try | Attempt |
| Helper | Agent instance / worker |
| Computer | Execution host |
| Output | Artifact (versioned) |
| Check | Evidence verification |
| Needs you | Gate / checkpoint |

**Advanced disclosure** stays one toggle (it already exists, `8ec84a72…js` `advanced`).
With it off: no ids, leases, digests, roles or JSON. With it on: a "Details"
drawer on each card shows the job id, attempt token, score digest, lease
owner and raw evidence. These are read-only. Nothing a normal Flow needs is
behind Advanced.

---

## 4. Shared domain model

```
Organization ─┬─ Project ─┬─ Flow ── FlowVersion (immutable, digest) ── compiles ─▶ Score
              │           │                 └─ StepSpec[] (typed inputs/outputs, reviewer, limits)
              │           └─ Run ── StepRun ── Attempt ── Lease (fenced) ── Effect (ledger row)
              │                         │          └─ Artifact (content-addressed, versioned, provenance)
              │                         ├─ Review (technical | creative, reviewer ≠ author)
              │                         └─ Approval (human, bound to artifact digest + effect)
              ├─ Connector ── ConnectorVersion (manifest: capabilities, cost model, idempotency, probe)
              ├─ Host (execution host: resources, mounts, allowed helpers, release state)
              ├─ Helper (identity) ── runs a Model ── on a Host ── with a Role
              ├─ Budget (org/project/run caps) ── Reservation ── Charge
              └─ Grant (digest-, run-, tenant-bound; existing score_authority)
```

Key rules. Each one exists to prevent a known failure:

- **Model, coordinator role and execution host are three separate fields.**
  Today the host is *inferred from the instance-id spelling*, and unknown
  prefixes fail closed (`delivery.py:155-165`). v2 adds an explicit
  `host_id` on Helper and Host records. "Codex on Beast" and "Claude on Mac,
  executing on Beast over SSH" become different, inspectable facts.
  `remote_exec` is its own capability, with its own end-to-end proof.
- **Host release state.** A Host can be `active`, `testing` (no inference
  or rendering) or `retired`. Mac Studio is set to `testing` until the owner
  releases it. Preflight refuses any step that would compute on a `testing` host.
- **StepSpec has typed ports.** Each input and output has a type (`text/markdown`,
  `image/png`, `audio/wav`, `video/mp4`, `git/commit`, `git/pr`, `report/test`,
  `bundle/review-pack`), a schema version, and validation rules (size, duration,
  loudness, resolution). A FlowVersion will not save if ports do not match.
- **An Artifact is immutable and content-addressed.** It records `sha256`,
  type, producing attempt, connector version, model and seed when relevant,
  inputs (by artifact id), and cost. A "new version" is a new artifact with a
  `supersedes` link.
- **An Approval names exactly what it approves.** It is a tuple of
  `(run, flow digest, step, artifact sha256, effect kind)`. It is decided by
  an authenticated human session, never an agent token (this already exists:
  `score_policy.authenticated_human`, `score_policy.py:59-67`).
- **An Effect is a ledger row before it is an action.** States: `intended >
  submitted > succeeded | retry_safe_failure | uncertain > reconciled`. The
  three result states already exist (`score_adapters.py:38-41`).

---

## 5. Execution architecture

```
 Console (Home/Projects/Library/Operations)        Phone review (same pages, responsive)
        │  plain-language Flow edits, approvals (session auth)
        ▼
 Flow service ── validate ports ── compile ──▶ Score v1+ (digest) ──▶ Grants (score_authority)
        │                                                    │
        ▼                                                    ▼
 Preflight ── connectors.probe() · hosts · helpers · capacity(U01) · budget · resources
        │ pass → Run created (durable, SQLite or Postgres)
        ▼
 Conductor tick (score_conductor / score_sweep, event-driven + timer fallback)
   plan → reserve budget + resource → outbox dispatch (score_dispatcher) → job board
        │                                                         │
        │                                       fenced lease (leases.py) → Helper on Host
        │                                                         │
        │                       Connector adapter (allowlisted) ── Effect ledger ── external service
        ▼                                                         │
 Evidence verify (score_evidence + typed media/code verifiers) ◀──┘ artifacts → Artifact store
        ▼
 Independent review → Human gate (score_policy, generalized) → accepted → unlock dependents
```

Design decisions:

- **Keep the job board as the delivery substrate.** Leases, the delivery
  watchdog, same-host reroute (PR #155) and presence are proven pieces.
  Score already dispatches through it (`score_dispatcher.py`).
- **Generalize `score_policy`.** Today it knows exactly two VIA reasons and
  hardcodes the task id `G09` (`score_policy.py:27-48`). v2 makes policy a
  per-Project document (gates per effect kind, spend cap, review
  requirements) and keeps `VIA_OWNER_POLICY` as one instance of it.
- **Lift the bridge's three refusals one at a time, each behind its own
  tests:** multi-attempt, then budgeted/paid, then publish effects
  (`score_bridge.py:143-149`).
- **Wire `resource_arbiter` into lease acquisition** so the GPU is a fenced
  resource, not a convention. `Score.tasks[].resources` already exists and
  `blockers()` already checks overlap (`scores.py:283-284`). The arbiter adds
  cross-process and cross-tool exclusivity plus hard deadlines.
- **Waiting costs nothing.** Waits are event-driven (WebSocket wake, Score
  sweep timer). No model call is spent polling. ComfyUI progress arrives
  over its WebSocket.
- **Reasoning effort default is Medium.** A StepSpec may request High only
  with a recorded reason, such as "multi-file refactor across 3 packages".
  The reason is shown in Details and counted in Spend.

### 5.1 ComfyUI: build a connector, do not fork it

**Recommendation: do not reverse-engineer ComfyUI.** Its server API is open
source and documented, and it covers everything a connector needs.
[source: docs.comfy.org comms_routes / comms_messages, read 2026-10-18]

| Need | Documented surface |
|---|---|
| Capability inventory | `GET /object_info`, `GET /object_info/{node_class}`, `GET /models/{folder}`, `GET /system_stats` (devices, VRAM), `GET /features` |
| Submit | `POST /prompt` returns `prompt_id` and `number`, or `error` and `node_errors` |
| Progress | `/ws`: `execution_start`, `executing`, `progress {value,max}`, `executed {output}`, `execution_cached`, `execution_success`, `execution_error`, `execution_interrupted`, `status {queue_remaining}` |
| Results | `GET /history/{prompt_id}`, then `GET /view` (parameters defined in `server.py`) |
| Cancel | `POST /interrupt` (current execution), `POST /queue` (clear pending or running) |
| Inputs | `POST /upload/image`, `POST /upload/mask` |

Connector rules:

- **Workflow templates are versioned BitCadence assets**, stored as API-format
  graphs with named, typed parameter slots. People pick "Character portrait,
  v4". They never see node ids.
- **Preflight** checks that every node class in the template appears in
  `/object_info`, that every referenced model file appears in `/models/*`,
  and that VRAM in `/system_stats` meets the template's declared need. A
  missing node or model fails preflight in plain words: *"Beast is missing
  the model 'sdxl-character-v2'. Ask an admin to install it, or pick
  another template."*
- **Duplicate-generation guard.** Before submitting, the connector writes
  an Effect row with an idempotency key, and stamps the key in the prompt's
  `extra_data`. After a crash, it reconciles: it searches `/history` and
  `/queue` for that key before it submits again. If the result is ambiguous,
  the Effect goes to `uncertain` and the run asks a person. It never
  submits blindly. **This relies on ComfyUI keeping `extra_data` in history.
  That is [runtime unknown] and is local probe C-3 in the backlog.**
- **The REA skill applies only if** a specific packaged custom node or
  binary used in production has behavior its source and docs do not
  establish (for example, an extension that writes outputs outside
  `/history`). The backlog has the exact local probe. Licenses are respected:
  ComfyUI is GPL-3.0, so BitCadence talks to it over HTTP and never vendors
  or links its code.

---

## 6. Recovery, authority and cost

### 6.1 Failure states the person sees

Every failure card answers three questions in plain words: **what happened,
what is saved, and what you can do.** It offers one recommended action. Other
actions sit behind "More options".

| Situation | Card text (plain) | One action | Engine behavior |
|---|---|---|---|
| Builder quota exhausted | "Claude ran out of quota. The branch and test results are saved." | Continue with Codex on Beast | Checkpoint kept. Next attempt goes to the next fallback with the same host constraint. A Grok fallback is offered only for steps marked short. |
| Helper died mid-step | "The helper on Beast stopped responding 4 min ago. Nothing it was writing was accepted." | Try again | Lease expires and is fenced (F1-F3). A late write from the dead helper is rejected. |
| GPU busy | "Waiting for the GPU (training holds it until 2:00 AM)." | Wait | Arbiter queue. No model spend while waiting. |
| Credential expired | "GitHub sign-in for MyMeals expired. Your work is saved." | Sign in again | The secret value is never shown. Resumes from the checkpoint after sign-in. |
| Uncertain paid effect | "We can't tell if the narration was generated. We will not pay twice." | Check now | Reconcile probe. If still unknown, a human decides "already done" or "generate again". |
| Permission denied | "You can approve this, but only Joe can release to production." | Ask Joe | No bypass. The request is sent to the person who holds the grant. |
| Review rejected | "Reviewer: narration mispronounces 'Carlo' at 02:12." | Fix just that part | A bounded correction attempt within the retry and spend limits. |

### 6.2 Authority: fixing "cancel/reassign returned 403 despite owner takeover"

[source] Cancel and reassign check that the *caller's role* is an approver
role (`routes.py:1309-1310`, `:1478-1479`). An owner saying "take over" in
chat grants nothing the server can see, so a worker token gets 403. That is
correct behavior, and the product failure is that nothing better was offered.

v2 adds a **Takeover grant**: the Chief seat, the Deputy Chief seat, or Joe
(in that escalation order, §6.4) grants helper X the right to cancel or
reassign jobs *in run R* for *N hours*. It is
recorded in the audit log and shown on the run card. It expires on its own.
Old pending jobs from the replaced owner are fenced at grant time, so another
worker cannot take them. The 403 becomes a plain state: *"Codex can't cancel
this yet. [Ask the Chief to let Codex take over this run]"*.

### 6.3 Cost

- **Before start:** the estimate per step and the total, the cap, and what is
  already reserved. Unknown estimates show as "unknown" and need an explicit
  OK.
- **During the run:** reserved and spent per step. A step that would cross
  the tighter of the project and organization caps stops at a gate (§6.5).
  This already exists as an idea in `SPEND_ABOVE_CAP` (`score_policy.py:15-17`).
- **Failed attempts keep their reservation** until reconciled. This is a
  conservative rule that already exists (`scores.py:200`).

### 6.4 Takeover grants (decided)

- **Who may grant, in order:** first the holder of the **Chief** seat (`claude-desktop` when online, otherwise the `claude-cio` sidecar, per `ops/cio-sidecar-brief.md` and `cio_policy.chief_is_online`). If the Chief seat is unavailable, the **Deputy Chief** seat. Then **Joe**.
  **The Deputy Chief seat is held by Grokbot** (owner decision, 2026-10-22). The seat is not defined anywhere in the repository today, so work packet P2d adds it as a named seat and assigns Grokbot to it. Who holds a seat is configuration, not code, so Joe can reassign it later without a code change.
  **[assumption]** Grokbot is the fleet's existing Grok worker (role `grok`, instance `grok-beast` per `ops/cio-sidecar-brief.md:52`). P2d confirms the exact registered identity before binding the seat.
- **A seat never grants a takeover to itself.** Grantor and grantee must be different identities.
- **Deputy and reviewer stay separate.** Grok is also the fleet's primary reviewer (`ops/cio-sidecar-brief.md:52-53`). If Grokbot issues a takeover as Deputy Chief on a run, it may not be the independent reviewer for work in that run that the takeover touched. That review goes to the second reviewer or to Joe. This extends the existing author-is-not-reviewer rule (`scores.py:379-393`) to the person who authorized the takeover.
- **Joe can always revoke or override** a grant issued by either seat.
- **Scope:** cancel, reassign and fence jobs inside one run. A grant never covers approvals, publishing, release, spend above caps, or new grants.
- **Duration:** up to 4 hours **[assumption]**. It expires on its own. Every grant, use and revocation is written to the audit trail and shown on the run card.

### 6.5 Spend caps (decided)

- Two caps apply to every paid step: the **organization** monthly cap and the **project** monthly cap. A step must fit both, and the tighter one wins.
- Project caps start from the values already in `cio_policy.SPEND_CAPS_CENTS`: Via $150, MyMeals $15, Sim Lab $25 with a hard stop at $40, Operate $10. A project with no cap fails closed, which `cio_policy` already does.
- The organization cap value is an **[assumption]** until Joe sets it in Settings. It is shown as "not set" and blocks paid work, so it is never treated as unlimited.

### 6.6 Publish gate (decided)

- **Default:** every publish effect (YouTube, a website, a social post, a release to production) pauses at a human gate. The approval names the exact artifact version and destination.
- **Bypass:** Settings > Publishing > "Publish without asking me". It is set **per flow template and destination**. It is never a global switch.
  - It is off by default.
  - Only an authenticated human owner session can turn it on. Agent tokens and the Chief and Deputy Chief seats cannot.
  - Turning it on is audited.
  - While it is on, every run of that flow shows a "Publishes automatically" badge before Start.
- A bypass removes only the final publish gate. It does **not** remove the other gates in the production flow: creative review, prayer sign-off, technical checks, spend caps.

### 6.7 Paid steps in Experimental flows (decided)

- The first run of an Experimental flow that contains any paid step stops before Start for an **initial approval**.
- That approval sets a **flow budget**, for example "$5 for further runs of this flow".
- Later runs of the **same flow version** draw from that budget without asking. When it runs out, the next run asks again.
- **[assumption]** The budget is tied to the flow version's digest. Editing the flow makes a new version, which needs a new initial approval, so changing the steps can't silently inherit a budget.
- Flow budgets still count against the project and organization caps (§6.5).

---

## 7. The 92.6% reliability target

**This is a target. It has not been measured.** No fleet run data was available to this design.

### 7.1 Definition

**Unattended completion rate (UCR)** = completed-without-repair ÷ eligible runs.

- **Eligible run (the denominator).** A run started from a *validated* template
  version, on a *supported configuration*, that **passed preflight**. Excluded,
  but reported separately: preflight blocks, Experimental (chat-drafted)
  Flows, and runs the person cancels before the first step starts.
- **Completed.** All `launch_requires` steps are accepted, every artifact
  passes its checks, and independent review passed.
- **Without repair.** No *operator repair* happened. Repair means any action
  that needs logs, a terminal or CLI, editing stored data, manually starting
  or reassigning a helper, or using a credential outside the guided sign-in.
- **Not failures:** time spent waiting on human approval, creative review,
  prayer sign-off, or an owner-triggered relay. These pauses are designed in.
  A guided one-tap action the product offers (such as "Sign in again" or
  "Continue with Codex") counts as an **intervention**, not a repair.
- **Supported configuration v1 (owner decision 2):** gateway on Windows or Linux,
  SQLite or Postgres store, helpers Claude, Codex and Grok (short steps only),
  hosts Beast (active) and Mac Studio (testing; no compute), connectors
  GitHub, ComfyUI and ElevenLabs.
- **Workload mix (owner decision 2):** Coding 40%, Media 40%, AI Ops 20%. Each
  domain is also reported on its own, so one domain cannot hide another.

### 7.2 Metrics reported separately (never folded into UCR)

1. Technical completion: steps whose outputs pass their checks.
2. Accepted quality: creative or code acceptance by the reviewer and owner, first time and within limits.
3. Recovery success: of the runs hit by an injected or real fault, how many finished without repair.
4. Intervention rate: guided one-tap actions per run.
5. Cost: actual compared with estimate, per run and per step.
6. Latency: wall clock excluding human waits, with p50 and p90.

### 7.3 Evaluation plan

- **Goal (owner decision 1).** 92.6% is a **point-estimate working goal**:
  it is met when observed UCR is at least 92.6% over an evaluation window of
  at least 200 eligible runs.
- **Confidence, shown for information.** The dashboard also shows the 95%
  Wilson lower bound. For reference, a lower bound of 0.926 would need
  **193 of 200** clean runs (96.5% observed), or **287 of 300** (95.7%).
  At an observed rate of exactly 92.6%, the true rate could be lower. The
  dashboard says so instead of hiding it.
- **Per domain:** at least 60 eligible runs each before a per-domain number
  is published.
- **Fault-injection suite** (each one runs as a test, then as a scheduled canary):

| # | Inject | Pass condition |
|---|---|---|
| F-1 | Kill the helper mid-step | Lease fenced, retry from checkpoint, no duplicate artifact |
| F-2 | Step timeout | Expires to retry or blocked, with reason shown |
| F-3 | Provider quota (429 or exhausted) | Checkpoint kept, fallback respects host, no lost work |
| F-4 | GPU held by a foreign process | Waits with a reason, zero model spend while waiting, resumes |
| F-5 | Credential expired mid-run | Guided sign-in card, resumes, no secret in logs or UI |
| F-6 | Gateway restart mid-dispatch | Outbox replays, no duplicate job (`create_with_id`) |
| F-7 | Duplicate delivery (the same job delivered twice) | One effect, second returns the existing receipt |
| F-8 | Network drop after a paid POST | Effect `uncertain`, reconcile, no second charge |
| F-9 | Reviewer is the same identity as the author | Refused by the engine |
| F-10 | Result envelope shape variant (PR #153 class) | Strict decoder accepts both, rejects prose |
| F-11 | Approval on artifact v1, then v2 produced | Approval void for v2 |
| F-12 | Step targets a `testing` host | Preflight refuses, with a plain reason |

---

## 8. Phases (details in the backlog)

| Phase | Outcome | Smallest useful slice |
|---|---|---|
| **P0** | Land in-flight Score fixes | Merge or refresh PR #153 (envelopes) and PR #148 (`pr_url`). Both are `behind` main. |
| **P1: Coding vertical** | *Fix a bug* template: Ask, then Score, then draft PR, with the status ladder and review on phone | Ask writes a Score instead of plain jobs. Add push and open-PR as an effect. Allow multiple attempts. |
| **P2: AI Ops workspace** | Hosts, capability probe, queue with reasons, resource arbiter wired in, takeover grant | Explicit `host_id`. Probe for Beast. GPU as a fenced resource. |
| **P3: Media vertical** | *Narrate a script* (ElevenLabs) and then *Character art* (ComfyUI), with previews and bounded correction | Effect ledger with reconciliation. Artifact store over 16 MB. Paid budget reservations. |
| **P4: Cross-domain** | Project-level typed artifact handoff between Flows; separate publish Flow | Input-changed detection. |
| **P5: Measurement** | Fault suite as canaries, and a UCR dashboard | 200-run evaluation. |

---

## 9. Owner decisions (recorded 2026-10-18)

Joe answered these in the review conversation. Where his answer left a detail
open, the detail is marked **[assumption]** and can be changed in review.

| # | Decision | What it changes |
|---|---|---|
| 1 | **92.6% is a point-estimate working goal.** | §7.3: the goal is met when observed UCR ≥ 92.6% over the evaluation window. The Wilson lower bound is still shown on the dashboard for information. It is not the bar. |
| 2 | **Mix and supported configuration as proposed.** Joe left this choice to the designer. | §7.1 values stand: Coding 40%, Media 40%, AI Ops 20%. Supported configuration as listed. Each domain is still reported on its own. |
| 3 | **Five places: Home, Projects, Library, Operations, Settings.** The bar: *it must not look or feel overwhelming.* | §3 stands. Every new screen is reviewed against that bar. When in doubt, a control goes behind "Details" or Advanced. |
| 4 | **Takeover grants are issued by the Chief seat, then the Deputy Chief seat, then Joe**, in that escalation order. **Grokbot holds the Deputy Chief seat** (decided 2026-10-22). | §6.4. |
| 5 | **Spend caps at both levels**: an overall organization cap and a cap per project. | §6.5. Builds on the per-project caps that already exist in `cio_policy.SPEND_CAPS_CENTS` (`src/mco/orchestrator/cio_policy.py:28-34`). |
| 6 | **The final publish step has a human gate by default.** It can be bypassed only by a specific setting in the BitCadence Settings GUI. | §6.6. |
| 7 | **A paid step in an Experimental flow needs an initial approval. That approval sets a budget for further runs of that flow.** | §6.7. |

Still open. Joe didn't address these, so the earlier recommendation stands as an **[assumption]**:

- Which media checks hard-fail. Assumed: loudness, duration and resolution.
- Whether the Gemini ear review is required. Assumed: required before publish, advisory for drafts.
- The longest a takeover grant may last. Assumed: 4 hours.
