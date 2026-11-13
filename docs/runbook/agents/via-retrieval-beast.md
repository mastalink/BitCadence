# via-retrieval-beast

## Purpose
Collects Catholic parish schedules, Mass times, confession times, and parish metadata from public sources for the Via Catholic church finder app.

## Good at
- Scraping public parish websites, bulletins, and diocesan directories.
- Extracting and parsing multi-lingual Mass and confession schedules.
- Structuring raw parish schedule data into standard JSON payloads.

## Not allowed (hard rules)
- Never writes directly to the production Via database; writes to staging files or worktree PR branches only.
- Never bypasses schema validation gates.
- Never runs high-concurrency scrapers that hammer small parish servers.

## How to give it a job
Send via `mco_send`:
- `to_role`: `via-retrieval`
- `to_instance`: `via-retrieval-beast`
- `title`: Parish schedule collection task
- `instructions`: Target diocese, parish URLs, geographic region, and destination output path
- `priority`: 1 to 3

## Job template and worked example
```json
{
  "to_role": "via-retrieval",
  "to_instance": "via-retrieval-beast",
  "title": "Ingest parish Mass schedules for Diocese of Cleveland (Lorain County)",
  "instructions": "In C:\\AI\\via-catholica\\data\\incoming\\, retrieve public Mass and confession schedules for the 12 listed parishes in Lorain County. Format output as valid parishes.json complying with via schema v2. Return retrieval report with source URLs.",
  "priority": 2
}
```

## What it needs in the job
- Source URLs or diocesan directory endpoints.
- Output JSON schema specification.
- Local repository path (`C:\AI\via-catholica` on Beast).

## Signs it is stuck and the fix
- **Signs:** HTTP timeouts, anti-bot captcha blocks, or parser failures on non-standard parish HTML.
- **Fix:** Inspect retrieval logs; adjust scraping delays or user-agent headers; fall back to manual bulletin download.

## Who reviews its work
`codex-beast`, `antigravity-beast`, and the owner before data merge.
