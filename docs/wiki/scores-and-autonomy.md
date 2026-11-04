# Autonomous Scores & Conductor

## Goal
Author, validate, and monitor **Scores** — versioned, deterministic JSON contracts that coordinate long-running autonomous development and operations runs with cryptographic evidence, resource locks, and policy gates.

---

## Step-by-Step Instructions

### 1. What is a Score?
Unlike simple jobs or legacy workflows, a **Score** is an immutable, digest-bound contract that defines:
- The complete goal hierarchy and dependency graph.
- Required worker capabilities and role boundaries (independent authors and reviewers).
- Resource locks (such as isolated repository worktrees or deployment lanes).
- Mandatory test evidence and verification receipts.
- Explicit human checkpoints and integer-cent budget limits.

### 2. Watching Score Runs in the Console
1. Open **Home**. Each Score run appears under **What's running** as one card per run, with a state word, a sentence from the worker's progress and the step count from the real plan.
2. Tap a Score card to watch its read-only live drawing.

See [Console Home](Console-home.md) and [Live Score](Score-live.md) for what the cards and the drawing show. The older **Live look** window on the Overview screen no longer exists.

### 3. Validating and Compiling Scores Offline
Before submitting any score, validate and compile it locally using the offline kernel:

```powershell
# 1. Validate score structure against JSON Schema
python -m mco.orchestrator.scores validate examples\scores\via-cloud.score.json

# 2. Compile score into deterministic, digest-bound work packets
python -m mco.orchestrator.scores compile examples\scores\via-cloud.score.json

# 3. Preview planned tasks, dependencies, and capability requirements
python -m mco.orchestrator.scores preview examples\scores\via-cloud.score.json
```

---

## The CLI Equivalent

Manage and advance Score Conductor runs from PowerShell:

```powershell
# Check status of conductor and active score runs
mco score status --run-id <run-id>

# List the runs in the conductor database
mco score list

# Advance the conductor sweep engine
mco score tick --run-id <run-id>

# Grant local development policy authority
mco score grant-local examples/scores/via-cloud.score.json --run-id <run-id> --action <action> --resource <resource>
```

---

## What You'll See

- **Independent Review Enforced:** A worker cannot approve its own work. If `codex-build-1` authors a change, the Conductor requires an independent reviewer (`reviewer-beast` or `gemini-qa-1`) before the task is accepted.
- **Worker Completion ≠ Task Acceptance:** A worker returning `completed` does not advance the score. Only verified evidence artifacts and independent review receipts unlock downstream tasks.
- **Evidence Binding:** Every event is tied to the score document digest (SHA-256) and run ID, guaranteeing that changing a score contract invalidates previously issued approvals.

---

## If It Goes Wrong

### Authenticated completion blocked on "Strict JSON result required"

The bridge accepts both a legacy `output_payload` containing JSON text itself
and the canonical `output_payload.result` containing JSON text. Both must decode
to one strict JSON object: prose, code fences, non-object results, non-finite
numbers, duplicate keys, and malformed JSON are refused. Completion actor/role,
lease identity, immutable job intent, independent reviewer identity, artifact
SHA-256, and exact review evidence/verdict checks still apply. Stored jobs and
results are never rewritten to make them pass.

An existing blocked run stays blocked after upgrading. Following independent
review of the repair, its original owner may explicitly invoke
`Conductor.retry_validation(run_id)` (or `ScoreBridge.retry_validation(run_id,
board)`) once. The command below invokes that same API, uses the normal CLI
credential configuration, and requires explicit database and artifact root paths:

```powershell
# Run from the independently reviewed checkout with the ORIGINAL owner's credential.
$env:PYTHONPATH = 'src'
python -m mco.orchestrator.score_conductor retry-validation `
  --run-id mymeals-current-review-9d1d3ee-20261012 `
  --db "$env:USERPROFILE\.mco\score-runs.db" `
  --root "$env:USERPROFILE\.mco\score-artifacts"
```

Those paths are the CLI defaults; use the run's original paths if it was started
with custom configuration. This command has **not** been executed against the
live run. A successful invocation validates existing completions only; advancing
new work remains an explicit normal tick/sweep operation.

Recovery requires a read-only audit run blocked solely by the exact envelope
error. It preflights original owner credential, authenticated completion events,
all existing completed evidence, independent reviewers, exact passing review
bindings, and unexpired active dispatch deadlines. Rejected/failed jobs or
reviews, other blocking causes, repository-write capabilities, missing events,
or modified previously validated evidence refuse recovery without reopening the
run. It never extends deadlines, issues authority, replaces results, or
redispatches completed reviewers. In particular, **an expired deadline cannot be
recovered with this command**, even if the connector-envelope repair is valid.

One durable `validation_retry` event records the owner, Organization, Score
digest, original blocking event sequence IDs, completed submitted job IDs, and
active deadlines. The retry budget is one per run and survives restarts;
concurrent requests cannot both consume it. Ordinary ticks and sweeps never call
the recovery API automatically. If evidence changes between preflight and polling,
normal validation blocks again and the consumed retry cannot be repeated.

### 1. "Score validation error: Cyclic dependency"
- **Cause:** One goal packet depends on another in a circular loop.
- **Fix:** Run `scores validate` to see the exact cycle path reported by the validator.

### 2. "Score run paused at human checkpoint"
- **Cause:** The score reached a stage with `checkpoint: true` requiring explicit owner authorization.
- **Fix:** Open the Governance or Approvals panel to sign off on the checkpoint decision.

### 3. "Budget reservation exceeded"
- **Cause:** Planned tasks request more compute or token expenditure than the authorized integer-cent limit (`authorized_budget_cents`).
- **Fix:** Issue an updated grant with higher budget limits using `mco score grant-local`.
