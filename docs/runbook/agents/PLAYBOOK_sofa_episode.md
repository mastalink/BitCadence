# Playbook: Embracing the Sofa Episode

From concept and script to scheduled YouTube upload. Plain words, short sentences.

## Standing Safety Rules
1. **Uploads are private + scheduled only.** Never publish public directly. Scheduled via Video Shepherd.
2. **Two different model families must pass video reviews.** No agent reviews its own work.
3. **Nobody deploys to production except the owner.**
4. **Never print, copy, or log secrets.**

---

## Step 1: Script Writing and Satire Inversion
- **Who:** Head writer: `muse`.
- **Reference:** `factory/playbooks/comic-voice-bible.md` and `factory/playbooks/anti-guru-inversion.md`.
- **Core contract:** The narrator is high-status, calm, sincere, and wrong about everything. Sincere absurdity meets the dry undercut.
- **Rules:**
  - Satire is aimed at the guru culture and hustle clichés, **never** at Dan, Maya, or Rosa.
  - Never put real people's names in scripts or prompts.
  - Lead rotates between recurring cast members (Maya, Dan, Rosa). Rosa is never the butt of the joke.
  - Write short beats with room for physical comedy and smug soaks (1.6-2.5s pauses).

## Step 2: Comedy Script Gate (`comedy_script`)
- **Who:** Reviewer: `grok-beast` (or `reviewer-beast` if Grok wrote draft).
- **Rubric:** `docs/COMEDY_GATE.md`. Seven categories scored 1-10:
  1. Deadpan integrity
  2. Literal logic
  3. Image strength
  4. Undercut timing
  5. Escalation
  6. Punch-up list (3 proposed replacements)
  7. Kill list (must be empty: no punching down, no real names, no mean-spirited lines)
- **Pass threshold:** Average score >= 8.0, no individual score < 7.0, and kill list empty. If failed twice, episode goes on HOLD for the owner.
- **Recording:** Write report to `<episode>/reviews/comedy_script_<family>.json`.

## Step 3: Narration Generation
- **Who:** Builder: `claude-beast`.
- **Tools:** ElevenLabs API (`voice-lab-eleven`). Narrator voice "Storytime the owner" or "Confident Idiot" on `eleven_v4`.
- **Rules:** Deliver lines flat and unbothered. Respect timing pauses in narration files.

## Step 4: Visual Generation and Sight-Gags
- **Who:** Builder: `claude-beast`.
- **Tools:** ComfyUI on Beast (port 8188, `G:\ComfyUI`).
- **Rules:**
  - Third-person perspective: we watch the guru suffer in wide/medium shots, not his POV.
  - Always stage where a witness or neighbor can react.
  - Reference stills for Maya, Dan, Rosa (`factory/cast/<name>/ref.png`) with plain wardrobe.
  - **Prop rule (LESSONS.md):** Render props with blank/blurred text and composite crisp readable text in post using ECC tracking (`track_overlay.py` / `cards.py`). Never leave garbled AI text on props.
  - No boomerangs, ping-pong loops, or reversing shots.

## Step 5: Assembly and Captions
- **Who:** Builder: `claude-beast`.
- **Tools:** FFmpeg assembly script (`assemble*.py`).
- **Rules:**
  - Loudness normalized to -14 LUFS.
  - Captions (.srt / ASS) must never appear before their spoken words, especially on a punchline.
  - Music drops to silence on punchlines and visual gags.
  - Include the satire disclaimer and AI disclosure card.

## Step 6: Review Gates (`comedy_cut`, `brand`, `visual`, `watch`, `tech`)
- **Who:**
  - `comedy_cut` and `watch`: `agy-studio-1..4` (Gemini on Mac Studio) listening to the actual audio.
  - `brand` and `visual`: `reviewer-beast` (GPT).
  - `tech`: automated by `shepherd.py tech <id>`.
- **Process:** Mac reviewer inspects timing, audio pauses, and prop overlays, writing `<episode>/reviews/comedy_cut.verdict.json`.
- **Requirement:** Two different model families must pass `visual` and `watch`.

## Step 7: Scheduled Release
- **Who:** Orchestrator: `shepherd.py release <id>`.
- **Process:** Tool verifies all gates are passed against current video sha256. Uploads via YouTube profile `sofa` as **Private + publishAt**. The owner reviews in YouTube Studio during the scheduled window.
