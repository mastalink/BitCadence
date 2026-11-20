# Turn on the Beast Gemini slots agy-studio-5..8 as unattended board workers (owner OK'd 2026-10-27).
# Run in an ADMIN PowerShell:  & {{HOME}}\.mco\agy-workers\activate-beast-slots.ps1
# Creates one wake task per slot (a copy of the antigravity-beast one), starts it, and lets the autopilot send work.
#   -Slots 5,6   only those     -Off   stop and remove the tasks and mark the slots away again
param([int[]]$Slots = (5..8), [switch]$Off)
$shep = "C:\AI\content-studio\tools\shepherd\shepherd.py"
$src = Get-ScheduledTask -TaskName 'BitCadence-wake-antigravity-antigravity-beast'
$a0 = $src.Actions[0]
foreach ($n in $Slots) {
    $i = "agy-studio-$n"; $t = "BitCadence-wake-antigravity-$i"
    if ($Off) {
        Stop-ScheduledTask -TaskName $t -ErrorAction SilentlyContinue
        Unregister-ScheduledTask -TaskName $t -Confirm:$false -ErrorAction SilentlyContinue
        python $shep away $i 2099-01-01T00:00:00Z --reason "turned off with activate-beast-slots.ps1 -Off" | Out-Null
        "$i off"; continue
    }
    $act = New-ScheduledTaskAction -Execute $a0.Execute -WorkingDirectory $a0.WorkingDirectory `
        -Argument "-m mco.cli wake --role antigravity --exec {{HOME_FWD}}/.mco/bin/$i-run.cmd --min-interval 60 --instance $i"
    Register-ScheduledTask -TaskName $t -Action $act -Trigger $src.Triggers -Settings $src.Settings -Principal $src.Principal -Force | Out-Null
    Start-ScheduledTask -TaskName $t
    python $shep away $i --clear | Out-Null
    "$i on"
}
Start-Sleep 5
Get-ScheduledTask | Where-Object TaskName -match 'agy-studio' | Select-Object TaskName, State | Format-Table
