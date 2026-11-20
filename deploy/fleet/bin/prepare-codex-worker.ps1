param([Parameter(Mandatory=$true)][string]$Role,
      [Parameter(Mandatory=$true)][string]$Instance)
$ErrorActionPreference = 'Stop'
$result = & 'C:/AI/baton/Batoncadence/.venv/Scripts/python.exe' '{{HOME_FWD}}/.mco/bin/codex-worker-policy.py' --role $Role --instance $Instance
if ($LASTEXITCODE -ne 0) { throw 'Codex worker preflight failed; inference was not started' }
$plan = $result | ConvertFrom-Json
if (-not $plan.has_work) { return }
# Timer and sidecar entry points share one lock, held until Codex exits.
$mutex = New-Object System.Threading.Mutex($false, "Local\BitCadence-Codex-$Instance")
$held = $false
try { $held = $mutex.WaitOne(0) }
catch [System.Threading.AbandonedMutexException] { $held = $true }
if (-not $held) { $mutex.Dispose(); return }
$plan | Add-Member -NotePropertyName WorkerMutex -NotePropertyValue $mutex
return $plan
