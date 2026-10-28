# Backup builder: what to do if Claude runs out of usage (owner request 2026-10-17)

The "builder" (claude-beast, Claude Sonnet) does the video, GPU and ElevenLabs work. If Claude usage runs out mid-build, another model on Beast can keep the production moving. This runbook says who, how, and what never to do.

A copy lives at `K:\SaintsStudio\config\BACKUP_BUILDER.md` so Grokbot can read it.

## 1. Why a backup is possible
The Saints and Sofa builds are scripted and logged, not kept in the builder's head:
- The scripts are `K:\SaintsStudio\tools\carlo_v12_*.py` (voice, stills, cutout, h3, build, qa, contact). A Sofa episode has its own `assemble*.py` and `mkvoice.py` in its draft folder.
- The state is in `V12_PROGRESS.md`, `FIX_LOG.md`, `ROUND_FIXLIST.md` and `script.json` in the saint's folder.
- The rules are in `K:\SaintsStudio\config\CHIEF_NOTES_FOR_GROKBOT.md`.

Any model with a shell on Beast can read those and continue. The risk is judgment: whether a picture matches its words, or whether a take sounds wrong. That is why every backup run ends with an independent check (section 6).

## 2. When to switch (any one is enough)
- A builder job fails or the worker exits with a usage-limit or quota message.
- claude-beast is offline or has no heartbeat for more than 30 minutes while builder jobs sit pending, and a relaunch fails (see section 8).
- Joseph says "Claude is out."

The Chief or the Deputy (grok-beast) makes the switch. If the Chief is also out, the Deputy does it and logs it in `docs\runbook\incident-log.md`.

## 3. Who can stand in (best first)

| Order | Worker | Where | Sees K:? | Good for | Notes |
|---|---|---|---|---|---|
| 1 | **codex-beast** (Codex) | Beast | Yes | Running and fixing the build scripts, voice regen, renders, QA scripts | Pin by name only. No auto-fallback exists. Codex ran out of credits once (incident 43); check it first. |
| 2 | **grok-beast** (Grok, the Deputy) | Beast | Yes | Re-running scripts, bookkeeping, small edits | It is also the Deputy. Use it only for short jobs so the Deputy duties don't stall. |
| 3 | **antigravity-beast** (Gemini) | Beast | Yes | QA and light script runs | Not a builder by default. |
| 4 | **opencode-beast** | Beast | Yes | A launcher exists. VERIFY its model and whether it can run long jobs before relying on it. | |
| - | **agy-studio-1..4** (Gemini, Mac Studio) | Mac | **No** | Review and research only, through jobs that carry the text | Cannot read K: or the GPU. Never send them build work. |
| - | **Gemini 4 relay** (Drive) | Joseph's account | Via a Drive pack | Listening reviews only | See `GEMINI_RELAY.md`. Not a builder. |
| Last | **Joseph by hand** | Beast | Yes | Run one stage script from a terminal | See section 9. |

A backup is a **stand-in**. It finishes the current slice and keeps the queue alive. Don't start new creative work with it unless Joseph says so.

## 4. Switch procedure (Chief or Deputy)

1. **Freeze the old builder.**
   - Check `mco_jobs` for jobs leased to claude-beast. A dead lease blocks its job until it expires (incident 50). Re-send the remaining work as a NEW job to the backup. Don't wait for the lease.
   - `mco_cancel` is for approver roles only. A claude or grok session gets 403, so re-send instead of cancelling, and note the orphan in the new job.
2. **Write a lock file** so two builders never work on the same slice:
   `K:\SaintsStudio\saints\<year>\<folder>\BUILDER_LOCK.txt`
   with one line: `<instance> <UTC time> <job id> <slice>`. Delete it when the job completes or fails.
3. **Send ONE job** with `mco_send`:
   - `to_role` is the real role (`codex`, `grok`), and `to_instance` is the instance name (`codex-beast`). Never put an instance name in `to_role` (incident 49).
   - `priority` matches the old job. `escalate_to_role=grok`. `expects_successor=true` if the slice hands off to a conductor job.
   - Use the prompt template in section 5.
4. **Make sure the worker is awake.** Check `mco_agents`. If a worker is offline, wake it with `mco wake`, or start its `*-beast-run.cmd` from `C:\Users\masta\.mco\bin\`.
5. **Tell Joseph in one sentence** what is running and who is doing it.

## 5. Prompt template for the backup job

Copy this and fill in the slice. The job instructions must stand alone.

```
You are the BACKUP BUILDER for Saints Storybook / Embracing the Sofa. The usual builder (Claude) is out of usage. Continue the work below exactly as it would have.

READ FIRST, in this order:
1. K:\SaintsStudio\config\CHIEF_NOTES_FOR_GROKBOT.md  (owner rules; they win over everything)
2. <saint folder>\V12_PROGRESS.md  (state), FIX_LOG.md and the newest ROUND_FIXLIST.md  (what is left)
3. <saint folder>\script.json  (the ONLY script source; never edit words except to apply a logged owner note)
4. The tool scripts you need in K:\SaintsStudio\tools\  (carlo_v12_*.py). Reuse them; don't rewrite them. Fix a script only if it is broken, and keep a .bak copy.

YOUR SLICE: <one slice only, with its inputs and outputs>

