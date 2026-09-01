# BitCadence Enterprise Integrations

How to connect BitCadence to the agent surfaces of enterprise platforms -
**ServiceNow** (ITSM control tower), **Dynatrace** (observability/AIOps), and
any other system via the **generic webhook contract**. This is the
"centralized orchestration and monitoring of multiple AI agents across
vendors" capability the platform was validated on.

Connectors do two things:

1. **Ingestion** - open platform objects (incidents, problems, alerts) become
   MCO jobs, so AI agents work them through the normal lease -> execute ->
   approve lifecycle with full audit.
2. **Control** - agents and operators push decisions back into the platform
   (create/resolve incidents, comment on/close problems), either directly
   (approver-gated) or as auditable connector-role jobs.

---

## 1. Configuration

Connector credentials are ordinary MCO config keys, which means they belong in
the **AES-256-GCM secret store** (they are in `SENSITIVE_KEYS`, so `mco setup`
encrypts them rather than leaving them in plaintext `.env`).

### ServiceNow
| Key | Required | Notes |
|---|---|---|
| `SERVICENOW_INSTANCE_URL` | yes | e.g. `https://acme.service-now.com` |
| `SERVICENOW_TOKEN` | one of | OAuth bearer token (takes precedence) |
| `SERVICENOW_USERNAME` / `SERVICENOW_PASSWORD` | one of | basic auth |
| `SERVICENOW_SYNC_QUERY` | no | `sysparm_query` for ingestion. Default: `active=true^assignment_group.name=AI Agents` |
| `SERVICENOW_TARGET_ROLE` | no | role ingested incidents are addressed to (default `claude`) |

### Dynatrace
| Key | Required | Notes |
|---|---|---|
| `DYNATRACE_BASE_URL` | yes | e.g. `https://abc12345.live.dynatrace.com` |
| `DYNATRACE_API_TOKEN` | yes | scopes: `problems.read`, `problems.write` |
| `DYNATRACE_TARGET_ROLE` | no | role ingested problems are addressed to (default `claude`) |

### Platform-wide
| Key | Notes |
|---|---|
| `MCO_SYNC_INTERVAL` | seconds between background sync passes in `mco serve` (unset/0 = off) |
| `MCO_WEBHOOK_SECRET` | shared secret enabling `/api/integrations/{name}/webhook` (unset = endpoint disabled) |
| `MCO_WEBHOOK_TARGET_ROLE` | default role for webhook-ingested jobs (default `claude`) |
| `MCO_ESCALATION_CONNECTOR` | connector that mirrors terminal job failures into the platform (e.g. `servicenow`) |

Connectors load automatically when their keys are present - restart the
gateway after configuring, then verify:

```bash
mco connectors        # name | health | supported actions
```

### From the console

The console's **Settings → Connectors** panel is the GUI counterpart to
`mco setup` / `mco connectors` / `mco sync <name>` - useful when you'd rather
not open a terminal on the machine running the gateway. An operator enters
the ServiceNow/Dynatrace credentials, clicks **Save** (writes them through
the same encrypted settings path as `mco setup`), then:

- **Test connection** - reruns the connector's `health()` probe server-side
  and reports reachability/auth back inline, the same check `mco connectors`
  runs from a shell.
- **Sync now** - pulls open platform objects into the job board on demand,
  equivalent to `mco sync <name>`.

Each connector row shows a health dot reflecting live reachability/auth
state, so a credential going stale or a platform outage is visible without
running `mco connectors`.

---

## 2. Ingestion: platform objects -> agent jobs

### Polling sync (pull)
```bash
mco sync servicenow    # open incidents matching SERVICENOW_SYNC_QUERY -> jobs
mco sync dynatrace     # OPEN problems -> jobs
```
REST: `POST /api/integrations/{name}/sync` (any authenticated agent).
MCP tool: `mco_sync_connector(name)`.

Sync is **idempotent**: every ingested object carries a stable
`input_payload.external_id` (e.g. `servicenow:<sys_id>`), and objects already
on the board are skipped. Set `MCO_SYNC_INTERVAL=60` to have `mco serve` run
sync passes automatically in the background.

Ingested jobs arrive as `pending` for the configured target role with a
ready-to-run prompt (incident/problem context plus instructions to report
back through the connector role), and are audited as created by
`connector:<name>`.

### Webhook ingestion (push)
Set `MCO_WEBHOOK_SECRET`, then point the platform at:

```
POST /api/integrations/{name}/webhook
X-MCO-Webhook-Secret: <secret>
```

Recognized payload shapes:
- `name=servicenow` - business-rule/record payloads (`sys_id`, `number`, `short_description`, `description`)
- `name=dynatrace` - problem-notification payloads (`ProblemID`, `ProblemTitle`, `ProblemDetailsText`, `State`)
- any other name - the **generic contract**: `{"id": "...", "title": "...", "description": "...", "target_agent_role": "..."}` (use this for PagerDuty, LogicMonitor, Datadog, custom apps...)

Webhook ingestion dedupes by the same `external_id` rule, so a webhook and a
poll sync of the same object create one job, not two.

---

## 3. Control: acting on the platforms

### As auditable jobs (recommended for agents)
Address a job to the connector's role and run a connector worker - the action
executes through the normal lease/audit lifecycle:

```bash
mco listen --role servicenow --instance snow-bridge-1   # connector worker
```

```python
# any agent, e.g. via MCP:
mco_send(to_role="servicenow", title="Resolve INC0042",
         instructions="closing out",
         ...)  # with input_payload={"action": "resolve_incident", "params": {"sys_id": "..."}}
```

Combine with approval gates for write-actions you want a human to sign off:
target the connector role with `requires_approval=True` and the action only
fires after a human approves.

### Directly (operators; approver roles only)
```bash
mco platform servicenow create_incident --params '{"short_description": "Demo"}'
mco platform dynatrace add_comment --params '{"problem_id": "P-123", "comment": "Agent triage done"}'
```
REST: `POST /api/integrations/{name}/action` `{"action": ..., "params": {...}}` -
gated on `MCO_APPROVER_ROLES`, like the approval endpoints.
MCP tool: `mco_platform_action(name, action, params)`.

**Supported actions**
- ServiceNow: `create_incident`, `update_incident`, `add_comment`, `resolve_incident`, `get_incident`
- Dynatrace: `add_comment`, `close_problem`, `get_problem`, `list_problems`

---

## 4. Escalation bridge (MCO -> ITSM)

Set `MCO_ESCALATION_CONNECTOR=servicenow` and any job that reaches terminal
failure (retry budget exhausted) is **mirrored as a ServiceNow incident**
carrying the job id, target role, last error, and original instructions -
in addition to MCO's own internal escalation job. The bridge is best-effort:
a platform outage never breaks the orchestration path. The audit trail records
an `escalated_external` event with the platform reference (incident number).

---

## 5. End-to-end example: Dynatrace problem -> AI triage -> ServiceNow closure

1. `MCO_SYNC_INTERVAL=60`, both connectors configured.
2. Dynatrace raises a problem; the background sync creates a `pending` job for
   role `claude` with the problem context as the prompt.
3. A Claude worker leases the job, triages, and sends a follow-up job to role
   `dynatrace` with `action=add_comment` documenting findings.
4. If remediation needs a change, the agent submits a workflow whose final
   step targets role `servicenow` with `action=create_incident` (or
   `resolve_incident`) and `requires_approval=True` - a human approves from
   the console/dashboard, then the connector worker executes it.
5. Every hop is in the immutable audit trail; ntfy alerts fire throughout.
