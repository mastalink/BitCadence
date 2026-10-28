# agy-studio-4

## Purpose
Gemini video reviewer on Mac Studio. Operates parallel `watch` and `comedy_cut` gate reviews, set/prop design audits, and frame-level QA.

## Good at
- High-volume review passes: checking audio-to-video alignment and silence gaps.
- Detecting visual glitches: AI-garbled prop text, clipping through solid objects, or back-of-head filler.
- Verifying cast consistency against character bibles (Maya, Dan, Rosa).
- Inspecting 480p proxy passes for fast frame-by-frame pacing audits.

## Not allowed (hard rules)
- Must not use Windows paths (`K:\`, `C:\`); use `/Volumes/beast/` and `/Volumes/AI/`.
- Never run darkbloom inference commands, local models, or heavy RAM builds on Mac Studio.
- Never run `/usr/bin/python3` (use `/opt/homebrew/bin/python3`).
- Do NOT run `shepherd.py` directly; writes `<gate>.verdict.json`.
- Cannot review builds authored by the Gemini family.

## How to give it a job
Send via `mco_send`:
- `to_role`: `antigravity`
- `to_instance`: `agy-studio-4`
- `title`: Video review or visual comedy audit
- `instructions`: Mac paths, file sha256, gate name, and target verdict path
- `priority`: 2 to 4

## Job template and worked example
```json
{
  "to_role": "antigravity",
  "to_instance": "agy-studio-4",
  "title": "Watch and comedy cut review for Embracing the Sofa Ep 29",
  "instructions": "Review /Volumes/AI/content-studio/drafts/2026-10-23-ep29/preview/ep29_master.mp4 (sha256: 9e33d...). Listen to audio track; ensure captions never appear before spoken words. Verify third-person framing with neighbor reactions. Write report to reviews/watch_report.md and save reviews/watch.verdict.json.",
  "priority": 3
}
```

## What it needs in the job
- Mac mount paths (`/Volumes/AI/...`, `/Volumes/beast/...`).
- File sha256 checksum.
- Target verdict JSON file path.

## Signs it is stuck and the fix
- **Signs:** Lease sitting idle or proxy generation failing.
- **Fix:** Check ffmpeg availability at `/opt/homebrew/bin/ffmpeg` and clean `/tmp` storage.

## Who reviews its work
Video Shepherd `tick` validates verdict JSON; Joseph.
