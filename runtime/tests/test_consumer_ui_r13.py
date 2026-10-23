"""Completion/voice presentation contract; owner-tagged browser identity is not a security boundary."""
from pathlib import Path
from verified_alexa import simulator
import base64,json,time
import pytest
from verified_alexa.cdp import BrowserConnection
from verified_alexa.voice_input import offline_speech_available

def test_consumer_voice_handler_matches_wrapper_contract():
    html=(simulator.ASSETS/'index.html').read_text(encoding='utf-8')+(simulator.ASSETS/'consumer.js').read_text(encoding='utf-8')
    assert 'handleVoiceResponse' in html
    assert 'd.speech_input.text' not in html
    assert 'Confirm recognized request' in html

def test_foreign_player_is_explicitly_remote():
    html=(simulator.ASSETS/'index.html').read_text(encoding='utf-8')+(simulator.ASSETS/'consumer.js').read_text(encoding='utf-8')
    assert 'Remote target verified' in html
    assert '/api/player-binding' in html
    assert 'X-Verified-Player-Session' in html

def test_devtools_port_read_retries_partial_file_and_permission_error(monkeypatch):
    class Browser:
        def poll(self):return None
    class File:
        def __init__(self):self.calls=0
        def read_text(self,**kwargs):
            self.calls+=1
            if self.calls==1:raise PermissionError('browser still writing')
            return '43210\n' if self.calls==2 else '43210\n/devtools/browser/example\n'
    f=File(); monkeypatch.setattr(simulator.time,'sleep',lambda _:None)
    assert simulator.read_devtools_port(f,Browser(),retries=3)==43210 and f.calls==3

def test_real_consumer_waveform_preview_confirm_mcp_and_owner_binding(tmp_path):
    if not offline_speech_available():pytest.skip('Windows zh-TW recognizer unavailable; synthetic waveform UI path unverified')
    runtime=simulator.run_owned(tmp_path,port=0,headless=True);launch=next(runtime)
    try:
        with BrowserConnection(launch['cdp_endpoint'],launch['browser_target_id']) as c:
            c.socket.settimeout(45)
            for _ in range(40):
                if c.evaluate("typeof window.handleVoiceResponse==='function'"):break
                time.sleep(.1)
            assert c.evaluate('window.refreshOwnership()') is True
            c.command('Page.reload')
            for _ in range(40):
                try:
                    if c.evaluate("typeof window.handleVoiceResponse==='function' && Boolean(window.__verifiedOwnedSession)"):
                        break
                except RuntimeError: pass
                time.sleep(.1)
            assert c.evaluate('window.refreshOwnership()') is True, 'The bound owned window must retain ownership after page reload'
            original=c.evaluate("window.sendText('Play Clip A')")
            assert original['final_status']=='VERIFIED_DONE'
            c.evaluate("api('/api/drift',{mission_id:mid})")
            preview=c.evaluate("document.getElementById('fixture').onclick()")
            (tmp_path/'ACTUAL_VOICE_PREVIEW.json').write_text(json.dumps(preview,ensure_ascii=False,indent=2),encoding='utf8')
            assert preview['confirmation_required'] is True and preview['recognition']['text']=='好修它'
            assert c.evaluate("document.getElementById('status').textContent")=='Review the recognized request — no action taken'
            repaired=c.evaluate('window.confirmVoice()')
            assert repaired['final_status']=='VERIFIED_DONE' and repaired['mission_id']==original['mission_id']
            assert repaired['attempt_id']!=original['attempt_id']
            assert c.evaluate("document.getElementById('status').textContent").startswith('Verified')
            # Foregrounding a new remote control tab hides the actual target.
            # Native decoding alone cannot pass the visible compositor check.
            foreign_id=c.command('Target.createTarget',{'url':launch['ui_url']})['targetId']
            try:
                with BrowserConnection(launch['cdp_endpoint'],foreign_id) as foreign:
                    foreign.socket.settimeout(45)
                    for _ in range(40):
                        if foreign.evaluate("typeof window.handleVoiceResponse==='function'"):break
                        time.sleep(.1)
                    assert foreign.evaluate('window.refreshOwnership()') is False
                    foreign.command('Page.bringToFront')
                    rechecked=foreign.evaluate("window.sendText('Check again')")
                    (tmp_path/'FOREIGN_HIDDEN_TARGET_RECHECK.json').write_text(json.dumps(rechecked,ensure_ascii=False,indent=2),encoding='utf8')
                    assert rechecked['final_status']=='NOT_VERIFIED'
                    hidden=rechecked['final_independent_verification']
                    assert hidden['checks']['native_player_observation'] and hidden['checks']['element_identity']
                    assert not hidden['checks']['visible_compositor']
                    assert all(not sample['presentation']['document_visible'] for sample in hidden['native_samples'])
                    assert foreign.evaluate("document.getElementById('status').textContent")=='Current outcome not verified'
                    assert foreign.evaluate("document.querySelector('#player').currentSrc")==''
                    # Restore actual target visibility. The same foreign tab
                    # may request a check, but cannot claim to be that player.
                    c.command('Page.bringToFront')
                    visible_recheck=foreign.evaluate("window.sendText('Check again')")
                    (tmp_path/'FOREIGN_VISIBLE_TARGET_RECHECK.json').write_text(json.dumps(visible_recheck,ensure_ascii=False,indent=2),encoding='utf8')
                    assert visible_recheck['final_status']=='VERIFIED_DONE'
                    assert visible_recheck['final_independent_verification']['checks']['visible_compositor']
                    assert foreign.evaluate('window.refreshOwnership()') is False
                    assert foreign.evaluate("document.getElementById('status').textContent").startswith('Remote target verified')
                    assert foreign.evaluate("document.querySelector('#player').currentSrc")==''
            finally:c.command('Target.closeTarget',{'targetId':foreign_id})
            evidence={'scope':'actual browser handler, real synthetic waveform ASR and MCP; physical mic/user study unverified',
                      'original':original,'preview':preview,'repaired':repaired,'foreign_hidden':rechecked,'foreign_visible':visible_recheck}
            (tmp_path/'CONSUMER_UI_VOICE_EVIDENCE.json').write_text(json.dumps(evidence,ensure_ascii=False,indent=2),encoding='utf8')
    finally:runtime.close()
