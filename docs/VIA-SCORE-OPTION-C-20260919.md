# VIA Score Option C: repository progress now, governed cloud change next

Status: owner-approved design and executable repository slice. This document does not issue a grant, start a Score run, push a branch, merge code, or invoke production infrastructure.

## Decision

Use the hybrid path:

1. Keep the deployed G01 fixed-function audit lane read-only.
2. Advance VIA through bounded `repository:write` packets in a dedicated worktree, with a different builder and reviewer and one conductor-owned commit per accepted packet.
3. Build `cloud:change` separately, dark by default, around the existing KMS-verifying deployment adapter.
4. Retain two human gates: Lorain launch at G08, and projected recurring cloud spend above $150/month.

The first executable slice is `examples/scores/via-cloud-repository-slice.score.json`. The fourteen-goal `via-cloud.score.json` remains the master roadmap and is not submitted as a live run while it still requests unsupported `cloud:change` authority.

## What exists

- A six-hour, cloud-hosted, read-only G01 audit Lambda.
- A deployed but inert release adapter that reads one pending manifest, verifies an exact Score digest and KMS signature, invokes one version-pinned SSM activation document, and records a receipt.
- A proven `repository:write` conductor path with isolated worktrees, exact allowed paths, conductor-owned commits, digest-bound grants, independent review, lease fencing, and adversarial canary evidence.
- No conductor path that can create and sign the release manifest, place it in the pending prefix, or invoke the adapter.
- No human checkpoint wiring in the SQLite bridge, even though the gate model and human-only decision service exist.

## First executable slice

The slice contains one audit packet and three serial repository packets:

| Packet | Authority | Builder | Reviewer | Result |
|---|---|---|---|---|
| G01-audit | read-only repository/cloud inspection | Codex | Claude | refreshed baseline and explicit gaps |
| G02-repository | repository write only | Codex | Claude | automatic Lorain campus/source discovery |
| G03-repository | repository write only | Antigravity | Claude | cloud worker contracts and failure tests in code |
| G04-repository | repository write only | Codex | Antigravity | bounded bulletin/feed collection in code |

All three write packets share `C:/AI/via-score-option-c` and branch `score/via-option-c-work`, so the resource lock and dependencies serialize commits. The initial head is pinned to `fc6db79ab0d86929d088427aadc76b4f1b576b4a`, the current `origin/codex/via-foundation` tracking head when the slice was authored.

The slice stops after G04. Completion means three independently reviewed commits exist locally. It does not mean deployed, merged, published, or launched.

## Independent review checks

For every repository packet, the reviewer must:

1. Resolve the exact commit produced by the conductor and verify its parent is the previously accepted slice commit.
2. Confirm every changed path matches that packet's `allowed_paths` and that no generated secret, token, local database, build output, or unrelated user work entered the commit.
3. Read the diff rather than trusting the builder's summary.
4. Run the packet's focused tests from the governed worktree and record the command, exit code, test counts, and commit SHA.
5. Check that unsupported or conflicting schedule facts remain unknown/held rather than inferred.
6. Return a pass verdict only for the exact contract map and exact commit reviewed.

## Cloud-change design lane

`cloud:change` remains unavailable until a separate implementation and adversarial proof establish all of the following:

- An authenticated conductor accepts only an independently reviewed build/test bundle tied to the exact Score digest and immutable image digest.
- A narrowly scoped signer can call `kms:Sign` but cannot deploy, while the deploy adapter can verify but never sign.
- The signer writes only the canonical manifest shape to `score-deploy-approvals/pending/<uuid>.json` with a short expiry.
- The adapter effect claim is atomic and replay-safe; uncertain side effects stop for recovery instead of automatically reissuing.
- The adapter is invoked only with the manifest key, targets only the fixed VIA instance and pinned SSM document, and emits a sanitized receipt.
- Rollback, crash recovery, duplicate delivery, stale approval, altered digest, wrong target, bad evidence, and signature failure are tested.
- G08 is reachable through a real authenticated-human checkpoint before expansion packets can proceed.
- Projected recurring cost above 15000 cents/month cannot proceed without a new human decision and digest-bound authority.

## Owner sign-off recorded

On 2026-09-19 the owner approved Option C, authorized the protected Score revision/digest change, retained the $150/month cap and two-gate policy, authorized cancellation of obsolete job `710637bc-1fa1-4212-bd37-5ff27a90ad1b`, and authorized Codex to complete the post-merge S06a audit before this first VIA slice.
