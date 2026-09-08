<#
.SYNOPSIS
  Run an Antigravity worker for one BitCadence fleet instance, via the
  Antigravity IDE desktop app.

.DESCRIPTION
  The antigravity role previously exec'd the `gemini` CLI (see ROLE_COMMANDS in
  src/mco/orchestrator/executors.py). That path is dead: the CLI now refuses to
  start with

      IneligibleTierError: This client is no longer supported for Gemini Code
      Assist for individuals. To continue using Gemini, please migrate to the
      Antigravity suite of products.

  The error is the instruction. This runner drives the Antigravity IDE instead,
  through its `chat` subcommand, which takes a prompt and runs it in an agent
  session.

  TWO THINGS THAT DIFFER FROM THE OTHER RUNNERS:

  1. `chat` DISPATCHES AND RETURNS. It hands the prompt to the IDE window and
     exits ~immediately with code 0. It is not a blocking headless executor, so
     the waker gets no result and no exit status from the agent's actual work.
     Completion is observable only through the gateway (the agent calls
     mco_lease / mco_complete over MCP). Do not read exit 0 as "job done".
     This also means the machine must be awake with a desktop session — this
     role cannot run headless the way codex and grok can.

  2. THE .cmd SHIM MANGLES QUOTED ARGUMENTS. bin/antigravity-ide.cmd passes
     %* through cmd.exe, which strips the quotes out of JSON and long prompts.
     Invoke Antigravity IDE.exe with resources/app/out/cli.js directly under
     ELECTRON_RUN_AS_NODE=1, as below.

  MCP REGISTRATION IS SEPARATE AND EASY TO MISS. The IDE does not read
  ~/.gemini/*/mcp_config.json — those are Gemini-CLI shaped. It keeps its own
  config at %APPDATA%/Antigravity IDE/User/mcp.json. Register mco with:

      & $exe $cli --add-mcp '<json>'

  passing {name, command, args, env{MCO_GATEWAY_URL, MCO_AGENT_TOKEN,
  AGENT_ROLE, AGENT_INSTANCE_ID}}. Without it the agent starts and simply has
  no mco tools.

.PARAMETER Instance
  Fleet instance id, e.g. antigravity-beast.
#>
[CmdletBinding()]
param(
  [Parameter(Mandatory = $true)][string] $Instance,
  [string] $Role = 'antigravity',
  [string] $IdeRoot = "$env:LOCALAPPDATA\Programs\Antigravity IDE"
)

$ErrorActionPreference = 'Stop'

$exe = Join-Path $IdeRoot 'Antigravity IDE.exe'
$cli = Join-Path $IdeRoot 'resources\app\out\cli.js'
foreach ($f in @($exe, $cli)) {
  if (-not (Test-Path $f)) { Write-Error "Antigravity IDE not found at: $f"; exit 1 }
}

$promptFile = Join-Path $env:USERPROFILE ".mco\prompts\$Instance.txt"
if (-not (Test-Path $promptFile)) { Write-Error "missing prompt: $promptFile"; exit 1 }
$prompt = (Get-Content $promptFile -Raw).Trim()
if (-not $prompt) { Write-Error "prompt file is empty: $promptFile"; exit 1 }

# Identity reaches the agent through the IDE's registered mco server, not this
# process's environment -- the IDE window is already running and does not
# inherit from here. Verify registration rather than assuming it.
$ideMcp = Join-Path $env:APPDATA 'Antigravity IDE\User\mcp.json'
if (-not (Test-Path $ideMcp)) {
  Write-Error "mco is not registered with Antigravity IDE ($ideMcp missing) - see --add-mcp in this script's help"; exit 1
}
$registered = (Get-Content $ideMcp -Raw | ConvertFrom-Json).servers.mco
if (-not $registered) { Write-Error "no 'mco' server in $ideMcp"; exit 1 }
if ($registered.env.AGENT_INSTANCE_ID -ne $Instance) {
  Write-Error "IDE mco server is registered as '$($registered.env.AGENT_INSTANCE_ID)', not '$Instance' - re-run --add-mcp"; exit 1
}

$env:ELECTRON_RUN_AS_NODE = '1'
& $exe $cli chat --mode agent --reuse-window $prompt
exit $LASTEXITCODE
