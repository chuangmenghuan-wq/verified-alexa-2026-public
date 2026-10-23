"""R13 actual media/native-rendering regressions; no successful outcome stubs.

RED baseline: evidence/r13/observer/RED_R12_REPRODUCED.json captured R12
native-paused player receiving VERIFIED_DONE after the exact page getter attack.
"""
import json
from pathlib import Path
import pytest
from verified_alexa.simulator import run_owned
from verified_alexa.media_runtime import MediaExecutor,atomic_save,config_path
from verified_alexa.consumer_media_mission import LivingRoomPlaybackMission
from verified_alexa.cdp import BrowserConnection
from verified_alexa.media_catalog import get_clip

@pytest.fixture(scope='module')
def native_runtime(tmp_path_factory):
    root=tmp_path_factory.mktemp('r13_native')
    app=run_owned(root,port=0,headless=True)
    launch=next(app)
    yield root,launch,MediaExecutor(root),LivingRoomPlaybackMission(root)
    app.close()

def binding(executor,mid,operation='play',content='clip-a'):
    return {**executor.bind(mid,operation,content),'attempt_id':'attempt-native-test'}

def record(root,name,data):
    atomic_save(root/(name+'.json'),data)

def test_bind_preserves_real_world_and_target(native_runtime):
    root,_,executor,mission=native_runtime
    a=binding(executor,'stable-a')
    path=config_path(root,a['target_id'])
    before=path.read_bytes()
    b=binding(executor,'stable-b',content='clip-b')
    assert a['target_id']==b['target_id']
    assert path.read_bytes()==before
    assert json.loads(before)['route']=='live'
    assert a['content_sha256']!=b['content_sha256']

def test_real_stream_and_decoded_content_and_native_progress(native_runtime):
    root,_,executor,mission=native_runtime
    a=binding(executor,'real-a')
    executor.execute(a)
    evidence=mission._probe(a)
    record(root,'REAL_A',evidence)
    assert evidence['passed'],evidence
    assert evidence['http_sha256']==get_clip('clip-a')['sha256']
    assert evidence['checks']['content_identity']
    assert evidence['samples'][0]['frame_hash']!=evidence['samples'][1]['frame_hash']
    assert evidence['rendered_frame_delta']>=12

def test_http_a_but_buffered_player_b_rejects_a(native_runtime):
    root,_,executor,mission=native_runtime
    b=binding(executor,'cached-b',content='clip-b')
    executor.execute(b)
    assert mission._probe(b)['passed']
    path=config_path(root,b['target_id'])
    config=json.loads(path.read_text(encoding='utf-8'))
    config['content_id']='clip-a'
    atomic_save(path,config)
    a=binding(executor,'expect-a-with-buffered-b')
    evidence=mission._probe(a)
    record(root,'HTTP_A_PLAYER_B',evidence)
    assert evidence['checks']['stream_identity'] is True,evidence
    assert evidence['checks']['target_identity'] is True,evidence
    assert evidence['checks']['content_identity'] is False,evidence
    assert evidence['passed'] is False

def test_r12_native_paused_getter_spoof_now_rejected(native_runtime):
    root,_,executor,mission=native_runtime
    a=binding(executor,'getter-spoof-a')
    executor.execute(a)
    assert mission._probe(a)['passed']
    with BrowserConnection(a['cdp_endpoint'],a['browser_target_id']) as browser:
        browser.evaluate("""(()=>{const v=document.querySelector('#player');v.pause();
          Object.defineProperty(v,'paused',{get:()=>false,configurable:true});
          Object.defineProperty(v,'currentTime',{get:()=>performance.now()/1000,configurable:true});
          v.getVideoPlaybackQuality=()=>({totalVideoFrames:Math.floor(performance.now()/10),droppedVideoFrames:0});return true;})()""")
    evidence=mission._probe(a)
    record(root,'GETTER_SPOOF_REJECTED',evidence)
    assert evidence['passed'] is False,evidence
    assert evidence['checks']['content_identity'] is True
    assert evidence['checks']['goal_state'] is False
    assert all(s['paused'] for s in evidence['native_samples'])
    with BrowserConnection(a['cdp_endpoint'],a['browser_target_id']) as browser:
        browser.evaluate("(()=>{const v=document.querySelector('#player');delete v.paused;delete v.currentTime;delete v.getVideoPlaybackQuality;return true;})()")

