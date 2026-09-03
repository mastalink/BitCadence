# BitCadence EPCOT — the city in AWS

A fully autonomous, always-on BitCadence deployment that **breaks itself on a
schedule and files the evidence**. Nothing here runs on your PC. The design
and the regulatory mapping are in `docs/DEMO-epcot.md`; this file is how to
stand it up.

## What gets built

| Layer | Resources |
|---|---|
| **Hub** | ECS Fargate gateway (+ PostgREST + nginx for the `postgres` backend), RDS Postgres 16 *or* EFS-backed LocalStore |
| **Ring** | One Fargate worker per role in `worker_roles`, each invoking Bedrock with its task role |
| **Greenbelt** | ADOT collector → Amazon Managed Prometheus → Managed Grafana; optional OTLP metrics to Dynatrace |
| **Pavilions** | ServiceNow and Dynatrace connectors (your tenants, wired by variables) |
| **Evidence** | S3 with Object Lock **COMPLIANCE** + KMS; append-only ledger mirror; per-run evidence bundles |
| **Dead-man** | Route53 health check → CloudWatch (us-east-1) → SNS email/SMS. Shares nothing with the gateway. |
| **Chaos** | FIS templates: stop worker, stop gateway, partition subnet. EventBridge Scheduler runs the conductor on `chaos_schedule`. |
| **Status** | Public S3 website the conductor rewrites after each run |

## Prerequisites (one-time, in the console)

1. **Bedrock model access** enabled in `var.region` for the model you set in `bedrock_model_id`.
2. **IAM Identity Center** enabled (Managed Grafana uses it for sign-in).
3. Optional pavilions: a **ServiceNow** instance (a free Personal Developer Instance works) and/or a **Dynatrace** tenant (trial works) with a token scoped `problems.read`, `problems.write`, `metrics.ingest`.
4. `terraform >= 1.6`, `aws` CLI v2, Docker.

## Apply

```bash
cd infra/aws
cp terraform.tfvars.example terraform.tfvars   # edit: alert_email, bedrock_model_id, tenants
terraform init
terraform apply
```

Then build and push the three images (ECR URLs are in `terraform output ecr`):

```bash
ACCT=$(aws sts get-caller-identity --query Account --output text)
REG=$(terraform output -raw region 2>/dev/null || echo us-east-1)
aws ecr get-login-password --region $REG | docker login --username AWS --password-stdin $ACCT.dkr.ecr.$REG.amazonaws.com
cd ../..   # repo root - the Dockerfiles copy src/ from here
for c in gateway worker conductor; do
  docker build -f infra/aws/$c/Dockerfile -t $(cd infra/aws && terraform output -json ecr | jq -r .$c):latest . && \
  docker push $(cd infra/aws && terraform output -json ecr | jq -r .$c):latest
done
cd infra/aws && aws ecs update-service --cluster $(terraform output -raw cluster 2>/dev/null || echo bitcadence-epcot) --service gateway --force-new-deployment --region $REG
```

Confirm the two SNS subscription emails. Then run the conductor once without waiting for the schedule:

```bash
eval "$(terraform output -raw run_conductor_now)"
```

The first run **registers the cloud workers** through the public API, stores their tokens in Secrets Manager, and rolls each worker service so ECS injects them. Workers boot before that happens and simply retry until it does.

Open `terraform output console_url`, sign in with `MCO_LOCAL_TOKEN` from `terraform output secret_arn`, and watch the feed. The public `status_page_url` shows the last chaos run.

## First-apply risks — read before you `apply`

These are the first-apply seams. The PostgREST path below is locally exercised;
the remaining AWS-managed integrations still need a live-account rehearsal.

1. **PostgREST ⇄ Supabase client.** This seam is exercised locally against PostgreSQL 16, `postgrest/postgrest:v12.2.3`, and `nginx:1.27-alpine`. The gateway mints a JWT with `role: bitcadence`; `PGRST_JWT_SECRET` must match `JWT_SECRET`. A bare RDS database does not have the three core objects that the additive project migrations assume, so the gateway first applies the idempotent SQL in `gateway/migrations-overlay/`, then runs the normal migrations and reloads PostgREST's schema cache. Keep that overlay in the gateway image. **Escape hatch:** `store_backend = "local"` removes this seam entirely.
2. **Migration entry point.** The entrypoint calls `mco.migrations_runner.apply_postgres(DATABASE_URL)`. If that signature has moved, the gateway task exits at boot with the traceback in CloudWatch under `/ecs/<name>`.
3. **Events table columns.** The ledger shipper orders `agent_job_events` by `(created_at, id)`. If `id` is not text-castable or `created_at` is named differently, the shipper logs `shipper.error` and the gateway keeps serving — the vault just stays empty until the query is corrected.
4. **Agent registration.** The conductor registers workers via `POST /api/agents` and expects the token in the response. If the field name differs, the conductor logs it and the workers stay unauthenticated.
5. **Bedrock model ID** default is a placeholder that fails loudly. Set it.
6. **SNS SMS** may be in sandbox; email always works once confirmed.
7. **Managed Grafana** requires IAM Identity Center in the account or the workspace creation fails.

## What it costs to leave running

Rough, us-east-1, before credits: Fargate (gateway 1 vCPU + 4 workers + collector) ≈ $70–90 · RDS `db.t4g.small` ≈ $25 · NAT ≈ $35 + data · ALB ≈ $18 · Managed Grafana ≈ $9/editor · AMP + FIS + S3 + KMS ≈ $10 · **≈ $170–200/month**. Five thousand in credits is roughly two years of a city that never sleeps.

## Teardown

```bash
terraform destroy
```

The **evidence bucket will refuse to be destroyed** while it holds locked objects — that is the point of it. `prevent_destroy` is set; remove it and empty the bucket only after retention elapses. Everything else tears down.
