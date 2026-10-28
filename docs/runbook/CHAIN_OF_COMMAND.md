# Chain of command (owner request 2026-10-14)

```
Joseph (owner)
  └─ Chief: Claude Opus (claude-desktop); backup when offline: claude-cio
       └─ Deputy Chief: grok-beast (Grok); owner reaches it via MosesGrok
            ├─ Watch officer: chief-beast (Codex). Detects stalls only, then forwards to the Deputy
            ├─ Leads:  Saints Storybook → grok-beast / MosesGrok (research + scripts)
            │          Embracing the Sofa → claude-beast + Muse
            │          BitCadence code → claude-code (Codex-beast when pinned)
            ├─ Builders: claude-beast (video/GPU/ElevenLabs), claude-code (code, scripts, Blender),
            │            codex-beast (code, ONLY when pinned by name)
            └─ Reviewers: antigravity-beast (facts, code review), reviewer-beast (theology, kid safety)
```

## Who decides what
| Level | Decides | Never decides |
|---|---|---|
| **Joseph** | Approvals (scripts, looks, publishing), money/credits, accounts, new projects, rules | — |
| **Chief (Opus)** | Policy and rule interpretation, recording owner approvals, merges of clean AI-reviewed PRs, escalations the Deputy can't settle, theology doubts | Anything needing Joseph's approval; spending money |
| **Deputy (Grok)** | Daily operations: status reports, dispatching and re-dispatching (once), relaunching stuck workers, delivery QA, triaging every escalation FIRST, incident log | Approvals, merges, publishing, money, rule changes |
| **Watch officer (chief-beast)** | Spotting stalls and dead workers, then forwarding a one-line alert to the Deputy | Dispatching repairs, creating escalation chains |
| **Leads / builders / reviewers** | How to do their own job; failing with a clear reason | Changing scope, overriding owner rules |

## Escalation path (one hop at a time; batch questions)
worker → **Deputy** → **Chief** → **Joseph**
- Workers set `escalate_to_role: grok` (the Deputy). The `chief` role is only for jobs the Deputy forwards.
- The Deputy escalates to the Chief only for: an owner decision, the same failure twice, money/accounts/publishing/secrets/security, or theology/kid-safety doubts. Send ONE batched job.
- The Chief brings Joseph only real decisions, with a recommendation.

## How Joseph uses it
- **"How's it going?"** → ask MosesGrok (the Deputy). It costs no Claude usage.
- **Decisions, approvals, new direction** → the Chief (this app).
- Status or files the Deputy can't answer → it escalates. Joseph doesn't have to route anything.
