# Completion branch review response

Review baseline: `d3c5e52`. Remediation is on `codex/bitcadence-completion`.

| Finding | Disposition | Evidence or change |
|---|---|---|
| 1. Environment HMAC overrides vault | Fixed | Vault first; environment fallback requires `MCO_ALLOW_ENV_AUDIT_KEY=1`, warns without printing a key, and cannot bypass a locked vault. ECS explicitly opts in for Secrets Manager injection. |
| 2. Immutable OIDC subject invalid | Disproved | GitHub's repository API returns `sub_claim_prefix=repo:mastalink@72055896/BitCadence@1245844706`. AWS's live role used that exact prefix and candidate ref. GitHub introduced this default for repositories created after July 15, 2026. Replacing it with the proposed older format would break deployment. |
| 3. Spokes can overwrite secrets | Disproved | The conditional in the reviewed Terraform already restricts `PutSecretValue` to the hub. Fresh IAM reads confirm both worker and reviewer have only `GetSecretValue` for their own token and the shared CA; neither has secret-write permission. |
| 4. Desktop tests share the user's singleton | Fixed | Each test uses a real Windows mutex with a unique test name, temporary metadata/PID files and a free gateway port. The singleton conflict test still exercises two controllers competing for the same test mutex. The descendant test waits for complete PID contents, closing an observed code-level race; this is not proof it explains the reviewer's intermittent failure. |
| 5. Year-long default lock | Fixed | Both Python and Terraform now default to one day of COMPLIANCE retention. Invalid nonpositive retention is rejected. Existing retained versions are unaffected. Retention expiration permits deletion; it does not automatically delete objects or stop storage charges. |
| 6. Repeated whole-history S3 uploads | Fixed with explicit boundary | A versioned, locked acknowledgement in S3 persists the acknowledged count, head hash and retention deadline. Signed installations also HMAC-sign it. Only the appended suffix is uploaded until prefix retention needs renewal. A missing or stale receipt causes repeat publication; conflicting history or an invalid signature fails closed. Full-chain database reads and verification remain O(events), deliberately preserving detection of a corrupted prefix. Each changed boundary adds one checkpoint PUT; unchanged boundaries use one GET. |
| 7. Hash timestamp comes from shadow JSON | Fixed | Verification derives UTC with six fractional digits from the actual timestamp. `canonical_content` never supplies hash input. PostgreSQL already emits the same fixed format. A deterministic legacy whole-second encoding remains verifiable without consulting shadow JSON. |
| 8. Breaking audit behavior undocumented | Fixed | CHANGELOG explains failed audit/required evidence writes fail the operation, the database may already have committed, and callers must retry the same attempt/result. |
| 9. Deployment dies after branch merge | Fixed | Manual dispatch only, with exact candidate and `main` refs in both workflow and immutable OIDC trust. Remove candidate access after merge. |

Primary OIDC reference: [GitHub immutable subject claims](https://docs.github.com/en/actions/reference/security/oidc#immutable-subject-claims).

The lease module again explains F1–F6, restore rotation, atomicity, manual retry
and downstream idempotency. Public lease methods have type annotations. Shared
store transaction helpers now live below both audit and leases, eliminating the
audit module's dependency on the lease module.

## Infrastructure changes applied

The reviewed bootstrap plan changed exactly two resources, with no additions or
deletions: the deployment trust adds the exact `main` subject, and the hub gets
`ListBucket` on the evidence bucket so a nonexistent acknowledgement produces
`NoSuchKey` rather than an ambiguous authorization failure. Spoke permissions
remain unchanged. Bootstrap state was backed up to the versioned state bucket.

For the separate ECS configuration, the gateway can read acknowledgement files
only, and decrypt them with the evidence KMS key. It receives no delete permission.
That ECS configuration has not been deployed by this remediation.

## Validation and release boundary

- Real Windows desktop process, descendant cleanup, singleton and readiness tests.
- Fresh disposable PostgreSQL 16 bootstrap twice, then real PostgREST acceptance
  and tamper probes: 28 passed.
- Container smoke: real TLS certificate verification, scoped worker authentication,
  SDK leasing and result completion passed; AWS calls were explicitly stubbed.
- Terraform bootstrap validation and a reviewed two-resource apply succeeded.
- Complete suite with both real PostgreSQL and SQLite paths enabled: **861 passed,
  zero failed, zero skipped**, one dependency deprecation warning, in 62.78 seconds.
  `PYTHONPATH` explicitly selected this worktree rather than the main editable install.

One combined test run lost its disposable WSL services and reported connection
refusals. The rerun keeps a WSL session attached to the database container rather
than mistaking an idle VM shutdown for an application defect.

The updated runtime image has not been rolled out to the AWS lab. Prior cloud
demo evidence remains available, but it is not validation of this remediation.
The lab's earlier intermittent startup failure remains a release limitation;
run the manual bounded cloud workflow before treating this candidate as promoted.
