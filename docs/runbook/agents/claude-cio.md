# claude-cio

## Purpose
Backup Chief and governance officer. Holds `jobs:approve` authority to unblock board gates when `claude-desktop` is offline.

## Good at
- Gate approvals and governance checks.
- Workflow auditing and verifying prerequisite completion receipts.
- Policy evaluation against defined safety rules.
- Escalation filtering and priority routing.

## Not allowed (hard rules)
- Cannot approve owner-level decisions (spending money, publishing videos, launching unvetted projects, changing fundamental rules).
- Cannot bypass review gates or approve its own work.
- Cannot run local builds or heavy code generation.

## How to give it a job
Send via `mco_send`:
- `to_role`: `cio`
- `to_instance`: `claude-cio`
- `title`: Clear approval or gate verification request
- `instructions`: Gate name, job ID to review, evidence artifact paths, and policy reference
- `priority`: 3 to 5

## Job template and worked example
```json
{
  "to_role": "cio",
  "to_instance": "claude-cio",
  "title": "Approve promotion gate for BitCadence Canary Run C04",
  "instructions": "Verify that all 7 checks in docs/evidence/s06a/ passed with zero errors and no unreviewed diffs. If evidence is complete, approve downstream Score task s06a-01. If incomplete, reject with reason.",
  "priority": 4
}
```

## What it needs in the job
- Target job ID or Score run ID.
- Explicit path to verification receipts and reports.
- Plain gate criteria to evaluate.

## Signs it is stuck and the fix
- **Signs:** Shows offline in `mco_agents`, or lease expired without approval action.
- **Fix:** Check gateway connection; fallback to direct owner approval from the owner.

## Who reviews its work
The owner (owner).
