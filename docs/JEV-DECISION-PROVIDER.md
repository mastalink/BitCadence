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
