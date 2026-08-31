#!/usr/bin/env bash
# ============================================================================
# BatonCadence Setup Script (macOS / Linux)
# ============================================================================
# One-shot install — mirrors scripts/install.ps1:
#   0. Check for updates (git fetch)
#   1. Locate Python 3.9+ (offers brew/apt/dnf install if missing)
#   2. Create .venv in the repo root
#   3. pip install -e . (editable, for local dev)
#   4. Ask whether this machine is a server or client
#   5. Write ~/.mco/.env (global config home)
#   6. Symlink mco -> ~/.local/bin/mco; add to $SHELL config
#   7. CLI self-check (mco --help)
#   8. Demo-mode or connect-now launch choice
#
# Usage (from the repo root, or via bootstrap):
#   bash scripts/install.sh
#   bash scripts/install.sh --no-prompt   # CI / unattended
# ============================================================================
set -euo pipefail

# ---- colours ----------------------------------------------------------------
GRN='\033[0;32m'; YLW='\033[0;33m'; CYN='\033[0;36m'; RED='\033[0;31m'; NC='\033[0m'
ok()   { echo -e "${GRN}[OK]${NC} $*"; }
step() { echo -e "${CYN}->  ${NC}$*"; }
warn() { echo -e "${YLW}[!] ${NC}$*"; }
fail() { echo -e "${RED}[X] ${NC}$*"; exit 1; }

# ---- flags ------------------------------------------------------------------
NO_PROMPT=0
INSTALL_ROLE=""
GATEWAY_URL=""
AGENT_TOKEN=""

while [ "$#" -gt 0 ]; do
    case "$1" in
        --no-prompt|-y)
            NO_PROMPT=1
            shift
            ;;
        --role)
            [ "${2:-}" ] || fail "--role requires server or client."
            INSTALL_ROLE="$2"
            shift 2
            ;;
        --role=*)
            INSTALL_ROLE="${1#*=}"
            shift
            ;;
        --gateway)
            [ "${2:-}" ] || fail "--gateway requires a URL."
            GATEWAY_URL="$2"
            shift 2
            ;;
        --gateway=*)
            GATEWAY_URL="${1#*=}"
            shift
            ;;
        --token)
            [ "${2:-}" ] || fail "--token requires a token."
            AGENT_TOKEN="$2"
            shift 2
            ;;
        --token=*)
            AGENT_TOKEN="${1#*=}"
            shift
            ;;
        *)
            shift
            ;;
    esac
done

# ---- stdin fix: restore terminal when piped through curl --------------------
# Interactive + openable tty: attach so prompts work after curl|bash.
# Skip when --no-prompt, or when the device is missing (CI / a pipe with no
# controlling terminal). Unconditional `exec </dev/tty` died at ~2s with
# `/dev/tty: No such device or address` and never parsed flags.
if [ "$NO_PROMPT" -eq 0 ] && [ ! -t 0 ]; then
    if (exec </dev/tty) 2>/dev/null; then
        exec </dev/tty
    fi
fi


# ---- helpers ----------------------------------------------------------------
set_env_value() {
    local key="$1"
    local value="$2"
    local tmp

    if [ -f "$ENV_PATH" ] && grep -q "^${key}=" "$ENV_PATH" 2>/dev/null; then
        tmp="$(mktemp)"
        awk -v key="$key" -v value="$value" '
            $0 ~ "^" key "=" { print key "=" value; next }
            { print }
        ' "$ENV_PATH" > "$tmp"
        mv "$tmp" "$ENV_PATH"
    else
        echo "${key}=${value}" >> "$ENV_PATH"
    fi
}

parse_join_line() {
    local line="$1"
    if [[ "$line" =~ ^[[:space:]]*mco[[:space:]]+join[[:space:]]+([^[:space:]]+)[[:space:]]+--token[[:space:]]+([^[:space:]]+) ]]; then
        GATEWAY_URL="${BASH_REMATCH[1]}"
        AGENT_TOKEN="${BASH_REMATCH[2]}"
        return 0
    fi
    return 1
}

