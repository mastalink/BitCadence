# agy-studio-1

## Purpose
Gemini video and cut reviewer on Mac Studio. Primary reviewer for `watch` and `comedy_cut` gates, listening to audio, checking motion pacing, and verifying set/prop designs.

## Good at
- Watching MP4 video files and listening to actual audio tracks on Mac.
- Checking comic timing, pauses, music silences, and deadpan delivery.
- Detecting held/frozen frames longer than 1.0 second (fails watch gate per 2026-10-23 owner rule).
- Set and prop design inspection (ensuring readable prop text is composited cleanly in post).

## Not allowed (hard rules)
- Cannot see Beast's native `K:` or `C:` directly; must use Mac mounts (`/Volumes/AI/` for `C:\AI\`, `/Volumes/beast/` for `K:\`).
- Never run darkbloom inference commands, load local LLMs, or consume heavy GPU/RAM on Mac Studio.
- Never run `/usr/bin/python3` (hangs on Xcode license; use `/opt/homebrew/bin/python3`).
- Do NOT run `shepherd.py` directly; writes `<episode root>/reviews/<gate>.verdict.json` for `shepherd.py tick` to ingest.
- Never reviews its own build.

## How to give it a job
Send via `mco_send`:
- `to_role`: `antigravity`
- `to_instance`: `agy-studio-1`
- `title`: Video watch review or comedy cut review
- `instructions`: Mac paths to MP4, original sha256, gate name, and target verdict path
- `priority`: 2 to 4

## Job template and worked example
```json
{
  "to_role": "antigravity",
  "to_instance": "agy-studio-1",
  "title": "Watch gate review for Saints Storybook Carlo Acutis preview",
  "instructions": "Review /Volumes/beast/SaintsStudio/saints/2027/1012_carlo-acutis/out/carlo_v14_cut.mp4 (sha256: 4b29c...). Listen to audio for clicks, verify narrator is Storytime Joe, verify locked camera with no zoom, and ensure no frame is held frozen >1s. Write report to /Volumes/beast/SaintsStudio/saints/2027/1012_carlo-acutis/reviews/watch_review.md and write /Volumes/beast/SaintsStudio/saints/2027/1012_carlo-acutis/reviews/watch.verdict.json.",
  "priority": 3
}
```

## What it needs in the job
- Mac mount paths (`/Volumes/AI/...`, `/Volumes/beast/...`). Never Windows drive letters.
- Expected file sha256 checksum (reviewer must verify against local file).
- Clear verdict JSON schema: `{"gate","verdict":"pass|fail","by":"agy-studio-1","family":"gemini","sha256","report":"<Mac path>"}`.

## Signs it is stuck and the fix
- **Signs:** Shell command hung on Python execution or video read timeout.
- **Fix:** Ensure scripts invoke `/opt/homebrew/bin/python3` and `/opt/homebrew/bin/ffmpeg`. Make a 480p proxy in `/tmp` if the full MP4 exceeds memory limits.

## Who reviews its work
Video Shepherd `tick` validates verdict JSON; Joseph.
