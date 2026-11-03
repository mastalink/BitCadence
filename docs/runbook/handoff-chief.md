# Handoff: BitCadence Chief (fresh chat)

**Why this exists:** Usage was at 92% of the 5-hour window until 11:20 PM. The old chat is long and costly. Start cheap: read this, check the status commands below, then ask the owner what to focus on.

## Who and how
- Owner: The owner ("The owner"). Use plain words. Call the background Claude worker "the builder", never "claude-beast". Name projects fully (e.g. "the BitCadence app redesign").
- Escalate immediately. Any "X is broken" line in a worker report gets verified, logged in `C:\AI\baton\Batoncadence\docs\runbook\incident-log.md`, and told to the owner in the same reply.
- Drafts only. Nothing is uploaded or posted without owner approval.
- Never print or copy secrets. The ElevenLabs key lives in `C:\AI\elevenlabs.txt`, locked to the user. Read it only inside scripts.

## Workers (board names)
- `claude-beast`: the video builder (Sonnet). Renders, music, Ep builds.
- `claude-code`: the code builder (Sonnet). Score steps and code PRs only.
- `claude-light`: Haiku 5.5, small chores. Fails big jobs back.
- Score runs target `claude-code` for the claude role.

## Current state (as of 2026-10-08 ~01:50 UTC)
1. **BitCadence app redesign, run -05:** running on `claude-code`. S4 (Ask box) was leased at priority 50. Check: `mco score status --run-id bitcadence-redesign-20261006-05` from `C:\AI\score-runtime\bitcadence-redesign`. Start script: `start.ps1` (now -05). Tracker mod and shepherd point at -05.
2. **Ep 4 "Deliberate Inertia":** The owner's voice (ElevenLabs "The owner - Confident Idiot" on eleven_v4) is approved. Choir at climaxes confirmed. Preview v4 is in `C:\AI\content-studio\drafts\2026-10-06-deliberate-inertia\preview\`. Open jobs:
   - `23de9f2f` visual pass: longer chained shots, dissolves, cuts on the music.
   - `5173ffa1` smug soak: 1.6–2.5 s holds after punchlines. Amends 23de9f2f.
   - `9b8128e4` lunch riff: 3–4 sips or crunches inside the soaks. Amends 5173ffa1.
   These are one combined pass. Owner has not yet heard the soak or riff.
3. **Ep 5–7:** held until the owner approves Ep 4's final cut. Then they get the owner plus the continuous score.
4. **Abby's video "Exactly Critical":** done and accepted by the owner. Do not re-do.
5. **Ep 8 and later:** on hold for the owner.

## Decisions pending from the owner
- **Pilot edition (private `bitcadence-pilot` repo):** A extract / B redraw the line / C warn-only first. Recommended: C. Gate on the Core-vs-Pilot checks was flagged by the reviewer.
- **Create private repos** `bitcadence-pilot` and `fleet-ops` (bug whacker idea: GitHub issues, Haiku fixes small bugs).
- **Admin step:** `schtasks /create /tn "BitCadence-ComfyUI" /tr "C:\Users\masta\.mco\bin\comfyui-start.cmd" /sc onlogon /rl limited /f` in an admin PowerShell, so ComfyUI (video renders) starts after reboot. ComfyUI is running now.
- **Builder #2 install:** `C:\AI\baton\Batoncadence\.venv\Scripts\mco.exe fleet apply` in admin PowerShell (if not yet run).

## License
- PR #140 (BitCadence): switch Core from MIT to Prosperity Public License 3.0.0. Free for personal use; businesses get a 30-day trial then license. Releases ≤0.5.0rc1 stay MIT.
- Owner checklist: contributor name (the owner Owner vs LLC), website copy, counsel review of CLA and enterprise license.
- Review by Antigravity requested changes once; fixed in a follow-up commit. Re-check before merge.

## Runbook
- `C:\AI\baton\Batoncadence\docs\runbook\incident-log.md`: 29 incidents, with prevention rules. Keep adding rows.
- Key rules: restart the board server after merging gateway or Score code. Repair jobs never edit tracked code. Health check covers disk, pagefile, ComfyUI, workers' auth.

## Suggested skills for the next session
- `handoff` if this chat gets long again.
- `diagnose` for any worker failure or board issue.
- `engineering:incident-response` when logging a new incident.

## First three actions
1. Run `mco score status` for run -05 and read the board's latest notes (use `curl` to `127.0.0.1:18789`; token from `~/.mco/.env`, never printed).
2. Check `C:\AI\content-studio\drafts\2026-10-06-deliberate-inertia\preview\` for a new Ep 4 v5 preview.
3. Ask the owner which to focus on: Ep 4 review, Pilot decision, or BitCadence redesign.