detect_join_url() {
    local host=""
    if command -v hostname &>/dev/null; then
        host="$(hostname -f 2>/dev/null || hostname 2>/dev/null || true)"
        host="${host%%$'\n'*}"
    fi
    host="${host:-127.0.0.1}"
    echo "http://${host}:18789"
}

verify_client_gateway() {
    local agents_url="${GATEWAY_URL%/}/api/agents"
    local status
    local curl_rc

    if ! command -v curl &>/dev/null; then
        fail "curl is required to verify client access to $agents_url."
    fi

    status="$(curl -sS -o /dev/null -w "%{http_code}" \
        --connect-timeout 10 \
        -H "Authorization: Bearer $AGENT_TOKEN" \
        "$agents_url" 2>/dev/null)"
    curl_rc=$?
    if [ "$curl_rc" -ne 0 ]; then
        fail "Could not reach $agents_url. Check the Gateway URL and network."
    fi

    case "$status" in
        200) ok "Verified client access to $GATEWAY_URL" ;;
        401|403) fail "Gateway rejected the token (HTTP $status). Check the token and retry." ;;
        *) fail "Gateway verification failed with HTTP $status from $agents_url." ;;
    esac
}

case "$INSTALL_ROLE" in
    ""|server|client) ;;
    *) fail "--role must be either server or client." ;;
esac

if [ -z "$INSTALL_ROLE" ] && { [ -n "$GATEWAY_URL" ] || [ -n "$AGENT_TOKEN" ]; }; then
    INSTALL_ROLE="client"
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$ROOT"

echo ""
echo -e "${CYN}  BatonCadence Setup${NC}"
echo -e "${CYN}  ==================${NC}"
echo ""

# ----------------------------------------------------------------------------
# 0. Check for updates
# ----------------------------------------------------------------------------
step "Checking for updates..."

if ! command -v git &>/dev/null; then
    echo "     git not found - skipping update check."
else
    if ! git -C "$ROOT" rev-parse --git-dir &>/dev/null 2>&1; then
        echo "     This folder is not a git repo (probably a ZIP download)."
        echo "     To get updates: https://github.com/mastalink/Batoncadence"
    else
        git -C "$ROOT" fetch --quiet origin 2>/dev/null || {
            echo "     Could not reach GitHub (offline?) - skipping update check."
        }
        LOCAL=$(git -C "$ROOT" rev-parse HEAD 2>/dev/null || true)
        REMOTE=$(git -C "$ROOT" rev-parse origin/main 2>/dev/null || true)
        if [ -n "$LOCAL" ] && [ -n "$REMOTE" ] && [ "$LOCAL" = "$REMOTE" ]; then
            ok "Already up to date"
        elif [ -n "$LOCAL" ] && [ -n "$REMOTE" ]; then
            BEHIND=$(git -C "$ROOT" rev-list "HEAD..origin/main" --count 2>/dev/null || echo 0)
            AHEAD=$(git -C "$ROOT" rev-list "origin/main..HEAD" --count 2>/dev/null || echo 0)
            if [ "$BEHIND" -gt 0 ]; then
                echo ""
                echo -e "${YLW}  ============================================================${NC}"
                echo -e "${YLW}  $BEHIND new update(s) available on GitHub.${NC}"
                echo -e "${YLW}  ============================================================${NC}"
                echo ""
                LOG=$(git -C "$ROOT" log "HEAD..origin/main" --oneline --no-decorate 2>/dev/null | head -8 || true)
                if [ -n "$LOG" ]; then
                    echo -e "${CYN}  What's new:${NC}"
                    while IFS= read -r line; do echo "    $line"; done <<< "$LOG"
                    echo ""
                fi
                if [ "$NO_PROMPT" -eq 1 ]; then
                    if git -C "$ROOT" pull --ff-only origin main; then
                        ok "Updated to latest version"
                    else
                        warn "Could not fast-forward onto origin/main; installing this checkout"
                    fi
                else
                    read -rp "  Pull updates now? [Y/n] " upd
                    upd="${upd:-Y}"
                    if [[ "$upd" =~ ^[Yy] ]]; then
                        git -C "$ROOT" pull --ff-only origin main
                        ok "Updated to latest version"
                    else
                        warn "Skipping update - continuing with current version"
                    fi
                fi
            elif [ "$AHEAD" -gt 0 ]; then
                warn "Your copy is $AHEAD commit(s) ahead of GitHub (local changes present)"
            fi
        fi
    fi
