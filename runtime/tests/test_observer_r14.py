"""Real Chrome/compositor/decoder R14 regressions; preserve original probes.

Uses fresh owned state and a real observer subprocess. No successful observer
result or screenshot is stubbed. Evidence remains outside release source.
"""
import json
import os
import subprocess
import sys
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime,timezone
from pathlib import Path
import pytest
from verified_alexa.simulator import run_owned
from verified_alexa.cdp import BrowserConnection
from verified_alexa.media_catalog import get_clip,register_media,catalog,decoder_run
from verified_alexa.media_runtime import MediaExecutor,atomic_save,config_path

OUT=Path(os.getenv('VERIFIED_ALEXA_R14_TEST_EVIDENCE',str(Path(__file__).resolve().parents[2]/'r14_observer_evidence'/'green-native')))

@pytest.fixture(scope='module')
def actual_runtime():
    root=OUT/('owned-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ'))
    prior=os.environ.get('VERIFIED_ALEXA_STATE_DIR');os.environ['VERIFIED_ALEXA_STATE_DIR']=str(root)
    runtime=run_owned(root,port=0,headless=True)
    launch=next(runtime)
    with BrowserConnection(launch['cdp_endpoint'],launch['browser_target_id']) as browser:identity=browser.player_identity()
    launch.update(identity);atomic_save(root/'launch.json',launch)
    yield root,launch,MediaExecutor(root),identity
    runtime.close()
    if prior is None:os.environ.pop('VERIFIED_ALEXA_STATE_DIR',None)
    else:os.environ['VERIFIED_ALEXA_STATE_DIR']=prior

def evaluate(runtime,script,native=False):
    _,launch,_,_=runtime
    with BrowserConnection(launch['cdp_endpoint'],launch['browser_target_id']) as b:
        b.socket.settimeout(15)
        return b.evaluate_native(script) if native else b.evaluate(script)

def binding(runtime,name,operation='play',content='clip-a'):
    root,_,executor,identity=runtime
    value=executor.bind(name,operation,content)
    return {**value,**identity,'state_dir':str(root),'attempt_id':'attempt-'+name}

def probe(runtime,name,value):
    root,_,_,_=runtime
    env=os.environ.copy();env['PYTHONPATH']=str(Path(__file__).resolve().parents[1]/'src')
    completed=subprocess.run([sys.executable,'-m','verified_alexa.playback_observer',json.dumps(value),uuid.uuid4().hex],
        capture_output=True,env=env,timeout=15)
    (root/(name+'.stdout.json')).write_bytes(completed.stdout)
    (root/(name+'.stderr.log')).write_bytes(completed.stderr)
    assert completed.returncode==0,completed.stderr
    observation=json.loads(completed.stdout)
    assert observation['probe_pid']!=os.getpid()
    assert observation['binding']==value
    return observation

def play(runtime,name,content='clip-a'):
    value=binding(runtime,name,content=content);runtime[2].execute(value);time.sleep(.3);return value

def cleanup(runtime):
    evaluate(runtime,"(()=>{document.querySelector('#review-decoy')?.remove();document.querySelector('#review-freeze')?.remove();const v=document.querySelector('#player');v.removeAttribute('style');return true;})()")

def overlay(runtime,canvas=False,pointer_none=False):
    code="""(async()=>{const v=document.querySelector('#player'),p=v.parentElement;p.style.position='relative';
const r=v.getBoundingClientRect(),pr=p.getBoundingClientRect(),d=document.createElement(TYPE);d.id=ID;
d.style.cssText=`position:absolute;left:${r.left-pr.left}px;top:${r.top-pr.top}px;width:${r.width}px;height:${r.height}px;z-index:20;POINTER`;
if(TYPE==='canvas'){d.width=640;d.height=360;d.getContext('2d').drawImage(v,0,0,640,360);}else{d.src='/assets/clip-b.webm';d.muted=true;d.loop=true;p.appendChild(d);await d.play();return true;}p.appendChild(d);return true;})()"""
    return evaluate(runtime,code.replace('TYPE',json.dumps('canvas' if canvas else 'video')).replace('ID',json.dumps('review-freeze' if canvas else 'review-decoy')).replace('POINTER','pointer-events:none' if pointer_none else ''))

def test_actual_visible_compositor_and_source_reference(actual_runtime):
    a=play(actual_runtime,'baseline')
    evidence=probe(actual_runtime,'BASELINE',a)
    assert evidence['passed'],evidence
    assert evidence['checks']['reference_content'] and evidence['checks']['visible_compositor'] and evidence['checks']['element_identity']
    assert all(frame['sha256'] for frame in evidence['presentation_frames'])

@pytest.mark.parametrize('css',['opacity:0','position:fixed;left:-10000px;top:0;width:640px;height:360px'])
def test_hidden_or_offscreen_native_player_rejected(actual_runtime,css):
    a=play(actual_runtime,'hidden-'+str(len(css)))
    evaluate(actual_runtime,"document.querySelector('#player').style.cssText="+json.dumps(css))
    try:
        evidence=probe(actual_runtime,'HIDDEN_'+str(len(css)),a)
        assert evidence['passed'] is False,evidence
        assert evidence['checks']['visible_compositor'] is False
    finally:cleanup(actual_runtime)

