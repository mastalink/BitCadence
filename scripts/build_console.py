#!/usr/bin/env python3
"""Build (and extract) the BitCadence console bundle.

The console at /console ships as a single self-contained HTML file
(``src/mco/static/console.html``): a gzip+base64 manifest of assets plus a
template that references them by UUID. That file is a *generated artifact* —
this script is the missing build step that makes it maintainable.

Editable application sources live in ``src/mco/console_src/`` (one file per
asset, plus ``index.json`` mapping each file to its UUID/mime). Fonts and the
big React/Babel vendor bundles are NOT extracted — they never change and stay
embedded in the committed HTML, which is used as the build base.

    python scripts/build_console.py extract   # HTML  -> console_src/  (one-time / refresh)
    python scripts/build_console.py build      # console_src/ -> HTML  (after editing sources)
    python scripts/build_console.py verify      # round-trip check, writes nothing

The app scripts carry no Subresource-Integrity hashes (and the loader strips
SRI at runtime anyway), so re-gzipping with a different compressor is safe:
fidelity means the *decompressed* bytes match, not the compressed bytes.
"""
from __future__ import annotations

import base64
import gzip
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HTML = ROOT / "src" / "mco" / "static" / "console.html"
SRC = ROOT / "src" / "mco" / "console_src"
INDEX = SRC / "index.json"

_MANIFEST_RE = re.compile(
    r'(<script type="__bundler/manifest">)(.*?)(</script>)', re.S
)

# Assets small enough to be hand-edited application code. Everything else in the
# manifest (fonts, the React/ReactDOM/Babel vendor bundles) is left untouched.
_VENDOR_UUIDS = {
    "96ede57e-3723-49a9-a82a-1cc965906cbc",
    "ab485911-042c-47fa-ae82-59a76eb04b94",
    "0b8ac89f-6446-474a-8a90-5672b3fc131f",
}
_EXT = {"text/javascript": "js", "application/javascript": "js", "text/jsx": "jsx"}


def _read_manifest(html: str) -> dict:
    m = _MANIFEST_RE.search(html)
    if not m:
        raise SystemExit("Could not find the bundler manifest in console.html")
    return json.loads(m.group(2))


def _write_manifest(html: str, manifest: dict) -> str:
    payload = json.dumps(manifest, separators=(",", ":"))
    return _MANIFEST_RE.sub(
        lambda m: m.group(1) + "\n" + payload + "\n  " + m.group(3), html, count=1
    )


def _decode(entry: dict) -> bytes:
    data = base64.b64decode(entry["data"])
    return gzip.decompress(data) if entry.get("compressed") else data


def _encode(raw: bytes, compressed: bool) -> str:
    # mtime=0 keeps the output deterministic across runs.
    data = gzip.compress(raw, mtime=0) if compressed else raw
    return base64.b64encode(data).decode("ascii")


def extract() -> None:
    html = HTML.read_text(encoding="utf-8")
    manifest = _read_manifest(html)
    SRC.mkdir(parents=True, exist_ok=True)
    index = {}
    for uuid, entry in manifest.items():
        if uuid in _VENDOR_UUIDS or entry["mime"].startswith("font/"):
            continue
        ext = _EXT.get(entry["mime"], "txt")
        fname = f"{uuid}.{ext}"
        (SRC / fname).write_bytes(_decode(entry))
        index[fname] = {
            "uuid": uuid,
            "mime": entry["mime"],
            "compressed": bool(entry.get("compressed")),
        }
    INDEX.write_text(json.dumps(index, indent=2) + "\n", encoding="utf-8")
    print(f"Extracted {len(index)} editable sources to {SRC.relative_to(ROOT)}")


def build(check_only: bool = False) -> None:
    html = HTML.read_text(encoding="utf-8")
    manifest = _read_manifest(html)
    index = json.loads(INDEX.read_text(encoding="utf-8"))
    changed = 0
    for fname, meta in index.items():
        raw = (SRC / fname).read_bytes()
        uuid = meta["uuid"]
        if _decode(manifest[uuid]) != raw:
            changed += 1
        # Always re-encode from source so the manifest reflects console_src/.
        manifest[uuid]["data"] = _encode(raw, meta["compressed"])
    out = _write_manifest(html, manifest)
    if check_only:
        # Decode every patched entry back out and confirm it matches the source.
        rebuilt = _read_manifest(out)
        for fname, meta in index.items():
            if _decode(rebuilt[meta["uuid"]]) != (SRC / fname).read_bytes():
                raise SystemExit(f"Round-trip mismatch for {fname}")
        print(f"verify OK — {len(index)} sources round-trip; {changed} differ from current HTML")
        return
    if not changed:
        print("No source changes; console.html is up to date.")
        return
    HTML.write_text(out, encoding="utf-8")
    print(f"Rebuilt console.html ({changed} asset(s) updated).")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "build"
    if cmd == "extract":
        extract()
    elif cmd == "verify":
        build(check_only=True)
    elif cmd == "build":
        build()
    else:
        raise SystemExit(f"Usage: {sys.argv[0]} [extract|build|verify]")
