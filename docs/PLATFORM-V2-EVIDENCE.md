# Platform v2: current-state and gap matrix

**SHA:** `62bb5c59dc75560af4de7d5082ab5a350ed09f0c` (`main`, 2026-10-14 21:00 -0400). Read 2026-10-18.
Paths are relative to the repository root. Line numbers refer to that SHA.

**What the statuses mean:**

- **exists**: the source implements it and the repository has tests or receipts for it.
- **partial**: the source implements part of it, or only for a narrow scope.
- **planned**: a document describes it, but no implementation exists.
- **unverified**: the source exists, but there is no evidence it works end to end on the fleet.
- **missing**: nothing in any authorized repository.

This design ran **no code against the fleet**. Every "runtime" column entry
comes from repository documents or PR text, and its source is named.

## A. Engine (Score, job board, leases)

| Capability | Status | Source evidence | Runtime evidence / unknowns |
|---|---|---|---|
| Declarative step DAG (legacy workflows) | exists | `src/mco/orchestrator/workflows.py:48-99` (validate), `:102-121` (topo), `:124-166` (submit as jobs with approval, retries, escalation) | Plain jobs only. No typed I/O, no review step. |
| Score v1 contract | exists | `src/mco/orchestrator/scores.py:69-158`: strict fields, independent `review_role` (`:98-99`), `on_reject` chains (`:100-145`), `commit` gated on `repository:write` (`:113-124`) | `docs/SCORE-V1.md`, `docs/SCORE-VERIFICATION.json` |
| Typed step input/output ports | **missing** | Tasks have `evidence` *names* only (`scores.py:88`, `:104-105`). No types or schemas. | n/a |
| Blocker reasons (why a step waits) | exists (sandbox) | `scores.py:250-285`: dependencies, authority, human_checkpoint, budget, parallel_limit, resource_lock | Not shown in the console's plain copy |
| Live Score bridge | partial | `src/mco/orchestrator/score_bridge.py:1-5`. **Refuses** `budget_cents` (`:143-144`), `max_attempts != 1` (`:146-147`) and `max_cost_cents` (`:148-149`) | Read-only audit and repo-write canaries recorded in `docs/SCORE-S06A-LOCAL-CANARY-20260916.md`, `docs/SCORE-C04-GATEWAY-ONLY-CANARY-20260916.md` |
| Conductor tick + sweep | exists | `score_conductor.py:1-20`, `score_sweep.py` (docstring); timer in `health.py:52-56`, off unless `MCO_SCORE_SWEEP_SECONDS` is set | Unknown whether the sweep is enabled on the fleet |
| Transactional outbox dispatch | exists | `score_dispatcher.py:1-6`, `:39` (`score_job_id`, deterministic) | `docs/COMPLETION-2026-09.md` F10-F11 |
| Fenced leases (one owner per attempt) | exists | `leases.py:1-25` (F1-F6), `:132` acquire, `:208` fenced_update | `docs/COMPLETION-2026-09.md`: SQLite and Postgres acceptance |
| Attempt idempotency key | exists | `leases.py:255-256` | "External effects still require the downstream service to honor the key" (`leases.py` F6) |
| Delivery watchdog, pinned and same-host reroute | exists | `delivery.py:1-20`, `:168-172`; PRs #154, #155 merged | Merged 2026-10-14 |
| Execution host as a field | **partial** | Inferred from instance-id spelling, fails closed on unknown prefixes; "registry rows carry no host field" (`delivery.py:155-165`) | Model, role and host are conflated in identity strings |
| Remote execution (Mac to Beast over SSH) as a capability | **missing** | No model of it in source | The owner reports it works for some workers. Needs an end-to-end proof (checklist C-5). |
| Host release state (Mac Studio "testing") | **missing** | n/a | Owner policy, not encoded |
| GPU or resource exclusivity | partial / unverified | `resource_arbiter.py:1-30` (priority queue, hard daily cutoff), CLI `:281-320`. **No importers** in `src/` | PR #125 merged. Not wired to leases or Score. |
| Worker presence states | exists | `presence.py:1-20`: working / broken / standby / offline, derived from leases and stalls rather than heartbeat alone | Directly addresses "heartbeat ≠ working" |
| Job statuses | exists | `contracts.py:5-16` | No "artifacts verified / reviewed / approved / delivered" rungs at job level |

## B. Authority, review, evidence, policy

| Capability | Status | Source evidence | Notes |
|---|---|---|---|
| Digest-, run- and tenant-bound grants | exists | `score_authority.py:83` (`require_grant`), `:157` (`GrantService`), signature `:30-45` | |
| Human-only gate decisions | exists | `score_policy.py:59-67` (session or trusted-header only; agent tokens refused) | |
| Owner policy | **partial (VIA-specific)** | `score_policy.py:27-32` (`VIA_OWNER_POLICY`), `:35-48` hardcodes task id `G09` and a $150 cap | Needs generalizing per Project |
| Independent review enforced | exists | `scores.py:379-393` (author ≠ reviewer, also across the `on_reject` lineage) | |
| Server-side evidence verification | exists (code) | `score_evidence.py:1-6`, `:61-99` (allowlisted locations, digest match), `:172` requires `test_receipt` + `code_review` | 16 MB artifact cap (`:64`). No media evidence kinds. |
| `pr_url` evidence | planned (PR open) | PR #148 (`fix/score-pr-url-evidence` @ `1eba603`), `behind` main | |
| Result envelope shape fix | planned (PR draft) | PR #153 (`fix/score-envelope-validation-retry` @ `823db42`). The PR body reports 120 focused tests passing on its head. | Addresses the "review envelope shape" incident. Not merged; `behind` main. |
| Cancel / reassign authority | exists (role-only) | `routes.py:1300-1310`, `:1466-1479`: approver *role* required | Explains the 403 incident. No delegated, run-scoped takeover. |
| Takeover grant | **missing** | n/a | Proposed in design §6.2 |
| Approval bound to an artifact version | partial | Gates are bound to `(org, run, digest, task, kind)` (`scores.py:318-321`) but not to an artifact sha256 | |

