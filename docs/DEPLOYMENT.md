# Deploying BatonCadence - Any Cloud, Multi-Tenant, Guarded

The gateway is a stateless container in front of a Postgres/Supabase data
plane: it runs identically on AWS (ECS/EKS/App Runner), GCP (Cloud Run/GKE),
Azure (Container Apps/AKS), Fly.io, or bare Docker.

## Quick start

```bash
docker build -t mcorchestr8 .
docker run -p 18789:18789 \
  -e SUPABASE_URL=... -e SUPABASE_KEY=...
  mcorchestr8
# Uvicorn can print running while /healthz still 500 for ~10s.
# Retry until 200; do not treat the first 500 as death.
for i in $(seq 1 30); do
  curl -sf http://localhost:18789/healthz && break
  sleep 1
done
```

Or `docker-compose up` for gateway + a codex worker (see `docker-compose.yml`).
CI builds and tests every push (`.github/workflows/ci.yml`, Python 3.11/3.12 + image build).

- **Liveness/readiness:** `GET /healthz` (unauthenticated, no secrets) - wire it
  to your LB/orchestrator health checks with retries; a first 500 right after
  Uvicorn starts is warmup, not a dead process. Docker `HEALTHCHECK` is preconfigured.
- **Secrets:** for one host, mount the AES-256-GCM local secret-store volume.
  For Saved Instances or multiple replicas, set
  `MCO_SECRET_VAULT_BACKEND=database` and inject `MCO_VAULT_MASTER_KEY` from
  your cloud secret manager. The database receives ciphertext only. The
  container runs as a non-root user.
- **Workers anywhere:** `mco listen` workers run in customer/edge networks and
  make outbound-only connections to the gateway - the hybrid control-plane/
  data-plane split enterprises expect from credential-holding tools.

For native enterprise OIDC, also inject `MCO_SESSION_SECRET`, keep
`MCO_SESSION_COOKIE_SECURE=true`, and expose the gateway only through HTTPS.
The callback URL registered in Okta/Entra/Auth0 is
`https://<gateway>/api/auth/oidc/<provider-id>/callback`.

## Multi-tenancy (hosted SaaS mode)

Run `docs/migrations/2026-06_multi_tenancy.sql` (idempotent). Every agent,
job, audit event, and Drumline entry carries an `org_id` (existing rows backfill
to `default`, so single-tenant installs are unaffected).

- Register agents into a tenant: `mco register --name x --role codex --org acme`
- The org boundary is enforced on every read and write: job listings, pending
  polls, leasing, status updates, approvals, retries, audit trails, agent
  lists, and Drumline recall are all scoped to the caller's org. Cross-org
  access returns 404 - other tenants' resources are invisible, not just
  forbidden.
- Drumline memory is per-org: tenant knowledge never leaks across the boundary.

## Guardrails

| Variable | Effect |
|---|---|
| `MCO_POLICY_GATED_ROLES` | Jobs targeting these roles (e.g. `servicenow,dynatrace`) are **always** forced to `needs_approval`, regardless of sender intent - no agent writes to a gated platform without a human. |
| `MCO_KILL_SWITCH` | `true` = global pause: job creation and leasing return 503; in-flight jobs may still report status; humans can still approve/audit. `/healthz` reports `paused: true`. |
| `MCO_APPROVER_ROLES` | Who may approve/reject/retry and run direct platform actions (default `human,admin,operator`). |
| `MCO_DRUMLINE_INJECT` / `MCO_DRUMLINE_DISTILL` | Shared-context injection/distillation toggles (default on). |
| `MCO_SYNC_INTERVAL` | Background connector sync cadence in seconds (0 = off). |
| `MCO_WEBHOOK_SECRET` | Enables inbound webhook ingestion; unset = endpoint disabled. |

Recommended production baseline: gate every connector role, keep distillation
on, set the webhook secret, and rehearse the kill switch.

## Farming work out to the mesh

Use the bundled workflows to delegate routine work to your agents and keep a
continuous QA cycle running:

```bash
mco workflow configs/workflows/saas_hardening.yaml   # one-shot hardening pass
mco workflow configs/workflows/qa_loop.yaml          # self-perpetuating QA loop
```

The QA loop's final step is approval-gated: each next round needs a human
click, so the loop is alive but never unsupervised. Round outcomes distill
into Drumline automatically - every iteration starts smarter than the last.
