# BitCadence Website

Static, zero-build marketing site — one HTML file, same philosophy as the
product's console. No node_modules, no framework, no build step.

## Preview locally

```bash
python -m http.server 8788 --directory website
# → http://localhost:8788
```

## Deploy to Cloudflare Pages (free tier)

One-time setup:

```bash
npm i -g wrangler
wrangler login
```

Deploy (from the repo root):

```bash
wrangler pages deploy website --project-name bitcadence
```

For a release, deploy a preview branch first with
`wrangler pages deploy website --project-name bitcadence --branch preview/<release>`.
Check the preview on desktop and mobile, verify installation links and Jev
claims against the exact release commit, then deploy `--branch main` for the
production custom domain. Record the previous production deployment ID from
`wrangler pages deployment list --project-name bitcadence` so it can be rolled
back in Cloudflare Pages if the new deployment is unhealthy.

First run creates the project and prints the live URL
(`https://bitcadence.pages.dev`). Subsequent runs deploy in seconds.

**Custom domain:** Cloudflare dashboard → Pages → bitcadence →
Custom domains → add `bitcadence.ai` (already owned).

**Zero-config alternative:** the Cloudflare dashboard also accepts a
drag-and-drop of the `website/` folder, or can auto-deploy from this
GitHub repo on every push (Pages → Create → Connect to Git → set build
output directory to `website`, no build command).

## Editing

Everything lives in `index.html` — design tokens are CSS variables at the
top (`--paper`, `--ink`, `--indigo`, `--signal`). The hero terminal
animation script is at the bottom; edit the `SCRIPT` array to change the
demo narrative.

## Captions

The final demo (`media/bitcadence-demo.mp4`, 2:48) has captions burned into the picture, so no `.vtt` track is needed (WCAG 1.2.2 met). It is click-to-play with sound, `preload="none"`, no autoplay. The hero film (`media/bitcadence-march.mp4`, 45 s) is click-to-play, `preload="none"`, no autoplay, and shown on mobile too. It has no speech: the soundtrack is music only (an organ toccata, a snare cadence, then an orchestral march), and every on-screen line is text in the picture, so captions are not needed; the aria-label describes it.