HARD RULES
- Nothing is published, uploaded or sent. Output goes to the saint's out\ folder only.
- Never print, copy or log any secret. ElevenLabs: in your own process only, set ELEVENLABS_API_KEY from C:\AI\elevenlabs.txt, e.g.
    $env:ELEVENLABS_API_KEY = (Get-Content -Raw 'C:\AI\elevenlabs.txt').Trim()
  Never echo it, put it in a job or a file, or write it to a log.
- Voice: Storytime Joe aERVKfcBzYTPdoYoqMpB, model eleven_v4, DEFAULT settings. No [tags] and no heavy "!" in the TTS text.
- Style: locked camera, no zoom; character-locked stills from characters\v12\; no AI-made text in pictures; no circle wipes except the one into the 16-bit sprite; no burned-in captions (write the .srt only); NO subscribe line; "Saint" spelled out; never pray TO a saint.
- GPU: ComfyUI is at 127.0.0.1:8188 on G:\ComfyUI. Check its queue first, run one heavy job at a time, and don't kill other processes.
- Append progress to V12_PROGRESS.md after every unit of work (clip, still, voice chunk) so a restart can resume.
- You are headless. Don't use background tasks or wait on timers. Call mco_renew between long steps.
- If something is ambiguous or an owner rule conflicts with the plan, stop that unit, log it in V12_PROGRESS.md, and ask through mco_complete or mco_fail. Don't guess on faith or theology questions.

DONE MEANS: outputs exist at the paths above, the QA script passes (carlo_v12_qa.py or carlo_motion.py), and your mco_complete handoff lists files, decisions, anything unverified and follow-ups. Then send the next job named in the slice (usually the round conductor job to grok-beast).
```

## 6. Quality gate for any stand-in work
Claude, Codex and Grok judge pictures and sound differently, so every stand-in slice gets a second opinion before it counts:
- **Automatic checks (the backup runs these):** `carlo_motion.py` (global and local motion at least 80%, no freeze over 1.5 s), loudness (-14 LUFS, true peak at most -1 dBTP), clicks at joins, Whisper transcript against the script, and f0 per voice line.
- **Independent review:** the next round goes to the review team as usual (antigravity-beast, reviewer-beast, grok-beast, codex-beast, claude-code) **plus the Gemini 4 Drive relay**, which is the only reviewer that actually listens.
- **Never let the backup be the only judge** of whether a picture matches its words or a voice sounds right.

## 7. What a stand-in should and should not do

| Safe for a backup | Needs the Chief or Joseph |
|---|---|
| Re-run a stage script (voice, stills, render, QA) | Any new look, new character, new script wording |
| Apply a logged fix from `ROUND_FIXLIST.md` | Changing a rule or a decision in CHIEF_NOTES |
| Regenerate a rejected clip, still or voice line | Prayer text (Joseph signs off on every prayer) |
| Write contact sheets, logs and handoffs | Uploading, scheduling, posting, spending money |
| Mark items FIXED or WAIVED in `FIX_LOG.md` | Installing or downloading new models |

## 8. Known gotchas
- **Worker won't start from a shell:** set `BC_HOME=C:\AI\baton\Batoncadence` first, or the launcher fails silently (incident 50).
- **"Inbox empty" on relaunch:** a job already leased to that instance is not in its inbox. Re-send the remaining work as a new job.
- **Misrouting:** `to_role` must be a registered role (`claude`, `codex`, `grok`, `antigravity`, `reviewer`), never an instance name.
- **Codex:** pin by instance name. Unattended Codex workers must run with memories off or they lease stale tasks (see the Codex memory note). `codex-beast-run.cmd` already calls `prepare-codex-worker.ps1`.
- **Mac workers (agy-studio):** cannot read K:. Don't send them build work.
- **chief-beast** is the watch officer, not intake. Send stand-in jobs to the workers above, not to chief-beast.
- **GPU:** only one ComfyUI. If it shows an unexpected busy state, look at `/queue` before assuming a conflict.
- **Usage limits reset:** don't leave the stand-in running once Claude is back.

## 9. Last resort: Joseph runs it by hand
From a PowerShell window on Beast, after reading `V12_PROGRESS.md` for the current slice:
```
cd K:\SaintsStudio\tools
python carlo_v12_build.py --help
```
Each stage script has its own `--help` and logs to the saint's folder. Ask the Chief or any worker to list which stage is next.

## 10. Switching back to Claude
1. Confirm Claude is back (a test job completes, or `mco_agents` shows claude-beast online with a fresh heartbeat).
2. Let the stand-in finish or fail its current job. Don't interrupt a render or a ComfyUI run.
3. Delete `BUILDER_LOCK.txt`.
4. Send the next slice to claude-beast with "resume from V12_PROGRESS.md". It reads the stand-in's log.
5. Note the switch in the incident log (who, when, what was done, anything left unverified).

## 11. Readiness check (do this before you need it)
- Send codex-beast a **read-only** job: "Read CHIEF_NOTES and V12_PROGRESS.md, run `carlo_v12_qa.py` against the newest cut without writing outside a temp folder, and report what you found." This proves it can reach K:, run the scripts and finish a lease.
- Check that Codex has credits.
- Confirm the Deputy can find this file. It is referenced from `CHIEF_NOTES_FOR_GROKBOT.md`.
