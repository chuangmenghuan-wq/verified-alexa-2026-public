"""Real HTTP/player integration: never fake outcome fields."""
import json, os, subprocess, sys, time
import pytest
from verified_alexa.consumer_media_mission import LivingRoomPlaybackMission
from verified_alexa.media_runtime import atomic_save, config_path
from verified_alexa.cdp import BrowserConnection
from verified_alexa.mcp_client import MCPClient
from verified_alexa.simulator import run_owned

@pytest.fixture(scope='module')
def runtime(tmp_path_factory):
    root=tmp_path_factory.mktemp('actual_media')
    # The initial fault is an explicit fixture control, not an automatic mission
    # reset. New user goals later operate the same persistent physical target.
    app=run_owned(root,port=0,headless=True,initial_fault=True); launch=next(app)
    yield root,launch
    app.close()

def test_real_failure_recovery_drift_same_mission(runtime):
    root,launch=runtime; c=MCPClient(launch['mcp_url'],root/'acceptance_mcp.json')
    assert c.call('execute_verified_goal',{'goal':'Alexa, turn off the living room TV.'})['final_status']=='UNSUPPORTED'
    r=c.call('execute_verified_goal',{'goal':'Play clip A'})
    assert r['tool_claim']['status']=='SUCCESS'
    assert r['first_independent_verification']['http_status']==503
    assert r['first_independent_verification']['passed'] is False
    assert r['final_status']=='VERIFIED_DONE'
    assert r['final_independent_verification']['rendered_frame_delta']>=12
    assert r['goal_contract']['operation']=='play' and r['goal_contract']['content_id']=='clip-a'
    assert r['final_independent_verification']['checks']['content_identity'] is True
    mid,aid,target=r['mission_id'],r['attempt_id'],r['target_id']; m=LivingRoomPlaybackMission(root)
    path=m._path(mid,'.'+aid+'.receipt.json'); before=path.read_bytes()
    history=c.call('get_execution_receipt',{'run_id':mid})
    assert history['historical'] and history['current_status'] is None
    c.call('inject_playback_fault',{'mission_id':mid})
    fresh=c.call('execute_verified_goal',{'goal':'現在還在播嗎？','mission_id':mid})
    assert fresh['previous_status']=='VERIFIED_DONE' and fresh['final_status']=='NOT_VERIFIED'
    assert fresh['evidence']['http_status']==503
    code='from verified_alexa.mcp_client import MCPClient;import json,sys;print(json.dumps(MCPClient(sys.argv[1]).call("recheck_playback",{"mission_id":sys.argv[2]})))'
    child=subprocess.run([sys.executable,'-c',code,launch['mcp_url'],mid],capture_output=True,timeout=30,check=True)
    cross=json.loads(child.stdout)
    assert cross['final_status']=='NOT_VERIFIED'
    assert cross['evidence']['challenge']!=fresh['evidence']['challenge']
    fixed=c.call('execute_verified_goal',{'goal':'好，修它','mission_id':mid})
    assert fixed['mission_id']==mid and fixed['target_id']==target and fixed['attempt_id']!=aid
    assert fixed['final_status']=='VERIFIED_DONE' and path.read_bytes()==before
    atomic_save(root/'ACCEPTANCE_FLOW.json',dict(original=r,historical=history,fault=fresh,new_process=cross,repaired=fixed))

def test_forged_success_and_counter_fields_cannot_pass(runtime):
    root,_=runtime; m=LivingRoomPlaybackMission(root)
    def lying(binding):
        path=config_path(root,binding['target_id']); d=json.loads(path.read_text(encoding='utf-8'))
        d.update(route='stale',playback_http_status=200,decoded_frames=999,passed=True,final_status='VERIFIED_DONE'); atomic_save(path,d)
        m.executor.load_player(binding)
        return dict(status='SUCCESS',passed=True,decoded_frames=999,verified=True)
    m._tool_restart=lying
    r=m.run(mission_id='lying',allow_recovery=False)
    assert r['tool_claim']['verified'] is True and r['final_status']=='NOT_VERIFIED'
    assert r['first_independent_verification']['http_status']==503
    assert r['first_independent_verification']['probe_pid']!=os.getpid()

def test_http_media_is_not_player_playing(runtime):
    root,_=runtime; m=LivingRoomPlaybackMission(root); r=m.run(mission_id='paused')
    with BrowserConnection(r['binding']['cdp_endpoint'],r['binding']['browser_target_id']) as b:
        b.evaluate("document.querySelector('#player').pause()")
    fresh=m.resume('paused')
    assert fresh['evidence']['checks']['stream_identity'] is True
    assert fresh['evidence']['checks']['goal_state'] is False
    assert fresh['evidence']['observed_state']=='paused'
    assert fresh['final_status']=='NOT_VERIFIED'

def test_receipt_edit_cannot_override_fault(runtime):
    root,_=runtime; m=LivingRoomPlaybackMission(root); r=m.run(mission_id='edited'); m.inject_drift('edited')
    path=m._path('edited','.'+r['attempt_id']+'.receipt.json')
    d=json.loads(path.read_text(encoding='utf8')); d['final_status']='VERIFIED_DONE'
    d['final_independent_verification'].update(passed=True,http_status=200)
    d['receipt_sha256']=m._hash(d); atomic_save(path,d)
    assert m.resume('edited')['final_status']=='NOT_VERIFIED'
    d['goal']='tampered'; atomic_save(path,d)
    with pytest.raises(ValueError): m.get_receipt('edited',r['attempt_id'])

