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
  [string] $IdeRoot = "$env:LOCALAPPDATA\Programs\Antigravity IDE",
  [string] $GatewayUrl = 'http://127.0.0.1:18789',
  # The worker drives its OWN IDE instance, never the human's. See below.
  [string] $ProfileDir = (Join-Path (Join-Path $env:USERPROFILE ".mco") "antigravity-profile"),
  # Cold start into the isolated profile measured ~60s to first lease
  # (the human's profile took ~150s, carrying extensions and a workspace).
  [int] $LeaseTimeoutSeconds = 300,
  [int] $MaxRunSeconds = 3600
)

$ErrorActionPreference = 'Stop'

# Windows PowerShell 5.1 writes UTF-16 to a redirected stdout, which lands in
# the worker log as mojibake and makes it unreadable. Force UTF-8 before
# anything is written.
try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch { }
$OutputEncoding = [System.Text.Encoding]::UTF8

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
$ideMcp = Join-Path (Join-Path $ProfileDir "User") "mcp.json"
if (-not (Test-Path $ideMcp)) {
  Write-Error "mco is not registered with Antigravity IDE ($ideMcp missing) - see --add-mcp in this script's help"; exit 1
}
$registered = (Get-Content $ideMcp -Raw | ConvertFrom-Json).servers.mco
if (-not $registered) { Write-Error "no 'mco' server in $ideMcp"; exit 1 }
if ($registered.env.AGENT_INSTANCE_ID -ne $Instance) {
  Write-Error "IDE mco server is registered as '$($registered.env.AGENT_INSTANCE_ID)', not '$Instance' - re-run --add-mcp"; exit 1
}

# Read the token for our own gateway polling below. The IDE authenticates with
# its own copy from mcp.json; this is only so the runner can see whether the
# agent actually claimed anything.
$tokFile = Join-Path (Join-Path $env:USERPROFILE ".mco") (Join-Path "tokens" "$Instance.token")
if (-not (Test-Path $tokFile)) { Write-Error "no token at $tokFile"; exit 1 }
$token = (Get-Content $tokFile -Raw).Trim()
if (-not $token) { Write-Error "token file is empty: $tokFile"; exit 1 }

# `chat` returns 0 the moment it hands the prompt to the window, and prints
# nothing. Without a line here, "dispatched fine" and "never ran at all" leave
# byte-identical evidence (an untouched log), which is not a diagnosable state.
# The worker log is written here rather than by a `>>` redirect in the .cmd.
# A redirect holds the file open for the whole run, so one stale handle from a
# killed run made every later run die instantly with a bare "cannot access the
# file" and exit 1 - no dispatch, no diagnostic, and nothing in the log saying
# so. Appending per line, with a per-process fallback, means a locked log
# costs a line of output instead of the entire worker.
$script:LogPath = Join-Path $env:USERPROFILE ".mco\logs\$Instance.log"
$script:LogFallbackNoted = $false

function Log([string] $m) {
  $line = "[{0}] {1}" -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $m
  Write-Output $line
  try {
    Add-Content -Path $script:LogPath -Value $line -Encoding UTF8 -ErrorAction Stop
  } catch {
    $alt = "$($script:LogPath).$PID.log"
    if (-not $script:LogFallbackNoted) {
      Write-Output "[log] $($script:LogPath) is locked; writing to $alt instead"
      $script:LogFallbackNoted = $true
    }
    try { Add-Content -Path $alt -Value $line -Encoding UTF8 -ErrorAction Stop } catch { }
  }
}

# ---------------------------------------------------------------------------
# Preferred path: headless gemini CLI.
#
# The IDE path below works only on a cold start and cannot be isolated from the
# human's editor (measured 2026-09-09):
#   * `chat` is dispatched to whichever instance is already running, and
#     `--user-data-dir` is NOT honoured for that subcommand - it warns
#     "'user-data-dir' is not in the list of known options for subcommand
#     'chat'" and the prompt lands in the default profile regardless.
#   * A warm instance still has mco server processes alive but stops
#     heartbeating, so the agent silently has no tools and never leases. A
#     dispatch into it is indistinguishable from success.
# Together that means reliable unattended use would require killing the human's
# editor before every job, which this must never do.
#
# gemini with an API key has neither problem: no GUI, no shared instance, and
# the IneligibleTierError that killed the old executor is a restriction on the
# free OAuth tier, not on API-key auth. Set GEMINI_API_KEY (or GOOGLE_API_KEY)
# and this path is used automatically.
$apiKey = if ($env:GEMINI_API_KEY) { $env:GEMINI_API_KEY } elseif ($env:GOOGLE_API_KEY) { $env:GOOGLE_API_KEY } else { $null }
if ($apiKey) {
    $gemini = (Get-Command gemini -ErrorAction SilentlyContinue)
    if (-not $gemini) {
        Log "GEMINI_API_KEY is set but the gemini CLI is not on PATH; falling back to the IDE"
    } else {
        Log "dispatching headless via gemini CLI as $Instance (prompt $($prompt.Length) chars)"
        $env:GEMINI_API_KEY = $apiKey
        $env:MCO_GATEWAY_URL   = $GatewayUrl
        $env:MCO_AGENT_TOKEN   = $token
        $env:AGENT_ROLE        = $Role
        $env:AGENT_INSTANCE_ID = $Instance
        $prompt | & gemini --yolo
        $gcode = if ($null -eq $LASTEXITCODE) { 0 } else { $LASTEXITCODE }
        Log "gemini exited $gcode"
        exit $gcode
    }
}