fi
echo ""

# ----------------------------------------------------------------------------
# 1. Find Python 3.9+
# ----------------------------------------------------------------------------
step "Checking for Python 3.9 or newer..."

find_python() {
    for cand in python3 python python3.13 python3.12 python3.11 python3.10 python3.9; do
        if command -v "$cand" &>/dev/null; then
            if "$cand" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3,9) else 1)' 2>/dev/null; then
                echo "$cand"
                return 0
            fi
        fi
    done
    return 1
}

PYTHON_CMD=$(find_python || true)
if [ -n "$PYTHON_CMD" ]; then
    PY_VER=$("$PYTHON_CMD" --version 2>&1)
    ok "$PY_VER found"
else
    warn "Python 3.9+ was not found."
    if [ "$NO_PROMPT" -eq 1 ]; then
        fail "Python is required. Install from https://www.python.org/downloads/ and retry."
    fi
    read -rp "  Install Python automatically? [Y/n] " ans
    ans="${ans:-Y}"
    if [[ "$ans" =~ ^[Yy] ]]; then
        if command -v brew &>/dev/null; then
            step "Installing Python via Homebrew..."
            brew install python@3.12
        elif command -v apt-get &>/dev/null; then
            step "Installing Python via apt..."
            sudo apt-get install -y python3 python3-venv python3-pip
        elif command -v dnf &>/dev/null; then
            step "Installing Python via dnf..."
            sudo dnf install -y python3
        else
            fail "Could not auto-install Python. Install from https://www.python.org/downloads/"
        fi
        PYTHON_CMD=$(find_python || fail "Python installed but not visible - open a new terminal and retry.")
        ok "Python installed"
    else
        fail "Python is required. Install from https://www.python.org/downloads/ and retry."
    fi
fi

# ----------------------------------------------------------------------------
# 2. Virtual environment
# ----------------------------------------------------------------------------
step "Setting up the virtual environment (.venv)..."

VENV="$ROOT/.venv"
if [ ! -x "$VENV/bin/python" ]; then
    "$PYTHON_CMD" -m venv "$VENV"
    ok "Virtual environment created"
else
    ok "Virtual environment already exists"
fi

PY="$VENV/bin/python"

# ----------------------------------------------------------------------------
# 3. Install BatonCadence
# ----------------------------------------------------------------------------
step "Installing BatonCadence and its dependencies..."

# Air-gapped install: an offline/wheels folder (created by
# scripts/make-offline-bundle.sh on a connected machine) means we install
# entirely from local wheels - no internet required, nothing leaves the host.
WHEEL_DIR="$ROOT/offline/wheels"
if [ -d "$WHEEL_DIR" ]; then
    echo "     Offline wheel bundle detected - installing without network access."
    "$PY" -m pip install --no-index --find-links "$WHEEL_DIR" --upgrade pip --quiet
    "$PY" -m pip install --no-index --find-links "$WHEEL_DIR" -e "$ROOT" --quiet || \
        fail "Offline installation failed. Rebuild the bundle with make-offline-bundle.sh on a machine with the same OS/Python."
else
    "$PY" -m pip install --upgrade pip --quiet
    "$PY" -m pip install -e "$ROOT" --quiet || fail "Installation failed. Check your internet connection and retry."
fi
ok "BatonCadence installed"

