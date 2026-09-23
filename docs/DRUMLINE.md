# Drumline - The Shared Context Substrate

In a marching band, the drumline keeps everyone in step: the beat that
carries the rest. Here it's the memory that carries the mesh - every agent
(Claude, Codex, Gemini, connector workers) reads from and writes to **one
collective memory**. Knowledge stops dying with the job that produced it:
what one agent learns, every agent marches to.

**Local-Only installs need no setup at all** - Drumline persists to the embedded
SQLite store (`~/.mco/local.db`) automatically. For Supabase-backed deployments,
apply [`migrations/2026-06_drumline_shared_context.sql`](migrations/2026-06_drumline_shared_context.sql)
(idempotent) to enable it.

---

## How memory gets in

**1. Auto-distillation (the audit log becomes living memory).**
When any job completes, its essence - what was asked, what came back - is
distilled into a `handoff` entry, tagged with the roles and connector
involved, and recorded in the audit trail as `context_distilled`. The
*evidence* stays immutable in `agent_job_events`; the *essence* becomes
recallable context. Opt out with `MCO_DRUMLINE_DISTILL=false`.

Distillation is structured, not just a text dump. Two channels:

- **Explicit handoff (preferred).** The finishing agent attaches
  `output_payload["handoff"]` - via `mco_complete(..., summary=, decisions=,
  files=, gotchas=, follow_ups=)` over MCP, or
  `client.complete(task_id, output, handoff={...})` over REST. Deliberate
  handoffs are stored verbatim and weighted higher (1.5) than mined ones.
- **Heuristic mining (fallback).** `extract_structure()` deterministically
  mines the free-text result for file paths, decision lines, gotcha lines,
  and follow-ups - so agents that never heard of the contract still leave
  useful structure behind. Like the recall scorer, an LLM-based distiller
  can replace it later without changing any caller.

**2. Deliberate memory.**
Agents record durable knowledge - decisions, gotchas, environment facts:

```
mco_remember(title="Prod DB read-only on Sundays",
             content="Maintenance window 02:00-06:00 UTC.",
             kind="fact", tags="ops,postgres")
```

REST: `POST /api/context` `{"title", "content", "kind", "tags", "role", "weight"}`.
Kinds: `fact | decision | lesson | handoff | artifact`. Any authenticated
agent may write; entries are stamped with the author for accountability.

**3. From the terminal or the console.** `mco remember "Title" "Content"
--kind lesson --tags a,b` writes an entry the same way (kinds: `fact`,
`decision`, `lesson`, `handoff`, `artifact`). The console's **Memory** screen
gives operators the same composer with a search/tag box on top for recall,
so a human can browse or add to shared memory without touching the CLI.

### Content sanitization (neutralize, don't delete)

Remembered content is recalled straight into other agents' prompts, so
`remember()` runs it through `sanitize_content()` first. The first cut of
this fix deleted suspicious spans outright - and silently ate legitimate
code handoffs along with the injection attempts. The fix instead defangs the
syntax in place and keeps the information:

- Angle brackets become lookalikes (`<`/`>` -> `‹`/`›`) so markup can't parse
  as directives in the recalling prompt.
