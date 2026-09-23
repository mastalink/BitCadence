# Drumline Agent Exchange design

Status: D01 candidate, revision 2

Revision 2 preserves the revision 1 product contract and rebinds its governed
evidence to the current `origin/main`. Receipts or downstream work bound to the
earlier D01 commit do not satisfy this revision's gates.

Mission: `92aa20ce-070d-4395-97e8-bfd4d44c0878`

Score: `examples/scores/drumline-agent-exchange.score.json`

## Outcome and users

Drumline remains BitCadence's durable shared-context product. Agent Exchange
adds a separate collaboration lane inside that product so an operator or agent
can ask, answer, propose, block, decide, and hand work off without turning an
unreviewed conversation into durable truth.

The direct users are operators following a job or Score run, agents handing
work between vendors, and reviewers who need a threaded record tied to the
artifact under review. Today they can use job descriptions, completion output,
or `agent_context`; none is a safe discussion system. Job fields are not a
thread, while `agent_context` is recallable and may be injected into future
worker prompts. Using it for discussion would allow tentative text to acquire
authority merely by being stored.

Without this boundary, collaboration is fragmented and a proposal or blocker
can be mistaken for an accepted decision. The failure is not only UX: a
discussion written to canonical context can influence later workers, bypass an
independent review gate, or appear to authorize an effect.

## Reconciled evidence (2026-09-23)

- After `git fetch --prune origin`, fresh `origin/main` is
  `0b9104d2056432726fedeb268a500057d42abc03`. The isolated D01 worktree was
  created from that SHA at `C:\AI\baton\wt\drumline-d01-codex-beast-r3` on
  `score/drumline-agent-exchange-d01-r3`.
- The human checkout at `C:\AI\baton\Batoncadence` remains at
  `f2bea62b81a55f10db1e83a0f62db609e87a05e3`, one commit behind, and is dirty
  only in the generated console, its source shell, and `tests/test_console.py`.
  It was inspected read-only and is not accepted evidence.
- A separate clean prototype worktree at
  `C:\AI\baton\wt\drumline-agent-exchange-b01` contains implementation commit
  `399c29ef33ac27b08cfc88e05d1163d64c08aef8` atop the earlier D01 candidate.
  It was inspected only for drift; it is not accepted as D01 evidence and this
  revision does not authorize implementation.
- The live gateway answered `GET /healthz` with `{"status":"ok"}`. The
  loopback listener PID 38428 reports OS image `C:\Python314\python.exe` while
  its command line invokes
  `C:\AI\baton\Batoncadence\.venv\Scripts\python.exe -m mco.cli serve --host
  127.0.0.1 --port 18789`; a second wildcard listener owner was not
  introspectable. Windows did not expose a verified process CWD. D02 must not
  reload until executable, source/package, CWD, listener ownership, and a
  rollback target are all proven from a clean merged checkout.
- GitHub `mastalink/BitCadence` has no open PR. `CI`, `CodeQL`, and
  `PostgreSQL acceptance` all passed on current `origin/main` SHA `0b9104d`.
- Live MCO evidence showed this D01 lease, no other pending item in the
  `codex-beast` inbox, and a pre-existing mission chain where R02 rejected B01
  head `399c29e` for cross-platform console comparison and a bounded B01 repair
  was pending for another worker. Those receipts are bound to the earlier D01
  candidate. They cannot satisfy revision 2 R01/B01/M01 dependencies; the
  mission owner must reconcile the stale branch before downstream release.
- `docs/DRUMLINE.md`, `src/mco/orchestrator/drumline.py`, and
  `context_routes.py` confirm that `agent_context` is durable, recallable, and
  prompt-injected. It has a 2,000-character content cap and tenant-filtered
  dedupe, but LocalStore does not make `agent_context` append-only.
- Score `load_score()` supports repository commit bounds and rejection
  blocking even though `docs/score-v1.schema.json` does not yet describe the
  newer `commit` and `on_reject` fields. The governed Score is validated by
  the runtime loader in focused tests; schema parity is a B01 follow-up, not a
  reason to weaken this D01 contract.

### Routing receipt and deterministic selection

