param([string]$Python = "python")
$ErrorActionPreference = 'Stop'
$DesktopRepo = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$DesktopPython = (& $Python -c 'import sys; print(sys.executable)').Trim()
if ($LASTEXITCODE -ne 0) { throw 'Python could not start.' }
& $DesktopPython -m pip install --target (Join-Path $DesktopRepo '.codex/desktopdeps') pystray Pillow
if ($LASTEXITCODE -ne 0) { throw 'Desktop dependency installation failed.' }
$env:PYTHONPATH = (Join-Path $DesktopRepo 'src') + ';' + (Join-Path $DesktopRepo '.codex/desktopdeps')
$DesktopIcon = Join-Path $DesktopRepo '.codex/bitcadence.ico'
& $DesktopPython -c 'from mco.desktop.app import make_icon; import sys; make_icon().save(sys.argv[1], sizes=[(16,16),(32,32),(48,48),(64,64)])' $DesktopIcon
if ($LASTEXITCODE -ne 0) { throw 'Icon generation failed.' }
$DesktopPythonw = Join-Path (Split-Path $DesktopPython) 'pythonw.exe'
if (-not (Test-Path -LiteralPath $DesktopPythonw)) { throw 'pythonw.exe is required for window-free launch.' }
$DesktopShell = New-Object -ComObject WScript.Shell
foreach ($DesktopFolder in @([Environment]::GetFolderPath('Desktop'), [Environment]::GetFolderPath('Programs'))) {
    $DesktopLink = $DesktopShell.CreateShortcut((Join-Path $DesktopFolder 'BitCadence.lnk'))
    $DesktopLink.TargetPath = $DesktopPythonw
    $DesktopLink.Arguments = '"' + (Join-Path $DesktopRepo 'scripts/desktop.pyw') + '"'
    $DesktopLink.WorkingDirectory = $DesktopRepo
    $DesktopLink.IconLocation = $DesktopIcon
    $DesktopLink.Description = 'Manage the local BitCadence server and agents'
    $DesktopLink.Save()
}
Write-Output 'Installed BitCadence shortcuts on the Desktop and Start menu.'
