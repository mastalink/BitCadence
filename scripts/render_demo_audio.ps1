[CmdletBinding()]
param([string]$Voice = 'Microsoft David Desktop')
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Speech
$demoRoot = Join-Path (Split-Path $PSScriptRoot -Parent) 'output\playwright\demo-pack'
$audioRoot = Join-Path $demoRoot 'narration'
$manifest = Get-Content -Raw (Join-Path $audioRoot 'manifest.json') | ConvertFrom-Json
$speaker = New-Object System.Speech.Synthesis.SpeechSynthesizer
$speaker.SelectVoice($Voice)
$speaker.Rate = 0
function Run-Media([string[]]$MediaArgs) {
    & ffmpeg @MediaArgs
    if ($LASTEXITCODE -ne 0) { throw 'Audio/video rendering failed' }
}
try {
    foreach ($clip in $manifest) {
        $inputs = @(); $filters = @(); $mixLabels = @()
        for ($idx = 0; $idx -lt $clip.segments.Count; $idx++) {
            $segment = $clip.segments[$idx]
            $wav = Join-Path $audioRoot "$($clip.stem)-$idx.wav"
            $speaker.SetOutputToWaveFile($wav)
            $speaker.Speak([string]$segment.text)
            $speaker.SetOutputToNull()
            $durationText = & ffprobe -v error -show_entries format=duration -of default=noprint_wrappers=1:nokey=1 $wav
            $length = [double]::Parse($durationText, [Globalization.CultureInfo]::InvariantCulture)
            $end = if ($idx + 1 -lt $clip.segments.Count) { $clip.segments[$idx + 1].start } else { $clip.duration }
            $available = [double]$end - [double]$segment.start - 0.3
            $tempo = [Math]::Max(1, $length / $available)
            if ($tempo -gt 1.35) { throw "Narration is too long for $($clip.stem) cue $idx. Shorten the script." }
            $tempoText = $tempo.ToString('0.000', [Globalization.CultureInfo]::InvariantCulture)
            $delay = [int]($segment.start * 1000)
            $inputs += @('-i', $wav)
            $filters += "[$($idx):a]atempo=$tempoText,adelay=$($delay):all=1[a$idx]"
            $mixLabels += "[a$idx]"
        }
        $filters += ($mixLabels -join '') + "amix=inputs=$($clip.segments.Count):normalize=0,apad,atrim=0:$($clip.duration)[out]"
        $track = Join-Path $audioRoot "$($clip.stem)-voiceover.mp3"
        Run-Media (@('-hide_banner','-loglevel','error','-y') + $inputs + @('-filter_complex',($filters -join ';'),'-map','[out]','-c:a','libmp3lame','-q:a','2',$track))
        Run-Media @('-hide_banner','-loglevel','error','-y','-i',(Join-Path $demoRoot "$($clip.stem).mp4"),'-i',$track,'-map','0:v:0','-map','1:a:0','-c:v','copy','-c:a','aac','-movflags','+faststart','-shortest',(Join-Path $demoRoot "$($clip.stem)-narrated.mp4"))
        Write-Output "Rendered $($clip.stem)-narrated.mp4"
    }
} finally { $speaker.Dispose() }