# ----------------------------------------------------------------------------
# 4. Server or client role
# ----------------------------------------------------------------------------
if [ -z "$INSTALL_ROLE" ]; then
    if [ "$NO_PROMPT" -eq 1 ]; then
        INSTALL_ROLE="server"
    else
        echo ""
        echo -e "${CYN}============================================================${NC}"
        echo -e "${CYN}  Is this machine a server or client?${NC}"
        echo -e "${CYN}============================================================${NC}"
        echo ""
        echo "  [1] Server - runs the gateway, agents connect to it."
        echo "  [2] Client - runs agents that talk to a gateway elsewhere."
        echo ""
        read -rp "Choose [1] or [2] (default: 1): " ROLE_CHOICE
        ROLE_CHOICE="${ROLE_CHOICE:-1}"
        case "$ROLE_CHOICE" in
            1|server|Server) INSTALL_ROLE="server" ;;
            2|client|Client) INSTALL_ROLE="client" ;;
            *) fail "Choose 1 for server or 2 for client." ;;
        esac
    fi
fi

if [ "$INSTALL_ROLE" = "client" ]; then
    if [ -n "$GATEWAY_URL" ]; then
        parse_join_line "$GATEWAY_URL" || true
    fi

    if [ -z "$GATEWAY_URL" ] || [ -z "$AGENT_TOKEN" ]; then
        if [ "$NO_PROMPT" -eq 1 ]; then
            fail "Client install requires --gateway <url> and --token <tok> when --no-prompt is used."
        fi

        if [ -z "$GATEWAY_URL" ]; then
            echo ""
            echo "Paste the server join line, or enter the Gateway URL."
            echo "Example: mco join http://server:18789 --token mco_tok_..."
            read -rp "Gateway URL or join line: " CLIENT_JOIN
            if ! parse_join_line "$CLIENT_JOIN"; then
                GATEWAY_URL="$CLIENT_JOIN"
            fi
        fi

        if [ -z "$AGENT_TOKEN" ]; then
            read -rsp "Agent token: " AGENT_TOKEN
            echo ""
        fi
    fi

    GATEWAY_URL="${GATEWAY_URL%/}"
    [ -n "$GATEWAY_URL" ] || fail "Gateway URL is required for client install."
    [ -n "$AGENT_TOKEN" ] || fail "Agent token is required for client install."
fi

# ----------------------------------------------------------------------------
# 5. Global config home (~/.mco/.env)
# ----------------------------------------------------------------------------
MCO_HOME="$HOME/.mco"
mkdir -p "$MCO_HOME"
ENV_PATH="$MCO_HOME/.env"
REPO_ENV="$ROOT/.env"

# Migrate older install (repo-local .env -> global home)
if [ -f "$REPO_ENV" ] && [ ! -f "$ENV_PATH" ]; then
    mv "$REPO_ENV" "$ENV_PATH"
    ok "Moved existing configuration to $ENV_PATH (works from any directory now)"
fi

if [ "$INSTALL_ROLE" = "server" ] && [ ! -f "$ENV_PATH" ]; then
    LOCAL_TOKEN="mco_tok_$("$PY" -c 'import secrets; print(secrets.token_hex(24))')"
    cat > "$ENV_PATH" <<EOF
# BatonCadence configuration (created by install.sh)
# Local-Only profile: everything runs on this computer, no database
# or cloud account needed. Run 'mco setup' later to change anything.
MCO_PROFILE=Local-Only
OPERATOR_NAME=$(whoami)
MCO_NODE_ROLE=server
MCO_LOCAL_TOKEN=$LOCAL_TOKEN
EOF
    ok "Created Local-Only configuration ($ENV_PATH) with access token"
elif [ "$INSTALL_ROLE" = "server" ]; then
    if ! grep -q 'MCO_LOCAL_TOKEN' "$ENV_PATH" 2>/dev/null; then
        LOCAL_TOKEN="mco_tok_$("$PY" -c 'import secrets; print(secrets.token_hex(24))')"
        echo "MCO_LOCAL_TOKEN=$LOCAL_TOKEN" >> "$ENV_PATH"
        ok "Added MCO_LOCAL_TOKEN to existing configuration"
    else
        ok "Configuration already exists at $ENV_PATH - leaving it mostly untouched"
    fi
    set_env_value "MCO_NODE_ROLE" "server"
else
    if [ ! -f "$ENV_PATH" ]; then
        cat > "$ENV_PATH" <<EOF
