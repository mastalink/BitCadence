# mac-studio-codex

## Purpose
Code builder on Mac Studio. Handles Mac-side build scripts, API bridges, and repository validation tasks.

## Good at
- Building and executing macOS automation tasks.
- Integrating Mac-side tools (`ffmpeg`, `ffprobe`, audio extraction).
- Cross-platform file path normalization and verification.

## Not allowed (hard rules)
- Cannot see `K:` drive. Never send Beast GPU or media generation work.
- Never run darkbloom commands or disturb local model serving memory on the Mac Studio.
- Never run `/usr/bin/python3`. Always use `/opt/homebrew/bin/python3`.
- Never publish code or merge to master without PR review.

## How to give it a job
Send via `mco_send`:
- `to_role`: `codex`
- `to_instance`: `mac-studio-codex`
- `title`: Mac-native script or code task
- `instructions`: Self-contained instructions with macOS paths
- `priority`: 1 to 3

## Job template and worked example
```json
{
  "to_role": "codex",
  "to_instance": "mac-studio-codex",
  "title": "Build audio verification helper script for video review",
  "instructions": "In /Volumes/AI/content-studio/tools/, update review_audio_loudness.py to accept an mp4 path and check LUFS using /opt/homebrew/bin/ffmpeg. Return the updated script diff and test execution output on sample media.",
  "priority": 2
}
```

## What it needs in the job
- Mac-accessible paths (`/Volumes/AI/...`).
- Explicit tool versions and flags.
- Clear diff or test report output requirements.

## Signs it is stuck and the fix
- **Signs:** Shows standby with no pickup or unhandled error log on Mac.
- **Fix:** Check Mac worker runner logs in `~/.mco/logs/` and restart worker socket.

## Who reviews its work
`agy-studio-*`, `claude-code`, and the owner.
