# Platform v2: backlog, work packets and local verification checklist

Base: `main` @ `62bb5c59dc75560af4de7d5082ab5a350ed09f0c`. Design: [PLATFORM-V2-DESIGN.md](PLATFORM-V2-DESIGN.md).
Evidence: [PLATFORM-V2-EVIDENCE.md](PLATFORM-V2-EVIDENCE.md).

Every packet below is designed to be done independently:

- **One owner per packet.** Each packet lists exactly the files it may touch.
- **Its gate has to pass before it merges.** A gate is the specific test or proof that shows the packet works.
- **No packet dispatches production agents, spends money, deploys, or publishes.**
  Any live proof belongs to the owner or to the local Codex checklist (§3).

How to read each packet's estimate:

- **Effort:** S is under half a day of agent work, M is about a day, L is several days.
- **Reasoning:** the model reasoning level to use. Medium unless the packet says otherwise.

---

## 1. Smallest useful vertical slices

- **Code slice (P1a + P1b + P1c):** the *Fix a bug* template, typed from Ask,
  runs as a Score. It goes through worktree commit, then a test receipt, then
  an independent review, then a draft PR. The console shows the status ladder.
  Every one of these pieces exists today except three: Ask writing a Score,
  a PR effect, and the ladder UI.
- **Media slice (P3a + P3b + P3c):** the *Narrate a script* template turns
  approved script text into ElevenLabs narration. A technical check runs
  (duration, loudness, and no silence over 2 seconds), then human creative
  review, then one bounded correction. This is the smallest real paid effect,
  so it proves the effect ledger, reconciliation and budget reservations
  before any GPU work happens. ComfyUI character art comes next (P3d).

---

## 2. Work packets

### P0: Land in-flight fixes (owner action, not agent work)

| ID | What | Gate |
|---|---|---|
| P0-1 | Bring PR #153 up to date with main, review it independently, decide whether to merge | Its focused tests pass on the refreshed head |
| P0-2 | Bring PR #148 up to date with main, review it, decide | `tests/test_score_bridge.py` passes |
| P0-3 | Decide on PR #156 (worker runner ElevenLabs loader) | It does not echo the key; review the script diff |

### P1: Coding vertical

