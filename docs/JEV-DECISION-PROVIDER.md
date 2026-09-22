# Jev decision-provider contract

BitCadence integrates TypeSafe/Jev as an optional semantic decision primitive,
not as an agent, approver, or source of authority. The provider is disabled by
default and ordinary BitCadence startup performs no TypeSafe network request.

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

VIA may use Jev to rank candidate evidence, classify already discovered text,
or verify a field against an immutable source span. Deterministic code remains
responsible for source permits, campus identity, dates, recurrence expansion,
budgets, grants, reviewer eligibility, and publication.

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
