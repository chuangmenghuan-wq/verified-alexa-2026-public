param([Parameter(Mandatory=$true)][string]$Config,[Parameter(Mandatory=$true)][string]$Output)
$ErrorActionPreference='Stop'
Add-Type -AssemblyName System.Speech
$spec=Get-Content -LiteralPath $Config -Raw -Encoding UTF8 | ConvertFrom-Json
$speaker=New-Object System.Speech.Synthesis.SpeechSynthesizer
$voice=$speaker.GetInstalledVoices() | Where-Object {$_.Enabled -and $_.VoiceInfo.Culture.Name -eq 'zh-TW'} | Select-Object -First 1
if(-not $voice){throw 'No installed zh-TW voice. Synthetic plan audio remains unavailable.'}
$speaker.SelectVoice($voice.VoiceInfo.Name);$speaker.Rate=2;$speaker.Volume=100
$format=New-Object System.Speech.AudioFormat.SpeechAudioFormatInfo(22050,[System.Speech.AudioFormat.AudioBitsPerSample]::Sixteen,[System.Speech.AudioFormat.AudioChannel]::Mono)
try{$speaker.SetOutputToWaveFile([System.IO.Path]::GetFullPath($Output),$format);$speaker.Speak($spec.text);$speaker.SetOutputToNull()}
finally{$speaker.Dispose()}
