# bitcadence.ai website v2: design brief (Stage A)

Status: design + static mockup only. `website/` is untouched. Mockup: `mockup.html` (open directly; it pulls console screenshots from `../../docs/wiki/img/`). Screenshots: `mockup-desktop.png` (1440 px), `mockup-mobile.png` (500 px wide; headless Edge will not render narrower, the CSS breakpoint is 820 px).

## 1. Concept

BitCadence is the **drum major**. The logo is already a baton (indigo tile, white stroke, orange tip), so the baton is the site's one recurring motif and the only thing that is ever orange. Every agent is an instrument. **Drumline** keeps the shared beat and memory. The fleet marches as one.

The point of the page: BitCadence conducts **all** LLMs, not four. Claude, Codex, Gemini/Antigravity and Grok are the first chairs; the same cue reaches 4, 40 or 400 agents on any model or vendor, cloud or local (Codex, Claude Code, OpenCode, Cursor and Grokbot run on Linux, Windows or macOS). Tagline unchanged: **Every agent. One beat.**

Tone: classy, cinematic, editorial. Not cartoony, no clip-art, no SaaS gradient blobs, no band emoji. The metaphor is carried by geometry (formations of points of light) and by copy, never by illustrations of trumpets.

## 2. Visual system

| Token | Value | Use |
|---|---|---|
| Paper | `#faf9f5` | page ground (light sections) |
| Ink | `#1c1b18` | text, rules, primary button |
| Night | `#14130f` | hero, Drumline, flow strip, footer |
| Indigo | `#4a4ac4` | logo tile, links |
| Brass | `#b88a1f` | eyebrows, first-chair points, version pill, step numbers |
| Signal orange | `#ff7a18` | baton tip and human-grant gate only |

Type: Fraunces (display, light 300, italic for the one emphasized phrase), Inter (body), JetBrains Mono (labels, commands). Self-host the three font files in production (no Google Fonts request) and keep Georgia/system as the fallback stack. Hairline rules, generous 110 px section rhythm, product screenshots in thin-bordered frames with a long soft shadow. Screenshots come from the real v0.6.0 console (`docs/wiki/img/`), light theme on paper sections, same frames on night sections.

## 3. Page structure and copy

1. **Nav**: logo + BitCadence, `v0.6.0` pill (links to the release), anchors (The band, Product, Score, Install, GitHub), Install button.
2. **Hero** (night): eyebrow "The drum major for every AI agent". H1 "Every agent. *One beat.*" Lede: "Claude, Codex, Gemini, Grok, or whatever model comes next, in the cloud or on your own machine. BitCadence leads them all as one band: shared memory, a clear score, and a human hand on every gate." Buttons: Install v0.6.0, Watch the demo. **Hero slot**: 20 s launch clip.
3. **The band** (paper): "Four chairs. Forty. Four hundred." Three formation panels (4 / 40 / 400), the four first chairs, and a row of pills: any hosted model, any local model, any vendor, cloud, on your machine.
4. **Product: jobs and messages** (paper): job board screenshot (`04-console-job-board`) beside "Jobs and messages, in the open."; audit screenshot (`10-console-activity-audit`) beside "Every cue, written down."
5. **Drumline** (night): "The beat that keeps everyone together." One agent learns, another on a different model and machine recalls. Screenshot `09-console-drumline-memory`.
6. **Score** (paper): "From plan to merged, with hands on the gates." Six-step strip: Plan, Build, Review, Fix (rotation), **Human grant** (orange rule), Merge. Three captioned screenshots: Approval gates (`05`), Helpers in plain words (`08`), Ask, check, approve (`21`).
7. **Demo** (paper): about 3 min video slot, captions on.
8. **Install v0.6.0** (warm paper): Windows card (`iwr -useb https://bitcadence.ai/install.ps1 | iex`, release link, `install.bat`), macOS/Linux card (`curl -sSf https://bitcadence.ai/install.sh | bash`, docs/INSTALL.md link), license paragraph.
9. **Footer**: tagline, GitHub, v0.6.0 release, Changelog, License, Commercial license, Trademarks.

## 4. Licensing rules (apply everywhere, including FAQ and meta tags)

