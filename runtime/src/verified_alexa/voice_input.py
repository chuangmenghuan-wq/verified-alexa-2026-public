"""Optional Windows offline zh-TW recognition; never treats UI text as audio."""
import base64, json, os, subprocess, uuid, wave
from pathlib import Path

def offline_speech_available():
    if os.name != 'nt': return False
    powershell = os.environ.get('SYSTEMROOT', 'C:/Windows')+'/System32/WindowsPowerShell/v1.0/powershell.exe'
    code = "Add-Type -AssemblyName System.Speech; [bool]([System.Speech.Recognition.SpeechRecognitionEngine]::InstalledRecognizers() | Where-Object {$_.Culture.Name -eq 'zh-TW'})"
    try:
        result = subprocess.run([powershell, '-NoProfile', '-Command', code], capture_output=True, timeout=10)
        return result.returncode == 0 and result.stdout.strip() == b'True'
    except (OSError, subprocess.TimeoutExpired): return False

def recognize_wave(root, encoded):
    if os.name!='nt': raise RuntimeError('Offline Mandarin speech needs Windows and an installed zh-TW recognizer. Type instead.')
    raw=base64.b64decode(encoded,validate=True)
    if len(raw)>2_000_000: raise ValueError('Audio too large; record at most 8 seconds')
    directory=Path(root)/'audio'; directory.mkdir(parents=True,exist_ok=True)
    path=directory/(uuid.uuid4().hex+'.wav'); path.write_bytes(raw)
    try:
        with wave.open(str(path)) as wav:
            if wav.getnchannels()!=1 or wav.getsampwidth()!=2 or not 8000<=wav.getframerate()<=96000 or not 0<wav.getnframes()/wav.getframerate()<=9:
                raise ValueError('Expected mono 16-bit PCM, at most 9 seconds')
        script=Path(__file__).with_name('assets')/'recognize_zh.ps1'
        powershell=os.environ.get('SYSTEMROOT','C:/Windows')+'/System32/WindowsPowerShell/v1.0/powershell.exe'
        p=subprocess.run([powershell,'-NoProfile','-ExecutionPolicy','Bypass','-File',str(script),'-WavePath',str(path)],capture_output=True,timeout=15)
        if p.returncode: raise RuntimeError('Offline speech unavailable or unsupported audio. Please type your request.')
        result=json.loads(p.stdout.decode('utf-8-sig'))
        if not isinstance(result.get('text'),str) or not result['text'].strip(): raise ValueError('No speech recognized')
        from .intent import parse_request
        result['parsed_request']=parse_request(result['text'])
        result['preview_only']=True
        result['execution_authorized']=False
        result['preview_confidence_threshold']=.50
        result['low_confidence']=float(result.get('confidence',0))<result['preview_confidence_threshold']
        if result['low_confidence']:
            result['parsed_request']={'operation':'unsupported','content_id':None,'original_text':result['text'],
                                      'reason':'Low speech confidence. Type the intended request.'}
        result['audio_sha256']=__import__('hashlib').sha256(raw).hexdigest()
        return result
    finally:
        path.unlink(missing_ok=True)
