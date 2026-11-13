# Standing Score grants (local authority store)

An operator can approve a reusable Score class once, then start runs from that
class without issuing another run grant. A class is the **exact canonical Score
digest**: constraints, budget, capabilities, resources, instructions, checkpoints,
and commit configuration are all bound. A different run ID is allowed; any change
to the Score needs a fresh approval. There are no wildcard classes.

Use the existing interactive Windows local-human approval boundary with
`MCO_LOCAL_HUMAN_GRANTS=true` and a configured Score signing key. Agent bearer
tokens and noninteractive terminals cannot issue or revoke local standing grants.

```powershell
mco score grant-standing-local examples/scores/score-human-gate-canary.score.json --expires-hours 24
# Inspect the displayed policy and type the exact confirmation phrase once.
# Keep the returned grant ID for unattended start commands.
mco score start examples/scores/score-human-gate-canary.score.json --run-id class-run-001 --standing-grant GRANT_ID --target score-canary-worker=builder --target score-canary-review=reviewer
mco score tick --run-id class-run-001 --watch

# One command, no second confirmation phrase:
mco score revoke-standing-local GRANT_ID
```

Replace `GRANT_ID` with the issued ID and `builder`/`reviewer` with registered
instances for the example's worker/review roles. The example deliberately keeps
an explicit human checkpoint to demonstrate that standing authority preserves it.

Keep the existing conductor sweep for recurring progression. No new daemon or
scheduled process is installed. `--live-repository-write` is still required on
every tick for a repository-write Score. Existing independent review, branch
denylist, allowed-path restrictions, resource locks, evidence checks and human
checkpoints still apply. Standing grants never approve checkpoint decisions.

Standing authority is restricted to `repository:read`, `repository:write`,
`evidence:write`, `evidence:review`, and `cloud:inspect`, and to `test`, `development`,
or `staging` environments. Unknown capabilities are refused. Spending, public
publishing and production deployment are outside standing authority and require
separate human approval. Capability names describe the enforced adapter contract;
untrusted worker text cannot grant authority.

The budget ceiling is per run and bound by the Score digest. It is not a money
movement authorization or an aggregate spending allowance. The current local
bridge continues to refuse paid execution. Expiry is mandatory, at most 316 hours,
with a default of 24 hours. Both not-before and expiry are checked at use time.

Run startup with `--standing-grant` checks the exact class before creating the run.
The selected grant ID is stored with the run; every plan, dispatch (including
each job submission), and poll checks it again, including after process restart.
The authority service also checks the current source grant on every adapter use.
Revocation blocks future checks, including for previously started runs. Already
running commands are not cancelled; an effect that has passed its authority check
may finish. A blocked conductor run is not automatically resumed by a new grant.

New runs select the newest approval of an identical class. Every run is pinned
to its original source grant in the authority store on first successful use;
later approvals cannot revive a run whose original authority was revoked. A
revoked newest approval never falls back to older authority for a new run. A
separately approved, explicit run grant retains precedence and its own scope;
start such a run without `--standing-grant`. Standing-derived run grants are
ephemeral and cannot be copied into the durable run-grant table.

The local store lazily creates append-only `score_standing_grants`,
`score_standing_revocations`, `score_standing_runs`, and `score_standing_events` tables. Signed approvals
and revocations persist independently of conductor databases. Audit events cover
issuance, authorization, denial, and revocation, with grant/run/digest bindings,
timestamps, principal, and requested action/resource/environment/cost. Approval
and revocation records commit atomically with their audit event; audit write
failures prevent authorization from succeeding. Store write access is a trusted
boundary, as with existing run grants. The global STOP switch prevents standing
issuance and use; revocation remains available and never clears STOP.

This release supports the local SQLite authority store only. Remote authority
stores keep the existing explicit run-grant flow; no cloud schema or endpoint is
changed.
