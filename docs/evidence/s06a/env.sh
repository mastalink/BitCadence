# S06a isolated canary environment. Source me, after setting S6 and WT.
#
#   export S6=/path/to/a/throwaway/scratch/dir
#   export WT=/path/to/this/worktree
#
# Everything below is deliberately isolated from the live install: its own
# HOME (so its own LocalStore, conductor database, artifact root and tokens),
# its own port, and no notification topic.
: "${S6:?set S6 to a throwaway scratch directory}"
: "${WT:?set WT to this worktree}"
export HOME="$S6/home"
export USERPROFILE="$(cygpath -w "$S6/home" 2>/dev/null || echo "$S6/home")"
export MCO_ENV_FILE="$S6/home/.mco/.env"
export PYTHONPATH="$(cygpath -w "$WT/src" 2>/dev/null || echo "$WT/src")"

# The gateway sweep and `mco score start` MUST agree on these two, and the CLI
# resolves its own defaults from Path.home() rather than from the environment -
# so point the artifact root at the CLI's default or pass --artifact-root.
# See "A configuration trap" in the S06a report.
export MCO_SCORE_DB="$HOME/.mco/score-runs.db"
export MCO_SCORE_ARTIFACT_ROOT="$HOME/.mco/score-artifacts"

export MCO_SCORE_SWEEP_SECONDS=5      # C03's gateway sweep, on
export MCO_DELIVERY_STALL_SECONDS=0   # watchdog off: it must not be mistaken for the sweep
export MCO_LEASE_TTL_SECONDS=20       # short, so a crashed lease is reaped inside the C01 timeout
export NTFY_TOPIC=                    # no push leaves the machine
export MCO_GATEWAY_URL=http://127.0.0.1:18997
export PORT=18997

# One credential for the CLI and the gateway sweep; a run is bound to the
# credential that started it. Generate a throwaway value - never reuse a live
# board token, which would point this harness at the real board.
export MCO_LOCAL_TOKEN="${MCO_LOCAL_TOKEN:?generate a throwaway token, e.g. openssl rand -hex 16}"
export MCO_AGENT_TOKEN="$MCO_LOCAL_TOKEN"
export AGENT_INSTANCE_ID=local-operator
export AGENT_ROLE=admin
export PYBIN="${PYBIN:-$(which python)}"
