# S06b isolated repository:write adversarial environment
export S6="${S6:-C:/AI/baton/wt/scratch-s06b}"
export WT="${WT:-C:/AI/baton/wt/score-repo-write-s06b}"
export HOME="$S6/home"
export USERPROFILE="$S6/home"
export MCO_ENV_FILE="$S6/home/.mco/.env"
export PYTHONPATH="$WT/src"

export MCO_SCORE_DB="$HOME/.mco/score-runs.db"
export MCO_SCORE_ARTIFACT_ROOT="$HOME/.mco/score-artifacts"

export MCO_SCORE_SWEEP_SECONDS=2
export MCO_DELIVERY_STALL_SECONDS=0
export MCO_LEASE_TTL_SECONDS=10
export NTFY_TOPIC=
export MCO_GATEWAY_URL=http://127.0.0.1:18997
export PORT=18997

export MCO_LOCAL_TOKEN="a1b2c3d4e5f60718293a4b5c6d7e8f90"
export MCO_AGENT_TOKEN="a1b2c3d4e5f60718293a4b5c6d7e8f90"
export AGENT_INSTANCE_ID=local-operator
export AGENT_ROLE=admin
export MCO_SCORE_GRANT_KEY="0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"

export THROWAWAY_REPO="$S6/throwaway-repo"
export THROWAWAY_WT="$S6/throwaway-wt"