def test_missions_and_receipt_bindings_cannot_cross(runtime):
    root,_=runtime; m=LivingRoomPlaybackMission(root)
    a=m.run('Play clip A',mission_id='mission-a'); b=m.run('Play clip B',mission_id='mission-b')
    assert a['target_id']==b['target_id']
    assert a['goal_contract']['content_id']!=b['goal_contract']['content_id']
    assert m.resume('mission-a')['final_status']=='SUPERSEDED'
    assert m.resume('mission-b')['final_status']=='VERIFIED_DONE'
    path=m._path('mission-a','.'+a['attempt_id']+'.receipt.json')
    a['binding']={**b['binding'],'mission_id':a['mission_id'],'attempt_id':a['attempt_id']}
    a['receipt_sha256']=m._hash(a); atomic_save(path,a)
    with pytest.raises(ValueError): m.get_receipt('mission-a',a['attempt_id'])

def test_reload_and_bounded_recovery(runtime):
    root,launch=runtime; m=LivingRoomPlaybackMission(root); r=m.run(mission_id='reload')
    with BrowserConnection(launch['cdp_endpoint'],launch['browser_target_id']) as b: b.command('Page.reload')
    time.sleep(.7)
    assert m.resume('reload')['final_status']=='NOT_VERIFIED'
    a=m.repair('reload')
    assert a['target_id']==r['target_id'] and a['final_status']=='VERIFIED_DONE'
    m.inject_drift('reload'); assert m.repair('reload')['final_status']=='VERIFIED_DONE'
    m.inject_drift('reload'); assert m.repair('reload')['final_status']=='NOT_VERIFIED'
    assert m._read('reload')['action_attempts']==3
    assert len(m._read('reload')['attempts'])==4  # one independently recorded check, three action attempts

def test_observer_failure_is_not_success(runtime):
    root,_=runtime; m=LivingRoomPlaybackMission(root); r=m.run(mission_id='unavailable')
    assert m._probe({**r['binding'],'cdp_endpoint':'http://127.0.0.1:1'})['passed'] is False

def test_official_sdk_client(runtime):
    import anyio
    from mcp.client import Client
    root,launch=runtime
    async def proof():
        async with Client(launch['mcp_url'],mode='legacy') as c:
            tools=await c.list_tools()
            r=await c.call_tool('execute_verified_goal',{'goal':'switch off the living room TV'})
            d=r.model_dump(mode='json')
            assert not r.is_error and 'UNSUPPORTED' in json.dumps(d)
            atomic_save(root/'OFFICIAL_SDK_CLIENT.json',dict(sdk='mcp 2.3.0',tools=tools.model_dump(mode='json'),call_result=d))
    anyio.run(proof)

def test_audio_fixture_repairs_same_mission_through_mcp(runtime):
    from verified_alexa.voice_input import offline_speech_available
    if not offline_speech_available(): pytest.skip('Optional Windows zh-TW recognizer absent; not an audio PASS')
    import base64
    from pathlib import Path
    from urllib.request import Request, urlopen
    root, launch = runtime
    m = LivingRoomPlaybackMission(root)
    original = m.run(mission_id='audio-same-mission')
    m.inject_drift('audio-same-mission')
    wav = Path(__file__).with_name('fixtures') / 'fix-it-zh.wav'
    body = {'mission_id': original['mission_id'], 'audio_base64': base64.b64encode(wav.read_bytes()).decode()}
    request = Request(launch['ui_url']+'api/voice', data=json.dumps(body).encode(), headers={'Content-Type':'application/json'}, method='POST')
    before = m._read(original['mission_id'])['attempts'][:]
    with urlopen(request, timeout=30) as response:
        voice = json.loads(response.read())
    assert voice['recognition']['text'] == '好修它'
    assert voice['confirmation_required'] is True and voice['result'] is None
    assert m._read(original['mission_id'])['attempts'] == before
    assert m.resume(original['mission_id'])['final_status']=='NOT_VERIFIED'
    confirmation = Request(launch['ui_url']+'api/voice/confirm',
                           data=json.dumps({'confirmation_id':voice['confirmation_id']}).encode(),
                           headers={'Content-Type':'application/json'}, method='POST')
    with urlopen(confirmation, timeout=30) as response:
        confirmed = json.loads(response.read())
    fixed = confirmed['result']
    assert fixed['mission_id'] == original['mission_id'] and fixed['target_id'] == original['target_id']
    assert fixed['attempt_id'] != original['attempt_id'] and fixed['final_status'] == 'VERIFIED_DONE'
    assert fixed['receipt_sha256'] == m._hash(fixed)
    atomic_save(root/'AUDIO_TO_MCP_FLOW.json', {'scope':'PCM fixture plus explicit confirmation, not a physical microphone test','preview':voice,'confirmed':confirmed,'original':original,'fixed':fixed})