@pytest.mark.parametrize('pointer_none',[False,True])
def test_visible_b_occludes_measured_a(actual_runtime,pointer_none):
    a=play(actual_runtime,'occluded-a');overlay(actual_runtime,pointer_none=pointer_none)
    try:
        evidence=probe(actual_runtime,'VISIBLE_B_'+str(pointer_none),a)
        assert evidence['native_playback_observed'] is True,evidence
        assert evidence['checks']['stream_identity'] is True
        assert evidence['passed'] is False and evidence['checks']['visible_compositor'] is False
    finally:cleanup(actual_runtime)

def test_pointer_transparent_frozen_canvas_rejected_by_actual_compositor(actual_runtime):
    a=play(actual_runtime,'frozen-a');overlay(actual_runtime,canvas=True,pointer_none=True)
    try:
        evidence=probe(actual_runtime,'POINTER_NONE_FROZEN_CANVAS',a)
        assert evidence['native_playback_observed'] is True,evidence
        assert all(s['presentation']['unobstructed'] for s in evidence['samples']),evidence
        assert evidence['passed'] is False and evidence['checks']['visible_compositor'] is False,evidence
    finally:cleanup(actual_runtime)

def test_duplicate_selector_cannot_rebind_element(actual_runtime):
    a=play(actual_runtime,'duplicate-a')
    evaluate(actual_runtime,"(async()=>{const v=document.querySelector('#player'),d=v.cloneNode(true);d.style.cssText='position:fixed;left:-10000px;top:0;width:640px;height:360px';d.setAttribute('data-review-duplicate','yes');document.body.prepend(d);await d.play();return true;})()")
    try:
        evidence=probe(actual_runtime,'DUPLICATE_PLAYER',a)
        assert evidence['passed'] is False and evidence['checks']['element_identity'] is False,evidence
    finally:evaluate(actual_runtime,"document.querySelector('[data-review-duplicate]')?.remove()")

def test_same_document_replacement_cannot_rebind_element(actual_runtime):
    a=play(actual_runtime,'replaced-a')
    evaluate(actual_runtime,"(async()=>{const v=document.querySelector('#player'),d=v.cloneNode(true);window.__reviewOriginal=v;v.replaceWith(d);await d.play();return true;})()")
    try:
        evidence=probe(actual_runtime,'REPLACED_PLAYER',a)
        assert evidence['passed'] is False,evidence
    finally:evaluate(actual_runtime,"document.querySelector('#player').replaceWith(window.__reviewOriginal)")

@pytest.mark.parametrize('operation',['pause','stop'])
def test_pause_stop_cannot_complete_over_playing_visible_decoy(actual_runtime,operation):
    play(actual_runtime,'before-'+operation);overlay(actual_runtime)
    value=binding(actual_runtime,operation+'-decoy',operation)
    actual_runtime[2].execute(value)
    try:
        evidence=probe(actual_runtime,operation.upper()+'_B_STILL_PLAYING',value)
        assert evidence['checks']['goal_state'] is True,evidence
        assert evidence['passed'] is False and evidence['checks']['visible_compositor'] is False,evidence
        assert evaluate(actual_runtime,"document.querySelector('#review-decoy').paused") is False
    finally:cleanup(actual_runtime)

def test_paused_native_prototype_spoof_still_rejected(actual_runtime):
    a=play(actual_runtime,'prototype-a')
    evaluate(actual_runtime,"(()=>{const v=document.querySelector('#player');v.pause();window.__reviewPaused=Object.getOwnPropertyDescriptor(HTMLMediaElement.prototype,'paused');Object.defineProperty(HTMLMediaElement.prototype,'paused',{get:()=>false,configurable:true});return true;})()")
    try:
        evidence=probe(actual_runtime,'PROTOTYPE_SPOOF',a)
        assert evidence['passed'] is False and evidence['checks']['goal_state'] is False,evidence
        assert all(s['paused'] for s in evidence['samples'])
    finally:evaluate(actual_runtime,"(()=>{Object.defineProperty(HTMLMediaElement.prototype,'paused',window.__reviewPaused);return true;})()")

def test_generic_registered_file_no_marker_authority(actual_runtime):
    root,_,_,_=actual_runtime
    local=register_media(root,get_clip('clip-long')['asset_path'],title='Independent local selection')
    assert 'marker_samples' not in local and local['duration']>=60
    value=play(actual_runtime,'registered-long',local['content_id'])
    evidence=probe(actual_runtime,'REGISTERED_LONG',value)
    assert evidence['passed'],evidence
    assert evidence['checks']['reference_content']
    assert any(record['content_id']==local['content_id'] for record in catalog(root))
    # Changing registered bytes must invalidate fresh catalog/observer truth.
    path=local['asset_path'];before=path.read_bytes();path.write_bytes(before+b'changed')
    try:
        with pytest.raises(ValueError,match='digest'):get_clip(local['content_id'],root)
        changed=probe(actual_runtime,'REGISTERED_BYTES_CHANGED',value)
        assert changed['passed'] is False
    finally:path.write_bytes(before)

