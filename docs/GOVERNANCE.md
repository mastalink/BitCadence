# BitCadence Governance Guide

Usage documentation for the governance layer: **human-in-the-loop approval
gates**, the **immutable audit trail**, **retry/escalation paths**, the
**workflow DSL**, and the **control-plane dashboard**.

Prerequisite: the governance migration
([`migrations/2026-06_phase_a_governance.sql`](migrations/2026-06_phase_a_governance.sql))
must be applied to your Supabase project. It is idempotent - safe to re-run.

---

## 1. Job Lifecycle

```
                      +--> needs_approval --(approve)--> pending
waiting --(deps done)-+                       |
                      +--> pending            +--(reject)--> rejected  [terminal]

pending --> leased --> in_progress --> completed
                                   \-> failed --(retries left)--> pending (re-queued)
                                              \-(exhausted + escalate_to_role)--> escalation job created
```

| Status | Meaning |
|---|---|
| `waiting` | Blocked by incomplete `depends_on` jobs |
| `needs_approval` | Paused at a human approval gate |
| `pending` | Ready to be leased by a worker |
| `leased` / `in_progress` | Claimed / executing |
| `completed` | Done; unlocks dependents |
| `failed` | Failed; may auto-retry or escalate |
| `rejected` | Terminal: a human declined the approval gate |

---

## 2. Human-in-the-Loop Approval Gates

### Creating a gated job

Any job can be gated by setting `requires_approval`. The job pauses at
`needs_approval` instead of going live - including after its dependencies
complete.

**REST:**
```bash
curl -X POST http://127.0.0.1:18789/api/jobs \
  -H "Authorization: Bearer $MCO_AGENT_TOKEN" -H "Content-Type: application/json" \
  -d '{"title": "Deploy to prod", "target_agent_role": "codex", "requires_approval": true}'
```

**MCP tool (from Claude Desktop / Codex / Antigravity):**
```
mco_send(to_role="codex", title="Deploy to prod",
         instructions="Run the deploy script", requires_approval=True)
```

### Who can approve

Approval decisions are restricted to **approver roles**, configured via the
`MCO_APPROVER_ROLES` config/env variable (comma-separated, case-insensitive).
Default: `human,admin,operator`.

Register yourself a human approver agent once and save its token:
```bash
mco register --name joe --role human
```

### Deciding the gate

**CLI** (uses `MCO_AGENT_TOKEN` / `MCO_GATEWAY_URL` env vars):
```bash
mco approve <job_id>
mco reject  <job_id> --reason "too risky"
```

**REST:**
```bash
curl -X POST http://127.0.0.1:18789/api/jobs/<job_id>/approve \
  -H "Authorization: Bearer $APPROVER_TOKEN"
curl -X POST http://127.0.0.1:18789/api/jobs/<job_id>/reject \
  -H "Authorization: Bearer $APPROVER_TOKEN" -H "Content-Type: application/json" \
  -d '{"reason": "too risky"}'
```

**MCP tools:** `mco_approve(task_id)` / `mco_reject(task_id, reason)` - wire
the MCP server with an approver-role token and you can decide gates straight
from Claude Desktop.

**Dashboard:** open `/dashboard`, paste an approver token, and use the
Approve/Reject buttons in the Approval Queue.

Approving moves the job to `pending` (workers pick it up normally) and stamps
`approved_by`. Rejecting is terminal (`rejected`) and records the reason in
`error_message`. Both decisions are audited, and an ntfy alert
(`MCO Approval Required`, priority 4) fires whenever a job arrives at the gate.

---

## 3. Immutable Audit Trail

Every job mutation is appended to `agent_job_events`:

| Event | When |
|---|---|
| `created` | Job inserted (by an agent, or by the system for escalations) |
| `leased` | An agent atomically claimed the job |
| `status:<x>` | Any status update (e.g. `status:completed`, `status:failed`) |
| `approved` / `rejected` | A human decided the approval gate |
| `retried` | A failed job was re-queued (detail: attempt / max_retries) |
| `escalated` | Retries exhausted; an escalation job was created |

Each row carries `actor_id`, `actor_role`, a JSON `detail` payload, and a
database timestamp. The table is **append-only at the database level**: a
trigger rejects every UPDATE/DELETE, so history cannot be quietly rewritten -
even with a service_role key.

