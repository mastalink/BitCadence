# Jev Routing & Decision Provider

## Goal
Use Jev — a deterministic, bounded System One decision provider — to make fast, structured routing decisions, recommend optimal model providers for tasks, and classify incoming work without authorizing side effects.

---

## Step-by-Step Instructions

### 1. The Role of Jev in BitCadence
Jev provides **programmable common sense** as a core architectural primitive:
- **Annotation-Only:** Jev only classifies, ranks, and recommends. It never executes shell commands or writes database state directly.
- **Structured Decisions:** Turns natural language task descriptions into typed decisions (e.g., capability requirements, risk categories, optimal model selections).
- **Zero Hallucination Routing:** Evaluates objective criteria (latency, token costs, coding capability) to pick the right worker agent.

### 2. Testing Model Routing in the Console
1. Navigate to **Settings → LLM Connections / Routing**.
2. Enter a task scenario:
   - Example prompt: *"Refactor payment gateway webhook retry logic with idempotency guarantees."*
3. The routing advisor analyzes the requirements and outputs:
   - **Recommended Model:** `claude-3-7-sonnet` (high architectural reasoning) or `codex-davinci` (code generation).
   - **Confidence Score:** e.g., `0.94`.
   - **Rationale:** Explains why this model was chosen based on prompt complexity and coding requirements.

---

## The CLI Equivalent

Run Jev decision queries directly from your shell:

```powershell
# Query Jev model routing recommendation
mco jev route-model "Write comprehensive unit tests for local SQLite WAL concurrency"

# Inspect Jev operations health
curl http://127.0.0.1:18789/api/jev/health `
  -H "Authorization: Bearer $env:MCO_AGENT_TOKEN"
```

---

## What You'll See

Jev outputs a structured JSON evaluation:

```json
{
  "recommendation": "codex",
  "confidence": 0.95,
  "reasoning": "Task involves local filesystem and unit test generation. High fit for codex worker.",
  "fallback_role": "reviewer",
  "risk_tier": "low"
}
```

---

## If It Goes Wrong

### 1. "Jev provider unavailable or timeout"
- **Cause:** Local Jev socket or model connection timed out.
- **Fix:** BitCadence falls back gracefully to deterministic rule-based matching if Jev is unreachable. Verify connection in `mco doctor`.

### 2. "Confidence below threshold"
- **Cause:** Task prompt was ambiguous or lacked specific implementation details.
- **Fix:** Provide clearer instructions in the job description naming specific languages or frameworks.
