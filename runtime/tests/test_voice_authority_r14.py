"""Actual owned-browser/MCP regression for R13 consent supersession and revoke."""
import json,time
from pathlib import Path
import pytest
from verified_alexa.simulator import run_owned
from verified_alexa.cdp import BrowserConnection

def state(c):
    return c.evaluate("({status:document.getElementById('status').textContent,previewHidden:document.getElementById('speechpreview').hidden,currentMission:localStorage.getItem('verifiedAlexaActualMission'),paused:document.querySelector('#player').paused,time:document.querySelector('#player').currentTime,src:document.querySelector('#player').currentSrc})")

def confirm_raw(c, token):
    return c.evaluate("fetch('/api/voice/confirm',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({confirmation_id:"+json.dumps(token)+"})}).then(async r=>({http_status:r.status,body:await r.json()}))")

def start(c):
    c.socket.settimeout(55)
    for _ in range(60):
        if c.evaluate("typeof window.sendText==='function'"):return
        time.sleep(.1)
    raise AssertionError('Consumer did not load')

def preview(c):
    return c.evaluate("api('/api/voice/text-preview',{text:'Yes, fix it',mission_id:mid,new_mission:true}).then(window.handleVoiceResponse)")

def rejected(raw):
    b=raw.get('body',{})
    return raw['http_status']>=400 or b.get('final_status') in ('PREVIEW_REJECTED','NOT_VERIFIED','REJECTED')

@pytest.mark.parametrize('newer',['Stop playback','Play Clip B'])
def test_old_voice_consent_cannot_override_newer_goal(tmp_path,newer):
    runtime=run_owned(tmp_path,port=0,headless=True);launch=next(runtime)
    evidence={'scope':'actual owned Chrome UI, bridge and real MCP; controlled text-preview seam, not physical mic','newer':newer}
    try:
        with BrowserConnection(launch['cdp_endpoint'],launch['browser_target_id']) as c:
            start(c)
            evidence['original']=c.evaluate("window.sendText('Play Clip A')")
            evidence['preview']=preview(c)
            evidence['newer_command']=c.evaluate('window.sendText('+json.dumps(newer)+')')
            evidence['after_newer']=state(c)
            evidence['confirmation']=confirm_raw(c,evidence['preview']['confirmation_id'])
            evidence['after_confirmation']=state(c)
            (tmp_path/'AUTHORITY_PROBE.json').write_text(json.dumps(evidence,ensure_ascii=False,indent=2),encoding='utf8')
            assert rejected(evidence['confirmation']), 'Obsolete voice token executed after a newer goal'
            assert evidence['after_confirmation']['currentMission']==evidence['newer_command']['mission_id']
            if newer=='Stop playback':
                assert evidence['after_confirmation']['paused'] and evidence['after_confirmation']['time']<.1
            assert evidence['after_newer']['previewHidden'], 'Newer goal left obsolete confirmation visible'
    finally:runtime.close()

def test_cancel_rejects_retained_token_without_new_attempt(tmp_path):
    runtime=run_owned(tmp_path,port=0,headless=True);launch=next(runtime)
    evidence={'scope':'actual owned Chrome cancel plus retained-token API request; not microphone ASR'}
    try:
        with BrowserConnection(launch['cdp_endpoint'],launch['browser_target_id']) as c:
            start(c)
            evidence['original']=c.evaluate("window.sendText('Play Clip A')")
            evidence['preview']=preview(c)
            evidence['cancel_result']=c.evaluate("document.getElementById('cancelvoice').onclick()")
            evidence['after_cancel']=state(c)
            evidence['confirmation']=confirm_raw(c,evidence['preview']['confirmation_id'])
            evidence['after_confirmation']=state(c)
            (tmp_path/'AUTHORITY_PROBE.json').write_text(json.dumps(evidence,ensure_ascii=False,indent=2),encoding='utf8')
            assert rejected(evidence['confirmation']), 'Canceled voice token remains a live server capability'
            assert evidence['after_confirmation']['src']==evidence['after_cancel']['src'], 'Canceled confirmation changed route revision'
    finally:runtime.close()
