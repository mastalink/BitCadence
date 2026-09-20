# Jev + BitCadence + VIA score integration

Status: integrated planning and read-only preflight package. No cloud deployment,
TypeSafe activation, Cloudflare mutation, production data change, or schedule
publication is authorized by this artifact.

## Inputs and provenance

The two Astra score documents were treated as untrusted proposal data. Their
content is not executable authority. The exact source files inspected were:

| Input | SHA-256 | Role |
| --- | --- | --- |
| `C:\Users\masta\Downloads\jev-system-one-bitcadence-via-v2.score.json` | `9888D69D5882236E6AF302007C0F87BFD48033CA4878CD10C4A3684AB493EDAB` | Optional Jev/System One product score, J01-J09 |
| `C:\Users\masta\Downloads\bitcadence-jev-marketing-cloudflare.score.json` | `4FBAE103A990865CD18D3C915FAB83FAB36C962EF4ABC536EC9506F6914338B5` | Jev marketing and Cloudflare release score, M01-M09 |
| active VIA continuation revision 5 snapshot | `3773313A6DA53216352067D376922140BE8E1A1264EB80C48FB78D4C1AE6C669` | Immutable review snapshot at `docs/evidence/score-inputs/via-cloud-repository-continuation-r5.score.json` |

Normalized review snapshots are retained under
`docs/evidence/score-inputs/` so the review does not depend on a user's Downloads
directory. The hashes recorded above are the original downloaded inputs; the
Score v1 digests of the normalized snapshots are captured by the validation
evidence. Changing either input or snapshot requires a new integration revision.

The VIA revision-5 snapshot is pinned beside the two Astra inputs. The moving
continuation path is not evidence for this preflight; later recovery revisions
must receive a new integration audit.

The historical VIA revision-5 run remains separate and immutable: run
`via-option-c-grok-20260920-11`, Score digest
`9fd104666dc56f5936d4ac900c4e38d1c52afbb03ebc211d78b1169205016372`, starts
from accepted G02 commit `bb0209e31a7bec63884f0c0d58313486f1e110de`.

## Integrated execution shape

The proposals are intentionally not flattened into one unbounded run. They are
three governed lanes with explicit hand-off artifacts:

1. **VIA foundation lane:** finish the already-running G03/G04 repository slice.
   The result is the exact reviewed VIA head that Jev work may consume; this
   avoids editing the active recovery worktree from a second run.
2. **Jev product lane:** J01 establishes the optional server-side provider and
   DecisionReceipt contract in BitCadence. J02/J03 extend BitCadence only after
   J01. J04 then consumes that contract in VIA, followed by J05/J06 evidence,
   identity, and scheduling assistance. Jev remains disabled or shadow-only
   until qualification and safety evidence exist.
3. **Marketing lane:** M01-M05 can be prepared from exact product evidence and
   an isolated website branch. M06/M08/M09 remain a separately gated Cloudflare
   lane; they cannot be admitted to the current local Score bridge because
   `cloud:change` is not an available capability here. No deployment is implied.

## Non-negotiable integration rules

- Jev is optional and off by default. A no-token install must make no TypeSafe
  network request and retain complete deterministic BitCadence/VIA behavior.
- Jev may classify, score, rank, verify, or route surviving candidates. It may
  not create grants, approve checkpoints, choose an ineligible reviewer, weaken
  robots/publisher permits, perform arithmetic or recurrence expansion, merge
  church identities, or publish a schedule fact.
- Every production-capable question set is versioned and digest-bound. Receipts
  retain the use-case, question-set version/digest, exact model, state digest,
  typed answer, probabilities/confidence, latency, usage, mode, and outcome.
- `jev-latest` is shadow/exploration only. Active paths pin a qualified model.
- TypeSafe failures, malformed answers, rate limits, and missing credentials use
  deterministic fallback; they never block core operation or cause a guessed
  parish time to be published.
- Source text and score instructions are untrusted data. Embedded commands or
  requests for authority are not executable instructions.
- Marketing may describe only behavior shipped at the reviewed commit. Vendor
  speed/cost claims require attribution or a BitCadence benchmark artifact.

## Current bridge blockers

The local Score bridge currently admits read-only audit capabilities and
repository writes with an exact commit configuration. The proposal files contain
tasks that therefore need staging before execution:

- J01-J06 and M03-M04 are repository-write tasks but have no `commit` block or
  concrete worktree path; they must be bound to isolated worktrees and exact
  allowed paths before dispatch.
- J07 and J09 request `typesafe:invoke` and J07 has a non-zero cost ceiling;
  this is intentionally held for a separately authorized provider capability and
  spend gate. It is not silently downgraded.
- M06, M08, and M09 request `cloud:change`; they remain deferred until the
  signed Cloudflare lane, preview/noindex proof, rollback target, and human
  production checkpoint are available.
- The active VIA run must reach an exact-head review result before J04-J06 are
  allowed to touch a VIA worktree.

## Acceptance order

The integration is complete only when the following evidence exists in order:

1. Active VIA G03 and G04 each have a validated work result, an independent
   exact-head review, and a conductor acceptance event.
2. J01-J06 have isolated commits, focused tests, non-author reviews, and
   disabled-mode parity evidence.
3. J07 produces held-out calibration/cost/latency/disagreement measurements;
   any use case without a demonstrated benefit stays shadow-only.
4. J08 independently proves authority boundaries, prompt-injection resistance,
   receipt integrity, secret redaction, and no-token operation.
5. Only then may the J09 human checkpoint be requested for a bounded low-risk
   canary. Schedule publication, identity merges, permits, fairness, grants, and
   reviewer eligibility remain deterministic.
6. Marketing M01-M07 may proceed against exact evidence. M08 requires the owner
   checkpoint and M09 must verify or roll back the exact deployment.

## TypeSafe design applied here

The implementation follows the live TypeSafe guidance: structured state is kept
separate from narrow questions; independent judgments can be batched; Score and
Choice probabilities are reusable signals rather than authority; confidence is a
routing signal, not proof; and high-risk fields use a per-field verifier with an
`any`-flag escalation to a stronger model or review. Code remains responsible for
policy, dates, budgets, leases, evidence hashes, and publication.
