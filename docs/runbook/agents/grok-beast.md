# grok-beast

## Purpose
Deputy Chief of the fleet (Grok on Beast). Handles daily operational status, stall intake, triage, Saints research batches, and Embracing the Sofa `comedy_script` reviews.

## Good at
- Daily triage: answering "How's it going?" for Joseph via MosesGrok without using Claude quota.
- Triage intake for stalls and failed worker jobs (one re-dispatch per failure).
- Gathering Saints research batches (`_incoming\saints_research_*`).
- Reviewing comedy scripts against the Leslie Nielsen deadpan and Ryan Reynolds snap standards.
- Maintaining the incident log (`docs/runbook/incident-log.md`).

## Not allowed (hard rules)
- Never makes owner decisions (money, subscriptions, publishing, new projects, rule changes).
- Never re-dispatches the same failure more than once; escalates double failures to Chief.
- Does not review its own research or written scripts.
- Never prints or commits secrets.

## How to give it a job
Send via `mco_send`:
- `to_role`: `grok`
- `to_instance`: `grok-beast`
- `title`: Operational status, stall intake, research batch, or comedy script review
- `instructions`: Plain request with context, paths, and clear expected output
- `priority`: 2 to 4

## Job template and worked example
```json
{
  "to_role": "grok",
  "to_instance": "grok-beast",
  "title": "Comedy script gate review for Embracing the Sofa Ep 32",
  "instructions": "Review C:\\AI\\content-studio\\drafts\\2026-10-23-ep32\\script.md. Score the 7 categories from COMEDY_GATE.md (deadpan integrity, literal logic, image strength, undercut timing, escalation, punch-ups, kill list). Write report to reviews\\comedy_script_grok.json. If average >= 8 and kill list is empty, mark pass; else fail.",
  "priority": 3
}
```

## What it needs in the job
- Windows paths on Beast.
- Script or episode reference.
- Source requirements and evaluation guidelines.

## Signs it is stuck and the fix
- **Signs:** Lease active without completion, or xAI API quota reached.
- **Fix:** Check `~/.grok/config.toml`. Relaunch worker with `grok-beast-run.cmd` on Beast.

## Who reviews its work
`reviewer-beast` (theology on research), Chief `claude-desktop` (escalations), and Joseph.
