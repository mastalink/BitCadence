# VIA Score deployment adapter

## Boundary

The deployment adapter is a separate, signed-approval-only execution lane. It does not widen the audit Lambda.

It can perform exactly one action: invoke the version-pinned `via-score-activate-release` SSM command document on the fixed VIA instance. That document runs the host's existing `/opt/via/activate-release.sh activate <release-directory> <immutable-image-id>` script. The host script has its own release lock, image immutability check, local artifact checks, readiness/public-health checks, and failed-candidate rollback.

## Approval contract

The adapter accepts only an S3 key matching `score-deploy-approvals/pending/<uuid>.json`. Its KMS-signed JSON must contain exactly:

- schema version, UUID approval ID, action `activate`, fixed VIA instance, expiry, and the exact `via-cloud-launch` Score digest;
- an allowed `/opt/via/releases/via-*` release directory and a `sha256:` image ID;
- one SHA-256 hash each for build, tests, and independent review.

It verifies the signature through KMS but has no `kms:Sign` permission. An altered Score, stale approval, wrong target, mutable tag, extra field, missing evidence category, malformed artifact hash, arbitrary key, or invalid signature fails before SSM is called.

## Replay and recovery

Before issuing `SendCommand`, the adapter atomically creates a `dispatching` receipt row. It then persists the returned command ID. If it crashes between those steps, the approval remains blocked as an uncertain side effect; it is never automatically reissued. A later Score recovery adapter must inspect the fixed host state and make an explicit recovery decision.

SSM's `GetCommandInvocation` cannot be IAM-scoped to a particular command. The role has no session, list, or arbitrary-command permission, and the code stores only status and response code—never standard output/error. This limitation is called out explicitly in AWS's [Systems Manager authorization reference](https://docs.aws.amazon.com/service-authorization/latest/reference/list_ssm.html).

## Current status

The adapter is deployed: the distinct Lambda role, verification-only KMS key, encrypted replay-receipt table, version-pinned command document, and log group are active. Terraform reports no drift and the Lambda has no resource policy, schedule, or public invocation permission.

It must not be invoked until a future authenticated Score conductor produces a valid, evidence-accepted approval. No production VIA release was attempted while introducing this adapter.
