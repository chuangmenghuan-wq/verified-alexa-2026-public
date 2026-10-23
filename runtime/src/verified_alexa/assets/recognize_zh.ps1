param([Parameter(Mandatory=$true)][string]$WavePath)
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Speech
$installed = [System.Speech.Recognition.SpeechRecognitionEngine]::InstalledRecognizers() | Where-Object { $_.Culture.Name -eq 'zh-TW' } | Select-Object -First 1
if (-not $installed) { throw 'zh-TW offline speech recognizer is not installed' }
$engine = New-Object System.Speech.Recognition.SpeechRecognitionEngine($installed)
$choices = New-Object System.Speech.Recognition.Choices
$choices.Add([string[]]@(
  '幫我確認客廳播放','現在還在播嗎','好修它','好修理它','確認客廳播放','再確認一次',
  '播放影片A','播放影片B','播放A','播放B','暫停播放','停止播放',
  '從上次位置播放，到45秒停止，中斷先修一次否則問我',
  '不要修它','不用修','不要修理','不修','先不要修它','別修它','不要播放','不要暫停','不要停止',
  '不要播放影片A','不要播放影片B','不要修它而且不要播放','現在不要修它','謝謝不用修了'
))
$builder = New-Object System.Speech.Recognition.GrammarBuilder
$builder.Culture = $installed.Culture
$builder.Append($choices)
$grammar = New-Object System.Speech.Recognition.Grammar($builder)
$engine.LoadGrammar($grammar)
$dictationSupported = $true
try { $engine.LoadGrammar((New-Object System.Speech.Recognition.DictationGrammar)) }
catch { $dictationSupported = $false }
try {
  $engine.SetInputToWaveFile($WavePath)
  $segments = @()
  for ($i=0; $i -lt 8; $i++) {
    try { $result = $engine.Recognize([TimeSpan]::FromSeconds(8)) }
    catch {
      # Some installed recognizers throw after consuming the entire WAV instead
      # of returning null. Preserve collected segments only for that exact EOS.
      if ($segments.Count -gt 0 -and $_.Exception.InnerException.Message -match 'No audio input is supplied') { break }
      throw
    }
    if (-not $result) { break }
    $segments += $result
  }
  if ($segments.Count -eq 0) { throw 'No speech recognized' }
  $confidence = ($segments | Measure-Object -Property Confidence -Minimum).Minimum
  $text = ($segments | ForEach-Object {$_.Text}) -join ' '
  $alternates = @($segments | ForEach-Object {$_.Alternates | Select-Object -First 6 | ForEach-Object { @{text=$_.Text;confidence=$_.Confidence} }})
  [Console]::OutputEncoding = [System.Text.UTF8Encoding]::new()
  @{ text=$text; confidence=$confidence; alternates=$alternates; dictation_available=$dictationSupported;
     recognizer=$installed.Id; culture=$installed.Culture.Name; source='actual offline waveform recognition; preview only, never execution authorization' } | ConvertTo-Json -Depth 5 -Compress
} finally { $engine.Dispose() }
