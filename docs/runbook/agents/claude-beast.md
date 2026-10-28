# claude-beast

## Purpose
The primary media builder on Beast. Builds video, audio, and visual pipelines for Saints Storybook and Embracing the Sofa.

## Good at
- Running video generation, assembly, and QA scripts (`carlo_v12_*.py`, `assemble*.py`).
- ComfyUI workflows for stills and Wan 2.2 animation.
- Generating ElevenLabs speech takes with Storytime Joe.
- FFmpeg compositing, loudness normalization (-14 LUFS), and subtitle generation.

## Not allowed (hard rules)
- Never uploads or publishes directly to YouTube or public channels; output goes to draft or episode folders only.
- Never prints or writes secrets to disk or logs (ElevenLabs API key is read strictly in memory from `C:\AI\elevenlabs.txt`).
- Never prays TO saints (intercession only, addressed to God).
- No iris wipes or camera zooms on Saints Storybook.
- No boomerangs, ping-pong loops, or reversing shots on Embracing the Sofa.

## How to give it a job
Send via `mco_send`:
- `to_role`: `claude`
- `to_instance`: `claude-beast`
- `title`: Actionable slice description
- `instructions`: Self-contained build slice instructions referencing Windows paths
- `priority`: 2 to 4 (match production cadence)

## Job template and worked example
```json
{
  "to_role": "claude",
  "to_instance": "claude-beast",
  "title": "Assemble draft cut for Charles Borromeo scenes 1-4",
  "instructions": "Run K:\\SaintsStudio\\tools\\charles_build.py --scenes 1-4. Inputs: plates in K:\\SaintsStudio\\saints\\2027\\1104_charles-borromeo\\art\\stills\\ and voice in audio\\. Output to out\\charles_preview_s1_4.mp4. Check loudness (-14 LUFS) and write verification output.",
  "priority": 3
}
```

## What it needs in the job
- Windows paths (`K:\SaintsStudio\...`, `C:\AI\content-studio\...`). Never Mac paths.
- Exact slice boundary (scenes, stills, audio lines).
- Script hash and target output file path.
- Reference to `script.json` and fix log.

## Signs it is stuck and the fix
- **Signs:** Job pending >10 minutes without pickup, process memory spike, ComfyUI port (8188 or 8189) hanging, or Anthropic usage limit reached.
- **Fix:** Launch `powershell -File %USERPROFILE%\.mco\bin\claude-worker-run.ps1 -Instance claude-beast`. If Claude quota is exhausted, switch immediately to backup builder `codex-beast` using `docs/runbook/BACKUP_BUILDER.md`.

## Who reviews its work
Reviewer-beast (theology, visual, brand), agy-studio-1..4 (watch, audio timing, motion), Gemini 4 relay, and Joseph.
