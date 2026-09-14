# BitCadence Score v1

Status: implemented **offline contract, compiler and sandbox policy kernel**. Not an installed conductor, authenticated gateway endpoint, durable run store, or live agent dispatcher. Source base: cc01c60a7a44cb522c334ba42b886f3ce37cad90. Worktree: C:/AI/baton/wt/score-v1, branch codex/score-v1. No live jobs, database migrations, service restarts or cloud changes were performed for this prototype.

## Decision

A score is a versioned JSON contract for an outcome, its dependency graph, ownership, authority requirements, evidence, retries, explicit human checkpoints and launch criteria. The conductor advances it automatically. The person chooses policy and checkpoints up front; they are not a recurring task dispatcher.

Reuse existing BitCadence workflows, routing, lease fencing, audit and job board. **Do not submit a rich score through the legacy workflow endpoint:** it does not enforce evidence acceptance, score-bound authority, independent authorship, resource locks or atomic whole-run submission. Silently dropping those rules would be worse than rejecting the score.

## What exists now

- `src/mco/orchestrator/workflows.py`: YAML dependency graphs and per-step job submissions, workflow context stamps, retries and approval fields. Submission can partially succeed; a retry generates another run ID.
- `client.py`: authenticated gateway job submission and role/instance targeting.
- `handlers.py`: job transitions and dependent-job unlock; completion is not a product acceptance certificate.
- `leases.py`: lease/epoch/store-incarnation fencing. Reuse it; do not invent a second worker lease system.
- `routes.py`: approval routes require configured approver roles/scopes. A score's human-only gate needs a verified human principal, not merely any configured agent role with approval scope.
- `audit.py`, `evidence.py`, `drumline.py`: audit/evidence and shared workflow context. These do not establish that arbitrary attached test reports are semantically valid.

These are inspected code capabilities, not assertions of the deployed gateway revision or production readiness. Existing governance/cloud acceptance issues remain separate.

## New artifacts

`docs/score-v1.schema.json` is the structural JSON Schema. `load_score()` additionally checks duplicate JSON keys, whitespace-only values, unique IDs, known dependencies, acyclic graphs and distinct work/review roles. It accepts JSON text or objects, never a path supplied by a remote caller. The explicit offline CLI reads a file.

`compile_score()` produces deterministic, digest-bound work packets, preserving every task contract. Its output deliberately has no legacy `steps` property and declares `live_submission_supported: false`. No arbitrary command executor, network client or credential loader is included.

`SandboxRun` exercises the intended conductor policy without external effects: pending→running→review→accepted, bounded expiry/retry→blocked, dependency locks, shared resource locks through review, parallelism limits, external capability grants, conservative integer-cent budget reservations, explicit human gates, attempt tokens and score/run identity. Each event carries a sequence, run and score digest. Worker completion alone never unlocks a dependent task. Launch may be accepted while later expansion tasks remain planned.

**Sandbox trust boundary:** actor kinds, identities, grants, clock and evidence-verification results are caller-supplied trusted inputs. This is not authentication, cryptographic evidence verification or a production security boundary. Evidence values are structurally checked references, not fetched/hashes checked by this module. Events and state are in memory; durable restart recovery and concurrency require the adapter below. Do not expose these methods directly as worker-callable HTTP endpoints. Textual constraints are instructions until explicitly enforced by appropriate adapter policy.

## Usage

From the isolated worktree with its source on PYTHONPATH:

```powershell
$env:PYTHONPATH = 'C:\AI\baton\wt\score-v1\src'
& C:\AI\baton\Batoncadence\.venv\Scripts\python.exe -m mco.orchestrator.scores validate examples/scores/via-cloud.score.json
& C:\AI\baton\Batoncadence\.venv\Scripts\python.exe -m mco.orchestrator.scores compile examples/scores/via-cloud.score.json
& C:\AI\baton\Batoncadence\.venv\Scripts\python.exe -m mco.orchestrator.scores preview examples/scores/via-cloud.score.json
```

On another host use that environment's Python and checkout path. Output is JSON on stdout. Invalid files exit2. There is intentionally no submit or launch command yet.

## VIA's first score

`examples/scores/via-cloud.score.json` maps all14 goals from the existing VIA roadmap. G08 defines Lorain launch acceptance. Expansion G09–G13 and G14 operations follow their dependencies; G14 represents setup/one accepted operating cycle, with recurring cloud operations configured as its deliverable—not a job claiming to finish forever.