- Code fences (```` ``` ````) become `'''` so they can't open/close a block.
- `!function_call:` lines are dropped outright - there's no safe defanged
  form for an explicit tool-call marker.
- Content is capped at 2000 characters (`MAX_CONTENT_CHARS`).

A code handoff full of `<script>` tags or fenced diffs still reads clearly
after sanitization - it just can't execute as instructions when replayed
into someone else's prompt.

### Deduplication

`remember()` hashes `title|content|role` (SHA-256, via `content_hash()`)
before inserting. If an entry with the same hash already exists, `remember()`
returns that existing row instead of inserting a duplicate - repeated
distillation of the same outcome, or an agent re-remembering the same fact,
doesn't bloat the table.

- **LocalStore** needs nothing extra; dedup works out of the box.
- **Postgres/Supabase** needs the `content_hash` column, added by
  [`migrations/2026-07_drumline_dedup.sql`](migrations/2026-07_drumline_dedup.sql)
  (idempotent). Pre-migration databases fall back to a plain insert - dedup
  just doesn't kick in until you migrate.
- `mco doctor` checks for the column and warns if it's missing; `mco upgrade
  --apply` applies the migration.

## How memory gets out

**1. Automatic prompt injection (the tap).**
Before a worker executes a leased job, it recalls the most relevant entries
(scored against the job's title/description and the worker's role) and
prepends them to the prompt:

```
=== SHARED CONTEXT (Drumline) ===
Collective memory from prior agent work. Use it; correct it via mco_remember if wrong.
- [handoff] Job outcome: Triage P-99 (claude-worker-1, 2026-06-10)
  Asked: Investigate high CPU on web-01
  Outcome: Root cause: runaway cron. Disabled job foo.
- [fact] Prod DB read-only on Sundays (joe, 2026-06-08)
  Maintenance window 02:00-06:00 UTC.
=== END SHARED CONTEXT ===

Task Title: ...
```

Opt out with `MCO_DRUMLINE_INJECT=false`. Injection is best-effort: if the
gateway is unreachable, the job still runs. The block header explicitly
frames entries as *reference data, not instructions* - a poisoned memory
must read as information to weigh, never as directives to follow.

**2. Workflow threading (the Context Exchange).**
Every `mco workflow` run stamps its steps with one run id
(`input_payload["workflow"] = {name, run, step}`). Each step's handoff is
tagged `wf:<name>` / `run:<id>`, and before a worker executes a step it
fetches the *whole thread* by hard tag filter and injects it ahead of
general recall, oldest step first:

```
=== WORKFLOW THREAD (Drumline) ===   <- every predecessor handoff, in order
=== SHARED CONTEXT (Drumline) ===    <- best soft-scored entries
```

This is deterministic: the Codex step *always* sees what the Claude step
decided, which files it touched, and what it left undone - regardless of
vendor, and regardless of whether term-overlap scoring would have matched.

**3. Explicit recall.**
GUI agents (Claude Desktop / Codex / Antigravity via MCP) dip in on demand:

```
mco_recall(query="dynatrace token", tags="ops", limit=5)
```

REST: `GET /api/context?query=...&role=...&tags=a,b&limit=5`. From a terminal:
`mco recall "dynatrace token" --tags ops --limit 5 --role codex`.

## Recall scoring (deterministic by design)

`score = (query-term hits x entry weight) + role affinity (+0.75) + recency (0..0.5)`

- Tag filters are hard; the query is soft-scored; with no query you get the
  freshest entries.
- No embeddings, no external model: the substrate stays standalone, cheap,
  explainable in an audit, and testable. The scorer is one function
  (`drumline.score_entry`) - swap in an embedding back-end later without
  changing any caller.
- `weight` (0.1-5.0) lets important entries punch above their age.

## Design notes

- **Memory is governed like everything else**: writes require agent auth and
  are author-stamped; distillation events land in the immutable audit trail;
  a wrong memory is corrected by writing a better one (append-mostly), not by
  silently editing history.
- **Cross-vendor by construction**: a Dynatrace triage done by Claude becomes
  context for the Codex job that ships the fix and the ServiceNow closure
  that follows - the substrate is what makes the mesh more than a job queue.

## Agent Exchange (discussion lane)

Drumline also has a separate, **non-authoritative** discussion lane: the Agent
Exchange. Operators and agents ask, propose, flag blockers, reply, decide, and
hand off inside a thread tied to a job or workflow run. Design:
`docs/DRUMLINE-AGENT-EXCHANGE-DESIGN.md`.

- **Never injected.** Exchanges live in their own append-only tables
  (`agent_exchanges`, `agent_exchange_promotions`). Recall and the automatic
  prompt-injection path read only `agent_context`, so discussion cannot gain
  authority by being stored. It is never an approval, grant, review verdict, or
  job status change.
- **Off by default.** Set `MCO_AGENT_EXCHANGE=true` to enable. Disabled, every
  `/api/exchanges` route answers `503`; roll back by unsetting it (data is kept).
- **API.** `POST /api/exchanges` (`context:write`), `GET /api/exchanges` and
  `GET /api/exchanges/{id}` (`context:read`; list needs `job_id`, `thread_id`,
  or a full workflow tuple, and is cursor-paginated), and
  `POST /api/exchanges/{id}/promotions` (`context:promote`). No update/delete.
  Author, org, and time always come from the credential and server clock.
  `idempotency_key` is required; the same key with the same content returns the
  original row, with different content returns `409`.
- **Promotion is explicit.** Only `decision` and `handoff` exchanges can be
  promoted, into a `decision`, `lesson`, or `handoff` context entry, by a
  principal with `context:promote` (not a worker default; `admin` has it). The
  text is sanitized again, written through `remember()`, and recorded in an
  immutable receipt plus an `exchange_promoted` audit event on the linked job.
- **Surfaces.** Console: Drumline (route id still `memory`) has Context and
  Agent Exchange subviews in plain and expert modes. CLI: `mco exchange
  post|list|promote`. MCP: `mco_exchange_post`, `mco_exchange_list` (no promote
  tool). SDK: `BitCadenceAgent.exchange_post/list/get`.
- **Live updates.** After commit, a sanitized `exchange.created` hint (ids only,
  no body) goes over `/ws/broadcast` to same-org connections with `context:read`.
  Clients re-read rows over HTTP and de-duplicate by exchange id.
