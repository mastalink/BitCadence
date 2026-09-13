param(
    [string]$Pythonw,
    [switch]$Remove
)
$ErrorActionPreference = 'Stop'
$DesktopRepo = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$DesktopStartupLink = Join-Path ([Environment]::GetFolderPath('Startup')) 'BitCadence.lnk'
if ($Remove) {
    if (Test-Path -LiteralPath $DesktopStartupLink) { Remove-Item -LiteralPath $DesktopStartupLink }
    Write-Output 'Disabled BitCadence startup at sign-in. Running processes were not stopped.'
    return
}
if (-not $Pythonw -or -not (Test-Path -LiteralPath $Pythonw)) {
    throw 'Supply -Pythonw with the full path to the installed pythonw.exe.'
}
$DesktopShell = New-Object -ComObject WScript.Shell
$DesktopLink = $DesktopShell.CreateShortcut($DesktopStartupLink)
$DesktopLink.TargetPath = (Resolve-Path -LiteralPath $Pythonw).Path
$DesktopLink.Arguments = '"' + (Join-Path $DesktopRepo 'scripts/desktop.pyw') + '" --start-all --minimized'
$DesktopLink.WorkingDirectory = $DesktopRepo
$DesktopLink.Description = 'Start the BitCadence desktop supervisor and configured workers at sign-in'
$DesktopLink.Save()
Write-Output "Installed sign-in startup: $DesktopStartupLink"
