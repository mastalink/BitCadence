# agy (Antigravity CLI / Gemini) worker run for one Beast slot, invoked by `mco wake --exec`.
# Port of the Mac Studio's agy-worker-run.sh (backup: K:\byebye studio\agy-workers). Owner OK'd unattended 2026-10-27.
#   agy-slot-run.ps1 -Instance agy-studio-5
param([Parameter(Mandatory)][string]$Instance)
$ErrorActionPreference = "Continue"
$W = "{{HOME}}\.mco\agy-workers"
$Agy = "{{HOME}}\AppData\Local\agy\bin\agy.exe"
$Log = "$W\logs\$Instance.log"
$TokFile = "{{HOME}}\.mco\tokens\$Instance.token"
function Say($m) { Add-Content -Path $Log -Value "$(Get-Date -Format s) $m" -ErrorAction SilentlyContinue }

if (-not (Test-Path $TokFile) -or (Get-Item $TokFile).Length -eq 0) { Say "no token for $Instance"; exit 1 }

# Single-flight per slot (a lock older than 40 min is stale)
$Lock = "$W\$Instance.lock"
if (Test-Path $Lock) {
    if ((Get-Item $Lock).LastWriteTime -gt (Get-Date).AddMinutes(-40)) { exit 0 }
    Remove-Item $Lock -Recurse -Force -ErrorAction SilentlyContinue
}
New-Item -ItemType Directory $Lock -Force | Out-Null
try {
    $H = "$W\homes\$Instance"
    New-Item -ItemType Directory "$W\work\$Instance" -Force | Out-Null
    Set-Location "$W\work\$Instance"
    # Per-slot Google sign-in: own home, and the SSH vars keep agy on the slot's token file instead of the shared
    # Windows Credential Manager entry (see login-agy.ps1).
    $env:USERPROFILE = $H; $env:HOME = $H
    $env:SSH_CONNECTION = "127.0.0.1 50000 127.0.0.1 22"; $env:SSH_CLIENT = "127.0.0.1 50000 22"
    $env:AGY_CLI_DISABLE_AUTO_UPDATE = "1"
    # Board identity for the mco MCP server (it inherits this env). The token is read from disk and never logged.
    $env:MCO_AGENT_TOKEN = (Get-Content $TokFile -Raw).Trim()
    $env:AGENT_ROLE = "antigravity"; $env:AGENT_INSTANCE_ID = $Instance
    $env:MCO_GATEWAY_URL = "http://127.0.0.1:18789"; $env:MCO_HTTP_TIMEOUT = "170"
    $Prompt = Get-Content "{{HOME}}\.mco\prompts\$Instance.txt" -Raw
    Say "wake: run start"
    & $Agy -p $Prompt --dangerously-skip-permissions --print-timeout 1800s *>> $Log
    Say "run end rc=$LASTEXITCODE"
    exit $LASTEXITCODE
} finally {
    Remove-Item $Lock -Recurse -Force -ErrorAction SilentlyContinue
}