def test_pause_and_stop_are_their_own_native_goal_predicates(native_runtime):
    root,_,executor,mission=native_runtime
    a=binding(executor,'play-before-pause')
    executor.execute(a)
    assert mission._probe(a)['passed']
    pause=binding(executor,'pause-existing','pause')
    executor.execute(pause)
    paused=mission._probe(pause)
    record(root,'PAUSE',paused)
    assert paused['passed'],paused
    assert paused['goal_operation']=='pause' and paused['observed_state']=='paused'
    assert paused['playback_observed'] is False
    assert mission._probe(a)['passed'] is False
    stop=binding(executor,'stop-existing','stop')
    executor.execute(stop)
    stopped=mission._probe(stop)
    record(root,'STOP',stopped)
    assert stopped['passed'],stopped
    assert stopped['goal_operation']=='stop' and stopped['observed_state']=='stopped'
    assert all(abs(s['current_time'])<=.05 for s in stopped['native_samples'])

def test_check_binding_captures_prior_desired_state_without_execution(native_runtime):
    root,_,executor,mission=native_runtime
    stop=binding(executor,'stop-for-check','stop')
    executor.execute(stop)
    before=config_path(root,stop['target_id']).read_bytes()
    check=binding(executor,'check-stopped','check',None)
    evidence=mission._probe(check)
    assert check['desired_operation']=='stop'
    assert evidence['passed'] and evidence['goal_operation']=='check'
    assert evidence['observed_state']=='stopped'
    assert config_path(root,stop['target_id']).read_bytes()==before
    record(root,'CHECK_STOPPED',evidence)

def test_json_fake_outcome_fields_and_success_do_not_override_http_failure(native_runtime):
    root,_,executor,mission=native_runtime
    a=binding(executor,'bad-real-route')
    path=config_path(root,a['target_id'])
    config=json.loads(path.read_text(encoding='utf-8'))
    config.update(route='stale',passed=True,playback_http_status=200,decoded_frames=999)
    atomic_save(path,config)
    action=executor.execute(a)
    evidence=mission._probe(a)
    assert action['status']=='SUCCESS'
    assert evidence['http_status']==503 and evidence['passed'] is False
    record(root,'FAKE_OUTCOME_REJECTED',{'action':action,'observation':evidence})
    executor.repair(a)
    assert mission._probe(a)['passed']

def test_buffered_origin_outage_reports_player_and_stream_separately(native_runtime):
    root,_,executor,mission=native_runtime
    a=binding(executor,'buffer-origin-failure')
    executor.execute(a)
    assert mission._probe(a)['passed']
    path=config_path(root,a['target_id'])
    config=json.loads(path.read_text(encoding='utf-8'))
    config['route']='stale'
    atomic_save(path,config)
    evidence=mission._probe(a)
    record(root,'BUFFERED_ORIGIN_OUTAGE',evidence)
    assert evidence['http_status']==503 and evidence['passed'] is False
    assert evidence['playback_observed'] is True,evidence
    assert evidence['observed_state']=='playing'
    executor.repair(a)

def test_non_play_repair_cannot_start_video(native_runtime):
    _,_,executor,_=native_runtime
    pause=binding(executor,'repair-must-not-play','pause')
    with pytest.raises(ValueError,match='pause or stop'):
        executor.repair(pause)

def test_empty_target_stop_does_not_start_playback(native_runtime):
    root,launch,executor,mission=native_runtime
    with BrowserConnection(launch['cdp_endpoint'],launch['browser_target_id']) as browser:
        browser.command('Page.reload')
    # Reload yields an empty native element; actual stop adds binding but never src.
    import time
    time.sleep(.5)
    # A real document reload changes the native DOM identity. Use the trusted
    # same-owned-URL/loader reassociation, rather than reusing a stale node ID.
    stop=executor.resolve_binding(binding(executor,'stop-already-empty','stop'))
    executor.execute(stop)
    evidence=mission._probe(stop)
    record(root,'EMPTY_STOP',evidence)
    assert evidence['passed'] is False,evidence
    assert evidence['observed_state']=='stopped'
    assert evidence['native_stop_observed'] is True
    assert evidence['readiness']['status']=='genuinely_empty'
    assert evidence['empty_native_stopped'] is True and evidence['completion_limit']
    assert evidence['checks']['reference_content'] is False
    assert evidence['checks']['visible_compositor'] is False
    assert all(s['paused'] and not s['current_src'] for s in evidence['samples'])