def test_long_clip_endpoint_stop_position(actual_runtime):
    value=play(actual_runtime,'long-boundary','clip-long')
    evaluate(actual_runtime,"(()=>{const v=document.querySelector('#player');v.pause();v.currentTime=45;return true;})()",native=True)
    time.sleep(.25)
    endpoint={**value,'operation':'stop','desired_operation':'stop','stop_position_seconds':45}
    evidence=probe(actual_runtime,'LONG_STOP_AT_45',endpoint)
    assert evidence['passed'],evidence
    assert all(abs(sample['current_time']-45)<=.2 for sample in evidence['samples'])
    incorrect=probe(actual_runtime,'LONG_STOP_WRONG_ENDPOINT',{**endpoint,'stop_position_seconds':46})
    assert incorrect['passed'] is False and incorrect['checks']['goal_state'] is False
    evaluate(actual_runtime,"(()=>{const v=document.querySelector('#player');v.pause();v.currentTime=60;return true;})()",native=True)
    time.sleep(.25)
    full=probe(actual_runtime,'LONG_STOP_AT_DURATION',{**endpoint,'stop_position_seconds':60})
    assert full['passed'],full

@pytest.mark.parametrize('portrait',[False,True])
def test_actual_mp4_bytes_registration_and_playback(actual_runtime,portrait):
    root,_,_,_=actual_runtime
    path=root/('local-selected-portrait.mp4' if portrait else 'local-selected.mp4')
    conversion=['-vf','scale=360:640,setsar=1'] if portrait else []
    process=decoder_run(['-hide_banner','-loglevel','error','-y','-i',str(get_clip('clip-long')['asset_path']),
        '-t','12',*conversion,'-c:v','libx264','-preset','ultrafast','-pix_fmt','yuv420p',str(path)],capture_output=True,timeout=20)
    assert process.returncode==0,process.stderr
    selected=register_media(root,path.read_bytes(),filename='my-film.mp4',title='My selected MP4')
    assert selected['asset_path'].suffix=='.mp4' and selected['duration']>=11
    value=play(actual_runtime,'selected-mp4',selected['content_id'])
    if portrait:evaluate(actual_runtime,"document.querySelector('#player').style.objectFit='contain'")
    evidence=probe(actual_runtime,'SELECTED_MP4_'+str(portrait),value)
    assert evidence['passed'],evidence
    assert evidence['checks']['reference_content'] and evidence['checks']['visible_compositor']
    cleanup(actual_runtime)

@pytest.mark.parametrize('decoy',[False,True])
def test_empty_native_stop_has_no_visible_completion_proof(actual_runtime,decoy):
    root,_,executor,identity=actual_runtime
    evaluate(actual_runtime,"(()=>{const v=document.querySelector('#player');v.removeAttribute('src');v.load();v.pause();return true;})()",native=True)
    value={**executor.bind('empty-final-'+str(decoy),'stop',None),**identity,'state_dir':str(root),'attempt_id':'attempt-empty-'+str(decoy)}
    executor.execute(value)
    if decoy:overlay(actual_runtime,pointer_none=True)
    try:
        evidence=probe(actual_runtime,'EMPTY_STOP_'+str(decoy),value)
        assert evidence['native_stop_observed'] and evidence['observed_state']=='stopped'
        assert evidence['passed'] is False,evidence
        assert evidence['checks']['visible_compositor'] is False and evidence['checks']['reference_content'] is False
        assert evidence['readiness']['status']=='genuinely_empty'
        assert evidence['empty_native_stopped'] is True and evidence['completion_limit']
        # Cleared Chrome media may retain the previous currentSrc. It must
        # have no requested source, native NETWORK_EMPTY/HAVE_NOTHING, or
        # decoded frame; stale currentSrc is never visible completion proof.
        assert all(sample['paused'] and not sample['requested_src'] and sample['network_state']==0
            and sample['ready_state']==0 and sample['width']==0 and sample['height']==0 for sample in evidence['samples'])
        if decoy:assert evaluate(actual_runtime,"document.querySelector('#review-decoy').paused") is False
    finally:cleanup(actual_runtime)

def test_three_concurrent_observers_do_not_emulate_or_resize_viewport(actual_runtime):
    value=play(actual_runtime,'parallel-viewports','clip-long')
    width=evaluate(actual_runtime,"document.querySelector('#player').getBoundingClientRect().width",native=True)
    with ThreadPoolExecutor(max_workers=3) as pool:
        results=list(pool.map(lambda index:probe(actual_runtime,'CONCURRENT_'+str(index),value),range(3)))
    assert len({result['probe_pid'] for result in results})==3
    for result in results:
        assert result['passed'],result
        assert all(abs(sample['presentation']['element_rect']['width']-width)<1 for sample in result['samples'])