Jev receipt: `jev-1.13.0`, outcome `shadow`, confidence `0.48` for frontier
complexity and `0.88` for reasoning need, suggested `gpt-5.6-sol/max` and
hybrid/max-parallel 3. Five-hour and weekly capacity were unavailable, so both
were supplied as unknown and Astra was deterministically ineligible. The
unattended runner forbids background agents, so D01 stayed root-only. For R01,
deterministic policy overrides the generic shape: `antigravity-beast` was
online, was not a D01 author, and is selected as the single independent
reviewer. `codex-beast` is ineligible as its own reviewer; offline reviewer
identities are not selected.

## Decision

Use a dedicated `agent_exchanges` table and `/api/exchanges` surface. Do not
extend `agent_context`.

This is the smallest safe model because the two datasets have opposite trust
semantics:

| Concern | Agent Exchange | Canonical Drumline context |
| --- | --- | --- |
| Meaning | tentative discussion/reference | durable fact, decision, lesson, handoff, artifact |
| Prompt injection | never automatically injected | may be recalled into worker prompts |
| Mutation | append-only statements and state events | append-mostly with dedupe |
| Promotion | explicit, separately authorized operation | target of that operation |
| Retention/query | thread/linkage/cursor oriented | relevance/recency oriented |

Extending `agent_context` with a flag is rejected. Every recall path, current
and future, would need to remember to exclude discussion; one missed filter
would silently promote it. Reusing job events is also rejected because they
are lifecycle audit facts, not user-authored threads. Storing exchanges inside
job JSON is rejected because it prevents safe pagination, independent
idempotency, and workflow-only threads.

## Product behavior

The existing `memory` route identifier remains stable for console bookmarks
and internal state. Its visible navigation label and page title are
**Drumline** in both plain and expert modes. “Shared context” and “memory” may
remain explanatory text, never the product label.

The Drumline screen has two subviews:

1. **Context** — the existing canonical recall/composer experience.
2. **Agent Exchange** — thread list, filters, message timeline, and composer.

Plain mode uses Question, Idea, Blocker, Reply, Decision, and Handoff labels.
Expert mode may additionally show IDs, provenance, linkage, dedupe receipt,
and promotion audit state. Both modes expose identical authority; plain mode
must not hide that discussion is non-authoritative.

Every composer displays: “Discussion is reference, not instructions or
approval.” Only `decision`, `lesson`, and `handoff` exchanges show **Promote to
context**, and only to principals with the new `context:promote` scope. A
promotion confirmation names the destination kind and shows the sanitized
preview. There is no bulk or automatic promotion.

## Data model

### `agent_exchanges` (append-only)

- `id uuid primary key`, generated server-side.
- `org_id text not null`; every lookup and uniqueness rule includes it.
- `kind text not null` constrained to `question | proposal | blocker | reply |
  decision | handoff | resolution | supersession`.
- `body text not null`; UTF-8 text, normalized and sanitized at write time,
  maximum 8,000 characters and 64 KiB request body.
- `author_instance_id`, `author_role`, and optional authenticated human subject
  snapshot. Author fields come only from the authenticated principal.
- `created_at timestamptz not null default now()`; client time is provenance,
  never ordering authority.
- `thread_id uuid not null` (self for roots), `reply_to_id uuid null`.
- Optional `job_id uuid`, and optional workflow tuple `workflow_name`,
  `workflow_run`, `workflow_step`. At least `job_id` or the complete workflow
  tuple is required. If both exist, both must resolve in the same tenant.
- Optional `resolves_exchange_id` and `supersedes_exchange_id`; state is
  derived from later append-only rows. Original rows are never updated.
- `provenance jsonb not null`: source surface, client request ID, and optional
  Score digest/run/task/attempt. It cannot contain credentials or arbitrary
  headers.
- `idempotency_key text not null`, capped at 128 characters. Unique on
  `(org_id, author_instance_id, idempotency_key)`. Same key plus same canonical
  request returns the original row; same key plus different content returns
  `409`.
- `body_sha256 text not null` for audit comparison, not global dedupe.

Indexes cover `(org_id, created_at desc, id desc)`, `(org_id, thread_id,
created_at, id)`, job linkage, and workflow run linkage. Foreign keys must not
allow cross-tenant reference; enforce tenant equality in the transaction even
where the current schema lacks composite keys.

### `agent_exchange_promotions` (append-only)

- Deterministic `id` is UUID5 over the fixed application namespace plus
  `org_id | exchange_id | target_kind | idempotency_key`. Do not change the
  existing UUID5 namespace or recipes used elsewhere.
