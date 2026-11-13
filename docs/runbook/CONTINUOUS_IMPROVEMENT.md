# Continuous improvement program (owner request 2026-10-15)

"All of these should be continuously improved": MOSES, Via, the Little Saints kids app, BitCadence and MyMeals (plus Saints Storybook and Embracing the Sofa content).

## Who
- **Improvers:** agy-studio-1..4 (Antigravity, Gemini on the Mac Studio). They read, audit and propose; they write small changes only where allowed (see guardrails).
- **Builders:** claude-code (code), claude-beast (media), codex-beast (code, pinned).
- **Reviewer:** antigravity-beast; reviewer-beast for theology and kid safety.
- **Coordinator:** the Deputy (grok-beast) runs the cycle. The Chief sees one weekly digest plus anything that needs a decision.

## Portfolio
| Improver | Projects |
|---|---|
| agy-studio-1 | BitCadence (core runtime, console, site) |
| agy-studio-2 | Via (parish times) + MyMeals |
| agy-studio-3 | Little Saints app + Saints Storybook pipeline |
| agy-studio-4 | MOSES/Genesis + a cross-project QA reader |

Embracing the Sofa (`C:\AI\content-studio`) stays with claude-beast + Muse. It is not in the Gemini portfolio.

## Repo map (Beast, verified 2026-10-15 16:31 ET)

| Project | Beast path | Git remote | Default/current branch | Mac Studio reach |
|---|---|---|---|---|
| BitCadence | `C:\AI\baton\Batoncadence` | `https://github.com/mastalink/BitCadence.git` | `main` (checkout was behind origin/main by 1; dirty console files unrelated to this program) | **GitHub yes. Beast disk no.** Clone this remote. Do not assume `C:\` or `K:\` is mounted. |
| Via | `C:\AI\via-catholica` (canonical; documented in `docs/VIA-MESH-START-HERE.md`). Active backend worktree `C:\AI\via-global-data-design` is owned by another task — do not share it. | `https://github.com/mastalink/via.git` (`origin`) | `codex/via-foundation` on the canonical checkout | GitHub yes. Beast disk no. Via **code changes are Claude/Codex only**; Gemini audits and proposes. |
| MyMeals | `C:\AI\MyMeals` | `https://github.com/mastalink/MyMeals.git` | `codex/recipe-browser-week-picker` (behind origin by 1; working tree has local archive/docs dirt) | GitHub yes. Beast disk no. **CODE and synthetic fixtures only.** Never `blue_apron_archive/`, `food_data_archive/`, `.env`, tokens, or household data. |
| Little Saints Academy | `C:\Users\masta\Documents\Codex\2026-06-04\files-mentioned-by-the-user-build\work\little-saints-site` | `https://github.com/mastalink/little-saints-academy.git` (`github`); also a `sites` remote | `main` (dirty local tree) | GitHub yes. Beast disk no. COPPA / kid-safety; theology changes need reviewer-beast + owner. |
| MOSES / Genesis | `C:\AI\moses\moses3\moses` | `https://github.com/mastalink/MosesAI.git` (`moses3`) | `sync/local-main-2026-09-26` | GitHub yes. Beast disk no. **CODE and synthetic fixtures only.** Never Genesis memories, persona, family facts, `.env`, or tokens. `C:\AI\genesis-console` is the same remote on branch `feat/genesis-console`. |
| Saints Storybook pipeline | `K:\SaintsStudio` (Beast local NTFS volume named "AI BEAST", DriveType 3, ~4 TB). Not a git repo. | none | n/a | **Unreachable from Mac unless they mount Beast.** K: on Beast is local disk, not a Mac share. `Get-SmbShare` was empty. Tailscale ping to `joes-mac-studio` (<tailscale-ip>) is 5 ms; SSH from Beast is **not** available (`.ssh/config` permission error; do not change security settings to fix). Jobs must carry whatever source the improver needs, or skip the pipeline and say so. |
| Embracing the Sofa | `C:\AI\content-studio` | none (no `.git`) | n/a | Out of Gemini portfolio. claude-beast + Muse. |

