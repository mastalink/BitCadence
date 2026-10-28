# Playbook: Saints Storybook Episode

From initial research to scheduled YouTube upload. Plain words, short sentences.

## Standing Safety Rules
1. **Uploads are private + scheduled only.** Never publish public directly. Always set `publish_at` for feast day 07:00 America/New_York.
2. **Two different model families must pass video reviews.** No agent reviews its own work.
3. **Nobody deploys to production except the owner.**
4. **Never print, copy, or log secrets.** The ElevenLabs key is read in-memory from `C:\AI\elevenlabs.txt`.

---

## Step 1: Research and Fact Sourcing
- **Who:** Lead: `grok-beast` (MosesGrok writes batch); Fact Check: `codex-beast`.
- **Sources:** Tier-1 primary: `causesanti.va` and `vatican.va`. Traditional secondary: Catholic Encyclopedia, Butler's Lives, diocesan records.
- **Rules:** Check `K:\SaintsStudio\config\banned_claims.txt`. Miracles must be authentic approved miracles ("God healed through Saint N's prayers"). Sourcing-only doubts do not block; record them in `owner_confirm` in `facts.json`.
- **Artifacts:** Batch folder `K:\SaintsStudio\_incoming\saints_research_<date>\<slug>\` containing `research.md`, `script.md`, `scenes.json`, `facts.json`, `captions_draft.srt`.

## Step 2: Script Gate and Prayer Sign-off
- **Who:** Reviewers: `reviewer-beast` (`theology` gate) and `codex-beast` (`facts` gate); Approver: The owner.
- **Process:** The Video Shepherd locks `facts` and `theology` reviews to `artifacts.script` BEFORE any art, GPU, or voice work.
- **Script rules:** Narrator is Storytime the owner (`aERVKfcBzYTPdoYoqMpB`, `eleven_v4`, default settings). TTS text spells out "Saint", never "St.". Never pray TO a saint (intercession to God only). Final scene must use the exact sign-off catchphrase: *"Go be a saint today, because I can't wait to add YOUR story to The Saints Storybook!"*
- **Owner gate:** Prayer text is copied to `K:\SaintsStudio\saints\PRAYERS-FOR-OWNER-APPROVAL.md`. The owner signs off on every prayer before it enters `script.json`.

## Step 3: Character Sheets and Still Plates
- **Who:** Builder: `claude-beast` (stand-in: `codex-beast`).
- **Tools:** FLUX 3 Klein via `K:\SaintsStudio\tools\venv_flux3` or ComfyUI storybook graph (port 8188).
- **Process:** Generate cut-paper storybook style stills. One base plate per shot into `<episode>\art\stills\`. The owner confirms character look once.
- **Rules:** Locked camera, no zoom, soft dissolves, no AI-generated gibberish text in images.

## Step 4: Narration Generation
- **Who:** Builder: `claude-beast` (stand-in: `codex-beast`).
- **Tools:** ElevenLabs API via per-episode voice script (`tools\carlo_v12_voice.py` pattern).
- **Process:** Generate takes per sentence into `<episode>\audio\takes\`. Pick best take. Normalize loudness to -14 LUFS.
- **Rules:** Do not put `[tags]` or heavy `!` in TTS text.

## Step 5: Animation Generation (Wan 2.2 First/Last Frame)
- **Who:** Builder: `claude-beast` (stand-in: `codex-beast`).
- **Tools:** Wan 2.2 I2V 14B on animation ComfyUI (port 8189, `start_comfy.bat`).
- **Process:** Each shot animates from start frame to end frame using one motion prompt. Output 1280x720, 24 fps, ~5 seconds per clip.
- **Rules:** Animation must cover every narration line. A frozen or held frame longer than 1.0s fails the watch gate. Extend scenes by chaining Wan segments from the last frame.

## Step 6: Assembly and Captions
- **Who:** Builder: `claude-beast` (stand-in: `codex-beast`).
- **Tools:** FFmpeg + Python assembly script (`build_v14.py` pattern).
- **Process:** Combine video clips, narration, Suno theme intro/outro (trimmed WAVs on Logo C), and sound effects into `<slug>_master.mp4`.
- **Rules:** Generate separate `.srt` file. Never burn captions into the video picture.

## Step 7: Independent Quality Gate Reviews
- **Who:**
  - `visual`: `reviewer-beast` (GPT) or `claude-desktop` (Claude).
  - `watch`: `agy-studio-1..4` (Gemini on Mac Studio) listening to the actual audio.
  - `tech`: Automated check by `shepherd.py tech <id>`.
- **Rules:** Two different model families must pass `visual` and `watch`. No model reviews its own build. Mac reviewer writes `<episode>\reviews\watch.verdict.json`.

## Step 8: Scheduled YouTube Upload
- **Who:** Orchestrator: `shepherd.py release <id>`.
- **Process:** Tool checks that all 5 gates (`facts`, `theology`, `visual`, `watch`, `tech`) have passed on the exact file sha256. Uploads via YouTube API profile `saints` as **Private + publishAt** for feast day 07:00 NY.
- **Receipt:** Tool writes `upload_receipt.json` and updates `registry.json`. Subtitles (`.srt`) are uploaded manually in Studio if needed.
