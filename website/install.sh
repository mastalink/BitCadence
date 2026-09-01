#!/usr/bin/env bash
# ============================================================================
# BitCadence — bootstrap installer
# ============================================================================
# Usage:
#   curl -sSf https://bitcadence.ai/install.sh | bash
#   # or, if stdin acts up:
#   bash <(curl -sSf https://bitcadence.ai/install.sh)
#
# What this does:
#   1. Detects an existing install (via PATH, common locations, or env var)
#   2. If found: pulls updates, re-runs setup in-place — never double-installs
#   3. If not found: clones to ~/BitCadence and runs setup
# ============================================================================
set -euo pipefail

GRN='\033[0;32m'; CYN='\033[0;36m'; YLW='\033[0;33m'; RED='\033[0;31m'; NC='\033[0m'

REPO="https://github.com/mastalink/BitCadence"

# If we're being piped (curl | bash), bash reads THIS script from stdin — so a
# later `exec </dev/tty` (needed for interactive prompts) hijacks bash's command
# stream and drops the user into a shell instead of installing. Re-exec from a
# real file. Attach /dev/tty when it opens so `curl | bash` is interactive.
# When it does not (CI, a pipe with no controlling terminal), skip the redirect:
# unconditional `exec ... </dev/tty` died at ~2s with `/dev/tty: No such device
# or address`. _BC_INSTALL_REEXEC stops a no-tty re-exec from looping.
if [ ! -t 0 ] && [ -z "${_BC_INSTALL_REEXEC:-}" ]; then
    _self="$(mktemp)"
    curl -fsSL "https://bitcadence.ai/install.sh" > "$_self"
    export _BC_INSTALL_REEXEC=1
    if (exec </dev/tty) 2>/dev/null; then
        exec bash "$_self" "$@" </dev/tty
    else
        exec bash "$_self" "$@"
    fi
fi

echo ""
echo -e "${CYN}  BitCadence installer${NC}"
echo -e "${CYN}  =======================${NC}"
echo ""

# ── 1. Locate an existing install ─────────────────────────────────────────
find_existing() {
    # Explicit override wins
    if [ -n "${BITCADENCE_INSTALL_DIR:-}" ] && [ -d "$BITCADENCE_INSTALL_DIR/.git" ]; then
        echo "$BITCADENCE_INSTALL_DIR"; return 0
    fi

    # mco already on PATH? resolve back to the repo root
    if command -v mco &>/dev/null; then
        local mco_bin
        mco_bin="$(command -v mco)"
        # follow symlink
        mco_bin="$(readlink -f "$mco_bin" 2>/dev/null || realpath "$mco_bin" 2>/dev/null || echo "$mco_bin")"
        # expected layout: <repo>/.venv/bin/mco  OR  <repo>/venv/bin/mco
        local candidate
        candidate="$(dirname "$(dirname "$(dirname "$mco_bin")")")"
        if [ -f "$candidate/pyproject.toml" ] && grep -q "bitcadence\|BitCadence\|mco" "$candidate/pyproject.toml" 2>/dev/null; then
            echo "$candidate"; return 0
        fi
    fi

    # Check common install locations
    for loc in \
        "$HOME/BitCadence" \
        "$HOME/bitcadence" \
        "/opt/BitCadence" \
        "/usr/local/BitCadence"
    do
        if [ -d "$loc/.git" ] && [ -f "$loc/pyproject.toml" ]; then
            echo "$loc"; return 0
        fi
    done

    return 1
}

EXISTING=$(find_existing || true)

if [ -n "$EXISTING" ]; then
    echo -e "${GRN}[OK] Found existing BitCadence install at:${NC}"
    echo -e "     ${EXISTING}"
    echo ""
    echo -e "${CYN}->  Pulling latest updates...${NC}"
    git -C "$EXISTING" fetch --quiet origin 2>/dev/null || true
    LOCAL=$(git -C "$EXISTING" rev-parse HEAD 2>/dev/null || true)
    REMOTE=$(git -C "$EXISTING" rev-parse origin/main 2>/dev/null || true)
    if [ "$LOCAL" = "$REMOTE" ]; then
        echo -e "${GRN}[OK] Already up to date — re-running setup to verify${NC}"
    else
        git -C "$EXISTING" pull --ff-only origin main 2>/dev/null && \
            echo -e "${GRN}[OK] Updated${NC}" || \
            echo -e "${YLW}[!]  Could not pull (local changes present) — continuing${NC}"
    fi
    echo ""
    exec bash "$EXISTING/scripts/install.sh" "$@"
fi

# ── 2. Fresh install ────────────────────────────────────────────────────────
DEST="${BITCADENCE_INSTALL_DIR:-$HOME/BitCadence}"

if [ -d "$DEST" ] && [ "$(ls -A "$DEST" 2>/dev/null)" ]; then
    echo -e "${RED}[X]  $DEST exists and is not empty (and is not a BitCadence repo).${NC}"
    echo -e "     Move it or set BITCADENCE_INSTALL_DIR to a different path."
    exit 1
fi

if ! command -v git &>/dev/null; then
    echo -e "${RED}[X]  git is required. Install it and retry.${NC}"
    exit 1
fi

echo -e "${CYN}->  Cloning BitCadence to $DEST...${NC}"
git clone --depth 1 "$REPO" "$DEST"
echo -e "${GRN}[OK] Repository ready at $DEST${NC}"
echo ""

exec bash "$DEST/scripts/install.sh" "$@"
