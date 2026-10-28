# antigravity-beast

## Purpose
Gemini QA and code reviewer running on Beast. Conducts code reviews, fact audits, and local video checks when files cannot be shared to Mac.

## Good at
- Code review on Windows checkouts.
- Fact verification and checking against project standards.
- Video QA scripts (`carlo_v12_qa.py`, `carlo_motion.py`).
- Antigravity IDE tool integration on Beast.

## Not allowed (hard rules)
- Never reviews its own code or own build artifacts.
- Never runs heavy GPU rendering or model training.
- Never pushes directly to remote repositories.
- Never logs or exposes credentials.

## How to give it a job
Send via `mco_send`:
- `to_role`: `antigravity`
- `to_instance`: `antigravity-beast`
- `title`: QA review or code audit task
- `instructions`: Path to diff, script, or video, sha256 checksum, and review criteria
- `priority`: 2 to 3

## Job template and worked example
```json
{
  "to_role": "antigravity",
  "to_instance": "antigravity-beast",
  "title": "Code review for BitCadence PR on lease fencing",
  "instructions": "In C:\\AI\\baton\\Batoncadence, review the changes in branch fix/lease-fencing against docs/runbook/incident-log.md. Verify that lease renewals enforce the store incarnation check. Write review report to docs/reviews/pr_lease_fencing.md.",
  "priority": 3
}
```

## What it needs in the job
- Windows paths (`C:\AI\...`, `K:\...`).
- Git diff, commit hash, or target file path.
- Clear pass/fail rubric.

## Signs it is stuck and the fix
- **Signs:** IDE process disconnected from MCP, or worker in standby while jobs wait.
- **Fix:** Restart Antigravity IDE on Beast with `%USERPROFILE%\.mco\bin\antigravity-worker-run.ps1`.

## Who reviews its work
`claude-code`, Chief `claude-desktop`, and the owner.