# The IDE's mco connection is only good for one cold start. Measured: a freshly
# launched instance connects and leases; the same instance an hour later still
# has mco server processes alive but has not heartbeated for 12 minutes, and the
# agent silently has no tools to lease with. Dispatching into a warm window
# therefore looks identical to success and does nothing at all.
#
# So each run gets a fresh instance. It runs under its OWN --user-data-dir for
# one reason: this must never kill the human's editor. Only processes whose
# command line names this profile are stopped.
$stale = @(Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
           Where-Object { $_.CommandLine -and $_.CommandLine -like "*$ProfileDir*" })
if ($stale.Count -gt 0) {
    Log "stopping $($stale.Count) stale worker-profile IDE process(es) so mco reconnects clean"
    foreach ($proc in $stale) {
        try { Stop-Process -Id $proc.ProcessId -Force -ErrorAction Stop } catch { }
    }
    Start-Sleep -Seconds 3
}

Log "dispatching to Antigravity IDE as $Instance (prompt $($prompt.Length) chars, profile $ProfileDir)"
$env:ELECTRON_RUN_AS_NODE = '1'
& $exe $cli --user-data-dir $ProfileDir chat --mode agent $prompt
# A GUI binary launched this way can leave $LASTEXITCODE unset; treat unset as
# success rather than logging a blank code or exiting on $null.
$code = if ($null -eq $LASTEXITCODE) { 0 } else { $LASTEXITCODE }
Log "chat dispatched (exit $code). This only means the prompt reached the window."
if ($code -ne 0) { exit $code }

# `chat` returns in about a second, but the agent behind it takes minutes - a
# cold IDE start measured 150s to first lease. Returning here would tell the
# waker the run was over while it had barely begun, freeing it to dispatch a
# second prompt into the same window on the next job. So block until the
# gateway shows real evidence, and let the exit code mean something.
function Get-MyJobs {
    try {
        $r = Invoke-WebRequest -Uri "$GatewayUrl/api/jobs" -Headers @{Authorization = "Bearer $token"} `
             -UseBasicParsing -TimeoutSec 15 -ErrorAction Stop
        $data = $r.Content | ConvertFrom-Json
        if ($data.PSObject.Properties.Name -contains 'result') { $data = $data.result }
        return @($data | Where-Object { $_.leased_by_instance_id -eq $Instance -and $_.status -eq 'leased' })
    } catch {
        # A blip in the gateway must not be read as "the worker did nothing".
        Log "gateway poll failed (treating as unknown, not as failure): $($_.Exception.Message)"
        return $null
    }
}

$deadline = (Get-Date).AddSeconds($LeaseTimeoutSeconds)
$leased = $null
while ((Get-Date) -lt $deadline) {
    Start-Sleep -Seconds 5
    $mine = Get-MyJobs
    if ($null -ne $mine -and $mine.Count -gt 0) { $leased = $mine[0]; break }
}

if (-not $leased) {
    Log "NO LEASE after ${LeaseTimeoutSeconds}s. The IDE took the prompt but never claimed a job - check that the mco server is connected in that window."
    exit 1
}
Log "leased $($leased.id) - $($leased.title)"

# Hold the run open while the agent works, so the waker cannot start a second
# one behind it. Capped: a wedged agent must not pin the worker forever.
$workDeadline = (Get-Date).AddSeconds($MaxRunSeconds)
while ((Get-Date) -lt $workDeadline) {
    Start-Sleep -Seconds 15
    $mine = Get-MyJobs
    if ($null -eq $mine) { continue }
    if ($mine.Count -eq 0) { Log "job left 'leased' - run finished"; exit 0 }
}
Log "still leased after ${MaxRunSeconds}s; releasing the worker slot. The job keeps its lease until it completes or the lease expires."
exit 0
