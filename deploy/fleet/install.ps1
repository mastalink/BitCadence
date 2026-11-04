#Requires -RunAsAdministrator
<#
Install the sanitized fleet templates into ~/.mco and register the wake tasks.
Run in an ADMIN PowerShell:  .\install.ps1 [-Force] [-SkipTasks]

Never creates, reads or prints tokens. After it finishes, run `mco register` once per worker
(the list is printed at the end).
#>
param([switch]$Force, [switch]$SkipTasks)
$ErrorActionPreference = 'Stop'

$here    = Split-Path -Parent $MyInvocation.MyCommand.Path
$home_   = $env:USERPROFILE
$homeFwd = $home_ -replace '\', '/'
$mco     = Join-Path $home_ '.mco'
$py      = 'C:\AI\baton\Batoncadence\.venv\Scripts\pythonw.exe'

function Expand-Template([string]$Text) { $Text.Replace('{{HOME_FWD}}', $homeFwd).Replace('{{HOME}}', $home_) }

# Never touch machine secrets, whatever the templates folder contains.
$skip = @('tokens', 'secrets', 'homes')

foreach ($sub in 'bin', 'prompts', 'agy-workers') {
    $dst = Join-Path $mco $sub
    New-Item -ItemType Directory -Force $dst | Out-Null
    foreach ($f in Get-ChildItem (Join-Path $here $sub) -File) {
        if ($f.Extension -in '.env', '.token', '.enc') { continue }
        $out = Join-Path $dst $f.Name
        if ((Test-Path $out) -and -not $Force) { "keep   $out (use -Force to overwrite)"; continue }
        if (Test-Path $out) { Copy-Item $out "$out.bak-install" -Force }
        $bytes = [IO.File]::ReadAllBytes($f.FullName)
        $text  = [Text.UTF8Encoding]::new($false).GetString($bytes)
        [IO.File]::WriteAllText($out, (Expand-Template $text), [Text.UTF8Encoding]::new($false))
        "wrote  $out"
    }
}
New-Item -ItemType Directory -Force (Join-Path $mco 'agy-workers\homes') | Out-Null

$toml = Join-Path $mco 'fleet.toml'
if ((Test-Path $toml) -and -not $Force) { "keep   $toml (use -Force to overwrite)" }
else {
    if (Test-Path $toml) { Copy-Item $toml "$toml.bak-install" -Force }
    [IO.File]::WriteAllText($toml, (Expand-Template (Get-Content (Join-Path $here 'fleet.toml') -Raw)), [Text.UTF8Encoding]::new($false))
    "wrote  $toml"
}

# role, instance, min-interval seconds (matches the live fleet)
$workers = @(
    @('antigravity', 'antigravity-beast', 10), @('chief', 'chief-beast', 300), @('cio', 'claude-cio', 10),
    @('claude', 'claude-beast', 10), @('claude', 'claude-code', 10), @('codex', 'codex-beast', 10),
    @('grok', 'grok-beast', 10), @('light', 'claude-light', 10), @('reviewer', 'reviewer-beast', 10),
    @('stealth', 'stealth-beast', 10), @('opencode', 'opencode-beast', 10)
)

if (-not $SkipTasks) {
    if (-not (Test-Path $py)) { Write-Warning "$py not found - build the BitCadence venv first, then re-run." }
    else {
        foreach ($w in $workers) {
            $role, $inst, $min = $w
            $run = "$homeFwd/.mco/bin/$inst-run.cmd"
            if (-not (Test-Path ($run -replace '/', '\'))) { "skip task for $inst (no run script)"; continue }
            $name = "BitCadence-wake-$role-$inst"
            $act  = New-ScheduledTaskAction -Execute $py -WorkingDirectory 'C:\AI\baton\Batoncadence' `
                -Argument "-m mco.cli wake --role $role --exec $run --min-interval $min --instance $inst"
            $trg  = New-ScheduledTaskTrigger -AtStartup
            $set  = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
                -StartWhenAvailable -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1)
            $prn  = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType S4U -RunLevel Limited
            Register-ScheduledTask -TaskName $name -Action $act -Trigger $trg -Settings $set -Principal $prn -Force | Out-Null
            "task   $name"
        }
        "Gemini slots: run $mco\agy-workers\activate-beast-slots.ps1 after the antigravity-beast task exists."
    }
}

""
"Next - tokens are NOT created here. For each worker run:"
foreach ($w in $workers) { "  mco register --name $($w[1]) --role $($w[0])" }
foreach ($n in 5..8)     { "  mco register --name agy-studio-$n --role antigravity" }
"Then sign each Gemini slot in with $mco\agy-workers\login-agy.ps1."