- Say: "source-available under the Prosperity Public License 3.0.0, free for personal and noncommercial use. Businesses: see the commercial license."
- Never "open source", never "MIT" for current Core (releases up to 0.5.0rc1 stay MIT; mention only in the changelog if at all).
- No prices, no trial, no sales email address. Commercial inquiries go only to the COMMERCIAL-LICENSE.md link.
- Do not name the owner's employer or any bank; no LinkedIn presence is implied.

## 5. Install links (must work before ship)

- Release: `https://github.com/mastalink/BitCadence/releases/tag/v0.6.0` (exists, marked Latest, 2026-10-09).
- Scripts served from the site root: `/install.ps1`, `/install.sh` (already in `website/`).
- Verify each link on the preview deployment (`--branch preview/v0.6.0`) before the production deploy, per `website/README.md`. The version string appears in nav, hero button, install heading and footer; keep it in one JS/data constant or a single find-and-replace.

## 6. Motion plan (restrained, long holds)

All motion is transform/opacity only, 60 fps, and fully disabled under `prefers-reduced-motion` (static frame shown instead).

- **Hero field** (SVG or canvas, about 400 points): starts with 4 brass points and the baton at rest. A 1.2 s slow baton arc (ease-in-out) cues the first formation. Points then arrive in ranks over about 8 s, 4 → 40 → 400, each stage held 2 s. Points drift in a very slow 8 px "march" sway on a shared 2 s beat (a quiet nod to Drumline). The baton tip pulses orange once per bar.
- **Scale panels**: each of 4/40/400 plays its formation once when scrolled 40% into view; no looping.
- **Score strip**: steps light left to right on scroll; the Human grant step holds 1.5 s with an orange rule before Merge completes.
- **Product frames**: fade up 12 px on entry, once.
- Hero clip on desktop only (see slot); autoplay muted loop, `playsinline`, poster = still frame so LCP is an image, not video.

## 7. Sound

Optional, **off by default**, one unobtrusive "Sound" toggle in the hero. No autoplay audio. Direction: epic chorus with a marching drumline and brass, building as the band grows (choir plus snare / tenor / bass-drum cadence, thickening at the 40 and 400 stages), about 20 s, loop-safe tail. Source: generate with **ACE-Step run locally** (or another properly licensed source); keep the prompt, seed, model version and license note in `assets/AUDIO-PROVENANCE.md`. No unlicensed tracks. The same cut is the music bed of the launch clip; narration (if any) stays separate, Higgs v2 local.

## 8. Asset list

| Asset | Spec | Status |
|---|---|---|
| Hero launch clip | 20 s, 16:9, 1080p H.264 + WebM, under 6 MB, muted loop, poster JPG (frame 0:14) | **to build** (hero slot in mockup) |
| Launch clip audio | 20 s ACE-Step cut, MP3 + Ogg, with provenance file | **to build** |
| Demo video | about 3 min, 1080p, captions (VTT), poster = job board | **to re-cut** on the v0.6.0 console (current `website/baton-demo2.mp4` predates it) |
| Formation art | generated SVG, no raster | in mockup (static) |
| Console screenshots | `docs/wiki/img/` 04, 05, 08, 09, 10, 21 (optionally 01, 22, 23) | exist; recompress to WebP for the site |
| Fonts | Fraunces, Inter, JetBrains Mono as woff2 | to self-host |
| Logo / favicon / OG image | baton tile; OG = hero field still, 1200x630 | OG to make |
| Footage for demo | jobs and messages flowing with the audit trail filling, Drumline learn then recall, Score plan to merge with the human grant, an approval gate | storyboard in the demo job |

## 9. Performance and accessibility

Static, no build step, no framework (same as today). Target LCP under 2 s on mobile: hero poster, deferred video, WebP screenshots with explicit width/height, fonts preloaded. Contrast: brass on night passes for large text and labels; brass on paper is used only for 12 px uppercase eyebrows, so darken to `#8f6a12` there in production. Real alt text on screenshots, visible focus rings, captions on the demo, one H1.

## 10. Open questions for the owner

1. Sound toggle in the hero, or music only inside the clip?
2. Is the Windows `install.ps1` one-liner the primary CTA, or the release download page?
3. Naming (decided 2026-10-10): never show the owner's machine or worker names; use public model, tool and platform names, in a varied band.

## 11. Next stages

B: build `website/` v2 from the approved mockup. C: launch clip + audio. D: demo re-cut. E: preview deploy, link and mobile check, production deploy.