# BatonCadence configuration (created by install.sh)
MCO_PROFILE=Client
OPERATOR_NAME=$(whoami)
MCO_NODE_ROLE=client
MCO_GATEWAY_URL=$GATEWAY_URL
MCO_AGENT_TOKEN=$AGENT_TOKEN
EOF
        ok "Created client configuration ($ENV_PATH)"
    else
        set_env_value "MCO_NODE_ROLE" "client"
        set_env_value "MCO_GATEWAY_URL" "$GATEWAY_URL"
        set_env_value "MCO_AGENT_TOKEN" "$AGENT_TOKEN"
        ok "Updated client configuration at $ENV_PATH"
    fi
fi

# Read the server token back when present
LOCAL_TOKEN=$(grep '^MCO_LOCAL_TOKEN=' "$ENV_PATH" 2>/dev/null | cut -d= -f2- | tr -d '[:space:]' || true)

if [ "$INSTALL_ROLE" = "client" ]; then
    step "Verifying client access to the gateway..."
    verify_client_gateway
fi

# ----------------------------------------------------------------------------
# 6. Symlink mco -> ~/.local/bin/mco  +  add to PATH
# ----------------------------------------------------------------------------
step "Making the 'mco' command available everywhere..."

BIN_DIR="$HOME/.local/bin"
mkdir -p "$BIN_DIR"
MCO_EXE="$VENV/bin/mco"
ln -sf "$MCO_EXE" "$BIN_DIR/mco"
ok "Linked mco -> $BIN_DIR/mco"

# Detect shell config
SHELL_CONFIG=""
if [[ "${SHELL:-}" == *"zsh"* ]]; then
    SHELL_CONFIG="$HOME/.zshrc"
elif [[ "${SHELL:-}" == *"bash"* ]]; then
    SHELL_CONFIG="${HOME}/.bashrc"
    [ ! -f "$SHELL_CONFIG" ] && SHELL_CONFIG="$HOME/.bash_profile"
else
    [ -f "$HOME/.zshrc" ]      && SHELL_CONFIG="$HOME/.zshrc"
    [ -z "$SHELL_CONFIG" ] && [ -f "$HOME/.bashrc" ] && SHELL_CONFIG="$HOME/.bashrc"
fi

if [ -n "$SHELL_CONFIG" ]; then
    if ! echo "${PATH:-}" | tr ':' '\n' | grep -qx "$BIN_DIR"; then
        if ! grep -q '\.local/bin' "$SHELL_CONFIG" 2>/dev/null; then
            {
                echo ""
                echo "# BatonCadence -- ensure ~/.local/bin is on PATH"
                echo 'export PATH="$HOME/.local/bin:$PATH"'
            } >> "$SHELL_CONFIG"
            ok "Added ~/.local/bin to PATH in $SHELL_CONFIG"
        else
            ok "~/.local/bin already in $SHELL_CONFIG"
        fi
    else
        ok "~/.local/bin already on PATH"
    fi
fi

# Make mco available in this shell session without restart
export PATH="$BIN_DIR:$PATH"

# ----------------------------------------------------------------------------
# 7. CLI self-check
# ----------------------------------------------------------------------------
step "Verifying the installation..."
"$PY" -m mco.cli --help >/dev/null 2>&1 || fail "The mco CLI failed its self-check."
ok "CLI self-check passed"

# ----------------------------------------------------------------------------
# 8. Launch
# ----------------------------------------------------------------------------
echo ""
ok "Setup complete!"
echo ""

if [ "$INSTALL_ROLE" = "server" ]; then
    JOIN_URL="$(detect_join_url)"
    echo -e "${CYN}  Share this with clients so they can join this gateway:${NC}"
    echo ""
    echo "    mco join $JOIN_URL --token $LOCAL_TOKEN"
    echo ""
else
    echo -e "${CYN}  Client configured for $GATEWAY_URL.${NC}"
    echo -e "${CYN}  Run 'mco listen' to start an agent on this machine.${NC}"
    exit 0
fi

