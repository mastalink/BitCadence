<#
.SYNOPSIS
  Run a Claude Code worker for one BitCadence fleet instance.

.DESCRIPTION
  BitCadence shipped runner scripts for codex, grok, opencode and reviewer but
  none for Claude, so a `claude`-role agent could be registered, hold a valid
  token and still never run. This is that missing runner.

  Identity is passed by ENVIRONMENT rather than written into a config file, so a
  token rotation needs no edit here. Note this is a convenience, not a security
  boundary: the agent runs with --dangerously-skip-permissions and inherits the
  token into every command it chooses to run. Scope the token accordingly.

  Two things this deliberately does NOT do:
    * It never runs in the shared checkout. git's branch, index and working tree
      are global per checkout, so an unattended worker sharing one races a human.
      fleet-worktree.ps1 hands out a per-instance detached worktree instead.
    * It passes --strict-mcp-config so a project-level .mcp.json cannot shadow
      this worker's identity. Without it a worker started inside a repo silently
      adopts whatever agent that repo's .mcp.json names, corrupting attribution
      while the token is live.

.PARAMETER Instance
  Fleet instance id, e.g. claude-beast. Must match ~/.mco/tokens/<Instance>.token.

.PARAMETER Role
  MCO role for this instance. Defaults to claude.

.EXAMPLE
  ./claude-worker-run.ps1 -Instance claude-beast
#>
[CmdletBinding()]
param(
  [Parameter(Mandatory = $true)][string] $Instance,
  [string] $Role = 'claude',
  [string] $GatewayUrl = 'http://127.0.0.1:18789',
  [string] $WorktreeScript,
  [string] $PythonPath
)

$ErrorActionPreference = 'Stop'

# Resolve the BitCadence checkout without hardcoding one machine's layout.
# Order: explicit parameter, BC_HOME, this script's own repo location.
function Resolve-BcPath([string] $Explicit, [string] $RelPath) {
  if ($Explicit) { return $Explicit }
  $roots = @()
  if ($env:BC_HOME) { $roots += $env:BC_HOME }
  $roots += (Join-Path $PSScriptRoot '..\..')          # scripts/workers -> repo root
  foreach ($r in $roots) {
    $candidate = Join-Path $r $RelPath
    if (Test-Path $candidate) { return (Resolve-Path $candidate).Path }
  }
  return $null
}

$WorktreeScript = Resolve-BcPath $WorktreeScript 'scripts/fleet-worktree.ps1'
if (-not $WorktreeScript) {
  Write-Error 'cannot locate scripts/fleet-worktree.ps1 - pass -WorktreeScript or set BC_HOME'; exit 1
}
$PythonPath = Resolve-BcPath $PythonPath '.venv/Scripts/python.exe'
if (-not $PythonPath) {
  $PythonPath = (Get-Command python -ErrorAction SilentlyContinue).Source
}
if (-not $PythonPath) {
  Write-Error 'cannot locate a Python for the mco MCP server - pass -PythonPath or set BC_HOME'; exit 1
}
# worker-mcp.json refers to ${MCO_PYTHON}; Claude Code expands it from the environment.
$env:MCO_PYTHON = $PythonPath

$mcoHome  = Join-Path $env:USERPROFILE '.mco'
$tokFile  = Join-Path $mcoHome "tokens\$Instance.token"
$mcpFile  = Join-Path $mcoHome 'worker-mcp.json'
$prompt   = Join-Path $mcoHome "prompts\$Instance.txt"

if (-not (Test-Path $tokFile)) {
  Write-Error "no token at $tokFile - run: mco register --name $Instance --role $Role"
  exit 1
}
$token = (Get-Content $tokFile -Raw).Trim()
if (-not $token) { Write-Error "token file $tokFile is empty"; exit 1 }

foreach ($f in @($mcpFile, $prompt)) {
  if (-not (Test-Path $f)) { Write-Error "missing required file: $f"; exit 1 }
}

# Arms the pre-commit guard: a commit from the shared checkout is refused
# rather than allowed to race a human session.
$env:BC_FLEET_WORKTREE = '1'
$env:MCO_GATEWAY_URL   = $GatewayUrl
$env:MCO_AGENT_TOKEN   = $token
$env:AGENT_ROLE        = $Role
$env:AGENT_INSTANCE_ID = $Instance

# fleet-worktree.ps1 signals success by returning the path and failure by
# calling exit inside Fail(); it does not set an exit code on the success path,
# so $LASTEXITCODE here would be whatever git last set. Trust the return value.
try {
  $wt = & $WorktreeScript -Instance $Instance
} catch {
  Write-Error "fleet-worktree failed: $_"; exit 1
}
$wt = @($wt | Where-Object { $_ }) | Select-Object -Last 1
if (-not $wt -or -not (Test-Path $wt)) { Write-Error 'fleet-worktree returned no usable worktree path'; exit 1 }
Set-Location $wt

Get-Content $prompt -Raw |
  & claude -p --mcp-config $mcpFile --strict-mcp-config --dangerously-skip-permissions
exit $LASTEXITCODE
