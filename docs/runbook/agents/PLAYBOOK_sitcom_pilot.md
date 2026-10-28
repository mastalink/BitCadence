# Playbook: Sitcom Pilot Production

Production workflow for the AI comedy sitcom pilot. Plain words, short sentences.

## Standing Safety Rules
1. **Uploads are private + scheduled only.** No public posting without the owner's explicit confirmation.
2. **Two different model families must pass reviews.** No agent reviews its own work.
3. **Nobody deploys to production except the owner.**
4. **Never print secrets or tokens.**

---

## Phase 1: Pilot Script Draft
- **Who:** Head writer: `muse`.
- **Reference:** Comic Voice Bible and Sitcom Character Bible.
- **Process:** Write full pilot scene draft. Build around core ensemble: Maya (the relatable junior marketer), Dan (the sincere suburban dad), and Rosa (the unflappable default mom).
- **Rules:** Clean dialogue beats, natural conversational rhythms, deadpan comedic timing, and zero punching down. Never use real people's names.

## Phase 2: GPT Writers' Room Notes
- **Who:** Reviewer: `reviewer-beast` (GPT).
- **Process:** Act as the senior comedy writers' room.
  - Review script structure, character voices, and escalating stakes.
  - Identify weak dialogue beats and provide 3 alternative punch-up lines per scene.
  - Enforce comedic pacing: setup, misdirection, undercut, and smug soak.
- **Artifact:** Output filed as `pilot_writers_room_notes.md`. Muse applies approved fixes.

## Phase 3: Set and Prop Design
- **Who:** Designers: `antigravity-beast` (Beast) and `agy-studio-1..4` (Mac Studio).
- **Process:**
  - Define visual style for key recurring sets: Maya's plant-filled apartment, Dan's kitchen with kids' art on the fridge, Rosa's minivan pickup line.
  - Design key sight-gag props (absurd invoices, productivity charts, motivational awards).
  - Specify prop layout so text can be rendered blank and composited cleanly in post.

## Phase 4: Locked Cast Plates
- **Who:** Builder: `claude-beast` (stand-in: `codex-beast`).
- **Tools:** ComfyUI FLUX 3 Klein and H3 Ref2VA (`factory/cast/<name>/ref.png`).
- **Process:**
  - Generate reference character plates for each cast member.
  - Enforce wardrobe lock: Maya (cream oversized sweater, messy bun), Dan (solid navy quarter-zip, khakis), Rosa (heather-grey hoodie, reading glasses).
  - The owner approves the master cast reference stills once before production.

## Phase 5: Keyframes and Wan 2.2 Motion
- **Who:** Builder: `claude-beast`.
- **Tools:** Wan 2.2 First/Last Frame workflow on ComfyUI (port 8189).
- **Process:**
  - Generate start and end keyframe plates for each shot.
  - Animate character subtle gestures, eye blinks, deadpan reactions, and physical comedy beats.
  - Chain 5-second segments to cover dialogue duration.
  - Enforce: no frozen frames >1.0s, no reversing boomerangs, locked camera framing.

## Phase 6: Character Voices and Audio Mix
- **Who:** Builder: `claude-beast`.
- **Tools:** ElevenLabs API with distinct assigned character voice IDs.
- **Process:**
  - Generate individual character dialogue takes.
  - Assemble dialog track with realistic conversation overlap and distinct reaction pauses.
  - Add Foley, room ambiance, and laugh/reaction beats (if multi-camera style).
  - Normalize loudness to -14 LUFS.

## Phase 7: Multi-Model Independent Review
- **Who:**
  - Visual and comedic integrity: `reviewer-beast` (GPT).
  - Watch, timing, and audio sync: `agy-studio-1..4` (Gemini on Mac Studio) listening to the mix.
  - Final cut verification: Chief `claude-desktop` compiles verdicts and presents to the owner.