No human checkpoint was invented. `checkpoint: null` means no score-authored human gate; external authority is still required. The budget is zero until measured limits are authorized. A zero budget is not a prediction of free operation, and agents cannot label chargeable work free to bypass accounting. Production actions are disabled in this prototype regardless of the example's requested capability strings.

G01 requests read-only repository/cloud inspection; later goal packets conservatively request repository write/cloud change and share a `via-release-lane` lock. This serializes them until G01 scopes real resources. These are coarse goal packets, **not a final fine-grained launch graph**. Before live execution, decompose each into bounded work/test/review/merge/deploy/observe packets, with least-privilege capabilities and exact file ownership. In particular, G07 needs separate API and Android lanes; cloud changes must not share a blanket grant with read-only testing.

The accompanying VIA package remains the acceptance authority. Runtime constraints do not permit manual schedule entry, local production dependencies, fabricated coverage or silent threshold changes. Human engineering acceptance at a chosen checkpoint is distinct from requiring a person to approve every parish claim.

## Remaining implementation, in dependency order

1. **S01: durable score store and version authority.** Add reviewed migrations using the actual migration head. Persist score document+digest, run ID, organization, authorized policy snapshot, task attempts and resource/budget reservations. Idempotency key unique per tenant/run/task/attempt. Commit planned dispatch and state transitions in one transaction. A changed score requires a new digest and explicit migration of unresolved work; old approvals cannot transfer automatically.
2. **S02: dispatcher/outbox bridge.** Only conductor-owned accepted states unlock tasks. Outbox writes existing job-board work with score/run/task/attempt stamps; deduplicate retries across timeout and uncertain acknowledgements. Reconcile created jobs after restart instead of resubmitting blindly. Existing job leases fence worker updates; score leases fence competing conductors. Resolve capabilities to online eligible identities, observe lease/presence, reassign only after safe expiry. No worker can advance the score by writing `completed` alone.
3. **S03: verified evidence and non-author review.** Fetch artifacts through allowed locations, verify digests, bind tests to exact head/build/deployment, reject skipped mandatory suites and author/reviewer overlap including re-applied commits. Server selects reviewers from identity/contribution records. A claimed `verified_evidence=true` in worker output is never sufficient. Ingest code review and test receipts as separate authenticated events.
4. **S04: policy and human gate UI.** Signed/authenticated owner-approved grants scoped to run digest, actions, resources, environments, time and budget. One explicit human gate view shows decision/evidence and approve/reject. No artificial per-task approval queue. Agent approvers cannot impersonate human principals. Missing authority pauses only the affected path. Authorizations and checkpoint approvals are separate records.
5. **S05: launch/recovery adapters and quiet operation.** Reuse repository/build/deploy tools through allowlisted adapters; protected isolated worktrees; before/after receipts; rollback and compensate known partial effects. Differentiate uncertain effect from retry-safe failure. Recheck desired deployment state before replaying deploy/notify operations. Conductor runs in approved cloud infrastructure. Notify only for chosen human gates, irrecoverable owner-action blockers or milestone completion; dashboard holds routine updates.
6. **S06: VIA executable decomposition and cloud canary.** Resolve G01 facts, pin/upload the accompanying plan artifacts, replace coarse capabilities/locks with narrow work packets, validate actual role availability and budget. Execute a no-production-effects score with a worker crash, duplicate delivery, rejected review, human gate, restart and completed evidence trail. Then a scoped cloud canary. Only after independent review and acceptance enable authorized VIA work through launch.

Each item should be one bounded AI work packet with a different reviewer. Database/policy interfaces land before concurrent adapter implementation. No new agent is claimed assigned or running by this document. Expected next delivery is S01+S02 on the existing job board, not a second orchestration product.

## Adapter acceptance matrix

Required before live use: unknown fields/cycles rejected; worker cannot forge actor or human status; cross-tenant and stale-digest receipts rejected; author aliases cannot self-review; expired grant fails closed; concurrent budget/resource reservations atomic; duplicate dispatch reconciled; lease expiry rejects stale writes; crash-before/after side-effect recoverable; restore invalidates old incarnation; no unsupported evidence accepts; gate rejection remains terminal until an explicit new decision; changed score invalidates scoped approvals; no launch on mere merged code; production rollback and notification idempotence tested.

The current unit tests cover only the policy subset stated above. They cannot prove authenticated actor identity, persistence, a cloud deployment or actual independent code review.