| ID | Packet | Touches | Gate | Effort |
|---|---|---|---|---|
| P1a | **Ask writes Score.** Add `ask_plan.to_score(plan, template)`, which builds a Score v1 with an independent `review_role` and evidence names. `/api/ask/start` starts a Score run when the template is Score-backed. Today's plain-jobs path stays as the fallback for "Experimental". | `src/mco/ask_plan.py`, `src/mco/orchestrator/ask_routes.py`, new `tests/test_ask_score.py` | The output passes `load_score()`. Reviewer ≠ author. No paid fields. Legacy Ask tests still pass. | M |
| P1b | **PR effect adapter.** `GitHubDraftPRAdapter` pushes the allowed branch and opens a *draft* PR, using an idempotency key in the PR body marker. Reconcile means finding an existing PR for the head branch. It refuses the denylisted branches from `score_adapters_live.py:56-66`. | new `src/mco/orchestrator/score_adapters_pr.py`, tests with `gh` faked (same pattern as PR #148) | Double submit produces one PR. A lost response reconciles. A denylisted target is refused. | M |
| P1c | **Status ladder in the Live view.** Map Score events to rungs: Accepted, Assigned, Working, Output checked, Reviewed, Approved, Delivered. Heartbeats never advance a rung. | `score_gate_routes.py` (`get_live_score_run` display fields), console source `43b328d0-…js` (`ScoreLive`), then `python scripts/build_console.py build` | A unit test covers the event-to-rung mapping. `build_console.py verify` passes. A screenshot at phone width. | M |
| P1d | **Multi-attempt in the bridge.** Lift the `max_attempts != 1` refusal (`score_bridge.py:146-147`). The retry reuses the checkpoint and gets a new fenced attempt token. | `score_bridge.py`, `tests/test_score_bridge.py` | F-1, F-2 and F-6 from the design §7.3 pass as tests | L, **High reasoning**: concurrency and fencing logic |
| P1e | **Template: Fix a bug**, stored as a versioned Library asset (JSON) | new `configs/templates/fix-a-bug.v1.json`, validator test | The validator plus `load_score` round-trip | S |

### P2: AI Ops workspace

| ID | Packet | Touches | Gate | Effort |
|---|---|---|---|---|
| P2a | **Explicit host.** Add `host_id` and `release_state` (`active`, `testing`, `retired`) to helper registration. `delivery.instance_host` prefers the field and falls back to today's parsing. | registry schema/migration, `delivery.py:155-165`, `presence.py` | Existing delivery tests pass. A new test: a `testing` host is never chosen for compute. | M |
| P2b | **Capability probe.** Each helper reports a manifest when it registers: models, tools, `remote_exec` targets, mounts, GPU and VRAM. No secret values are included. | `src/mco/sdk.py`, `orchestrator/admin_routes.py` | Schema test. Unknown fields are refused. Secrets are absent. | M |
| P2c | **Wire the resource arbiter to leases.** A task with `resources: ["gpu:beast"]` acquires the arbiter lease before the job lease, and releases it on any terminal state. | `leases.py`, `resource_arbiter.py`, `score_conductor.py` | F-4 test. A crash while holding the resource is cleared by the hard deadline. | L, **High reasoning**: cross-process locking |
| P2d | **Takeover grant.** The owner session grants helper X cancel and reassign rights for run R for N hours. It is audited and expires on its own. Pending jobs from the old owner are fenced. | `score_authority.py`, `routes.py:1300-1310`, `:1466-1479` | A worker without a grant still gets 403. With a grant, it succeeds inside the run. Outside the run, or after expiry, it gets 403. | M |
| P2e | **Operations UI.** Hosts, Queue (with blocker reasons), Resources, Spend (read-only first), Recovery | console sources, `build_console.py` | Phone and desktop screenshots. Every state is labeled in words. | L |
| P2f | **U01 capacity collectors** for Claude, Codex and ElevenLabs, following `docs/USAGE-CAPACITY-SIGNAL.md` | new `src/mco/capacity/` | Unknown stays unknown. No dashboard scraping. | M |

### P3: Media vertical

| ID | Packet | Touches | Gate | Effort |
|---|---|---|---|---|
| P3a | **Effect ledger.** Generalize `score_adapters` receipts to paid effects: `intended`, `submitted`, then `succeeded`, `retry_safe_failure`, `uncertain`, or `reconciled`. A paid effect requires a reservation. | `score_adapters.py`, new `score_effects.py` | F-7 and F-8 tests: one charge, never two | L, **High reasoning** |
| P3b | **Artifact store.** Content-addressed, larger than 16 MB, with a provenance manifest and `supersedes` links. The evidence verifier reads from it. | new `src/mco/artifacts/`, `score_evidence.py:64` | Hash mismatch is refused. A large file streams. Provenance round-trips. | M |
| P3c | **ElevenLabs narration connector** plus the *Narrate a script* template plus audio technical checks | new `src/mco/connectors_effects/elevenlabs.py` | Mocked API tests. Reconcile by request id. The checks catch silence and clipping. | M |
| P3d | **ComfyUI connector** (design §5.1). Preflight uses `/object_info`, `/models` and `/system_stats`. Submit and reconcile use `/prompt`, `/history` and `/queue`. Progress comes over `/ws`. Cancel uses `/interrupt`. | new `src/mco/connectors_effects/comfyui.py` | Tests against a recorded fixture server. Local probes C-1 to C-4 pass. | L |
| P3e | **Bridge budgets.** Lift the `budget_cents` and `max_cost_cents` refusals (`score_bridge.py:143-149`) behind generalized policy | `score_bridge.py`, `score_policy.py` | Spend over the cap pauses at a gate. Reservation survives a restart. | L, **High reasoning** |
| P3f | **Review UI.** Version compare, comments pinned to a timecode, bounded correction, artifact-bound approval | console sources | F-11 test. Screenshots. Keyboard-only and screen-reader pass. | L |
| P3g | **Ear-review pack.** Build a review bundle under 10 MB that keeps whole-film offsets. The step waits as "owner-triggered". | new template step, pack builder | Size and offset tests | S |

### P4 and P5

| ID | Packet | Gate |
|---|---|---|
| P4a | Typed artifact handoff between Flows in a Project, plus "input changed" detection | A downstream Flow refuses to start from a superseded input without confirmation |
| P4b | Separate *Publish* and *Release* Flows, each with its own gate | Approving one never satisfies the other |
| P5a | Fault suite F-1 to F-12 as scheduled canaries | Each canary reports its pass condition, not an HTTP status |
| P5b | UCR dashboard: denominator, exclusions, the six separate metrics, Wilson lower bound | It matches a hand-computed fixture |

---

## 3. Local verification checklist (for Codex on the owner's machines)

This design could not run anything locally. Each probe says exactly what to
run and what counts as a pass. **None of the probes generates paid media,
pushes, deploys or publishes.** Record the result next to the probe id, with
host, date and commit.

| ID | Probe | Pass condition |
|---|---|---|
| C-1 | On Beast, run `GET http://127.0.0.1:8188/system_stats` and `GET /object_info` (use the port actually configured). Save both JSON files into a scratch folder. Do not save any tokens. | Both respond. VRAM and device names are present. The node count is recorded. |
| C-2 | Compare the node classes used by the current production ComfyUI workflows with `/object_info`. List any custom-node packages and their git remotes and licenses. | Every class resolves. The license list is complete. |
| C-3 | Submit **one free, tiny** test graph (for example, an empty-latent decode at 64×64) with `extra_data: {"bitcadence_key": "probe-c3"}`. Then read `/history/{prompt_id}`. | Confirms whether `extra_data` is kept in history. This decides the duplicate-generation reconcile strategy. |
| C-4 | Run the C-3 graph again while holding a `/ws?clientId=probe` socket. Capture the message order. Then send `POST /interrupt` during a slightly longer free graph. | Messages match the design §5.1 table. Interrupt yields `execution_interrupted`. |
| C-5 | From each Mac worker, run SSH to Beast with `hostname && nvidia-smi -L`, and an SMB read of one known file on `K:`. | Records, per worker, whether `remote_exec` works and whether a storage read works, as two separate facts. |
| C-6 | Confirm the Mac Studio policy: list the inference and render services running there. | None are running while it is under test. |
| C-7 | `python scripts/build_console.py verify` on the current checkout | "verify OK". |
| C-8 | `python -m pytest tests/test_score_bridge.py tests/test_score_conductor.py tests/test_leases.py -q` (adjust if file names differ) | Pass, or a list of failures that also fail on `main`. |
| C-9 | Check whether `MCO_SCORE_SWEEP_SECONDS` is set on the running gateway (print the setting's name and whether it is set, never its value) | Records whether runs advance unattended today. |
| C-10 | `python -m mco.orchestrator.resource_arbiter --help` and a dry acquire and release on a scratch config | Works. Records the state-file location. |
| C-11 | For the ElevenLabs account, check whether the API returns a request id usable for reconcile, using a **free** endpoint (for example, list voices) and reading the response headers only | Records the header names, not their values. |

---

## 4. Test gates that apply to every packet

- The repository CI (`.github/workflows/ci.yml`, which runs `pytest tests/ -q`) must be green.
- Console changes: `build_console.py build`, commit both the sources and `console.html`, and `build_console.py verify` passes.
- New effect adapters: tests for double submit, lost response and denylist before any live use.
- Any change to approval, grant or lease code gets an independent review by a second identity before it merges.