## C. Effects and connectors

| Capability | Status | Source evidence | Notes |
|---|---|---|---|
| Effect result states incl. `uncertain` | exists | `score_adapters.py:38-41`, `RecoveryRequired` `:34` | |
| Allowlisted adapters | exists (dry-run) | `score_adapters.py:1-6`, `:110-125` (preview-only allowlist), `:203` refuses non-dry-run | "Live repository mutation, builds, deploys, and notifications remain unsupported" (`:5-6`) |
| Live git worktree commit | exists | `score_adapters_live.py:1-20`; denylist `:56-66` (main, deploy/local, production, release, `release/*`, `production/*`) | Push and PR creation are not adapters |
| Score v2 extension points | exists (protocols) | `score_extensions.py:52` `ScoreStore`, `:86` `EvidenceVerifier`, `:107` `LaneAdapter` | Natural home for connectors |
| Connector manifest / registry | **missing** | `src/mco/connectors/` holds ServiceNow and Dynatrace ingest connectors, not effect connectors | |
| ComfyUI connector | **missing** | No match for `comfy` in any authorized repo | Documented API is enough (design §5.1) |
| ElevenLabs connector | **missing** (source) | PR #156 only loads `ELEVENLABS_API_KEY` into a worker's environment | A worker can call it ad hoc. It is not a governed effect. |
| Publish / deploy effects | **missing** | Dry-run previews only (`score_adapters.py:119-125`) | |
| Paid-effect budget reservation | partial (sandbox only) | `scores.py:199-200`, `:278-279`, `:358`. The live bridge refuses budgets (`score_bridge.py:143`) | |
| Usage / capacity signal | planned | `docs/USAGE-CAPACITY-SIGNAL.md` (U01 contract). No source implementation found. | |

## D. Product surface (console)

| Capability | Status | Source evidence | Notes |
|---|---|---|---|
| Console build path | exists | `scripts/build_console.py:1-21` (`extract` / `build` / `verify`); sources in `src/mco/console_src/` mapped by `index.json` | Edit the sources, then run `build`, then commit both |
| Navigation | exists (12 items) | `src/mco/console_src/8ec84a72-…js:4-17` | |
| Plain / expert tone + Advanced toggle | exists | `8ec84a72-…js:71-74` (`PAGE_TITLES`), `:79` (`advanced` state) | Reuse for progressive disclosure |
| Ask for something | exists, **not Score-backed** | `src/mco/ask_plan.py` (docstring), `src/mco/orchestrator/ask_routes.py:43-60` creates plain jobs | The central gap |
| Live run view | exists (read-only) | `43b328d0-…js:635-668`, polls every 10 s (`:645`); API `score_gate_routes.py:241-246` | Shows "Work/Review, round N" but no artifacts, previews or ladder |
| Flow editing beyond light tweaks | **missing by design** | `design/redesign-v1/STEP-COUNT.md` rows 5-6 (builders removed); `ask_plan.py` "light tweaks only" | v2 proposes an outline editor, not a canvas |
| Artifact previews and version compare | **missing** | n/a | |
| Mobile review | partial | Redesign v1 phone tab bar and evidence captures under `design/redesign-v1/evidence-s*/` | Approve exists. Compare and comment do not. |

## E. Media domain

| Capability | Status | Evidence |
|---|---|---|
| Script, art, narration, edit, music pipeline | **missing in GitHub** | No pipeline source in BitCadence, MosesAI, Mose, sim-lab, via, moses-AI-v3 or skills. `website/media/*.mp4` are marketing videos. |
| Character, voice and brand library | **missing** | n/a |
| Gemini ear-review relay | **missing in GitHub** | `docs/runbook/GEMINI_RELAY.md` is not tracked |
| Backup builder runbook | **missing in GitHub** | `docs/runbook/BACKUP_BUILDER.md` is not tracked |

## F. Known incidents, mapped

| Incident (from the handoff) | Root cause in source | Status |
|---|---|---|
| Cancel/reassign 403 despite owner takeover | Role-only check, `routes.py:1309`, `:1478` | Design §6.2 (Takeover grant). Not built. |
| Review envelope shape mismatch | Bridge decoded only `output_payload.result` (PR #153 text) | PR #153 draft, unmerged |
| Heartbeat or "Running" mistaken for work | Heartbeat-only status | `presence.py` fixes this for workers. Runs still lack the ladder (design §1, item 5). |
