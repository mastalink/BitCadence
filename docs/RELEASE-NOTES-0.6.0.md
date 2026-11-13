# BitCadence v0.6.0: the plain-language redesign

## Why this release

BitCadence should feel like a calm control room, not a config file. v0.6.0 is the owner-approved redesign (`design/redesign-v1/`): plain words everywhere, no JSON, YAML, tokens or ports on screen in normal use, colour never the only signal, and every old `mco` command still working.

It was built the way BitCadence is meant to be used. A Score run had one AI build each slice and another review it, and a human-issued grant was required before anything touched the repository. Five slices were built and reviewed, then shipped as one pull request.

## What's new

- **Quiet start:** `bitcadence start` runs in the background with a tray status light (a word plus green, amber or red), a one-time sign-in link instead of a pasted token, and start-at-sign-in for your user only, with no administrator needed.
- **Home:** what needs you, and what's running.
- **Ask for something** replaces the drag-and-drop builders. Type a request ("fix the login bug, then ask me before deploying"), see the plan drawn as steps with approval gates, and approve once. The console and `bitcadence ask` share one planner.
- **Helpers:** every AI helper with a health light and a word ("Ready", "Working", "Stuck", "Not connected"), what it's doing in plain words, **Add a helper**, and **Fix it**, which shows a dry run first and repairs only after you confirm.
- **Connect an AI:** one tap per app (Claude, Codex, Gemini/Antigravity, Cursor). It finds the app and adds BitCadence to its connection settings, keeping a backup of the original first.
- **Schedules:** "Every weekday at 2:00 AM" pickers instead of cron lines.
- **Approvals:** no raw permission errors. A missing approver right gives one sentence and a one-key fix.
- **Settings:** plain labels; addresses and masked sign-ins only under **Show advanced**.
- **Shared GPU nights:** an exclusive resource lease arbiter, so long jobs on one GPU don't trample each other.

## Reliability fixes

- Score jobs dispatch above normal board traffic, and a late completion event no longer blocks a run (15-minute grace).
- A blocked or failed Score run pushes the owner one notification; stale alerts no longer replay.
- The push-notification topic lookup has a timeout and a cache, so a slow cloud call can't hold up startup.
- Helpers no longer mistake a Windows venv launcher and its child process for two copies.

## License change

From v0.6.0, BitCadence Core is licensed under the **[Prosperity Public License 3.0.0](../LICENSE)**, from Batoncadence LLC. It's **source-available**: free for personal and noncommercial use, and a business may use it for a 30-day trial, then needs a commercial license (see [COMMERCIAL-LICENSE.md](../COMMERCIAL-LICENSE.md)).

**Not retroactive:** releases up to and including 0.5.0rc1 were MIT and stay MIT for anyone who received them.

## Upgrading

- Every old `mco` command and flag keeps working; `--json` stays on read commands, and exit codes are unchanged.
- The Flow Control page (`/flow`) and the visual workflow builder are removed. Workflow YAML still runs through `bitcadence ask --file` and `mco workflow`.

Full details are in [CHANGELOG.md](../CHANGELOG.md).
