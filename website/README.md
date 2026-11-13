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

The final demo (`media/bitcadence-demo.mp4`, 2:48) has captions burned into the picture, so no `.vtt` track is needed (WCAG 1.2.2 met). It is click-to-play with sound, `preload="none"`, no autoplay. The hero clip (`media/brag.mp4`) is muted and decorative.


## User wiki

Canonical public help: https://bitcadence.ai/wiki/. Edit docs/wiki Markdown, then run `python scripts/build_wiki.py` before deployment. The build creates section search and checks every local link/fragment. CLI coverage comes from the real parser; GUI coverage records grouped controls, source hashes normalized for platform line endings, and guide anchors. Run both verifiers in docs/wiki/_review. Do not deploy an unrebuilt wiki after changing its source.

The retired batoncadence.com website project was removed November13,2026. Its apex/www hosts now redirect at the edge to bitcadence.ai with paths and query strings preserved. Mail records were preserved; DNS has redirect-only proxied records. Do not restore the obsolete Pages origin.
