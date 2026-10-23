param([Parameter(Mandatory=$true)][string]$Config,[Parameter(Mandatory=$true)][string]$Output)
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Speech
$narration = Get-Content -LiteralPath $Config -Raw -Encoding UTF8 | ConvertFrom-Json
$outDir = [System.IO.Path]::GetFullPath($Output)
[System.IO.Directory]::CreateDirectory($outDir) | Out-Null
$speaker = New-Object System.Speech.Synthesis.SpeechSynthesizer
$english = @($speaker.GetInstalledVoices() | Where-Object {$_.Enabled -and $_.VoiceInfo.Culture.Name -eq 'en-US'})
if (-not $english.Count) { throw 'No installed English narrator. Audio remains unverified.' }
$chosen = $english | Where-Object {$_.VoiceInfo.Name -match 'Zira'} | Select-Object -First 1
if (-not $chosen) { $chosen = $english[0] }
$speaker.SelectVoice($chosen.VoiceInfo.Name)
$speaker.Rate = 1
$speaker.Volume = 100
$format = New-Object System.Speech.AudioFormat.SpeechAudioFormatInfo(22050,[System.Speech.AudioFormat.AudioBitsPerSample]::Sixteen,[System.Speech.AudioFormat.AudioChannel]::Mono)
foreach ($segment in $narration.segments) {
    $speaker.SetOutputToWaveFile((Join-Path $outDir ($segment.id+'.wav')),$format)
    $speaker.Speak($segment.text)
    $speaker.SetOutputToNull()
}
@{voice=$chosen.VoiceInfo.Name;culture=$chosen.VoiceInfo.Culture.Name;source='Synthesized Microsoft System.Speech narration; not Alexa or human microphone';rate=1} | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $outDir 'VOICE_PROVENANCE.json') -Encoding UTF8
$speaker.Dispose()
