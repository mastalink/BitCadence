#!/usr/bin/env bash
# ============================================================================
# BitCadence - offline (air-gapped) bundle builder (macOS / Linux)
# ============================================================================
# Run this on a CONNECTED machine with the same OS family and Python minor
# version as the target. It produces dist/bitcadence-offline.tar.gz with
# the full repo plus every wheel needed to install with zero network access.
#
# On the air-gapped target:
#   1. Copy the tarball over (USB, file transfer, whatever your policy allows)
#   2. tar xzf bitcadence-offline.tar.gz && cd bitcadence && bash scripts/install.sh
#      The installer detects offline/wheels and uses --no-index automatically.
#
# Usage:
#   bash scripts/make-offline-bundle.sh
# ============================================================================
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
GRN='\033[0;32m'; CYN='\033[0;36m'; RED='\033[0;31m'; NC='\033[0m'

echo ""
echo -e "${CYN}  BitCadence offline bundle builder${NC}"
echo -e "${CYN}  ====================================${NC}"
echo ""

PY="$ROOT/.venv/bin/python"
[ -x "$PY" ] || PY="$(command -v python3 || command -v python)" || {
    echo -e "${RED}[X] No Python found. Run scripts/install.sh first.${NC}"; exit 1; }

STAGE="$ROOT/dist/offline-stage"
WHEELS="$STAGE/bitcadence/offline/wheels"
rm -rf "$STAGE"
mkdir -p "$WHEELS"

echo -e "${CYN}->  Downloading all dependency wheels (this machine's platform/Python)...${NC}"
"$PY" -m pip download -d "$WHEELS" "$ROOT" --quiet
"$PY" -m pip download -d "$WHEELS" pip setuptools wheel --quiet
COUNT="$(ls "$WHEELS" | wc -l | tr -d ' ')"
echo -e "${GRN}[OK] ${COUNT} wheels downloaded${NC}"

echo -e "${CYN}->  Staging the repository (tracked files only)...${NC}"
git -C "$ROOT" archive --format=tar HEAD | tar -x -C "$STAGE/bitcadence"

mkdir -p "$ROOT/dist"
OUT="$ROOT/dist/bitcadence-offline.tar.gz"
rm -f "$OUT"
echo -e "${CYN}->  Compressing bundle...${NC}"
tar -czf "$OUT" -C "$STAGE" bitcadence
rm -rf "$STAGE"

SIZE="$(du -h "$OUT" | cut -f1)"
echo ""
echo -e "${GRN}[OK] Bundle ready: $OUT ($SIZE)${NC}"
echo ""
echo -e "${CYN}  Move it to the air-gapped machine, extract, and run scripts/install.sh.${NC}"
echo -e "${CYN}  The installer detects offline/wheels and never touches the network.${NC}"
