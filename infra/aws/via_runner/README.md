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
  -var='evidence_bucket=THE_EXISTING_EVIDENCE_BUCKET'
terraform show via-score-audit.tfplan
terraform apply via-score-audit.tfplan
```

Immediately invoke the function once and verify an artifact is written under the output prefix.  A successful invocation proves only the audit lane.  It does not prove a release, restore, ingestion, extraction, reconciliation, publishing, notification, or APK path.

## What comes next

The next Score adapter must be a separately reviewed, narrowly allowlisted deployment lane.  It should be introduced only alongside an immutable build artifact, an explicit release wrapper, rollback behavior, exact-head evidence, and a new accepted policy grant.  Do not add deployment rights to this audit role.
