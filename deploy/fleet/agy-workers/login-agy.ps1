# Sign in each Antigravity worker slot on the Beast with its OWN Google account.
# Each slot has its own home folder. agy normally saves the sign-in in the shared Windows Credential Manager
# entry "gemini:antigravity", so every slot ends up on the same account. Setting SSH_CONNECTION makes agy treat
# the session as remote and keep the token in the slot's own folder instead (that is how the Mac slots stayed apart).
#   .\login-agy.ps1            sign in all 8 slots (one window each; paste the code agy asks for)
#   .\login-agy.ps1 -Slots 3,7 just those
#   .\login-agy.ps1 -Check     report which sign-in method and account each slot uses (no windows)
param([int[]]$Slots = (5..8), [switch]$Check)    # 1-4 are moving to the MacBook Pro (owner 2026-10-27, TBD)
$agy = "{{HOME}}\AppData\Local\agy\bin\agy.exe"
$root = "{{HOME}}\.mco\agy-workers\homes"
$ssh = "`$env:SSH_CONNECTION='127.0.0.1 50000 127.0.0.1 22'; `$env:SSH_CLIENT='127.0.0.1 50000 22'"
foreach ($n in $Slots) {
    $h = Join-Path $root "agy-studio-$n"
    New-Item -ItemType Directory -Force $h | Out-Null
    if ($Check) {
        $env:USERPROFILE = $h; $env:HOME = $h
        $env:SSH_CONNECTION = '127.0.0.1 50000 127.0.0.1 22'; $env:SSH_CLIENT = '127.0.0.1 50000 22'
        $null = & $agy -p 'Reply with just the word OK.' --print-timeout 90s 2>&1
        $log = Get-ChildItem "$h\.gemini\antigravity-cli\log" -Filter *.log | Sort-Object LastWriteTime | Select-Object -Last 1
        $how = (Select-String -Path $log.FullName -Pattern 'ChainedAuth: authenticated via (\S+)' | Select-Object -Last 1).Matches.Groups[1].Value
        $who = (Select-String -Path $log.FullName -Pattern 'authenticated successfully as (\S+)' | Select-Object -Last 1).Matches.Groups[1].Value
        "agy-studio-{0}: via {1}  account {2}" -f $n, ($(if ($how) { $how } else { 'NOT SIGNED IN' })), $who
        continue
    }
    $cmd = "`$env:USERPROFILE='$h'; `$env:HOME='$h'; $ssh; `$host.UI.RawUI.WindowTitle='agy-studio-$n sign-in'; " +
           "Write-Host 'Signing in agy-studio-$n - use the Google account for THIS slot, paste the code, then close this window.'; & '$agy'"
    Start-Process pwsh -ArgumentList "-NoExit", "-Command", $cmd
    Start-Sleep -Seconds 2
}
$env:USERPROFILE = '{{HOME}}'; $env:HOME = '{{HOME}}'
