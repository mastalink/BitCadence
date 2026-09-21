# Usage capacity signals

BitCadence must know when an account has usable capacity before it assigns new
work.  A rate-limit error after a job starts is an incident signal, not a
capacity signal.

This document defines the first, deliberately narrow slice: **U01**.  U01 is
read-only discovery and a normalized contract.  It does not change routing,
store credentials, scrape provider dashboards, estimate money, or enforce a
budget.

## Normalized observation

Each provider/account adapter emits an immutable observation containing:

- `org_id`, `provider`, `account_ref`, and optional `model`;
- `remaining` as a percentage or count when the provider supplies one;
- `reset_at` when the provider supplies a reset time;
- `observed_at`, `source`, `source_version`, and a bounded evidence digest;
- `freshness_seconds` and `status`: `fresh`, `stale`, `unknown`, or `error`;
- a sanitized failure class when collection was attempted but failed.

`account_ref` is an opaque stable identifier. It is never an API key, session
cookie, email address, or dashboard URL containing credentials.

## Safety rules

1. Unknown is not zero and is not usable capacity. It remains `unknown`.
2. A collector uses an official API, documented local CLI output, or a
provider-issued rate-limit header. Browser automation and HTML dashboard
scraping are excluded unless a later reviewed adapter explicitly permits them.
3. Credentials remain in SecretVault or the provider's existing local session.
The ledger stores only opaque references and redacted observations.
4. A stale observation may inform an operator display but cannot make a route
eligible or ineligible by itself.
5. U01 is advisory and read-only. Existing deterministic routing remains
unchanged until a separately reviewed U02 introduces a capability-aware policy.
6. Collection must be bounded, independently rate-limited, and must not spend
model tokens merely to learn account usage.

## Routing contract after U01

The future scheduler input is a verified `CapacitySignal`, not a guessed
percentage. The deterministic router may prefer a fresh account with adequate
capacity, must record the reason and alternatives, and must retain its current
fallback behavior whenever every signal is unknown or stale.

No Jev judgment decides capacity. Jev may eventually annotate a task's likely
complexity, but numeric limits, cooldowns, reservations, and hard budget
decisions remain deterministic code.

## Delivery sequence

1. **U01:** inventory the actual official/local sources available to every
   configured provider and publish fixtures plus the normalized contract.
2. **U02:** implement only adapters that have a permitted, testable source.
3. **U03:** add a read-only control-panel/metrics view with freshness and
   provenance.
4. **U04:** add deterministic capacity-aware routing with disabled/unknown
   parity and routing receipts.
5. **U05:** add the full reserve/meter/settle ledger and emergency spend
   breaker from WS4.

No later phase starts merely because U01 finds a dashboard. Each needs its own
Score task, exact-head review, and acceptance evidence.