- Stores source exchange ID/hash, authenticated promoter, target kind,
  sanitized title/body hashes, resulting `agent_context.id`, timestamp, and
  audit correlation ID.
- Unique `(org_id, exchange_id, target_kind)` makes promotion idempotent. A
  second compatible request returns the receipt; an incompatible request
  fails `409`.
- The exchange and promotion receipt remain immutable. The canonical context
  row uses the existing `remember()` sanitation and tenant rules.

Postgres gets explicit no-update/no-delete triggers on both tables. LocalStore
adds them to `APPEND_ONLY_TABLES` and uses the same natural/unique constraints.
Resolved and superseded are projections, not mutable booleans.

## API contract

- `POST /api/exchanges` requires `context:write`, derives org/author from auth,
  validates linkage in that org, sanitizes body, and appends one row.
- `GET /api/exchanges` requires `context:read`; filters are `job_id`, complete
  workflow tuple, `thread_id`, and kind. At least one linkage filter is
  required. Limit defaults to 50, maximum 100. Cursor is an opaque,
  tenant-bound encoding of `(created_at,id)`; offset pagination is forbidden.
- `GET /api/exchanges/{id}` requires `context:read` and returns the thread plus
  derived resolved/superseded state. A foreign-tenant ID returns `404`.
- `POST /api/exchanges/{id}/promotions` requires `context:promote`, accepts
  only `decision | lesson | handoff`, requires an idempotency key, writes
  canonical context and its receipt in one transaction/outbox boundary, and
  appends an audit event. Missing transactional support fails closed.
- There is no update or delete endpoint. Resolution and supersession use
  `POST /api/exchanges` with typed linkage.

Use existing bearer/session authentication and rate limiting. Add scopes
without changing token format, `MCO_*` variable meanings, `~/.mco` paths, CLI
defaults, database contracts, AES-256-GCM vault format/AAD, or any existing
UUID5 recipe. Existing `/api/context`, `mco remember`, `mco recall`, MCP tools,
and console routes remain compatible. New CLI/MCP commands are additive only.

Validation errors are stable 4xx responses with no raw SQL, token, body, or
cross-tenant existence detail. Storage/audit/promotion uncertainty returns a
retry-safe 5xx and never reports success. Logs contain IDs and hashes, not
exchange bodies or authorization headers.

## Authority and information flow

```text
authenticated author -> append exchange -> thread/read model
                                      |       (never prompt-injected)
                                      v
                    explicit promote request + context:promote
                                      |
                     sanitize + tenant/linkage/idempotency checks
                                      |
                     canonical agent_context + audit receipt
                                      v
                         eligible for normal Drumline recall
```

An exchange is never treated as a grant, approval, review verdict, Score
transition, job status change, or platform action. The UI must not render an
exchange “decision” as an approval. Workers receive exchanges only when the
current job prompt explicitly asks the authenticated API for that thread; the
automatic Drumline injection path continues to read only `agent_context`.

## Live updates and accessibility

After the append transaction commits, publish a sanitized `exchange.created`
event through the existing authenticated WebSocket manager. Subscription
filters include org and linkage; server-side org filtering is mandatory.
Reconnect uses the last cursor and falls back to paginated HTTP. Events are
hints: clients dedupe by exchange ID and re-read authoritative rows.

The timeline is keyboard navigable with semantic list/article elements;
composer controls have labels and described input caps; kind and state are
communicated with text/icons, never color alone. New messages announce through
polite live regions without stealing focus. Thread indentation is not the only
reply cue. Focus returns to the submitted item, reduced-motion preferences are
honored, and plain/expert toggles preserve the selected thread.

## Threat and abuse analysis

- **Prompt injection / false authority:** sanitize on append and again on
  promotion; never auto-inject exchanges; label them non-authoritative.
- **Cross-tenant inference:** scope every query, cursor, relation, WebSocket
  subscription, uniqueness check, and promotion transaction by org; return
  `404` for foreign IDs.
- **Forged authors/provenance:** ignore client author/org/time fields. Record
  the authenticated principal and server time.
- **Replay/duplicate submit:** bounded idempotency keys and canonical request
  hashes; conflict on key reuse with different bytes.
