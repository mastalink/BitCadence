# Air-Gapped Install - Zero Data Leaves Your Network

BitCadence's enterprise posture is *runs on your metal*: the Local-Only
profile already needs no cloud account, no external database, and no vendor
callback. The offline bundle closes the last gap - installing on a machine
with **no internet access at all**.

## 1. Build the bundle (connected machine)

Use a machine with the **same OS family and Python minor version** as the
air-gapped target (wheels are platform-specific):

```powershell
# Windows
powershell -ExecutionPolicy Bypass -File scripts\make-offline-bundle.ps1
# -> dist\bitcadence-offline.zip
```

```bash
# macOS / Linux
bash scripts/make-offline-bundle.sh
# -> dist/bitcadence-offline.tar.gz
```

The bundle contains the tracked repository plus every dependency wheel
(`pip download`), including pip/setuptools/wheel themselves.

## 2. Install on the air-gapped target

Move the bundle by whatever your policy allows (USB, secure file transfer),
extract it, and run the normal installer:

- **Windows:** double-click `install.bat`
- **macOS / Linux:** `bash scripts/install.sh`

The installer detects `offline/wheels` and switches to
`pip install --no-index --find-links offline/wheels` automatically - it never
touches the network. The update check degrades gracefully (offline is an
expected state, not an error).

## 3. What works offline

Everything in the community edition, by construction:

| Surface | Offline behavior |
|---|---|
| Job board, governance, workflows | Full - embedded SQLite (`~/.mco/local.db`) |
| Drumline shared context | Full - LocalStore backend, deterministic recall (no embedding service) |
| Dashboard, MCP server, CLI | Full - all local |
| ntfy push notifications | Skipped unless you self-host ntfy on-net |
| Supabase / connectors | N/A - point them at on-net endpoints if you have them |

Python itself must already be on the target (or in your golden image);
the bundle does not carry the Python installer.

## Updating an air-gapped install

Build a fresh bundle on the connected machine, transfer, extract over the
install directory, and re-run the installer. The installer re-installs from
the new wheels; your configuration (`~/.mco/.env`) and data (`~/.mco/local.db`)
live outside the repo and are untouched.
