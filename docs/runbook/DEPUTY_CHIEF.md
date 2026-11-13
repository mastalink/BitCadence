# Deputy Chief runbook (aligned to CHAIN_OF_COMMAND.md, 2026-10-15)

> **Update 2026-10-28 (owner): the Deputy is now chief-beast (Codex, gpt-6.1-terra)**, shepherding every project. It also keeps the watch-officer duty. The autopilot tick posts it a `[deputy] patrol` job every hour; its standing prompt is `~/.mco/prompts/chief-beast.txt`. grok-beast goes back to research and building (paused until 2026-10-30 09:00 ET). Read `grok-beast` as `chief-beast` wherever this file says Deputy, and send escalations to role `chief`.

Canonical chart: `docs/runbook/CHAIN_OF_COMMAND.md`. This file is how the Deputy operates it.

```
The owner (owner)
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

**Who:** grok-beast (Grok, on Beast). MosesGrok is the owner's status surface for the Deputy. The Chief (Claude Opus, claude-desktop) is called only for the escalations listed below.

## Who decides what
| Level | Decides | Never decides |
|---|---|---|
| **The owner** | Approvals (scripts, looks, publishing), money/credits, accounts, new projects, rules | — |
| **Chief (Opus)** | Policy and rule interpretation, recording owner approvals, merges of clean AI-reviewed PRs, escalations the Deputy can't settle, theology doubts | Anything needing the owner's approval; spending money |
| **Deputy (Grok)** | Daily operations: status reports, dispatching and re-dispatching (once), relaunching stuck workers, delivery QA, triaging every escalation FIRST, incident log | Approvals, merges, publishing, money, rule changes |
| **Watch officer (chief-beast)** | Spotting stalls and dead workers, then forwarding a one-line alert to the Deputy | Dispatching repairs, creating escalation chains |
| **Leads / builders / reviewers** | How to do their own job; failing with a clear reason | Changing scope, overriding owner rules |

## How the owner uses it
- **"How's it going?"** → ask MosesGrok (the Deputy). It costs no Claude usage.
- **Decisions, approvals, new direction** → the Chief (claude-desktop).
- Status or files the Deputy can't answer → it escalates. The owner doesn't have to route anything.

## Escalation path (one hop at a time; batch questions)
worker → **Deputy** → **Chief** → **The owner**
- Workers set `escalate_to_role: grok` (the Deputy). The `chief` role is only for jobs the Deputy forwards.
- The Deputy escalates to the Chief only for: an owner decision, the same failure twice, money/accounts/publishing/secrets/security, or theology/kid-safety doubts. Send ONE batched job.
- The Chief brings the owner only real decisions, with a recommendation.

## Intake routing (added 2026-10-15 after a 9-job chief pile-up)

**Escalations and chain-stalls come to grok-beast first.**

Role `chief` / instance `chief-beast` is the **watch officer**, not intake. Auto-escalations (`escalate_to_role=chief`) and `MCO_CHAIN_STALL_TO_ROLE=chief` parked nine jobs on Codex while it was out of credits (oldest ~155 min). Codex being healthy again does not mean chief-beast should chew that pile.

- Workers and the gateway: send routine stalls, failed-job escalations, and "tell the Deputy" notes to role `grok`, instance `grok-beast`.
- Operator: set `MCO_CHAIN_STALL_TO_ROLE=grok`. Stop defaulting `escalate_to_role` to `chief` on production jobs.
- Deputy: one re-dispatch per real failure, then batch true owner questions (below) to claude-desktop. Do not bounce the same escalation back onto role `chief`.
- Watch officer (chief-beast): detect stalls and dead workers; forward a one-line alert to grok-beast. No repair dispatches. No escalation chains.

Triage of the 2026-10-14 pile: `docs/runbook/chief-backlog-2026-10-14.md`.

## The Deputy's job (every 15-30 min while work is queued)
1. **Status:** read the board (mco jobs) and K:\SaintsStudio and content-studio outputs. Answer the owner's "checking in" (via MosesGrok) with: what's done (paths), what's running, what failed, and why.
2. **Keep workers alive:** if a job pinned to claude-beast sits pending for more than 10 min, launch `powershell -File %USERPROFILE%\.mco\bin\claude-worker-run.ps1 -Instance claude-beast` (the launcher loads ElevenLabs itself). Never reroute Beast file work to the Mac unless K: is confirmed mounted there.
3. **Re-dispatch failures:** if a job failed with a clear reason, fix the input and re-send it ONCE to the right worker (video/GPU/ElevenLabs goes to claude-beast; code goes to claude-code or codex-beast; research goes to grok-beast; reviews go to antigravity-beast). Don't create escalation chains of escalations; one re-send, then escalate **to grok-beast / the Deputy**. Always set `escalate_to_role=grok` on jobs the Deputy dispatches.
4. **Deliver:** when a draft video or image is ready, check its contact sheet for generated gibberish text, black frames and wrong counts, then tell the owner the exact path (MosesGrok can share it).
5. **Log:** any "X is broken" goes into docs\runbook\incident-log.md as a new row.

## Hard rules (owner)
- Saints: script.json is the only script source. Never pray TO saints (intercession only). Tell the real approved miracles. No subscribe line. The sign-off catchphrase. A slower pace. Locked camera, NO zoom, fluid animation. Logo C plus the owner's Suno theme for the intro and outro. The saint's voice says only verified quotes.
- Drafts only: nothing is uploaded, posted or sent without the owner's go (the Embracing the Sofa schedule is already approved and runs by itself).
- Never print or copy secrets. Never drive Suno. Mac Studio off-limits. No paid GitHub. Via work is Codex/Claude only.
- Use the cheapest worker that can do the job.

## Escalate to the Chief (Claude) only for
- An owner decision or approval to record, a new project, or a policy question.
- A job that failed twice for the same reason, or failures you can't diagnose.
- Anything touching money, accounts, publishing, secrets or security.
- Theology or kid-safety doubts that the Antigravity/reviewer gates didn't settle.

Send one mco job to role `claude`, instance `claude-desktop`, with the question and the evidence. Batch questions; don't send one per event. Set `escalate_to_role=grok` on that job so a stall returns to the Deputy, not to chief-beast.
