"""Consumer freshness and concurrent Stop with actual browser/HTTP/MCP."""
import json,time
import pytest
from verified_alexa.simulator import run_owned
from verified_alexa.cdp import BrowserConnection
from test_voice_authority_r14 import start,state

@pytest.mark.parametrize('stop_delay',[.05,.15,.5])
def test_late_real_http_response_cannot_replace_newer_stop(tmp_path,stop_delay):
    runtime=run_owned(tmp_path,port=0,headless=True);launch=next(runtime)
    e={'scope':'actual concurrent Chromium UI->HTTP->MCP requests; no fabricated completion','stop_delay_seconds':stop_delay}
    try:
        with BrowserConnection(launch['cdp_endpoint'],launch['browser_target_id']) as c:
            start(c)
            e['initial']=c.evaluate("window.sendText('Play Clip A')")
            c.evaluate("window.lateAction=window.sendText('Play Clip B');true")
            time.sleep(stop_delay)
            e['stop']=c.evaluate("document.getElementById('stop').onclick()")
            e['late_result']=c.evaluate('window.lateAction')
            time.sleep(.8)
            e['final_ui']=state(c)
            e['api_responses']=c.evaluate('window.apiResponses')
            context=tmp_path/'target-context.json'
            if context.exists():e['durable_context']=json.loads(context.read_text(encoding='utf8'))
            (tmp_path/'STOP_RACE.json').write_text(json.dumps(e,ensure_ascii=False,indent=2),encoding='utf8')
            assert e['stop']['final_status']=='VERIFIED_DONE'
            assert e['final_ui']['currentMission']==e['stop']['mission_id']
            assert e['final_ui']['paused'] and e['final_ui']['time']<.1
            assert 'stopped' in e['final_ui']['status'].lower()
    finally:runtime.close()

def test_actual_progress_frame_expires_without_new_observation(tmp_path):
    runtime=run_owned(tmp_path,port=0,headless=True);launch=next(runtime)
    e={'scope':'real observation frame first; then controlled browser request outage tests UI TTL only'}
    try:
        with BrowserConnection(launch['cdp_endpoint'],launch['browser_target_id']) as c:
            start(c);e['original']=c.evaluate("window.sendText('Play Clip A')")
            e['observed']=c.evaluate('window.refreshProgress()')
            e['before']=c.evaluate("({text:document.querySelector('#progressstatus').textContent,css:document.querySelector('#progresscard').className,thumb:document.querySelector('#evidenceframe').src})")
            # Drop only future browser progress HTTP calls; do not manufacture an
            # observation, timestamp or PASS. Existing real observation must age.
            c.evaluate("window.originalFetch=window.fetch;window.fetch=(url,...args)=>String(url).includes('/api/observe')?Promise.reject(new Error('controlled UI request outage')):window.originalFetch(url,...args);true")
            time.sleep(5)
            e['after']=c.evaluate("({text:document.querySelector('#progressstatus').textContent,css:document.querySelector('#progresscard').className,thumbHidden:document.querySelector('#evidenceframe').hidden})")
            (tmp_path/'PROGRESS_TTL.json').write_text(json.dumps(e,ensure_ascii=False,indent=2),encoding='utf8')
            assert e['before']['thumb'].startswith('data:image/')
            assert 'fresh' in e['before']['css']
            assert 'Awaiting fresh confirmation'==e['after']['text']
            assert 'fresh' not in e['after']['css'] and e['after']['thumbHidden']
    finally:runtime.close()
