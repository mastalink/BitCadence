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
Custom domains → add `bitcadence.ai` (already owned, catch-all email
configured — `pilots@bitcadence.ai` just works).

**Zero-config alternative:** the Cloudflare dashboard also accepts a
drag-and-drop of the `website/` folder, or can auto-deploy from this
GitHub repo on every push (Pages → Create → Connect to Git → set build
output directory to `website`, no build command).

## Editing

Everything lives in `index.html` — design tokens are CSS variables at the
top (`--paper`, `--ink`, `--indigo`, `--signal`). The hero terminal
animation script is at the bottom; edit the `SCRIPT` array to change the
demo narrative.