**Reading the trail:**
```bash
mco audit <job_id>                     # CLI table view
curl http://127.0.0.1:18789/api/jobs/<job_id>/events \
  -H "Authorization: Bearer $MCO_AGENT_TOKEN"        # REST (oldest first)
```
MCP tool: `mco_audit(task_id)`. Dashboard: the **View** button on any job row.

---

## 4. Retries & Escalation

Two optional fields on any job control what happens on failure:

- `max_retries` (int, default 0): on failure, the job is re-queued to
  `pending` (lease cleared, `retry_count` incremented) until the budget is
  exhausted.
- `escalate_to_role` (string): once retries are exhausted, the system creates
  a new `ESCALATION: <title>` job addressed to this role, carrying the last
  error and the original instructions, and fires a priority-5 ntfy alert. The
  original job stays `failed`; the audit trail links both directions.

```bash
curl -X POST http://127.0.0.1:18789/api/jobs \
  -H "Authorization: Bearer $MCO_AGENT_TOKEN" -H "Content-Type: application/json" \
  -d '{"title": "Flaky sync", "target_agent_role": "codex",
       "max_retries": 2, "escalate_to_role": "human"}'
```

A human with an approver-role token can also **manually re-queue** any
`failed` or `rejected` job (lease and error cleared, audited as `retried`):

```bash
mco retry <job_id>      # or POST /api/jobs/{id}/retry, or the mco_retry MCP tool
```

---

## 5. Workflow DSL

Declare a DAG of multi-agent steps in YAML; each step becomes one job and
`depends_on` references step ids (translated to job ids at submit time).
Steps accept the same governance fields as single jobs.

```yaml
# configs/workflows/example_release.yaml
name: release-pipeline
steps:
  - id: research
    role: claude                  # target_agent_role
    title: Research the change
    instructions: Summarize the open issues and decide the scope.

  - id: build
    role: codex
    title: Implement the change
    instructions: Apply the fixes identified by the research step.
    depends_on: [research]
    max_retries: 2
    escalate_to_role: human

  - id: ship
    role: codex
    title: Tag and publish the release
    instructions: Tag the release and publish artifacts.
    depends_on: [build]
    requires_approval: true       # pauses for a human after build completes
```

Optional per-step fields: `instance` (address a specific agent instance),
`requires_approval`, `max_retries`, `escalate_to_role`.

```bash
mco workflow configs/workflows/example_release.yaml --dry-run   # validate + print plan
mco workflow configs/workflows/example_release.yaml             # submit; prints step -> job id map
```

Validation catches missing names/roles, duplicate step ids, references to
unknown steps, and dependency cycles before anything is submitted.

---

## 6. Control-Plane Dashboard

Served by the gateway at `http://<host>:<port>/dashboard` (no build step, no
extra process). Paste an agent bearer token once (kept in browser
localStorage):

- **Approval Queue** - jobs at `needs_approval` with Approve/Reject buttons
  (requires an approver-role token).
- **Job Board** - latest 100 jobs with status badges, lease owner, and a
  per-job **audit viewer**.
- **Agent Fleet** - registered agents and online/offline presence.

The page polls every 5 seconds through the same authenticated REST API the
agents use - it has no privileged backdoor.

---

## 7. API Reference (governance endpoints)

| Method | Path | Who | Effect |
|---|---|---|---|
| `POST` | `/api/jobs` | any agent | Accepts `requires_approval`, `max_retries`, `escalate_to_role` |
| `GET` | `/api/jobs/{id}/events` | any agent | Audit trail, oldest first |
| `POST` | `/api/jobs/{id}/approve` | approver roles | `needs_approval` -> `pending`, stamps `approved_by` |
| `POST` | `/api/jobs/{id}/reject` | approver roles | `needs_approval` -> `rejected` (terminal); body: `{"reason": "..."}` |
| `POST` | `/api/jobs/{id}/retry` | approver roles | `failed`/`rejected` -> `pending` (human override; clears lease + error) |

Errors: `403` if the caller's role is not in `MCO_APPROVER_ROLES`, `400` if
the job is not at the gate, `404` if the job does not exist.