**Mac Studio network (2026-10-15):** `joes-mac-studio` Tailscale `<tailscale-ip>`, active, direct. Agents `agy-studio-1..4` (role `antigravity`) were online. `antigravity-mac` / `codex-mac` / `claude-mac-studio` were offline. `mac-studio-codex` was online with a stale heartbeat (~1 h).

**Rule for improvers:** if a Beast path is missing on the Mac, clone the GitHub remote into a local working directory. Never push to `main`. Cycle 1 is proposals only — put the markdown in `mco_complete` so the Deputy can file it on Beast.

## Daily cycle (Deputy dispatches once a day, about 06:00 ET)
1. **Scan** (per project): open issues/TODOs, failing or flaky tests, CI, error logs, the incident log, owner feedback logs, and dependency/security advisories.
2. **Propose up to 3 small improvements** per project with impact/effort and how to verify each, in `docs/improvement/<project>/<date>.md` (or the project's docs folder).
3. **Auto-proceed only for small, safe items:** tests, docs, lint, small bug fixes, perf tweaks. A builder makes a branch + draft PR, Antigravity reviews it, and CI must be green. The Chief may merge clean AI-reviewed BitCadence PRs (standing authorization). Other repos stay as draft PRs for the owner.
4. **Anything bigger** (features, schema/data changes, UX changes, cost, publishing) goes into the weekly digest as a proposal for the owner. It's not built without an OK.

## Trigger (set up 2026-10-15)

Existing mechanism: **MCO `schedules.yaml`**, ticked by `BitCadence-Fleet-Watchdog` every ~5 minutes (`C:\AI\ops\fleet-watchdog.ps1` section 4b → `C:\AI\baton\Batoncadence\.venv\Scripts\mco.exe schedule tick`).

Added launcher + schedule:

- File: `C:\Users\masta\.mco\schedules.yaml`
- Backup: `C:\Users\masta\.mco\schedules.yaml.bak-20261015-ci`
- Name: `continuous-improvement`
- Cron: `0 6 * * *` timezone `America/New_York` (06:00 ET daily)
- Target: role `grok`, instance `grok-beast` (the Deputy). The Deputy then sends one scan-and-propose job per `agy-studio-1..4`.
- No new Windows scheduled task. No security-setting changes.

If today is Sunday, the same 06:00 job also drafts `docs/improvement/SUNDAY_DIGEST.md` from the template and sends it to the owner via MosesGrok / the Deputy status path. Do not page claude-desktop unless a yes/no is required.

## Guardrails (hard)
- **Privacy:** MOSES/Genesis and MyMeals hold family and household data. Gemini improvers read CODE and synthetic fixtures only, never Genesis memories, family facts, meal/household data, tokens or .env files.
- **Via:** the code-writing policy is Codex/Claude only. Gemini may audit and propose, but Via code changes are made by claude-code or codex-beast. Calling stays disabled.
- **Kids app:** COPPA and kid-safety rules; theology changes need reviewer-beast plus the owner.
- No secrets in jobs, no paid services, no publishing or deploys, no pushes to main, no force-pushes. Respect the cost caps (Via $150/mo).
- One improvement PR per project open at a time (no PR floods). Stop and report after 2 failed attempts.

## Reporting
- The Deputy keeps `docs/improvement/LOG.md`: what shipped, what's waiting, what was rejected and why.
- **Weekly digest to the owner (Sundays):** the top wins, proposals needing a yes/no, and anything blocked. Template: `docs/improvement/SUNDAY_DIGEST.template.md`.
- Cycle proposal files live under each project's `docs/improvement/` and a copy under `C:\AI\baton\Batoncadence\docs\improvement\<project>\` when the Deputy files Mac job output.
