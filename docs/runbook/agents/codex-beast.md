# codex-beast

## Purpose
Code builder on Beast, secondary media backup builder, and fact checker for Saints Storybook research.

## Good at
- Running, testing, and fixing build scripts (`carlo_v12_*.py`, `carlo_motion.py`).
- Stand-in media builds when Claude usage is exhausted.
- Saint fact checks against causesanti.va and Vatican historical archives.
- Automated QA scripts and regression suites.

## Not allowed (hard rules)
- Must be pinned by name (`to_instance: codex-beast`). No auto-fallback exists.
- Unattended runs must have memories disabled (`prepare-codex-worker.ps1`).
- Never invents miracles, claims, or historical details; unsourced claims must be logged for owner review.
- Never prints or commits secrets.

## How to give it a job
Send via `mco_send`:
- `to_role`: `codex`
- `to_instance`: `codex-beast`
- `title`: Explicit code task, QA script run, or saint fact check
- `instructions`: Self-contained step with all required file paths and validation checks
- `priority`: 2 to 4

## Job template and worked example
```json
{
  "to_role": "codex",
  "to_instance": "codex-beast",
  "title": "Fact-check research draft for Saint Kateri Tekakwitha",
  "instructions": "Read K:\\SaintsStudio\\_incoming\\saints_research_20261020\\kateri\\research.md. Verify every biographical claim against causesanti.va. Check against K:\\SaintsStudio\\config\\banned_claims.txt. Write findings to facts.json. If any item is thinly sourced, mark it in the owner_confirm array. Do not delete good story points.",
  "priority": 3
}
```

## What it needs in the job
- Windows paths (`K:\SaintsStudio\...`, `C:\AI\...`).
- Path to source materials and verification rubrics.
- Exact script commands and test flags.

## Signs it is stuck and the fix
- **Signs:** Jobs accumulate untaken (shows `broken: N job(s) addressed to it untaken`), or exits with credit exhaustion (incident 43).
- **Fix:** Verify OpenAI Codex credits. If worker memory is corrupted or loading stale context, run `prepare-codex-worker.ps1` and launch `codex-beast-run.cmd`.

## Who reviews its work
`reviewer-beast` (theology and kid safety), `antigravity-beast`, and the owner.