if [ "$NO_PROMPT" -eq 1 ]; then
    echo -e "${CYN}  Run 'mco serve' to start the gateway.${NC}"
    echo -e "${CYN}  Open http://127.0.0.1:18789/console in your browser.${NC}"
    exit 0
fi

echo -e "${CYN}============================================================${NC}"
echo -e "${CYN}  How do you want to start?${NC}"
echo -e "${CYN}============================================================${NC}"
echo ""
echo -e "${YLW}  [1] Demo mode    Look around with sample data first.${NC}"
echo       "                   The console shows simulated jobs and agents."
echo       "                   You can connect to the live server any time."
echo ""
echo -e "${GRN}  [2] Connect now  Get the console talking to this machine${NC}"
echo       "                   right away."
echo ""
read -rp "Choose [1] or [2] (default: 1): " MODE_CHOICE
MODE_CHOICE="${MODE_CHOICE:-1}"
echo ""

if [ "$MODE_CHOICE" = "2" ]; then
    echo -e "${GRN}============================================================${NC}"
    echo -e "${GRN}  Connect the console to your server${NC}"
    echo -e "${GRN}============================================================${NC}"
    echo ""
    echo -e "${CYN}  Your access token:${NC}"
    echo ""
    echo "    $LOCAL_TOKEN"
    echo ""
    # Copy to clipboard if possible
    if command -v pbcopy &>/dev/null; then
        echo "$LOCAL_TOKEN" | pbcopy
        echo -e "  (Copied to your clipboard.)"
    elif command -v xclip &>/dev/null; then
        echo "$LOCAL_TOKEN" | xclip -selection clipboard
        echo -e "  (Copied to your clipboard.)"
    fi
    echo ""
    echo -e "${CYN}  When the browser opens:${NC}"
    echo ""
    echo "    1. The Gateway URL is http://127.0.0.1:18789 -- leave it as-is."
    echo "    2. Paste your token in the 'Agent token' box."
    echo "    3. Click Connect."
    echo ""
    echo -e "${GRN}============================================================${NC}"
    echo -e "${GRN}  Starting BatonCadence...${NC}"
    echo -e "${GRN}============================================================${NC}"
    echo ""

    # Open browser after a delay
    if command -v open &>/dev/null; then
        (sleep 5 && open "http://127.0.0.1:18789/console") &
    elif command -v xdg-open &>/dev/null; then
        (sleep 5 && xdg-open "http://127.0.0.1:18789/console") &
    fi

    echo -e "  Keep this terminal open. Press Ctrl+C to stop."
    echo ""
    "$PY" -m mco.cli serve

else
    echo -e "${YLW}============================================================${NC}"
    echo -e "${YLW}  Demo mode${NC}"
    echo -e "${YLW}============================================================${NC}"
    echo ""
    echo "  The console will open showing sample jobs, agents, and"
    echo "  workflows so you can explore the interface."
    echo ""
    echo -e "${CYN}  When you're ready to switch to real data:${NC}"
    echo ""
    echo "    1. Run: mco serve"
    echo "    2. Open: http://127.0.0.1:18789/console"
    echo "    3. Paste your token in the 'Agent token' box:"
    echo ""
    echo "       $LOCAL_TOKEN"
    echo ""
    echo "       (Also saved in ~/.mco/.env)"
    echo ""
    echo "    4. Click Connect."
    echo ""
    read -rp "Open the demo console now? [Y/n] " LAUNCH
    LAUNCH="${LAUNCH:-Y}"
    if [[ "$LAUNCH" =~ ^[Yy] ]]; then
        if command -v open &>/dev/null; then
            open "http://127.0.0.1:18789/console"
        elif command -v xdg-open &>/dev/null; then
            xdg-open "http://127.0.0.1:18789/console"
        else
            echo "  Open this URL in your browser: http://127.0.0.1:18789/console"
        fi
        echo ""
        echo "  Starting the gateway (Ctrl+C to stop)..."
        "$PY" -m mco.cli serve
    else
        echo ""
        echo -e "${CYN}  When you're ready: run 'mco serve' and open http://127.0.0.1:18789/console${NC}"
    fi
fi
