| Group | Setting / control | Type and use |
|---|---|---|
| governance | `MCO_KILL_SWITCH` | bool: Stop work (halt active jobs and pause intake) |
| governance | `MCO_APPROVER_ROLES` | text: Approver roles (comma-separated) |
| governance | `MCO_POLICY_GATED_ROLES` | text: Always-gated roles (jobs to these pause for a human) |
| governance | `MCO_ESCALATION_CONNECTOR` | text: Escalation connector (mirror terminal failures to ITSM) |
| governance | `MCO_DEFAULT_JOB_SORT` | choice: Default Job Board sort order Allowed: `created_desc`, `created_asc`, `priority_desc`, `status`. |
| memory | `MCO_DRUMLINE_DISTILL` | bool: Distill completed jobs into shared memory |
| memory | `MCO_DRUMLINE_INJECT` | bool: Inject shared memory into worker prompts |
| presence | `MCO_AGENT_OFFLINE_AFTER` | text: Mark an agent offline after no contact for (seconds) |
| delivery | `MCO_DELIVERY_STALL_SECONDS` | text: Re-wake a pending job nobody took after (seconds; 0 = off) |
| delivery | `MCO_ROUTE_FALLBACKS` | text: Reroute stalled work to (role:fallback\|fallback, comma-separated) |
| delivery | `MCO_DELIVERY_MAX_REROUTES` | text: Maximum reroutes per job before escalating to a human |
| score | `MCO_SCORE_SWEEP_SECONDS` | text: Advance score runs from the gateway every (seconds; 0 = off, runs only move when someone types `mco score tick`) |
| jev | `MCO_JEV_MODE` | choice: Jev mode Allowed: `disabled`, `shadow`, `assist`, `active`. |
| jev | `MCO_JEV_MODEL` | text: Jev model |
| jev | `MCO_JEV_TIMEOUT_SECONDS` | text: Jev timeout (seconds) |
| jev | `MCO_JEV_MAX_RETRIES` | choice: Jev retry count Allowed: `0`, `1`, `2`. |
| observability | `MCO_METRICS_TOKEN` | secret: Protect /metrics with a bearer token (blank = open, like /healthz) |
| observability | `MCO_LOG_JSON` | bool: Structured JSON logs (restart to apply) |
| tenancy | `MCO_ORGS` | text: Allowed orgs (comma-separated; 'default' always exists). Orgs are isolation boundaries - agents only see jobs and memory inside their own org. |
| edition | `MCO_EDITION` | choice: Edition (blank = infer from configuration) Allowed: blank (infer), `community`, `team`, `enterprise`. |
| security | `MCO_TRUSTED_HEADER_AUTH` | bool: Trusted-header SSO (only behind an auth proxy!) |
| security | `MCO_TRUSTED_HEADER_USER` | text: Identity header |
| security | `MCO_TRUSTED_HEADER_ROLE` | text: Role header |
| security | `MCO_TRUSTED_HEADER_DEFAULT_ROLE` | text: Default role for SSO users |
| security | `MCO_TRUSTED_HEADER_SECRET` | secret: Proxy shared secret (X-MCO-Proxy-Secret) |
| security | `MCO_WEBHOOK_SECRET` | secret: Inbound webhook secret (enables /webhook ingestion) |
| notifications | `NTFY_SERVER` | text: ntfy server |
| notifications | `NTFY_TOPIC` | secret: ntfy topic (blank = notifications off) |
| connectors | `SERVICENOW_INSTANCE_URL` | text: ServiceNow instance URL |
| connectors | `SERVICENOW_USERNAME` | text: ServiceNow username |
| connectors | `SERVICENOW_PASSWORD` | secret: ServiceNow password |
| connectors | `DYNATRACE_BASE_URL` | text: Dynatrace URL |
| connectors | `DYNATRACE_API_TOKEN` | secret: Dynatrace API token (problems.read + problems.write) |
