# claude-light

## Purpose
Lightweight worker (Claude Haiku). Executes fast chores, small status checks, text formatting, and simple routine tasks.

## Good at
- Quick log inspections and triage summaries.
- Single-file markdown formatting or linting.
- Simple JSON validation.
- Low-cost, fast-turnaround background chores.

## Not allowed (hard rules)
- Must fail back complex jobs (large refactors, architecture decisions, media generation).
- Never makes policy or theology judgments.
- Never prints or commits secrets.

## How to give it a job
Send via `mco_send`:
- `to_role`: `light`
- `to_instance`: `claude-light`
- `title`: Short chore description
- `instructions`: Self-contained, single-step instruction with small file scope
- `priority`: 0 to 2

## Job template and worked example
```json
{
  "to_role": "light",
  "to_instance": "claude-light",
  "title": "Format Markdown tables in documentation drafts",
  "instructions": "In /Volumes/AI/content-studio/docs/improvement/, read candidate_fixes.md, format the markdown tables cleanly with aligned pipes, and verify headers match the source schema. Return diff in handoff.",
  "priority": 1
}
```

## What it needs in the job
- Single file path or small snippet.
- Very specific, narrow acceptance criteria.
- Low token payload.

## Signs it is stuck and the fix
- **Signs:** Job fails immediately with capacity/complexity warning, or sits untaken.
- **Fix:** Re-route to `claude-code` or `codex-beast`.

## Who reviews its work
The requesting agent, or Deputy `grok-beast`.
