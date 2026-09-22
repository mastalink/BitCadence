# Jev decision-provider contract

BitCadence integrates TypeSafe/Jev as an optional semantic decision primitive,
not as an agent or general authority system. One separately owner-approved VIA
path may use Jev's bounded selection among existing Lorain schedule candidates
as authoritative for that selection, subject to code-side evidence revalidation
and deterministic fallback. This is not a general permission to publish facts
or execute effects. The provider is disabled by default and ordinary BitCadence
startup performs no TypeSafe network request.

## Why Jev is here

The same expensive generative agent should not have to deliberate over every
small routing, relevance, or triage question. Jev supplies fast, typed semantic
judgments for bounded questions; Claude, Codex, and other workers handle the
longer reasoning and execution. This is a two-speed workflow, not a second
authority system. Jev can suggest what a task *means*. Deterministic policy
still decides which models are eligible, how much work may run, whether a lease
is valid, and whether an effect needs a human grant or review. The narrowly
approved VIA exception lets Jev choose only among already-extracted Lorain
schedule candidates; code revalidates the choice against retained evidence and
uses a deterministic fallback on failure. It does not allow Jev to invent
candidates, create grants, approve checkpoints, or control unrelated effects.
No speed or cost improvement is claimed without a measured BitCadence benchmark.

The shipped Codex and Claude task/model routes are advisory. Drumline,
watchdog, and notification use Jev only for annotations. The VIA Lorain
candidate-selection authorization is a separate path and is not implemented by
this release candidate. A Jev outage or abstention returns a deterministic
fallback, not permission to skip a gate.

## Configuration

The admin API is `/api/jev`:

- `GET /api/jev` reports offline capability state and never returns a secret.
- `PUT /api/jev` accepts `mode`, `model`, `timeout_seconds`, `max_retries`, and
  an optional `api_key`. The key is stored through the tenant-scoped
  `SecretVault` reference `typesafe-jev/api_key`; it is never written to the
  provider metadata or returned by the API.
- `POST /api/jev/test` is the only configuration operation that deliberately
  performs model discovery against TypeSafe.

Modes are `disabled`, `shadow`, `assist`, and `active`. `jev-latest` is allowed
only for shadow evaluation. Assist and active modes require an exact model
name, and a response from any other model becomes a deterministic fallback.

### First-time setup on Windows PowerShell

Use an admin-scoped token from the BitCadence installation, **not** an ordinary
worker token. The usual local token is `MCO_LOCAL_TOKEN` in the
installation's `.env` or `~/.mco/.env`; do not paste it into logs or a ticket.
The dedicated Jev credential is stored in the server-side encrypted vault.
Run the following in one PowerShell session, with the gateway already running:

```powershell
$adminSecret = Read-Host 'BitCadence operator token' -AsSecureString
$typesafeSecret = Read-Host 'TypeSafe API key' -AsSecureString
$adminToken = [System.Net.NetworkCredential]::new('', $adminSecret).Password
$typesafeKey = [System.Net.NetworkCredential]::new('', $typesafeSecret).Password
$headers = @{ Authorization = "Bearer $adminToken" }
$body = @{ mode = 'shadow'; model = 'jev-latest'; api_key = $typesafeKey } | ConvertTo-Json -Compress
Invoke-RestMethod -Method Put -Uri 'http://127.0.0.1:18789/api/jev' -Headers $headers -ContentType 'application/json' -Body $body
Invoke-RestMethod -Method Post -Uri 'http://127.0.0.1:18789/api/jev/test' -Headers $headers
Remove-Variable adminSecret, typesafeSecret, adminToken, typesafeKey, headers, body -ErrorAction SilentlyContinue
```

The test response should report `configured: true`, `available: true`,
`ok: true`. It tests connectivity/model discovery, not a live Jev judgment;
`live_invocation: false` is expected in shadow mode. An `Invalid or missing
agent token` response (401) means the bearer value is absent, wrong, or not
accepted by this gateway. A 403 means the token is valid but lacks the `admin`
scope. An admin-scoped agent token is accepted by this Jev configuration API;
Score's human-only grant endpoints are a separate, stricter boundary.
The Admin Console's generic settings expose mode/model fields, but the
credential and explicit connection test currently use this API; there is no
dedicated Jev key form yet. This is a product gap, not a reason to put the key
in client-side code.

To stop future Jev requests, set `mode` to `disabled` through the same admin
endpoint. Disabling does not erase the encrypted key.

## DecisionReceipt

Every invocation returns a `DecisionReceipt`, including fallbacks. The receipt
contains:

