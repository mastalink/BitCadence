# Playbook: Owner Video Fix Notes

How to turn the owner's video feedback into an actionable, verified fix job. Plain words, short sentences.

## Standing Safety Rules
1. **Uploads are private + scheduled only.** Never publish public directly. Fixed cuts use `shepherd.py replace <id>`.
2. **Two different model families must pass reviews on the new cut.** No agent reviews its own work.
3. **Nobody deploys to production except the owner.**
4. **Never print, copy, or log secrets.**

---

## Step 1: Intake and Triage Owner Notes
- **Context:** The owner watches scheduled draft cuts during his review window (listed in `C:\AI\content-studio\REVIEW_QUEUE.md`). His notes arrive in chat or as a board job.
- **Triage action:**
  - Read the owner's notes verbatim. Do not reinterpret or drop requirements.
  - Determine which stage created the defect:
    - Text or theological error -> Stage `script`.
    - Character outfit drift or bad still -> Stage `art`.
    - Frozen frame, glitchy movement, or bad camera -> Stage `motion`.
    - Bad voice take, wrong inflection, or mispronunciation -> Stage `narration`.
    - Audio out of sync, bad dissolve, or bad caption timing -> Stage `assemble`.

## Step 2: Roll Back Shepherd Stage
- **Command:** Use the Video Shepherd tool to roll back the episode:
  ```bash
  python shepherd.py set <episode-id> stage=<target-stage>
  ```
  Example: if the fix is audio timing and clip order, set `stage=assemble`. If narration needs regen, set `stage=narration`.

## Step 3: Dispatch Fix Job to Builder
- **Who:** Builder: `claude-beast` (or stand-in `codex-beast`).
- **Dispatch via `mco_send`:**
  - `to_role`: `claude`
  - `to_instance`: `claude-beast`
  - `title`: `Fix owner notes on <Episode Name> (<Stage>)`
  - `instructions`:
    1. Quote the owner's exact note.
    2. Reference the current episode folder and existing assets.
    3. Specify exact fix: re-voice line N, re-render clip M, or adjust cut in `assemble.py`.
    4. Rebuild cut to `<slug>_v<N+1>.mp4`.
    5. Point `artifacts.video` to the new MP4 path.

## Step 4: Stale Review Handling and Automated Re-Review
- When `artifacts.video` updates to the new file, all previous review passes (`visual`, `watch`, `comedy_cut`) become **stale** automatically.
- The Shepherd tick posts fresh review jobs for the new file:
  - `visual`: `reviewer-beast` (GPT family).
  - `watch` / `comedy_cut`: `agy-studio-1..4` (Gemini family on Mac Studio).
- Both families must pass the new cut. Reviewers must confirm the owner's specific note was resolved.

## Step 5: Replace YouTube Upload
- **Command:** Once all gates pass on the new file sha256, execute replacement:
  ```bash
  python shepherd.py replace <episode-id>
  ```
- **What this does:**
  1. Uploads the fixed video with the **identical scheduled publishAt time**.
  2. Verifies the upload metadata.
  3. Moves the old video upload to **private and unscheduled** (it never deletes anything).
  4. Records both video IDs in the registry.
- If the original scheduled time has already passed, set a new future `publish_at` before running `replace`.

## Step 6: Log and Report
- Append the fix summary to `<episode>/FIX_LOG.md` and mark the note CLOSED.
- Report completion back to the owner with:
  1. Path to new video cut.
  2. YouTube private preview link.
  3. Confirmation that all review gates passed.
