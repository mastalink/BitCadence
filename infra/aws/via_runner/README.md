# VIA Score cloud audit runner

This is the first deployed BitCadence Score adapter: a low-cost EventBridge-to-Lambda runner that refreshes the fixed G01 VIA inventory without a local computer, an interactive session, stored credentials, deployment authority, shell execution, or source-data writes.

It is intentionally **not** a general agent executor.  It is the first proven cloud authority boundary.  The Lambda role can only inspect the fixed VIA host/stack/alarms, list the existing `backups/` prefix, call two fixed public API URLs, write a sanitized JSON report beneath `score-audits/`, and write its own logs.  It cannot read S3 objects, read secrets, access IAM, invoke SSM commands, restart services, deploy code, assume another role, or accept an event-controlled URL/command.

## Provisioning

Run from this directory with a currently authorized operator profile.  The profile is used by Terraform only and never becomes a Lambda credential.

First create the cloud-backed, versioned Terraform state. Terraform's native S3 lockfile prevents competing changes, so no local lock or DynamoDB table is required. This avoids making the runner's management depend on an operator workstation:

```powershell
cd bootstrap
terraform init
terraform apply -var='aws_profile=' # use one-process AWS credentials if required
Copy-Item backend.tf.example backend.tf
terraform init -migrate-state `
  -backend-config='bucket=OUTPUT_STATE_BUCKET' `
  -backend-config='key=via-score/bootstrap/terraform.tfstate' `
  -backend-config='region=us-east-1' `
  -backend-config='use_lockfile=true' `
  -backend-config='encrypt=true'
```

Then initialize this runner directory with the same backend, changing only the key to `via-score/audit-runner/terraform.tfstate`.

```powershell
terraform init
terraform fmt -check
terraform validate
terraform plan -out=via-score-audit.tfplan `
  -var='via_instance_id=THE_EXISTING_VIA_INSTANCE' `
  -var='evidence_bucket=THE_EXISTING_EVIDENCE_BUCKET' `
  -var='score_digest=THE_EXACT_VIA_SCORE_SHA256'
terraform show via-score-audit.tfplan
terraform apply via-score-audit.tfplan
```

Immediately invoke the function once and verify an artifact is written under the output prefix.  A successful invocation proves only the audit lane.  It does not prove a release, restore, ingestion, extraction, reconciliation, publishing, notification, or APK path.

## Signed release adapter

The same stack now includes an inert-by-default `via-score-deploy-runner`. It has a different IAM role from the audit runner. It can only read a pending approval manifest, verify its KMS signature and exact Score digest, execute the one version-pinned SSM document that invokes `/opt/via/activate-release.sh activate`, and write a sanitized deployment receipt.

The host's existing activation script requires an immutable local image ID, serializes releases, checks container and public API health, and restores the prior release after a failed candidate. The deployment adapter does not accept a shell command, arbitrary SSM document, target instance, role, URL, secret, rollback request, or mutable image tag.

No invocation path is deployed for this function yet. A future authenticated Score conductor must create a KMS-signed manifest under `score-deploy-approvals/pending/<approval-id>.json` after its independent evidence/review adapter accepts the exact build. The manifest is valid only for a short expiry window and must contain the exact Score digest plus build, test, and review hashes. This is the meaningful release gate; it is not a human data-entry workflow.

The deploy role has one unavoidable AWS limitation: `ssm:GetCommandInvocation` does not support resource-level authorization. Its code retains only SSM status and response code, never command output, and it has no list/session/command-cancel privilege. AWS documents that the API has no resource type, while `SendCommand` supports document and target resource authorization. [AWS Systems Manager authorization reference](https://docs.aws.amazon.com/service-authorization/latest/reference/list_ssm.html)
