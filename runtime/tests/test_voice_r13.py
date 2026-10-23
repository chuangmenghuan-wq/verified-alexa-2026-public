"""Speech authorization regression: audio recognition is a preview, never execution consent."""
import base64, json, os, threading
from http.server import ThreadingHTTPServer
from urllib.request import Request, urlopen
from urllib.error import HTTPError
from verified_alexa.simulator import make_handler
from verified_alexa import voice_input
import pytest

class RecordingClient:
    def __init__(self): self.calls=[]
    def call(self,name,args):
        self.calls.append((name,args))
        if name=='preview_request':
            from verified_alexa.intent import parse_request
            parsed=parse_request(args['goal'])
            allowed=parsed['operation'] not in ('unsupported','decline')
            return {'preview_id':'core-preview' if allowed else None,'confirmation_required':allowed,'parsed_request':parsed,
                    'final_status':'AWAITING_CONFIRMATION' if allowed else 'UNSUPPORTED'}
        return {'final_status':'VERIFIED_DONE','mission_id':'prior','assistant_message':'Verified'}

def test_audio_recognition_does_not_authorize_repair(tmp_path,monkeypatch):
    client=RecordingClient()
    monkeypatch.setattr(voice_input,'recognize_wave',lambda root,audio:{'text':'好修它','confidence':.98,'source':'fixture ASR'})
    server=ThreadingHTTPServer(('127.0.0.1',0),make_handler(tmp_path,client))
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    try:
        request=Request(f'http://127.0.0.1:{server.server_port}/api/voice',data=json.dumps({'audio_base64':'AA==','mission_id':'prior'}).encode(),headers={'Content-Type':'application/json'})
        result=json.load(urlopen(request))
        assert [name for name,args in client.calls]==['preview_request'], 'Recognition must call the shared MCP preview, never execution'
        assert result['confirmation_required'] is True and result['recognition']['text']=='好修它'
    finally: server.shutdown();server.server_close()

def test_negative_speech_is_not_constrained_to_three_positive_choices():
    from pathlib import Path
    script=(Path(voice_input.__file__).with_name('assets')/'recognize_zh.ps1').read_text(encoding='utf-8-sig')
    assert '不要修它' in script
    assert 'DictationGrammar' in script

def test_low_confidence_positive_audio_has_no_confirmation_token(tmp_path,monkeypatch):
    client=RecordingClient()
    monkeypatch.setattr(voice_input,'recognize_wave',lambda root,audio:{'text':'好修它','confidence':.24,'low_confidence':True})
    server=ThreadingHTTPServer(('127.0.0.1',0),make_handler(tmp_path,client))
    threading.Thread(target=server.serve_forever,daemon=True).start()
    try:
        r=json.load(urlopen(Request(f'http://127.0.0.1:{server.server_port}/api/voice',data=json.dumps({'audio_base64':'AA==','mission_id':'prior'}).encode(),headers={'Content-Type':'application/json'})))
        assert r['parsed_request']['operation']=='unsupported' and r['confirmation_required'] is False
        assert r['confirmation_id'] is None and [name for name,args in client.calls]==['preview_request']
    finally:server.shutdown();server.server_close()

def test_negative_text_preview_has_no_action_or_confirmation(tmp_path):
    client=RecordingClient();server=ThreadingHTTPServer(('127.0.0.1',0),make_handler(tmp_path,client))
    threading.Thread(target=server.serve_forever,daemon=True).start()
    try:
        r=json.load(urlopen(Request(f'http://127.0.0.1:{server.server_port}/api/voice/text-preview',data=json.dumps({'text':'不要修它','mission_id':'prior'}).encode(),headers={'Content-Type':'application/json'})))
        assert r['confirmation_required'] is False and r['confirmation_id'] is None and [name for name,args in client.calls]==['preview_request']
    finally:server.shutdown();server.server_close()

@pytest.mark.skipif(os.name!='nt',reason='Windows PCM recognition only; not a physical microphone test')
def test_original_negated_wave_cannot_be_repair(tmp_path):
    from pathlib import Path
    if not voice_input.offline_speech_available():pytest.skip('zh-TW recognizer unavailable')
    raw=(Path(__file__).with_name('fixtures')/'do-not-repair-zh.wav').read_bytes()
    try:r=voice_input.recognize_wave(tmp_path,base64.b64encode(raw).decode())
    except (ValueError,RuntimeError):return
    assert r['parsed_request']['operation']!='repair' or r['low_confidence']
    assert r['execution_authorized'] is False

@pytest.mark.parametrize('fixture',('do-not-repair-zh.wav','unknown-day-zh.wav'))
def test_actual_negative_unknown_audio_endpoint_cannot_execute(tmp_path,fixture):
    from pathlib import Path
    if not voice_input.offline_speech_available():pytest.skip('Windows zh-TW recognizer unavailable')
    raw=(Path(__file__).with_name('fixtures')/fixture).read_bytes()
    client=RecordingClient();server=ThreadingHTTPServer(('127.0.0.1',0),make_handler(tmp_path,client))
    threading.Thread(target=server.serve_forever,daemon=True).start()
    try:
        request=Request(f'http://127.0.0.1:{server.server_port}/api/voice',data=json.dumps({'audio_base64':base64.b64encode(raw).decode(),'mission_id':'unchanged'}).encode(),headers={'Content-Type':'application/json'})
        try:response=json.load(urlopen(request));status=200
        except HTTPError as e:response=json.load(e);status=e.code
        assert all(name=='preview_request' for name,args in client.calls), 'Actual negative/unknown wave recognition must never execute an MCP action'
        assert status==400 or (response['confirmation_required'] is False and response['confirmation_id'] is None)
        (tmp_path/(fixture+'.response.json')).write_text(json.dumps({'scope':'actual synthesized PCM; no physical microphone claim','http_status':status,'response':response,'MCP_calls':client.calls},ensure_ascii=False,indent=2),encoding='utf8')
    finally:server.shutdown();server.server_close()