- use-case id;
- question-set version and canonical SHA-256 digest;
- exact response model;
- canonical state digest;
- typed answers, probabilities and confidence values;
- latency, token usage and provider request id when available;
- configured mode, outcome, and sanitized error class.

Receipts intentionally contain neither the source state nor the API key. A
receipt is evidence of a judgment, not permission to act.

## VIA boundary

The separately approved Lorain path may let Jev choose which already-extracted,
evidence-backed schedule candidate VIA publishes. The choice is authoritative
only within that candidate set and must be revalidated against retained source
text. A Jev failure or out-of-set answer falls back to an uncontested newest
bulletin candidate or a hold for outreach. The implementation is outside this
release candidate. Deterministic code remains responsible for source permits,
campus identity, candidate extraction and validation, dates, recurrence
expansion, budgets, grants, reviewer eligibility, and publication mechanics.

Each VIA use case owns a versioned question registry. Changing instructions,
criteria, or candidate meanings creates a new version and digest. A caller
must retain the receipt alongside the artifact/evidence identifiers it used.
If Jev is disabled, unconfigured, unavailable, rate-limited, times out, returns
malformed data, or violates a pinned model, the provider returns a sanitized
fallback receipt and the caller follows its existing deterministic path.

No current Score capability permits Jev to create grants, approve checkpoints,
or execute cloud/repository effects. Any future `typesafe:invoke` Score adapter
requires its own digest-bound grant, budget, effect receipt, and adversarial
review before live use.

## J03 shadow operations

BitCadence paths may ask Jev for an annotation after deterministic code
has already produced an outcome. Even if the global mode is `assist` or
`active`, these paths treat Jev as annotation-only: `applied` is always false,
and no Jev answer mutates durable truth or authorizes an effect.

| Use case | Question set | What Jev may suggest | What code still owns |
| --- | --- | --- | --- |
| `drumline-ops` v1 | kind, inject, contradiction, staleness, sensitivity, relevance | Classify completed output; flag recall quality | Stored kind (`fact` / `decision` / `lesson` / `handoff` / `artifact`), content, weight, tags, recall order. `incident` is annotation-only and is not a Drumline kind. `inject=skip` cannot prevent `remember()`. |
| `watchdog-symptom` v1 | action (`retry` / `reroute` / `escalate` / `operator-review` / `noise`) | Classify an already-computed delivery step | Stall timers, max reroutes, reroute CAS, crash-loop safeguards, chain-stall detection, kill switch, who is rerouted. |
| `notify-quality` v1 | duplicate, urgency, impact | Quality of a push that `_allowed` already admitted | Rate budgets, urgent-bypass, identical-message dedup, the Priority header, and whether the message is sent. |

Question sets are frozen and digest-bound. Changing instructions, criteria, or
candidate meanings requires a new version string. DecisionReceipts are appended
as `jev_decision` audit events when a `job_id` is provided and the provider is
not disabled. Persist failure is logged and ignored; receipts are never rewritten.
`jev_decision` events are ignored by the delivery stall clock, so an annotation
cannot postpone rekick, reroute, or escalate. Disabled mode records process-level
metrics only and performs no network request and no audit write, so durable state
matches today's deterministic tests.

## Codex task routing

`mco_jev_route` is the Codex-facing MCP tool. It sends the current task and a
bounded context summary to the frozen `codex-task-route` registry. Jev may
describe task kind, semantic complexity, reasoning need, execution shape, and
whether the eventual task packet needs current information, workspace evidence,
acceptance criteria, or a material clarification.

The MCP server forwards this request to the gateway's `POST /api/jev/route`
endpoint using the narrow `jev:route` capability. This keeps the decision on
the tenant's configured SecretVault provider; if the gateway is unavailable,
the MCP tool falls back to the local deterministic route.

Jev does not select spend. Deterministic policy combines those annotations with
verified five-hour and weekly Codex capacity plus a caller-supplied context-
pressure label. The policy recommends Luna, Terra, Sol, or Astra, reasoning
effort, maximum parallelism, and root/subagent/BitCadence/hybrid execution.
Astra is ineligible when capacity is unknown, five-hour remaining is below 30%,
weekly remaining is below 20%, or context pressure is high. Below 15% five-hour
remaining, the policy conserves usage and suppresses parallel subagents.

The result remains advisory (`applied=false`). It cannot switch the already-
running root model, spawn a subagent, or create a BitCadence job. Codex applies
the recommendation through explicit spawn parameters or `mco_send`, preserving
the user's original scope and all existing authority boundaries.
