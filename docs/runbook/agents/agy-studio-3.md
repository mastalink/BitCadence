# agy-studio-3

## Purpose
Gemini video reviewer and documentation/runbook builder on Mac Studio. Operates video `watch` reviews, comedy cut assessments, prop design audits, and fleet documentation tasks.

## Good at
- Comprehensive video review: listening to voice takes, evaluating music balance, and checking visual transitions.
- Authoring exact, plain-English runbooks and operational playbooks.
- Checking script and audio alignment against subtitle timings.
- Auditing prop tracking and overlay quality.

## Not allowed (hard rules)
- Cannot use native Windows drive letters; use `/Volumes/AI/` and `/Volumes/beast/`.
- Never run darkbloom inference commands, load local LLMs, or disturb Mac Studio memory.
- Never run `/usr/bin/python3` (use `/opt/homebrew/bin/python3`).
- Do NOT run `shepherd.py` directly; writes `<gate>.verdict.json` for Shepherd ingestion.
- Never reviews its own creations.

## How to give it a job
Send via `mco_send`:
- `to_role`: `antigravity`
- `to_instance`: `agy-studio-3`
- `title`: Video review, documentation, or design task
- `instructions`: Detailed instructions with Mac mount paths, sha256 checksums, and explicit criteria
- `priority`: 2 to 4

## Job template and worked example
```json
{
  "to_role": "antigravity",
  "to_instance": "agy-studio-3",
  "title": "Watch gate review for Saint Frances Cabrini cut",
  "instructions": "Inspect /Volumes/beast/SaintsStudio/saints/2027/1113_frances-cabrini/out/cabrini_v1.mp4 (sha256: 3c91e...). Verify animation covers every narration line with no frozen frames exceeding 1s. Confirm soft dissolves only and no iris wipes. Save report and verdict JSON to /Volumes/beast/SaintsStudio/saints/2027/1113_frances-cabrini/reviews/watch.verdict.json.",
  "priority": 3
}
```

## What it needs in the job
- Mac mount paths (`/Volumes/AI/...`, `/Volumes/beast/...`).
- sha256 checksum of the target file.
- Required verdict JSON structure.

## Signs it is stuck and the fix
- **Signs:** Shell task backgrounded or hanging on external dependencies.
- **Fix:** Keep operations bounded; do not launch long-lived background watchers; restart worker.

## Who reviews its work
Video Shepherd `tick` validates verdict JSON; Joseph.
