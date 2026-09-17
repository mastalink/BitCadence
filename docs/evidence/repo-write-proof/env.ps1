# Isolated environment for repository:write S06b adversarial proof run
$env:S6 = "C:\AI\baton\wt\scratch-s06b"
$env:WT = "C:\AI\baton\wt\score-repo-write-s06b"
$env:HOME = "$env:S6\home"
$env:USERPROFILE = "$env:S6\home"
$env:MCO_ENV_FILE = "$env:S6\home\.mco\.env"
$env:PYTHONPATH = "$env:WT\src"

$env:MCO_SCORE_DB = "$env:HOME\.mco\score-runs.db"
$env:MCO_SCORE_ARTIFACT_ROOT = "$env:HOME\.mco\score-artifacts"

$env:MCO_SCORE_SWEEP_SECONDS = "2"
$env:MCO_DELIVERY_STALL_SECONDS = "0"
$env:MCO_LEASE_TTL_SECONDS = "10"
$env:NTFY_TOPIC = ""
$env:MCO_GATEWAY_URL = "http://127.0.0.1:18997"
$env:PORT = "18997"

$env:MCO_LOCAL_TOKEN = "a1b2c3d4e5f60718293a4b5c6d7e8f90"
$env:MCO_AGENT_TOKEN = "a1b2c3d4e5f60718293a4b5c6d7e8f90"
$env:AGENT_INSTANCE_ID = "local-operator"
$env:AGENT_ROLE = "admin"
$env:MCO_SCORE_GRANT_KEY = "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"

$env:THROWAWAY_REPO = "$env:S6\throwaway-repo"
$env:THROWAWAY_WT = "$env:S6\throwaway-wt"
