"""Actual PCM fixture recognition, not microphone/room acoustics acceptance."""
import base64,json,os
from pathlib import Path
from urllib.request import Request,urlopen
import pytest
from verified_alexa.voice_input import recognize_wave, offline_speech_available

@pytest.mark.skipif(os.name!='nt',reason='Windows offline speech path only')
def test_offline_waveform_recognition(tmp_path):
    if not offline_speech_available(): pytest.skip('Installed Windows zh-TW recognizer required; audio path unverified here')
    wav=Path(__file__).with_name('fixtures')/'fix-it-zh.wav'
    r=recognize_wave(tmp_path,base64.b64encode(wav.read_bytes()).decode())
    assert r['text']=='好修它' and r['confidence']>=.25
    (tmp_path/'VOICE_FIXTURE_EVIDENCE.json').write_text(json.dumps(r,ensure_ascii=False),encoding='utf8')
