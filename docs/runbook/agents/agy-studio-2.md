# agy-studio-2

## Purpose
Gemini video and cut reviewer on Mac Studio. Second parallel reviewer for `watch` and `comedy_cut` gates, listening to audio, inspecting motion continuity, and auditing visual comedy gags.

## Good at
- Multi-modal review: watching MP4s, listening to narration audio, checking sound effects synchronization.
- Evaluating comedy cut timing (undercut buttons, silence before punchlines).
- Verifying Wan 2.2 animation motion coverage (ensuring motion covers every narration line).
- Inspecting visual elements and sight gags on Embracing the Sofa.

## Not allowed (hard rules)
- Cannot use Windows drive paths (`K:\`, `C:\`). Must use Mac paths (`/Volumes/beast/`, `/Volumes/AI/`).
- Never run darkbloom inference commands, load local models, or run memory-heavy tasks on Mac Studio.
- Never run `/usr/bin/python3` (use `/opt/homebrew/bin/python3`).
- Do NOT run `shepherd.py` directly; writes verdict files to `<episode root>/reviews/<gate>.verdict.json`.
- Cannot review builds created by the Gemini family.

## How to give it a job
Send via `mco_send`:
- `to_role`: `antigravity`
- `to_instance`: `agy-studio-2`
- `title`: Video review or comedy timing review
- `instructions`: Explicit Mac paths, file sha256, gate name, and output verdict path
- `priority`: 2 to 4

## Job template and worked example
```json
{
  "to_role": "antigravity",
  "to_instance": "agy-studio-2",
  "title": "Comedy cut gate review for Embracing the Sofa Ep 28",
  "instructions": "Review /Volumes/AI/content-studio/drafts/2026-10-22-ep28/preview/ep28_cut.mp4 (sha256: 8a71f...). Listen to audio for deadpan delivery and pauses. Check that prop text is readable and composited in post. Verify no boomerangs or reverse loops. Write reviews/comedy_cut.verdict.json.",
  "priority": 3
}
```

## What it needs in the job
- Mac mount paths (`/Volumes/AI/...`, `/Volumes/beast/...`).
- File sha256 checksum.
- Target verdict JSON path.

## Signs it is stuck and the fix
- **Signs:** Worker active with no output or memory pressure warning.
- **Fix:** Restart worker session; verify mounts at `/Volumes/AI` and `/Volumes/beast`.

## Who reviews its work
Video Shepherd `tick` validates verdict JSON; Joseph.