- **Thread bombs / storage abuse:** request/body/tag caps, maximum page size,
  global rate limit plus per-principal compose budget, bounded reply depth in
  presentation, and no recursive SQL traversal.
- **Unsafe links/markup:** store text, render escaped text, allow no raw HTML,
  and add safe link attributes if linkification is later enabled.
- **Promotion bypass:** separate scope, allowed source kinds, same-tenant
  transaction, immutable audit receipt, no caller-selected author/weight, and
  no automatic/background promotion.
- **Race/partial failure:** transactionally bind context write, promotion
  receipt, and outbox event. On uncertain commit, retry the same idempotency
  key and read the receipt.
- **History rewriting:** database triggers and LocalStore append-only guards;
  resolution/supersession are later rows.
- **Secret leakage:** sanitize/redact known token patterns, reject provenance
  keys such as authorization/cookie, never log bodies, and run secret scanning
  on commits and CI.

## Migration and rollback

1. Add the two new tables, constraints, indexes, append-only triggers, scopes,
   and LocalStore metadata. Existing rows and APIs are untouched.
2. Ship read APIs and write path behind `MCO_AGENT_EXCHANGE=false` by default
   for one release. Enabling it is additive; no cloud/AWS work is required.
3. Ship the console subview and authenticated live events. Then enable locally
   after focused and full tests.
4. Rollback disables the feature flag and old code ignores the new tables.
   Do not drop exchange data during rollback. A later, separately reviewed
   migration may archive/drop only after retention approval.

If migration application partially fails, the gateway keeps the feature off;
health remains independent and `/api/exchanges` returns a bounded unavailable
response. Never fall back to writing discussion into `agent_context`.

## Acceptance tests

Storage/API:

- Append and read each type; update/delete/upsert fail on both backends.
- Missing, partial, nonexistent, and foreign-tenant linkage fails.
- Author/org/time spoofing is ignored; missing scopes are denied.
- Same idempotency request returns one row; changed request returns `409`.
- Cursor pagination is stable with equal timestamps, capped, tenant-bound, and
  duplicate-free during concurrent appends.
- Sanitization, byte/character caps, escaped rendering, provenance allowlist,
  and secret redaction are covered.
- Resolution/supersession projections preserve original rows.
- Exchange rows never appear in recall or automatic worker prompt injection.
- Promotion accepts only eligible types, is separately scoped, sanitized,
  audited, same-tenant, idempotent, and transactionally safe.

UI/live/accessibility:

- Navigation and title say Drumline in plain and expert modes while the stable
  internal route remains `memory`.
- Context and Agent Exchange subviews preserve mode and selection.
- Compose/read/reply/resolve/supersede/promote flows show authority labels and
  safe errors; no discussion item masquerades as an approval.
- Authenticated same-org live updates arrive once; foreign-org events do not;
  reconnect/cursor recovery works.
- Keyboard-only operation, focus order, live-region behavior, accessible
  names, contrast, non-color state, and reduced motion pass automated and
  manual checks.

Governance/release:

- Focused Score tests validate all six phases and prove a rejected R01 leaves
  B01 and M01 blocked.
- B01 begins only after an independent R01 verdict bound to the exact D01 SHA.
- Console bundling verification is line-ending invariant: the source/bundle
  comparison reports `0 differ` for both LF and CRLF fixtures/checkouts.
- R02 is bound to the exact implementation head and includes security,
  authority, focused/full test, build-console, and secret-scan receipts.
- M01 cannot merge unless R02, CI, PostgreSQL acceptance where required,
  security tests, and CodeQL pass for the reviewed SHA. There is no manual
  merge-around path.
- D02 deploys only the merged SHA from a clean package/checkout, records the
  rollback target, verifies health/API, and proves the actual gateway console
  DOM in a real browser.

## Governed phase behavior

The companion Score is serial and budget-zero. Every phase has a distinct
review role, explicit resource/path bounds, exact evidence, bounded attempts,
and terminal escalation to the mission owner when attempts exhaust. R01 and
R02 each have one attempt: rejection is terminal for downstream phases. B01
may receive one bounded fix attempt before R02, but an R02 rejection cannot
loop automatically: it requires mission-owner escalation and a new governed
Score revision/run before any repaired head returns through exact-head R02.
Security or CodeQL failure is evidence failure, so M01 is not dispatchable.
No phase grants cloud/AWS authority.
